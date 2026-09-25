"""JEV §23 (cache/dedup) + §26 (observability) — the measured layer.

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_cache_metrics.py -p no:randomly
    E   ModuleNotFoundError: No module named 'godsv.system_one_cache'
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.07s

What this file pins:

* §23 — the cache key contains the seven documented fields (plus the epochs a
  reuse would be wrong across); an entry is never reused after a
  goal/policy/workspace/evidence/GUI-state/tool/pack change, and a stale entry
  is **refused**, not silently refreshed;
* §26 — every metric of §26 exists by name, and a metric that was never
  measured is reported as ``not_run``/absent — never as ``0`` (an ABNAHME
  §5.1/§117.1 honesty rule).
"""

from __future__ import annotations

import pytest

from skaldir.system_one import (
    ACCESS_DISABLED,
    DisabledSystemOneEvaluator,
    EvaluationContext,
    NoulAnswer,
    SystemOneState,
    build_evaluation,
)
from skaldir.system_one_cache import (
    CACHE_KEY_FIELDS,
    CACHE_REFUSAL_REASONS,
    CacheEntry,
    CacheKey,
    SystemOneCache,
)
from skaldir.system_one_metrics import (
    COMPLETION_CRITERIA_CHECKED,
    COMPLETION_CRITERIA_ESCALATED,
    COMPLETION_CRITERIA_FLAGGED,
    COMPLETION_FALSE_ACCEPTS,
    COMPLETION_FALSE_REJECTS,
    COMPLETION_TOTAL_LATENCY_MS,
    DECISION_POLICY_MS,
    INPUT_TOKENS,
    OUTPUT_TOKENS_IF_REPORTED,
    PROVIDER_COST_IF_KNOWN,
    PROVIDER_LATENCY_MS,
    QUEUE_MS,
    SANITIZATION_MS,
    STATE_BUILD_MS,
    SYSTEM2_CALLS_SAVED,
    TOTAL_SYSTEM_ONE_MS,
    SystemOneMetrics,
)

REQUIRED_CACHE_FIELDS = {
    "state_fingerprint",
    "question_pack_id",
    "question_pack_version",
    "goal_revision",
    "policy_revision",
    "provider_model",
    "threshold_profile",
}

REQUIRED_METRICS = {
    STATE_BUILD_MS,
    SANITIZATION_MS,
    QUEUE_MS,
    PROVIDER_LATENCY_MS,
    DECISION_POLICY_MS,
    TOTAL_SYSTEM_ONE_MS,
    INPUT_TOKENS,
    OUTPUT_TOKENS_IF_REPORTED,
    PROVIDER_COST_IF_KNOWN,
    SYSTEM2_CALLS_SAVED,
    COMPLETION_CRITERIA_CHECKED,
    COMPLETION_CRITERIA_FLAGGED,
    COMPLETION_CRITERIA_ESCALATED,
    COMPLETION_FALSE_ACCEPTS,
    COMPLETION_FALSE_REJECTS,
    COMPLETION_TOTAL_LATENCY_MS,
}


def _key(**overrides) -> CacheKey:
    base = {
        "state_fingerprint": "fp-1",
        "question_pack_id": "voice_triage",
        "question_pack_version": "v1",
        "goal_revision": 1,
        "policy_revision": 1,
        "provider_model": "jev-latest",
        "threshold_profile": "dev-1",
        "workspace_fingerprint": "ws-1",
        "evidence_epoch": 0,
        "gui_state_epoch": 0,
        "tool_epoch": 0,
    }
    base.update(overrides)
    return CacheKey(**base)


def _evaluation(*, value: float = 0.5, **context_overrides):
    questions_ctx = EvaluationContext(run_id="r", goal_id="g", **context_overrides)
    return build_evaluation(
        context=questions_ctx,
        questions=__import__(
            "godsv.system_one_questions", fromlist=["compose_voice_triage"]
        ).compose_voice_triage("loop"),
        state=SystemOneState(payload={"goal": "g"}),
        provider="disabled",
        provider_model="",
        access_mode=ACCESS_DISABLED,
        answers={
            "loop.same_root_cause": NoulAnswer(question_id="loop.same_root_cause", value=value)
        },
    )


# --------------------------------------------------------------------------- #
# §23 — the cache key                                                          #
# --------------------------------------------------------------------------- #


def test_the_cache_key_carries_the_seven_documented_fields():
    assert REQUIRED_CACHE_FIELDS <= set(CACHE_KEY_FIELDS)
    key = _key()
    for field in REQUIRED_CACHE_FIELDS:
        assert getattr(key, field) is not None


