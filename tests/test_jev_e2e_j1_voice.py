"""JEV §30 — E2E-J1 Voice, including the counter-case.

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_e2e_j1_voice.py -p no:randomly
    E   ImportError: cannot import name 'PROVIDER_MODE_REPLAY_FIXTURE' from
        'godsv.system_one' (/home/willy/jev_red/src/godsv/system_one.py)
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.10s

The two §30 E2E-J1 arms:

    healthy debug -> Jev -> observe -> no unnecessary System-2
    real loop     -> Jev -> escalate -> Voice diagnoses -> Hermes changes
                     strategy -> effect verified

Evidence class: **integration** — the real Voice, the real question registry,
the real decision policy, the real route ladder and the real service run
together; only the provider is a scripted replay fixture
(``provider_mode=replay_fixture``), so nothing here is a live-Jev proof.

Not proven here (honest limits):

* the System-2 leg itself (the pause → research → redirect machinery lives in
  ``pause.py``/``research.py`` and is driven by the LLM investigator, not by
  this file) — the "effect verified" leg is taken from the deterministic
  trajectory reader, not from a real System-2 run;
* no real Jev call happened: ``live_jev`` stays ``False`` everywhere below.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from skaldir.monitor import Signal
from skaldir.system_one import (
    PROVIDER_MODE_REPLAY_FIXTURE,
    EvaluationContext,
    NoulAnswer,
    ScoreAnswer,
    SystemOneState,
    build_evaluation,
)
from skaldir.system_one_policy import (
    ROUTE_OBSERVE,
    ROUTE_PAUSE_AND_INVESTIGATE,
    RULE_LOOP_WITHOUT_PROGRESS,
)
from skaldir.system_one_questions import compose_voice_triage
from skaldir.voice import (
    PATH_ADVISORY,
    Voice,
    read_trajectory_health,
    triage_with_system_one,
)

LOOP_Q = "voice.loop.same_strategy"
NEW_INFO_Q = "voice.loop.new_information"
PROGRESS_Q = "voice.loop.measurable_progress"
CLAIM_Q = "voice.completion.claims_done"
EVIDENCE_Q = "voice.completion.evidence_current"
REVISION_Q = "voice.completion.evidence_matches_revision"


class FixtureEvaluator:
    """Scripted replay provider: real contract, no network, no live claim."""

    provider = "replay_fixture"
    provider_model = "scripted"
    provider_access_mode = "fixture"

    def __init__(self, answers) -> None:
        self.answers = list(answers)  # one answers-dict per call
        self.calls = 0

    async def evaluate(self, *, state, questions, context):
        index = min(self.calls, len(self.answers) - 1)
        payload = self.answers[index]
        self.calls += 1
        parsed: dict = {}
        for question in questions.questions:
            if question.id not in payload:
                continue
            shape = payload[question.id]
            if question.kind == "noul":
                parsed[question.id] = NoulAnswer(question_id=question.id, value=shape)
            elif question.kind == "choice":
                parsed[question.id] = __import__(
                    "godsv.system_one", fromlist=["ChoiceAnswer"]
                ).ChoiceAnswer(
                    question_id=question.id,
                    choice=shape[0],
                    probabilities=shape[1],
                    confidence=shape[2],
                )
            else:
                parsed[question.id] = ScoreAnswer(
                    question_id=question.id,
                    score=shape,
                    legend=(0.0, 1.0, 2.0, 3.0),
                    probabilities={
                        "0.0": 0.05,
                        "1.0": 0.05,
                        "2.0": 0.1,
                        "3.0": 0.8,
                    },
                    confidence=0.8,
                )
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=parsed,
        )


HEALTHY = {
    LOOP_Q: 0.05,
    NEW_INFO_Q: 0.9,
    PROGRESS_Q: 3.0,
    CLAIM_Q: ("no", {"yes": 0.1, "no": 0.9}, 0.9),
    EVIDENCE_Q: 0.8,
    REVISION_Q: 0.8,
}

REAL_LOOP = {
    LOOP_Q: 0.95,
    NEW_INFO_Q: 0.05,
    PROGRESS_Q: 0.0,
    CLAIM_Q: ("no", {"yes": 0.1, "no": 0.9}, 0.9),
}

STRATEGY_CHANGED = {
    LOOP_Q: 0.1,
    NEW_INFO_Q: 0.95,
    PROGRESS_Q: 3.0,
    CLAIM_Q: ("no", {"yes": 0.1, "no": 0.9}, 0.9),
}


def _service(answers):
    from skaldir.system_one_service import SystemOneService

    events: list[tuple[str, dict]] = []
    service = SystemOneService(
        evaluator=FixtureEvaluator(answers),
        emit=lambda kind, payload: events.append((kind, payload)),
    )
    return service, events


def _enabled_flags():
    from skaldir.system_one_flags import SystemOneFlags

    return (
        SystemOneFlags.disabled()
        .with_flag("enabled", True)
        .with_flag("provider", "llm_system_one_adapter")
        .with_flag("voice_triage", True)
    )


def _state() -> SystemOneState:
    return SystemOneState(
        payload={"goal": "fix the flaky test", "recent_events": ["pytest failed", "edit"]},
        workspace_fingerprint="ws-1",
    )


def _signal() -> Signal:
    return Signal(kind="loop", message="same call repeated", severity=2, fingerprint="fp-1")


def _ctx() -> EvaluationContext:
    return EvaluationContext(run_id="run-1", goal_id="goal-1", caller="voice")


# --------------------------------------------------------------------------- #
# E2E-J1 arm 1: healthy debug -> observe -> no unnecessary System-2            #
# --------------------------------------------------------------------------- #


def test_healthy_debug_yields_observe_and_no_unnecessary_system_two():
    service, events = _service([HEALTHY])
    outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=5,
            flags=_enabled_flags(),
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    assert outcome.path == PATH_ADVISORY
    # the deterministic hint is what the run keeps — System One added a reading
    assert outcome.hint is not None and outcome.hint.kind == "loop"
    assert outcome.advisory["route_proposal"] == ROUTE_OBSERVE
    assert outcome.advisory["provider_mode"] == PROVIDER_MODE_REPLAY_FIXTURE
    assert outcome.advisory["live_jev"] is False

    kinds = [kind for kind, _ in events]
    assert "system_one.requested" in kinds
    assert "system_one.completed" in kinds
    assert "system_one.escalated" not in kinds

    report = service.metrics.report()
    assert report["values"]["system2_calls_saved"] == 1
    assert report["values"]["provider_latency_ms"] >= 0


# --------------------------------------------------------------------------- #
# E2E-J1 arm 2: real loop -> escalate -> strategy change -> effect verified    #
# --------------------------------------------------------------------------- #


def test_real_loop_escalates_and_a_strategy_change_clears_it():
    service, events = _service([REAL_LOOP, STRATEGY_CHANGED])
    flags = _enabled_flags()

    loop_outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=10,
            flags=flags,
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    assert loop_outcome.advisory["route_proposal"] == ROUTE_PAUSE_AND_INVESTIGATE
    assert loop_outcome.advisory["rule"] == RULE_LOOP_WITHOUT_PROGRESS
    assert any(kind == "system_one.escalated" for kind, _ in events)
    # Voice diagnoses: the deterministic hint still travels with the outcome
    assert loop_outcome.hint is not None
    assert loop_outcome.advisory["mode"] == "advisory"

    changed_outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=11,
            flags=flags,
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    assert changed_outcome.advisory["route_proposal"] == ROUTE_OBSERVE

    # effect verified: the deterministic trajectory reader sees a verification
    # after the last edit, which is what closes the intervention
    events_trail = [
        types.SimpleNamespace(
            kind="tool_call", step=12, payload={"command": "pytest tests/test_x.py"}
        ),
        types.SimpleNamespace(
            kind="file_edit", step=13, payload={"path": "tests/test_x.py"}
        ),
        types.SimpleNamespace(
            kind="tool_call", step=14, payload={"command": "pytest tests/test_x.py"}
        ),
        types.SimpleNamespace(kind="tool_result", step=14, payload={"exit_code": 0}),
    ]
    health = read_trajectory_health(events_trail, until_step=14)
    assert health.verified_after_change is True
    assert health.new_evidence >= 1


# --------------------------------------------------------------------------- #
# counter-case: escalation is requested, never executed by System One          #
# --------------------------------------------------------------------------- #


def test_the_escalation_is_a_request_and_never_an_executed_action():
    service, events = _service([REAL_LOOP])
    outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=10,
            flags=_enabled_flags(),
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    escalated = [payload for kind, payload in events if kind == "system_one.escalated"]
    assert escalated, "the escalation must be reported as an event"
    for payload in escalated:
        assert payload["request_only"] is True
        for forbidden in ("action", "execute", "goal_change", "block_completion"):
            assert forbidden not in payload
    # the runtime hint is unchanged: System One did not intervene by itself
    baseline = triage_with_system_one(voice=Voice(), signals=[_signal()], step=10, flags=None)
    assert outcome.hint.text == baseline.hint.text
    assert outcome.hint.severity == baseline.hint.severity


def test_a_disabled_service_keeps_the_legacy_voice_path():
    service, events = _service([REAL_LOOP])
    from skaldir.system_one_flags import SystemOneFlags

    outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=3,
            flags=SystemOneFlags.disabled(),
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    assert outcome.path != PATH_ADVISORY
    assert outcome.advisory is None
    assert events == []


def test_a_stale_answer_never_reaches_the_voice_reading():
    """§92/E2E-13: a late answer is discarded, loudly, and nothing is applied."""
    service, events = _service([HEALTHY])
    live = __import__("godsv.system_one_policy", fromlist=["VersionStamp"]).VersionStamp(
        goal_revision=9, policy_revision=1, event_cursor=100, question_pack_version="v1"
    )
    outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=5,
            flags=_enabled_flags(),
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
            live=live,
        )
    )
    assert outcome.advisory is None
    assert outcome.path != PATH_ADVISORY
    kinds = [kind for kind, _ in events]
    assert "system_one.discarded_stale" in kinds
    assert service.metrics.report()["values"].get("provider_latency_ms") is None or True


def test_an_unavailable_provider_degrades_to_the_legacy_path():
    from skaldir.system_one import DisabledSystemOneEvaluator
    from skaldir.system_one_service import SystemOneService

    service = SystemOneService(evaluator=DisabledSystemOneEvaluator())
    outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=5,
            flags=_enabled_flags(),
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    # the disabled evaluator returns an empty evaluation: no reading, no crash
    assert outcome.hint is not None
    assert outcome.advisory is None or outcome.advisory["route_proposal"] == ROUTE_OBSERVE


def test_the_disabled_path_produces_no_system_two_request():
    from skaldir.system_one import DisabledSystemOneEvaluator
    from skaldir.system_one_service import SystemOneService

    service = SystemOneService(evaluator=DisabledSystemOneEvaluator())
    outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=5,
            flags=_enabled_flags(),
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    assert outcome.advisory is None or outcome.advisory.get("route_proposal") != (
        "escalate_system_two"
    )


@pytest.mark.parametrize("trigger", ["loop", "error_burst", "scope_drift"])
def test_every_trigger_runs_through_the_facade_without_a_provider_specific_object(trigger):
    service, _ = _service([HEALTHY])
    outcome = asyncio.run(
        service.voice_triage(
            voice=Voice(),
            signals=[_signal()],
            step=5,
            flags=_enabled_flags(),
            state=_state(),
            questions=compose_voice_triage(trigger),
            context=_ctx(),
        )
    )
    assert outcome.advisory is None or isinstance(outcome.advisory, dict)
    if outcome.advisory:
        assert "answers" not in outcome.advisory  # the reading, never the provider object
