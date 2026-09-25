"""JEV-13 — completion precheck: signals missing/stale evidence, never a certificate.

ROT-FIRST evidence, captured before the precheck existed:

    $ .venv/bin/python -m pytest tests/test_completion_precheck.py \
        tests/test_system_one_guistate.py -p no:randomly
    E   ImportError: cannot import name 'PRECHECK_CERTIFIES' from 'godsv.completion'
        (/home/willy/projects/gods-voice/src/godsv/completion.py)
    E   ImportError: cannot import name 'guistate_action_candidates' from 'godsv.computer_use'
        (/home/willy/projects/gods-voice/src/godsv/computer_use.py)
    !!! Interrupted: 2 errors during collection !!!
    2 errors in 0.12s

    $ .venv/bin/python -c "from godsv.completion import completion_precheck"
    ImportError: cannot import name 'completion_precheck' from 'godsv.completion'
    $ .venv/bin/python -c "GateRunner(repo_root='.').verify(r, [], precheck=object())"
    TypeError: GateRunner.verify() got an unexpected keyword argument 'precheck'

What this file pins (ABNAHME Teil XV §91/JEV-13, behaviour — never source text):

* the precheck signals MISSING and STALE completion evidence as advisory
  machine-readable reason codes;
* it never certifies: ``certifies`` is False by construction and
  ``certifies_completion()`` always returns False; a forged ``certifies=True``
  record is refused by the gate, not believed;
* if the precheck is the ONLY source of a completion request, the existing
  ``GateRunner`` refuses completion with the machine-readable reason
  ``completion-precheck-only-source``;
* the gate keeps sole authority: with runner-executed evidence the precheck
  neither blocks nor downgrades a genuine VERIFIED_COMPLETE;
* with the §98 flag off the precheck returns ``None`` — the existing path runs
  unchanged (no new blocking behaviour, no new codes).

Evidence class: contract (schema + state-machine proof on real ``GateRunner``
runs against a real git working tree). No live model is involved.
"""

from __future__ import annotations

from skaldir.completion import (
    INCOMPLETE,
    PRECHECK_CERTIFIES,
    PRECHECK_MISSING_EVIDENCE,
    PRECHECK_ONLY_SOURCE,
    PRECHECK_STALE_EVIDENCE,
    VERIFIED_COMPLETE,
    CompletionPrecheck,
    CompletionRequest,
    CompletionStore,
    DodItem,
    GateRunner,
    RunEvidence,
    completion_precheck,
)
from skaldir.system_one_flags import SystemOneFlags

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


class _FakeFlags:
    """Duck-typed flags: only the attributes the precheck reads (§98 names)."""

    def __init__(self, **overrides) -> None:
        self.enabled = True
        self.provider = "llm_system_one_adapter"
        self.shadow_mode = False
        self.voice_triage = False
        self.completion_precheck = True
        self.computer_use = False
        for key, value in overrides.items():
            setattr(self, key, value)


def _evidence(**overrides) -> RunEvidence:
    base = {
        "name": "pytest",
        "cmd": "pytest -q",
        "ok": True,
        "status": "passed",
        "check_id": "pytest",
        "source": "runner",
        "ts": 9e12,
        "revision": 4,
    }
    base.update(overrides)
    return RunEvidence(**base)


def _request(**overrides) -> CompletionRequest:
    base = {"user_goal": "finish the task", "claimed_completed": ["the task is done"]}
    base.update(overrides)
    return CompletionRequest(**base)


# --------------------------------------------------------------------------- #
# the advisory reading (signal, never certificate)                             #
# --------------------------------------------------------------------------- #


def test_precheck_signals_missing_evidence_without_certifying():
    precheck = completion_precheck(request=_request(), evidence=[], revision=4)
    assert precheck is not None
    assert PRECHECK_MISSING_EVIDENCE in precheck.reasons
    assert "completion_evidence_missing" in precheck.signals
    # the one rule that matters: a precheck is never a certificate
    assert precheck.certifies is False
    assert precheck.advisory_only is True
    assert precheck.certifies_completion() is False


def test_precheck_signals_stale_evidence_from_an_older_revision():
    precheck = completion_precheck(
        request=_request(), evidence=[_evidence(revision=3)], revision=4
    )
    assert precheck is not None
    assert PRECHECK_STALE_EVIDENCE in precheck.reasons
    assert PRECHECK_MISSING_EVIDENCE not in precheck.reasons
    assert precheck.certifies is False