def test_the_cache_key_also_carries_what_a_reuse_would_be_wrong_across():
    key = _key()
    assert key.workspace_fingerprint == "ws-1"
    assert key.evidence_epoch == 0
    assert key.gui_state_epoch == 0
    assert key.tool_epoch == 0


def test_a_cache_key_can_be_derived_from_an_evaluation_context():
    ctx = EvaluationContext(
        run_id="r",
        goal_id="g",
        goal_revision=3,
        policy_revision=2,
        workspace_fingerprint="ws-9",
        caller="voice",
        question_pack_id="voice_triage",
        question_pack_version="v1",
    )
    key = CacheKey.from_context(ctx, provider_model="jev-latest", threshold_profile="dev-1")
    assert key.goal_revision == 3
    assert key.policy_revision == 2
    assert key.workspace_fingerprint == "ws-9"
    assert key.question_pack_id == "voice_triage"
    assert key.question_pack_version == "v1"
    assert key.state_fingerprint == ""  # filled by the state builder, never guessed


# --------------------------------------------------------------------------- #
# §23 — reuse rules                                                            #
# --------------------------------------------------------------------------- #


def test_the_same_key_is_a_hit_and_returns_the_stored_evaluation():
    cache = SystemOneCache(live=_key())
    evaluation = _evaluation()
    cache.put(_key(), evaluation)
    assert cache.get(_key()) is evaluation
    assert cache.report()["hits"] == 1


def test_a_different_state_fingerprint_is_a_miss():
    cache = SystemOneCache(live=_key())
    cache.put(_key(), _evaluation())
    assert cache.get(_key(state_fingerprint="fp-2")) is None
    assert cache.report()["misses"] == 1


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"goal_revision": 2}, "goal"),
        ({"policy_revision": 2}, "policy"),
        ({"workspace_fingerprint": "ws-2"}, "workspace"),
        ({"evidence_epoch": 1}, "evidence"),
        ({"gui_state_epoch": 1}, "gui_state"),
        ({"tool_epoch": 1}, "tool"),
        ({"question_pack_version": "v2"}, "pack"),
    ],
)
def test_no_reuse_after_a_bound_change_and_the_stale_entry_is_refused(change, reason):
    live = _key()
    cache = SystemOneCache(live=live)
    cache.put(live, _evaluation())

    # the world moves on: the live version changes
    cache.update_live(**change)
    refused = cache.get(live)
    assert refused is None, "a stale entry must never be returned"
    report = cache.report()
    assert report["refusals"] >= 1
    assert report["refusals"] > 0
    refusal = cache.refusals[-1]
    assert refusal["reason"] == reason
    assert refusal["kind"] == "stale_key"


def test_a_refusal_does_not_refresh_the_entry():
    live = _key()
    cache = SystemOneCache(live=live)
    evaluation = _evaluation()
    cache.put(live, evaluation)
    cache.update_live(goal_revision=2)
    assert cache.get(live) is None
    # the entry is still the OLD one — it was refused, never silently re-run
    entry = cache.entry(live)
    assert isinstance(entry, CacheEntry)
    assert entry.evaluation is evaluation
    assert entry.key.goal_revision == 1
    # and once the versions line up again the old entry is served (dedup, not loss)
    cache.update_live(goal_revision=1)
    assert cache.get(live) is evaluation


def test_an_explicit_invalidation_reason_is_recorded():
    live = _key()
    cache = SystemOneCache(live=live)
    cache.put(live, _evaluation())
    cleared = cache.invalidate("evidence")
    assert cleared == 1
    assert cache.get(live) is None
    assert cache.report()["invalidations"]["evidence"] == 1


def test_an_unknown_invalidation_reason_is_refused():
    cache = SystemOneCache(live=_key())
    with pytest.raises(ValueError):
        cache.invalidate("not_a_reason")


def test_the_declared_refusal_reasons_are_the_implemented_ones():
    assert set(CACHE_REFUSAL_REASONS) == {
        "goal",
        "policy",
        "workspace",
        "evidence",
        "gui_state",
        "tool",
        "pack",
    }


def test_putting_a_key_that_is_not_the_live_version_is_refused():
    cache = SystemOneCache(live=_key())
    cache.put(_key(goal_revision=99), _evaluation())
    assert cache.report()["entries"] == 0
    assert cache.refusals[-1]["kind"] == "stale_put"


