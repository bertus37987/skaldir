"""JEV-20 — feature flags + safe fallback to the existing path (§98/§117.1).

ROT-FIRST evidence, captured before the modules existed:

    $ .venv/bin/python -m pytest tests/test_system_one_flags.py -p no:randomly
    E   ImportError: cannot import name 'system_one_action_gate' from 'godsv.computer_use'
    ... and, one symbol later, the same for godsv.system_one_flags.

What this file pins:

* the §98 flags **plus the §22 additions** (``goal_completion``, ``recovery``,
  ``llm_fallback``) exist with safe defaults (all off, provider disabled).
  JEV_IMPLEMENTATION_GOAL §22 extends §98's six — this pin moved with the
  delta, and the §22 test file (``tests/test_jev_flags_events.py``) pins the
  full nine;
* flags are **config-file driven** — a ``system_one:`` section in the state
  dir's ``settings.yaml``. Behavioural settings are never environment
  variables (the Hermes rubric forbids HERMES_* env vars for non-secret
  config); a stray ``HERMES_SYSTEM_ONE_*`` variable must change nothing;
* an unknown provider and a missing LLM fall back to the disabled evaluator;
* flipping each flag off returns Voice triage, the completion precheck and
  Computer Use to their existing path — asserted on the path marker, not on
  source text.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from skaldir.computer_use import system_one_action_gate
from skaldir.events import KINDS
from skaldir.system_one import (
    ACCESS_DISABLED,
    ACCESS_LLM_ADAPTER,
    DisabledSystemOneEvaluator,
    EvaluationContext,
    NoulAnswer,
    SystemOneState,
    build_evaluation,
    state_fingerprint,
)
from skaldir.system_one_adapters import LLMSystemOneAdapter
from skaldir.system_one_flags import (
    FLAG_DEFAULTS,
    PROVIDER_DISABLED,
    PROVIDER_LLM_ADAPTER,
    PROVIDER_TYPESAFE,
    SystemOneFlags,
    build_evaluator,
    load_system_one_flags,
    save_system_one_flags,
)
from skaldir.system_one_questions import compose_voice_triage
from skaldir.voice import (
    PATH_ADVISORY,
    PATH_LEGACY,
    PATH_SHADOW,
    Voice,
    triage_with_system_one,
)


class _LLM:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        return self.response


def _run(coro):
    return asyncio.run(coro)


def _all_on(**overrides) -> SystemOneFlags:
    base = {
        "enabled": True,
        "provider": PROVIDER_LLM_ADAPTER,
        "shadow_mode": False,
        "voice_triage": True,
        "completion_precheck": True,
        "computer_use": True,
    }
    base.update(overrides)
    return SystemOneFlags(**base)


def _evaluation(*, live: bool = False, value: float = 0.8):
    questions = compose_voice_triage("loop")
    payload = {"goal": "fix the flaky test", "signals": ["loop"]}
    state = SystemOneState(
        payload=payload, input_scope="task", fingerprint=state_fingerprint(payload)
    )
    return build_evaluation(
        context=EvaluationContext(run_id="r", goal_id="g"),
        questions=questions,
        state=state,
        provider=PROVIDER_LLM_ADAPTER,
        provider_model="m",
        access_mode=ACCESS_LLM_ADAPTER,
        answers={
            "loop.same_root_cause": NoulAnswer(question_id="loop.same_root_cause", value=value)
        },
    )


# --------------------------------------------------------------------------- #
# the six flags, config-file driven                                            #
# --------------------------------------------------------------------------- #


def test_the_required_flags_exist_with_safe_defaults():
    """§98's flags + §22's additions — every one of them defaults to off."""
    assert set(FLAG_DEFAULTS) == {
        "enabled",
        "provider",
        "shadow_mode",
        "voice_triage",
        "goal_completion",
        "computer_use",
        "recovery",
        "llm_fallback",
        "completion_precheck",
    }
    flags = load_system_one_flags("/nonexistent/state/dir")
    assert flags.enabled is False
    assert flags.provider == PROVIDER_DISABLED
    assert flags.shadow_mode is False
    assert flags.voice_triage is False
    assert flags.goal_completion is False
    assert flags.computer_use is False
    assert flags.recovery is False
    assert flags.llm_fallback is False
    assert flags.completion_precheck is False
    assert flags.as_dotted()["system_one.enabled"] is False
    assert flags.as_dotted()["system_one.provider"] == "disabled"
    # the §22 additions carry dotted names like the rest
    assert flags.as_dotted()["system_one.goal_completion"] is False
    assert flags.as_dotted()["system_one.recovery"] is False
    assert flags.as_dotted()["system_one.llm_fallback"] is False


def test_flags_are_read_from_the_settings_file(tmp_path):
    (tmp_path / "settings.yaml").write_text(
        "strictness: standard\n"
        "system_one:\n"
        "  enabled: true\n"
        "  provider: llm_system_one_adapter\n"
        "  shadow_mode: true\n"
        "  voice_triage: true\n"
        "  completion_precheck: false\n"
        "  computer_use: true\n",
        encoding="utf-8",
    )
    flags = load_system_one_flags(tmp_path)
    assert flags.enabled is True
    assert flags.provider == PROVIDER_LLM_ADAPTER
    assert flags.shadow_mode is True
    assert flags.voice_triage is True
    assert flags.completion_precheck is False
    assert flags.computer_use is True


def test_flags_round_trip_through_save_and_load(tmp_path):
    saved = save_system_one_flags(tmp_path, _all_on())
    assert saved.enabled is True
    assert load_system_one_flags(tmp_path) == saved
    # an unrelated section in the same file survives
    (tmp_path / "settings.yaml").write_text(
        "strictness: strict\nsystem_one:\n  enabled: true\n", encoding="utf-8"
    )
    import yaml

    data = yaml.safe_load((tmp_path / "settings.yaml").read_text(encoding="utf-8"))
    assert data["strictness"] == "strict"
    assert data["system_one"]["enabled"] is True


def test_broken_settings_file_falls_back_to_safe_defaults(tmp_path):
    (tmp_path / "settings.yaml").write_text(
        "system_one: [this is not a mapping]\n", encoding="utf-8"
    )
    flags = load_system_one_flags(tmp_path)
    assert flags.enabled is False
    assert flags.provider == PROVIDER_DISABLED


def test_behavioural_flags_are_not_environment_variables(monkeypatch, tmp_path):
    """§117.1: non-secret config is config-file driven, never HERMES_* env."""
    monkeypatch.setenv("HERMES_SYSTEM_ONE_ENABLED", "1")
    monkeypatch.setenv("HERMES_SYSTEM_ONE_PROVIDER", "typesafe_jev")
    monkeypatch.setenv("HERMES_SYSTEM_ONE_VOICE_TRIAGE", "true")
    monkeypatch.setenv("GODSV_SYSTEM_ONE_ENABLED", "1")
    flags = load_system_one_flags(tmp_path)
    assert flags == SystemOneFlags.disabled()


def test_unknown_provider_falls_back_to_the_disabled_evaluator(tmp_path):
    flags = _all_on(provider="some_other_vendor")
    evaluator = build_evaluator(flags, llm=_LLM("{}"))
    assert isinstance(evaluator, DisabledSystemOneEvaluator)


def test_disabled_flag_builds_the_disabled_evaluator():
    evaluator = build_evaluator(SystemOneFlags.disabled(), llm=_LLM("{}"))
    assert isinstance(evaluator, DisabledSystemOneEvaluator)


def test_llm_provider_without_an_llm_falls_back_to_disabled():
    evaluator = build_evaluator(_all_on(), llm=None)
    assert isinstance(evaluator, DisabledSystemOneEvaluator)


def test_llm_provider_builds_the_llm_adapter():
    questions = compose_voice_triage("loop")
    response = json.dumps(
        {"answers": {question.id: {"value": 0.4} for question in questions.questions}}
    )
    evaluator = build_evaluator(_all_on(), llm=_LLM(response), model="m")
    assert isinstance(evaluator, LLMSystemOneAdapter)
    assert evaluator.provider == PROVIDER_LLM_ADAPTER


def test_typesafe_provider_builds_the_live_adapter_class():
    from skaldir.system_one_adapters import TypeSafeJevEvaluator

    evaluator = build_evaluator(_all_on(provider=PROVIDER_TYPESAFE), credential_resolver=None)
    assert isinstance(evaluator, TypeSafeJevEvaluator)


# --------------------------------------------------------------------------- #
# flipping each flag returns the existing path                                 #
# --------------------------------------------------------------------------- #


def _legacy_hint():
    from skaldir.monitor import Signal

    signal = Signal(kind="loop", message="same call repeated", severity=2, fingerprint="fp-1")
    outcome = triage_with_system_one(
        voice=Voice(), signals=[signal], step=3, flags=SystemOneFlags.disabled(), evaluation=None
    )
    return outcome


def test_legacy_outcome_is_the_deterministic_voice_hint():
    outcome = _legacy_hint()
    assert outcome.path == PATH_LEGACY
    assert outcome.advisory is None
    assert outcome.hint is not None
    assert outcome.hint.kind == "loop"


@pytest.mark.parametrize(
    "flags",
    [
        SystemOneFlags.disabled(),
        _all_on(enabled=False),
        _all_on(provider=PROVIDER_DISABLED),
        _all_on(voice_triage=False),
        _all_on(shadow_mode=True, voice_triage=False, enabled=False),
    ],
    ids=[
        "defaults",
        "enabled_off",
        "provider_disabled",
        "voice_triage_off",
        "disabled_with_shadow_set",
    ],
)
def test_every_deactivation_keeps_the_existing_voice_path(flags):
    from skaldir.monitor import Signal

    signal = Signal(kind="loop", message="same call repeated", severity=2, fingerprint="fp-1")
    baseline = triage_with_system_one(voice=Voice(), signals=[signal], step=3, flags=None)
    outcome = triage_with_system_one(
        voice=Voice(), signals=[signal], step=3, flags=flags, evaluation=_evaluation()
    )
    assert outcome.path == PATH_LEGACY
    assert outcome.advisory is None
    assert outcome.hint is not None
    assert outcome.hint.kind == baseline.hint.kind
    assert outcome.hint.severity == baseline.hint.severity


def test_fully_enabled_triage_takes_the_advisory_path():
    from skaldir.monitor import Signal

    signal = Signal(kind="loop", message="same call repeated", severity=2, fingerprint="fp-1")
    flags = _all_on()
    outcome = triage_with_system_one(
        voice=Voice(), signals=[signal], step=3, flags=flags, evaluation=_evaluation()
    )
    assert outcome.path == PATH_ADVISORY
    assert outcome.advisory is not None
    assert outcome.advisory["live_jev"] is False
    # System One stays advisory: the deterministic hint is unchanged
    assert outcome.hint is not None and outcome.hint.kind == "loop"


def test_shadow_mode_records_without_consuming_the_answer():
    from skaldir.monitor import Signal

    signal = Signal(kind="loop", message="same call repeated", severity=2, fingerprint="fp-1")
    flags = _all_on(shadow_mode=True, voice_triage=False)
    outcome = triage_with_system_one(
        voice=Voice(), signals=[signal], step=3, flags=flags, evaluation=_evaluation()
    )
    assert outcome.path == PATH_SHADOW
    assert outcome.advisory is not None
    assert outcome.advisory["mode"] == "shadow"


def test_unknown_evaluation_yields_the_legacy_path():
    outcome = triage_with_system_one(
        voice=Voice(), signals=[], step=1, flags=_all_on(), evaluation=_evaluation()
    )
    assert outcome.path == PATH_LEGACY
    assert outcome.hint is None


def test_computer_use_flag_off_keeps_the_existing_action_path():
    disabled_gate = system_one_action_gate(
        flags=SystemOneFlags.disabled(), evaluation=_evaluation(), allowed_kinds=("cli",)
    )
    assert disabled_gate is None
    assert (
        system_one_action_gate(
            flags=_all_on(computer_use=False), evaluation=_evaluation(), allowed_kinds=("cli",)
        )
        is None
    )
    assert system_one_action_gate(flags=_all_on(), evaluation=None, allowed_kinds=("cli",)) is None


def test_computer_use_flag_on_returns_an_advisory_only():
    gate = system_one_action_gate(flags=_all_on(), evaluation=_evaluation(), allowed_kinds=("cli",))
    assert gate is not None
    assert gate["live_jev"] is False
    assert gate["mutates"] is False
    # the pre-approved action set is untouched: System One cannot widen it
    assert gate["allowed_kinds"] == ("cli",)


def test_system_one_event_kinds_are_registered():
    for kind in (
        "system_one_evaluation",
        "system_one_shadow",
        "system_one_error",
        "system_one_blocked",
        "system_one_stale",
        "system_one_decision",
    ):
        assert kind in KINDS, f"event kind not registered: {kind}"


def test_disabled_evaluator_needs_no_provider_configuration():
    questions = compose_voice_triage("loop")
    evaluation = _run(
        DisabledSystemOneEvaluator().evaluate(
            state=_evaluation_state(),
            questions=questions,
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert evaluation.provider_access_mode == ACCESS_DISABLED


def _evaluation_state() -> SystemOneState:
    payload = {"goal": "g"}
    return SystemOneState(
        payload=payload, input_scope="task", fingerprint=state_fingerprint(payload)
    )