def test_precheck_signals_stale_evidence_from_another_workspace_or_session():
    foreign_workspace = completion_precheck(
        request=_request(),
        evidence=[_evidence(workspace="somewhere-else")],
        revision=4,
        workspace="here",
    )
    assert foreign_workspace is not None
    assert PRECHECK_STALE_EVIDENCE in foreign_workspace.reasons
    foreign_session = completion_precheck(
        request=_request(),
        evidence=[_evidence(session="other-run")],
        revision=4,
        session="this-run",
    )
    assert foreign_session is not None
    assert PRECHECK_STALE_EVIDENCE in foreign_session.reasons


def test_precheck_signals_only_event_evidence_as_missing():
    """Tool-event evidence is context, never certification (§17) — so: missing."""
    precheck = completion_precheck(
        request=_request(),
        evidence=[_evidence(source="events")],
        revision=4,
    )
    assert precheck is not None
    assert PRECHECK_MISSING_EVIDENCE in precheck.reasons


def test_precheck_is_silent_when_bound_runner_evidence_exists():
    precheck = completion_precheck(request=_request(), evidence=[_evidence()], revision=4)
    assert precheck is not None
    assert precheck.reasons == ()
    assert PRECHECK_MISSING_EVIDENCE not in precheck.reasons
    assert PRECHECK_STALE_EVIDENCE not in precheck.reasons
    assert precheck.certifies is False


def test_precheck_fingerprint_is_stable_and_content_bound():
    same_a = completion_precheck(request=_request(), evidence=[], revision=4)
    same_b = completion_precheck(request=_request(), evidence=[], revision=4)
    other = completion_precheck(request=_request(), evidence=[_evidence()], revision=4)
    assert same_a is not None and same_b is not None and other is not None
    assert same_a.fingerprint == same_b.fingerprint
    assert same_a.fingerprint != other.fingerprint


def test_precheck_with_no_claim_signals_nothing_verdict_like():
    precheck = completion_precheck(
        request=CompletionRequest(user_goal="still working"), evidence=[], revision=1
    )
    assert precheck is not None
    assert PRECHECK_MISSING_EVIDENCE not in precheck.reasons
    assert precheck.certifies is False


def test_a_stale_evaluation_stays_inert_while_the_structural_reading_stands():
    """§92/JEV-14: a late System-One answer contributes nothing — not even a label."""
    from skaldir.system_one import ACCESS_DISABLED, PROVIDER_DISABLED
    from skaldir.system_one_policy import VersionStamp

    evaluation = _completion_evaluation()
    stale = completion_precheck(
        request=_request(),
        evidence=[],
        revision=4,
        evaluation=evaluation,
        live=VersionStamp(goal_revision=evaluation.goal_revision + 1),
    )
    assert stale is not None
    # the structural reading of the evidence list is independent of the answer
    assert PRECHECK_MISSING_EVIDENCE in stale.reasons
    # ... and the stale answer adds neither provider label nor model signal
    assert stale.provider == "" and stale.live_jev is False
    assert "completion_evidence_may_be_missing" not in stale.signals
    fresh = completion_precheck(
        request=_request(),
        evidence=[],
        revision=4,
        evaluation=evaluation,
        live=VersionStamp(goal_revision=evaluation.goal_revision),
    )
    assert fresh is not None
    assert fresh.provider == PROVIDER_DISABLED
    assert fresh.live_jev is False, "an LLM/disabled reading is never labelled live"
    assert "completion_evidence_may_be_missing" in fresh.signals
    assert ACCESS_DISABLED == evaluation.provider_access_mode


def _completion_evaluation():
    """A real evaluation record over the completion pack (no hand-made stub)."""
    from skaldir.system_one import (
        ACCESS_DISABLED,
        PROVIDER_DISABLED,
        EvaluationContext,
        NoulAnswer,
        SystemOneState,
        build_evaluation,
    )
    from skaldir.system_one_questions import compose_questions

    return build_evaluation(
        context=EvaluationContext(run_id="r", goal_id="g", goal_revision=4, event_cursor=10),
        questions=compose_questions("completion"),
        state=SystemOneState(payload={"task": "claim done"}, input_scope="task"),
        provider=PROVIDER_DISABLED,
        provider_model="disabled",
        access_mode=ACCESS_DISABLED,
        answers={
            "completion.evidence_current": NoulAnswer(
                question_id="completion.evidence_current", value=0.2
            )
        },
    )