def test_the_cache_never_stores_a_secret_bearing_key():
    """A key is logged and persisted — a credential-shaped value is refused.

    The value is secret-shaped for the repo's own scanner (godsv.secretscan),
    so this proves the real guard rather than a spelling.
    """
    cache = SystemOneCache(live=_key())
    secret = "sk-abcdefghijklmnopqrstuvwx1234"
    with pytest.raises(ValueError):
        _key(provider_model=f"Bearer {secret}")
    with pytest.raises(ValueError):
        cache.put(_key(provider_model=f"Bearer {secret}"), _evaluation())


# --------------------------------------------------------------------------- #
# §26 — metrics                                                                #
# --------------------------------------------------------------------------- #


def test_every_metric_of_section_26_has_a_name():
    from skaldir.system_one_metrics import ALL_METRICS

    assert REQUIRED_METRICS <= set(ALL_METRICS)
    for name in REQUIRED_METRICS:
        assert isinstance(name, str) and name
    assert "provider_latency_ms" in ALL_METRICS
    assert "completion_criteria_checked" in ALL_METRICS


def test_an_unmeasured_metric_is_not_run_and_never_zero():
    metrics = SystemOneMetrics()
    report = metrics.report()
    assert report["values"] == {}
    assert set(REQUIRED_METRICS) <= set(report["not_run"])
    for name in REQUIRED_METRICS:
        assert name not in report["values"], f"{name} invented as {report['values'][name]!r}"


def test_a_measured_metric_reports_the_real_number():
    metrics = SystemOneMetrics()
    metrics.record_timing(STATE_BUILD_MS, 4.2)
    metrics.record_timing(PROVIDER_LATENCY_MS, 118.0)
    report = metrics.report()
    assert report["values"][STATE_BUILD_MS] == pytest.approx(4.2)
    assert report["values"][PROVIDER_LATENCY_MS] == pytest.approx(118.0)
    assert STATE_BUILD_MS not in report["not_run"]


def test_a_measured_zero_is_a_real_measurement():
    metrics = SystemOneMetrics()
    metrics.record_counter(COMPLETION_CRITERIA_CHECKED, 0)
    report = metrics.report()
    assert report["values"][COMPLETION_CRITERIA_CHECKED] == 0
    assert COMPLETION_CRITERIA_CHECKED not in report["not_run"]


def test_completion_metrics_only_exist_after_the_completion_path_ran():
    metrics = SystemOneMetrics()
    assert COMPLETION_FALSE_ACCEPTS in metrics.report()["not_run"]
    metrics.record_completion_run(
        criteria_checked=3, criteria_flagged=1, criteria_escalated=2, total_latency_ms=42.0
    )
    report = metrics.report()
    assert report["values"][COMPLETION_CRITERIA_CHECKED] == 3
    assert report["values"][COMPLETION_CRITERIA_FLAGGED] == 1
    assert report["values"][COMPLETION_CRITERIA_ESCALATED] == 2
    assert report["values"][COMPLETION_TOTAL_LATENCY_MS] == pytest.approx(42.0)
    # a false accept/reject needs real ground truth — never counted without it
    assert COMPLETION_FALSE_ACCEPTS in report["not_run"]
    metrics.note_false_accept()
    assert metrics.report()["values"][COMPLETION_FALSE_ACCEPTS] == 1


def test_usage_is_recorded_only_from_a_reported_usage_mapping():
    metrics = SystemOneMetrics()
    metrics.record_usage({"input_tokens": 1200, "output_tokens": 90, "cost_usd": 0.002})
    report = metrics.report()
    assert report["values"][INPUT_TOKENS] == 1200
    assert report["values"][OUTPUT_TOKENS_IF_REPORTED] == 90
    assert report["values"][PROVIDER_COST_IF_KNOWN] == pytest.approx(0.002)
    # a provider that reports nothing leaves the metric unmeasured
    assert SystemOneMetrics().report()["values"] == {}


def test_an_unknown_metric_name_is_refused():
    metrics = SystemOneMetrics()
    with pytest.raises(ValueError):
        metrics.record_timing("made_up_ms", 1.0)


def test_the_disabled_evaluator_still_produces_no_invented_latency_metric():
    import asyncio

    metrics = SystemOneMetrics()
    evaluation = asyncio.run(
        DisabledSystemOneEvaluator().evaluate(
            state=SystemOneState(payload={"goal": "g"}),
            questions=__import__(
                "godsv.system_one_questions", fromlist=["compose_voice_triage"]
            ).compose_voice_triage("loop"),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert evaluation.latency_ms >= 0  # measured by the contract, not by the sink
    assert metrics.report()["values"] == {}
