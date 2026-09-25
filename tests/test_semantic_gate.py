"""The **semantic gate**: deterministic VERIFY → Jev verifier → promotion (MVP/93 §3).

Two things are pinned, and the second is the point of the whole step:

1. the chain runs in that order and the gate's own ceilings hold (a read-only
   ``verifier`` role, the verifier role's budget, a brief instead of a history);
2. **the deterministic verdict is never replaced.** The gate may keep a PASS from
   being *promoted*; it may never turn a FAIL/UNKNOWN into anything, and it may
   never be read as agreement when it returned no advisory.

Both required real-run cases are here: a run whose deterministic checks really
passed but whose claim the evidence does not support (the gate refuses, with a
reason), and a run that is fine (the gate passes it through).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from skaldir import subagent_roles as R
from skaldir import verify_service as V
from skaldir.jev.contracts import JEV_ANSWERS, SemanticVerificationAnswer
from skaldir.jev.fixture import FixtureJevEvaluator

# -- a real deterministic run ----------------------------------------------- #


def _real_run(tmp_path: Path, *, criterion: str = "the build answers", fail: bool = False) -> dict:
    """A run whose criterion is *really executed* in this process's interpreter."""
    root = tmp_path / ("ws-fail" if fail else "ws")
    (root / ".godsv").mkdir(parents=True, exist_ok=True)
    (root / ".godsv" / "verify.json").write_text(
        json.dumps(
            {
                "goal": "make the widget build",
                "criteria": [
                    {
                        "criterion": criterion,
                        "argv": [sys.executable, "-c", "print('BUILD OK')"],
                        "expect_exit": 1 if fail else 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    plan = V.build_plan([criterion], workspace=root)
    run = V.run_plan(plan, timeout=30, state_dir=None)
    assert run["verdict"] == ("FAIL" if fail else "PASS"), run["verdict_reason"]
    assert run["results"][0]["exit_code"] is not None, "the check really ran"
    return run


def _history(lines: int = 400) -> list[str]:
    return [
        f"worker turn {index}: inspected the workspace " + ("y" * 120)
        for index in range(lines)
    ]


# -- the two required cases ------------------------------------------------- #


def test_case_a_deterministic_pass_but_the_claim_is_not_supported(tmp_path: Path):
    """The checks passed; the claim overreaches; the verifier refuses with a reason."""
    run = _real_run(tmp_path)
    evaluator = FixtureJevEvaluator({"sem.claim.backed": {"value": 0.05}})
    claim = "the widget is deployed to production and the endpoint returns 200"

    gate = V.semantic_gate(run, claim=claim, evaluator=evaluator)

    assert gate["state"] == V.GATE_REFUSED
    assert gate["support"] == "unsupported"
    assert gate["promotion"] == V.PROMOTION_BLOCKED and gate["blocking"] is True
    assert gate["reasons"], "a refusal with no reason is not a finding"
    assert "does not support" in gate["reasons"][0]
    assert gate["evidence_gaps"], "a refusal names the gap a reader could close"
    # the deterministic verdict is untouched, and it was really a PASS
    assert gate["deterministic_verdict"] == V.VERDICT_PASS
    assert gate["verdict_after_gate"] == V.VERDICT_PASS
    assert gate["replaces_deterministic_checks"] is False
    # the gate is advisory by construction
    assert gate["authority"] == "advisory" and gate["certifies"] is False
    assert gate["status"] == "ok" and gate["provider_mode"] == "replay_fixture"
    # and the same readings are visible, so a reader can disagree with the reason
    assert gate["checks_run"] and gate["refusal_reason"] == ""
    assert gate["provider_choice"] == "supported"  # provider said fine; readings overrode it


def test_case_b_a_run_that_is_fine_passes_the_gate(tmp_path: Path):
    run = _real_run(tmp_path)
    gate = V.semantic_gate(
        run, claim="the widget builds: the build command exits 0", evaluator=FixtureJevEvaluator()
    )
    assert gate["state"] == V.GATE_SUPPORTED
    assert gate["promotion"] == V.PROMOTION_ALLOWED
    assert gate["blocking"] is False
    assert gate["reasons"] == [] and gate["verdict_after_gate"] == V.VERDICT_PASS
    assert gate["support"] == "supported"
    assert "the deterministic PASS verdict stands unchanged" in gate["promotion_reason"]


# -- the invariants --------------------------------------------------------- #


def test_a_gate_that_returns_no_advisory_is_not_agreement(tmp_path: Path):
    run = _real_run(tmp_path)
    blocked = FixtureJevEvaluator(error=RuntimeError("no credential"))
    gate = V.semantic_gate(run, claim="the widget builds", evaluator=blocked)
    assert gate["state"] == V.GATE_NO_ADVISORY
    assert gate["promotion"] == V.PROMOTION_UNREAD
    assert gate["verdict_after_gate"] == V.VERDICT_PASS
    assert "deterministic verdict stands alone" in gate["promotion_reason"]


def test_a_non_pass_run_is_never_sent_to_the_gate(tmp_path: Path):
    run = _real_run(tmp_path, criterion="the build answers", fail=True)
    assert run["verdict"] == V.VERDICT_FAIL
    evaluator = FixtureJevEvaluator()
    gate = V.semantic_gate(run, claim="everything is fine", evaluator=evaluator)
    assert gate["state"] == V.GATE_SKIPPED
    assert gate["verdict_after_gate"] == V.VERDICT_FAIL
    assert evaluator.calls == [], "a non-PASS run must not spend a semantic call"
    assert "not PASS" in gate["promotion_reason"]


def test_no_claim_means_unread_not_supported(tmp_path: Path):
    run = _real_run(tmp_path)
    evaluator = FixtureJevEvaluator()
    gate = V.semantic_gate(run, claim="   ", evaluator=evaluator)
    assert gate["state"] == V.GATE_NO_ADVISORY
    assert gate["promotion"] == V.PROMOTION_UNREAD
    assert evaluator.calls == []


def test_an_underspecified_reading_also_blocks_the_promotion(tmp_path: Path):
    run = _real_run(tmp_path)
    evaluator = FixtureJevEvaluator({"sem.gap.present": {"value": 0.9}})
    gate = V.semantic_gate(run, claim="the widget builds", evaluator=evaluator)
    assert gate["state"] == V.GATE_UNDERSPECIFIED
    assert gate["promotion"] == V.PROMOTION_BLOCKED
    assert gate["verdict_after_gate"] == V.VERDICT_PASS
    assert gate["reasons"]


# -- the gate's own ceilings ------------------------------------------------- #


def test_the_gate_needs_a_read_only_verifier_dispatcher(tmp_path: Path):
    run = _real_run(tmp_path)
    writer = R.RoleToolDispatcher("worker")
    gate = V.semantic_gate(run, claim="the widget builds", dispatcher=writer)
    assert gate["state"] == V.GATE_NO_ADVISORY
    assert gate["refusal_code"] == R.REFUSAL_TOOL_NOT_GRANTED
    assert "not a verifier" in gate["promotion_reason"]
    assert gate["verdict_after_gate"] == V.VERDICT_PASS

    reader = R.RoleToolDispatcher("verifier")
    good = V.semantic_gate(
        run, claim="the widget builds", dispatcher=reader, evaluator=FixtureJevEvaluator()
    )
    assert good["state"] == V.GATE_SUPPORTED
    assert reader.may_call("test_run") and not reader.may_call("edit")


def test_the_gate_has_its_own_budget_from_the_verifier_role(tmp_path: Path):
    run = _real_run(tmp_path)
    gate = V.semantic_gate(run, claim="the widget builds", evaluator=FixtureJevEvaluator())
    envelope = R.ROLE_BUDGETS["verifier"]
    assert gate["budget"]["role"] == "verifier"
    assert gate["budget"]["max_tokens"] == envelope.max_tokens
    assert gate["budget"]["max_turns"] == envelope.max_turns
    assert gate["budget"]["max_reads"] == envelope.max_tool_calls
    assert "ROLE_BUDGETS['verifier']" in gate["budget"]["source"]


def test_the_verifier_never_gets_a_field_that_could_be_a_verdict():
    """The layer-1 contract scan is the enforcement; this is its readable shadow."""
    forbidden = ("verdict", "pass", "fail", "certify", "grant", "permission")
    answer = SemanticVerificationAnswer(support="supported")
    for field in type(answer).model_fields:
        assert field.lower() not in forbidden
    assert JEV_ANSWERS["semantic_verification"] is SemanticVerificationAnswer
    assert "support" in type(answer).model_fields


def test_an_unsupported_reading_must_carry_a_reason():
    with pytest.raises(ValidationError):
        SemanticVerificationAnswer(support="unsupported")
    ok = SemanticVerificationAnswer(support="unsupported", reasons=("no evidence ref",))
    assert ok.reasons == ("no evidence ref",)


# -- the brief, and what it saves ------------------------------------------- #


def test_the_brief_is_what_the_verifier_reads_and_the_history_is_not_sent(tmp_path: Path):
    run = _real_run(tmp_path)
    history = _history(400)
    gate = V.semantic_gate(
        run, claim="the widget builds", transcript=history, evaluator=FixtureJevEvaluator()
    )
    effect = gate["token_effect"]
    assert effect["brief_tokens"] < effect["transcript_tokens"]
    assert effect["saved_tokens"] > 0
    assert effect["ratio"] < 0.5
    assert effect["transcript_lines"] == 400
    assert gate["brief"]["tail_lines"] <= V.DEFAULT_BRIEF_TAIL_LINES
    assert gate["brief"]["transcript_lines_total"] == 400
    assert gate["brief"]["truncated"] is True
    assert gate["brief"]["commands"] >= 1, "the executed command is in the brief"


def test_the_brief_is_measured_on_a_run_with_no_transcript_supplied(tmp_path: Path):
    run = _real_run(tmp_path)
    brief, lines = V.semantic_brief_from_run(run, claim="the widget builds")
    assert brief.objective == "make the widget build"
    assert brief.claim == "the widget builds"
    assert any(command.exit_code == 0 for command in brief.commands)
    assert brief.evidence_refs and lines, "the run's own evidence is in the brief"
    assert brief.chars > 0 and brief.schema == "skaldir.subagent_brief.v1"