def test_precheck_flag_off_keeps_the_existing_completion_path():
    off = completion_precheck(
        request=_request(), evidence=[], revision=4, flags=_FakeFlags(completion_precheck=False)
    )
    assert off is None
    master_off = completion_precheck(
        request=_request(), evidence=[], revision=4, flags=SystemOneFlags.disabled()
    )
    assert master_off is None


# --------------------------------------------------------------------------- #
# the gate keeps sole authority                                                 #
# --------------------------------------------------------------------------- #


def test_precheck_as_the_only_source_refuses_completion_with_a_reason(tmp_path):
    precheck = completion_precheck(request=_request(), evidence=[], revision=4)
    assert precheck is not None
    result = GateRunner(repo_root=tmp_path / "repo").verify(_request(), [], precheck=precheck)
    assert result.status == INCOMPLETE
    assert PRECHECK_ONLY_SOURCE in result.refusal_codes
    assert any("precheck" in failure for failure in result.blocking_failures)


def test_event_only_evidence_plus_precheck_is_still_precheck_only(tmp_path):
    result = GateRunner(repo_root=tmp_path / "repo").verify(
        _request(),
        [_evidence(source="events")],
        precheck=completion_precheck(request=_request(), evidence=[], revision=4),
    )
    assert result.status == INCOMPLETE
    assert PRECHECK_ONLY_SOURCE in result.refusal_codes


def test_a_forged_certifying_precheck_is_refused(tmp_path):
    """The gate, not the precheck, decides — a forged record cannot certify."""
    forged = CompletionPrecheck(signals=("all_green",), certifies=True)
    assert forged.certifies is True
    assert forged.certifies_completion() is False
    result = GateRunner(repo_root=tmp_path / "repo").verify(_request(), [], precheck=forged)
    assert result.status == INCOMPLETE
    assert PRECHECK_CERTIFIES in result.refusal_codes


def test_precheck_does_not_block_a_genuinely_verified_completion(tmp_path):
    precheck = completion_precheck(request=_request(), evidence=[_evidence()], revision=4)
    result = GateRunner(repo_root=tmp_path / "repo").verify(
        _request(), [_evidence()], precheck=precheck
    )
    assert result.status == VERIFIED_COMPLETE, (
        result.blocking_failures,
        result.major_failures,
    )
    assert PRECHECK_ONLY_SOURCE not in result.refusal_codes
    assert result.refusal_codes == []


def test_precheck_never_blocks_a_contract_with_a_declared_dod(tmp_path):
    items = [DodItem(id="c1", text="suite green", verification="pytest")]
    precheck = completion_precheck(request=_request(), evidence=[_evidence()], revision=1)
    result = GateRunner(repo_root=tmp_path / "repo", dod=items).verify(
        _request(), [_evidence()], precheck=precheck
    )
    assert result.status == VERIFIED_COMPLETE, (
        result.blocking_failures,
        result.major_failures,
    )


def test_existing_gate_behaviour_is_unchanged_without_a_precheck(tmp_path):
    """Regression pin: the additive precheck must not alter the legacy path."""
    runner = GateRunner(repo_root=tmp_path / "repo")
    rejected = runner.verify(_request(), [])
    assert rejected.status == INCOMPLETE
    assert rejected.refusal_codes == [], "uncoded findings stay uncoded"
    assert runner.verify(_request(), [_evidence()]).status == VERIFIED_COMPLETE


def test_precheck_is_recorded_on_the_contract_timeline(tmp_path):
    store = CompletionStore(tmp_path / "rt", session_id="pc1")
    items = [DodItem(id="c1", text="suite green", verification="pytest")]
    store.begin_goal(session_id="pc1", goal="g", dod=items)
    precheck = completion_precheck(request=_request(), evidence=[], revision=1)
    assert precheck is not None
    GateRunner(repo_root=tmp_path / "repo", dod=items).verify(
        _request(), [], store=store, precheck=precheck
    )
    rows = [row for row in store.timeline() if row.get("kind") == "precheck"]
    assert rows, "the advisory reading belongs on the audit trail"
    assert rows[0]["certifies"] is False
    assert PRECHECK_MISSING_EVIDENCE in rows[0]["reasons"]
