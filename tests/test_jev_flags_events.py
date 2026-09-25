"""JEV §22 (feature flags) + §24 (runtime events).

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_flags_events.py -p no:randomly
    E   ImportError: cannot import name 'SYSTEM_ONE_EVENT_KINDS' from
        'godsv.events' (/home/willy/jev_red/src/godsv/events.py)
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.08s

What this file pins:

* §22 — the nine flags of §22 exist, all defaulting off; the relationship
  between ``goal_completion`` (the new ledger preflight) and
  ``completion_precheck`` (the existing §91 advisory reading) is documented in
  the module docstring *and* observable in behaviour;
* §24 — the five new event kinds are registered next to the existing six; a
  System-One payload carries no secret and no full private state, and the
  payload guard is enforced, not commented.
"""

from __future__ import annotations

import pytest

import skaldir.system_one_flags as flags_module
from skaldir.events import (
    KINDS,
    SYSTEM_ONE_EVENT_KINDS,
    EventStore,
    SecretLeakError,
    system_one_event_payload,
)
from skaldir.secretscan import scan
from skaldir.system_one_flags import (
    FLAG_DEFAULTS,
    PROVIDER_DISABLED,
    SystemOneFlags,
    load_system_one_flags,
)

NINE_FLAGS = {
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

NEW_KINDS = (
    "system_one.requested",
    "system_one.completed",
    "system_one.failed",
    "system_one.discarded_stale",
    "system_one.escalated",
)

EXISTING_KINDS = (
    "system_one_evaluation",
    "system_one_shadow",
    "system_one_error",
    "system_one_blocked",
    "system_one_stale",
    "system_one_decision",
)


# --------------------------------------------------------------------------- #
# §22 — the nine flags                                                         #
# --------------------------------------------------------------------------- #


def test_the_nine_flags_of_section_22_exist_with_safe_defaults():
    assert set(FLAG_DEFAULTS) == NINE_FLAGS
    flags = SystemOneFlags.disabled()
    assert flags.enabled is False
    assert flags.provider == PROVIDER_DISABLED
    assert flags.shadow_mode is False
    assert flags.voice_triage is False
    assert flags.goal_completion is False
    assert flags.computer_use is False
    assert flags.recovery is False
    assert flags.llm_fallback is False
    assert flags.completion_precheck is False
    assert set(flags.as_dotted()) == {f"system_one.{name}" for name in NINE_FLAGS}


def test_the_new_flags_round_trip_through_the_settings_file(tmp_path):
    (tmp_path / "settings.yaml").write_text(
        "system_one:\n"
        "  enabled: true\n"
        "  provider: llm_system_one_adapter\n"
        "  goal_completion: true\n"
        "  recovery: true\n"
        "  llm_fallback: true\n",
        encoding="utf-8",
    )
    flags = load_system_one_flags(tmp_path)
    assert flags.goal_completion is True
    assert flags.recovery is True
    assert flags.llm_fallback is True
    assert flags.voice_triage is False


def test_an_unknown_flag_is_still_refused():
    from skaldir.system_one import SystemOneContractError

    with pytest.raises(SystemOneContractError):
        SystemOneFlags.disabled().with_flag("not_a_flag", True)


def test_the_module_documents_goal_completion_versus_completion_precheck():
    """§22: nobody may wonder which of the two to set."""
    doc = flags_module.__doc__ or ""
    assert "goal_completion" in doc
    assert "completion_precheck" in doc
    assert "ledger" in doc.lower()


def test_with_flag_accepts_the_new_flags():
    flags = (
        SystemOneFlags.disabled()
        .with_flag("enabled", True)
        .with_flag("goal_completion", True)
        .with_flag("recovery", True)
        .with_flag("llm_fallback", True)
    )
    assert flags.goal_completion and flags.recovery and flags.llm_fallback


# --------------------------------------------------------------------------- #
# §24 — runtime events                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_the_five_new_event_kinds_are_registered(kind):
    assert kind in KINDS


@pytest.mark.parametrize("kind", EXISTING_KINDS)
def test_the_existing_six_event_kinds_are_kept(kind):
    assert kind in KINDS


def test_system_one_event_kinds_are_one_named_set():
    assert set(SYSTEM_ONE_EVENT_KINDS) == set(NEW_KINDS) | set(EXISTING_KINDS)


#: A genuinely secret-shaped value: the repo's single scanner contract
#: (godsv.secretscan, "sk-" + 20+ alphanumerics) is the arbiter here — an
#: invented "Bearer sk-…" string that the scanner does not know would prove
#: nothing.
SECRET_SHAPED = "sk-abcdefghijklmnopqrstuvwx1234"


def test_a_system_one_payload_never_carries_a_secret(tmp_path):
    assert scan(SECRET_SHAPED), "the fixture must actually be secret-shaped"
    store = EventStore(tmp_path / "events.db")
    with pytest.raises(SecretLeakError):
        store.append(
            "s1",
            1,
            "system_one.completed",
            {"authorization": f"Bearer {SECRET_SHAPED}"},
        )
    store.close()


def test_the_payload_guard_strips_private_state_and_keeps_the_contract_fields():
    payload = system_one_event_payload(
        "system_one.completed",
        {
            "evaluation_id": "soe-1",
            "provider_mode": "llm_system_one_adapter",
            "live_jev": False,
            "latency_ms": 12,
            "chat_history": [{"role": "user", "content": "private"}],
            "recent_events": ["...", "..."],
            "state_payload": {"goal": "private goal"},
            "api_key": SECRET_SHAPED,
        },
    )
    assert payload["evaluation_id"] == "soe-1"
    assert payload["provider_mode"] == "llm_system_one_adapter"
    assert payload["live_jev"] is False
    for forbidden in ("chat_history", "recent_events", "state_payload", "api_key"):
        assert forbidden not in payload


def test_the_payload_guard_refuses_an_unknown_event_kind():
    with pytest.raises(ValueError):
        system_one_event_payload("system_one.not_a_kind", {})


def test_the_payload_guard_does_not_leak_a_secret_in_a_kept_field(tmp_path):
    payload = system_one_event_payload(
        "system_one.failed",
        {"reason": f"Bearer {SECRET_SHAPED}"},
    )
    store = EventStore(tmp_path / "events.db")
    with pytest.raises(SecretLeakError):
        store.append("s1", 1, "system_one.failed", payload)
    # the store can still persist it redacted — the leak is surfaced, not hidden
    event = store.append_redacted("s1", 1, "system_one.failed", payload)
    assert event.payload["_godsv_redacted"] >= 1
    store.close()
