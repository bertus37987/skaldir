"""§1.6 — health transitions write events and health gates selection.

What this proves, claim by claim:

- every state change of a tool appends one ``capability_health_changed`` event
  (and an unchanged state appends none);
- the kind is registered (append whitelist) *and* chat-visible;
- a failing event write never kills the caller — the health fact and its
  ledger row land regardless;
- ``selectable`` is the documented selection gate over ``routable``: an
  explicit matrix CANDIDATE → HEALTHY → BROKEN → QUARANTINED → RETIRED;
- the degradation path reacts to the *real* usage ledger (``observe_usage``)
  and is idempotent through its cursor.
"""

import json

from skaldir import capability_health as health
from skaldir.events import CHAT_KINDS, KINDS, EventStore


def _tool(runtime, name="demo_tool"):
    directory = runtime / "generated_tools" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return directory


def _health_events(runtime):
    store = EventStore(runtime / "events.db")
    try:
        return [event.payload for event in store.iter_session("capability-health")]
    finally:
        store.close()


def test_state_change_writes_an_event(tmp_path):
    _tool(tmp_path)
    state = health.record_verification(tmp_path, "demo_tool", ok=True, detail="gates green")
    assert state == "HEALTHY"
    payloads = _health_events(tmp_path)
    assert len(payloads) == 1
    assert payloads[0]["name"] == "demo_tool"
    assert (payloads[0]["from"], payloads[0]["to"]) == ("CANDIDATE", "HEALTHY")
    assert payloads[0]["auto_routable"] is True


def test_unchanged_state_writes_no_second_event(tmp_path):
    _tool(tmp_path)
    health.record_verification(tmp_path, "demo_tool", ok=True)
    health.record_verification(tmp_path, "demo_tool", ok=True)
    assert len(_health_events(tmp_path)) == 1


def test_event_kind_is_registered_and_chat_visible():
    assert "capability_health_changed" in KINDS
    assert "capability_health_changed" in CHAT_KINDS


def test_event_write_failure_does_not_kill_the_caller(tmp_path):
    _tool(tmp_path)
    # sqlite cannot open a directory as a database: every append fails
    (tmp_path / "events.db").mkdir(parents=True, exist_ok=True)
    assert health.record_verification(tmp_path, "demo_tool", ok=True) == "HEALTHY"
    assert (tmp_path / "capability_health.jsonl").exists()
    assert health.file_state(tmp_path, "demo_tool")["state"] == "HEALTHY"


def test_selectable_matrix(tmp_path):
    _tool(tmp_path)
    allowed, reason = health.selectable(tmp_path, "demo_tool")
    assert allowed is False and reason.startswith("CANDIDATE")

    health.record_verification(tmp_path, "demo_tool", ok=True)
    allowed, reason = health.selectable(tmp_path, "demo_tool")
    assert allowed is True and "HEALTHY" in reason

    health.record_trigger(tmp_path, "demo_tool", "false_pass", detail="certified a red run")
    allowed, reason = health.selectable(tmp_path, "demo_tool")
    assert allowed is False and "BROKEN" in reason

    for _ in range(3):
        health.record_trigger(tmp_path, "demo_tool", "timeout")
    allowed, reason = health.selectable(tmp_path, "demo_tool")
    assert allowed is False and "QUARANTINED" in reason

    health.retire(tmp_path, "demo_tool", reason="withdrawn")
    allowed, reason = health.selectable(tmp_path, "demo_tool")
    assert allowed is False and "RETIRED" in reason

    # an unknown tool is a candidate, never routable
    allowed, reason = health.selectable(tmp_path, "never_built")
    assert allowed is False and reason.startswith("CANDIDATE")


def test_every_transition_of_the_lifecycle_is_an_event(tmp_path):
    _tool(tmp_path)
    health.record_verification(tmp_path, "demo_tool", ok=True)
    health.record_trigger(tmp_path, "demo_tool", "false_pass")
    for _ in range(3):
        health.record_trigger(tmp_path, "demo_tool", "timeout")
    health.retire(tmp_path, "demo_tool", reason="withdrawn")
    states = [(payload["from"], payload["to"]) for payload in _health_events(tmp_path)]
    assert states == [
        ("CANDIDATE", "HEALTHY"),
        ("HEALTHY", "BROKEN"),
        ("BROKEN", "DEGRADED"),
        ("DEGRADED", "QUARANTINED"),
        ("QUARANTINED", "RETIRED"),
    ]


def test_observe_usage_degrades_from_the_real_ledger(tmp_path):
    _tool(tmp_path)
    health.record_verification(tmp_path, "demo_tool", ok=True)
    (tmp_path / "tool_usage.jsonl").write_text(
        json.dumps(
            {
                "ts": 5000.0,
                "name": "demo_tool",
                "ok": False,
                "error": "ModuleNotFoundError: No module named 'pandas'",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    first = health.observe_usage(tmp_path)
    assert first["count"] == 1
    assert first["observed"][0]["trigger"] == "dependency_incompatible"
    assert health.file_state(tmp_path, "demo_tool")["state"] == "BROKEN"
    second = health.observe_usage(tmp_path)  # cursor: the same line is never re-classified
    assert second["count"] == 0
    assert [payload["to"] for payload in _health_events(tmp_path)] == ["HEALTHY", "BROKEN"]


def test_recheck_and_retest_are_reported(tmp_path):
    _tool(tmp_path)
    health.record_verification(tmp_path, "demo_tool", ok=True)
    assert health.recheck(tmp_path, "demo_tool")["state"] == "HEALTHY"
    assert health.retest(tmp_path, "demo_tool", reason="repair")["state"] == "VERIFIED"
    transitions = [(payload["from"], payload["to"]) for payload in _health_events(tmp_path)]
    assert transitions == [("CANDIDATE", "HEALTHY"), ("HEALTHY", "VERIFIED")]


def test_retirement_is_terminal_even_under_later_issues(tmp_path):
    """A retired tool stays retired: later observations never re-open it."""
    _tool(tmp_path)
    health.record_verification(tmp_path, "demo_tool", ok=True)
    health.retire(tmp_path, "demo_tool", reason="withdrawn")
    for _ in range(4):
        health.record_trigger(tmp_path, "demo_tool", "false_pass", detail="late noise")
    state = health.file_state(tmp_path, "demo_tool")
    assert state["state"] == "RETIRED"
    allowed, reason = health.selectable(tmp_path, "demo_tool")
    assert allowed is False and "RETIRED" in reason
    # the observations themselves are still in the ledger (audit kept)
    assert len(health.events(tmp_path, "demo_tool")) == 1 + 1 + 4
