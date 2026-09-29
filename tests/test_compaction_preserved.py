"""What a compaction may never drop — GOAL §7 / CTX-04, item by item.

GOAL §7: "Kompaktierung erhält Primärgoal, offene Kriterien, Nutzerkorrekturen,
ungeklärte Wirkungen und wichtige Belege."

CTX-04: "Abbruch-/Approvalzustand, unknown Side Effects, Nutzerkorrekturen und Kriterien
dürfen nicht verloren gehen."

The earlier list held the goal, the criteria, the system prompt, the newest user turn and
the newest tool result — which covered two of the five named items but not the other
three. These tests name all of them.
"""

from __future__ import annotations

from skaldir.context_manager import (
    preserved_items,
    verify_preserved,
)


def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def test_the_goal_is_recorded_wherever_it_lives():
    items = preserved_items([_msg("user", "hello")], goal="ship the report")
    labels = [i.label for i in items]
    assert "goal" in labels
    goal = next(i for i in items if i.label == "goal")
    assert goal.kind == "out_of_band", "a goal passed separately must still be accounted for"
    assert goal.sha256 and goal.chars


def test_every_open_criterion_is_recorded_separately():
    items = preserved_items(
        [_msg("user", "hi")],
        goal="g",
        criteria=["sources compared", "report written", "figures checked"],
    )
    labels = [i.label for i in items]
    for n in range(3):
        assert f"criterion:{n}" in labels, f"criterion {n} was not recorded"


def test_user_corrections_are_recorded_and_not_just_the_last_turn():
    """CTX-04: Nutzerkorrekturen dürfen nicht verloren gehen."""
    items = preserved_items(
        [_msg("user", "the newest turn")],
        goal="g",
        user_corrections=[
            "use Europe/Berlin, not UTC",
            "the video editor is Kdenlive",
        ],
    )
    labels = [i.label for i in items]
    assert "user_correction:0" in labels
    assert "user_correction:1" in labels, "more than one correction must all survive"
    correction = next(i for i in items if i.label == "user_correction:0")
    assert "never dropped" in correction.note


def test_unknown_side_effects_are_recorded_so_they_cannot_be_forgotten():
    """CTX-04: unknown Side Effects dürfen nicht verloren gehen."""
    items = preserved_items(
        [_msg("user", "hi")],
        goal="g",
        unknown_effects=["a container was left running", "a file may have been overwritten"],
    )
    labels = [i.label for i in items]
    assert "unknown_effect:0" in labels
    assert "unknown_effect:1" in labels
    effect = next(i for i in items if i.label == "unknown_effect:0")
    assert "must not forget" in effect.note


def test_the_abort_and_approval_state_are_recorded():
    """CTX-04: Abbruch-/Approvalzustand darf nicht verloren gehen."""
    items = preserved_items(
        [_msg("user", "hi")], goal="g",
        cancel_state="stopped by double-escape",
        approval_state="ask",
    )
    labels = [i.label for i in items]
    assert "cancel_state" in labels
    assert "approval_state" in labels


def test_the_five_named_categories_are_all_covered():
    """One assertion per sentence of GOAL §7."""
    items = preserved_items(
        [_msg("user", "correct me here"), _msg("assistant", "ok"), _msg("tool", "result")],
        goal="the primary goal",
        criteria=["open criterion"],
        user_corrections=["a user correction"],
        unknown_effects=["an unresolved effect"],
        cancel_state="c",
        approval_state="a",
    )
    labels = {i.label for i in items}
    assert any(label == "goal" for label in labels), "Primärgoal"
    assert any(label.startswith("criterion:") for label in labels), "offene Kriterien"
    assert any(label.startswith("user_correction:") for label in labels), "Nutzerkorrekturen"
    assert any(label.startswith("unknown_effect:") for label in labels), "ungeklärte Wirkungen"
    assert any(
        label in ("last_tool_result", "current_user_turn") for label in labels
    ), "wichtige Belege"


def test_blank_corrections_and_effects_are_skipped_not_recorded_as_empty():
    items = preserved_items(
        [], goal="g", user_corrections=["  ", ""], unknown_effects=[""]
    )
    labels = [i.label for i in items]
    assert not any(label.startswith("user_correction:") for label in labels)
    assert not any(label.startswith("unknown_effect:") for label in labels)


def test_a_preserved_item_is_verified_after_compaction_not_just_remembered():
    """The record is only useful if something checks it afterwards."""
    messages = [_msg("system", "you are Skaldir"), _msg("user", "do the thing")]
    items = preserved_items(messages, goal="the goal")
    checks = verify_preserved(items, messages)
    assert checks, "verification must produce a verdict per item"
    for check in checks:
        assert check.ok or check.label == "goal", (
            f"{check.label} was not found after compaction: {check.detail}"
        )


def test_an_item_that_vanished_is_reported_as_such():
    """A missing item must be visible, never silently "ok"."""
    messages = [_msg("user", "the turn")]
    items = preserved_items(messages, goal="a goal")
    checks = verify_preserved(items, [_msg("assistant", "something else")])
    assert any(not c.ok for c in checks), (
        "a compaction that dropped everything was reported as clean"
    )
