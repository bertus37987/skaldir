"""State building and staged fitting — port of ``fast-jev-compaction`` ``src/state.ts``.

Asserts the pinned messages, the pairing of calls with results, the token
calibration and the six fitting stages in their upstream order (each stage only
applied when the previous one was not enough), plus the throw when the history
cannot be fitted at all.
"""

from __future__ import annotations

import json

import pytest
from test_jevcomp_fixtures import (
    call_chain,
    first_stage,
    long_texts,
    short_texts,
    state_tokens,
    transcript,
    walk_stages,
    write_fixture,
)

from skaldir.jevcomp import (
    STATE_CONTEXT,
    abridge,
    collect_tool_calls,
    estimate_tokens,
    fit_state,
    goal_from_messages,
    is_pinned,
    merge_call_runs,
    truncate,
)
from skaldir.jevcomp.errors import StateFittingError
from skaldir.jevcomp.types import HistoryEntry, Message, tool_results_of, wire_json


def test_estimate_tokens_matches_the_upstream_calibration() -> None:
    # Values taken from the TypeScript original (state.ts) on these literals.
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("internationalization") == 4
    assert estimate_tokens("12345678") == 4
    assert estimate_tokens("→") == 1
    # Words cost one token per six letters, digit runs half a token per digit.
    assert estimate_tokens("abcdefg") == 2
    assert estimate_tokens("1234") == 2


def test_estimate_tokens_does_not_undercount_json_heavily() -> None:
    payload = wire_json({"file_path": "/Users/x/src/a.ts", "old_string": "a = 1;", "n": 42})
    assert estimate_tokens(payload) >= len(payload) / 3


def test_truncate_and_abridge() -> None:
    assert truncate("abcdefghij", 10) == "abcdefghij"
    assert truncate("abcdefghij", 4) == "abc…"
    assert truncate("abcdefghij", 0) == "…"
    assert abridge("short", 400, 150) == "short"
    long_text = "h" * 400 + "m" * 500 + "t" * 150
    abridged = abridge(long_text, 400, 150)
    assert abridged.startswith("h" * 400)
    assert "[… 500 chars omitted …]" in abridged
    assert abridged.endswith("t" * 150)


def test_is_pinned_keeps_the_first_and_the_newest_messages() -> None:
    assert is_pinned(0, 10, 3) is True
    assert is_pinned(1, 10, 3) is False
    assert is_pinned(6, 10, 3) is False
    assert is_pinned(7, 10, 3) is True  # total - preserve == 7
    assert is_pinned(9, 10, 3) is True
    assert is_pinned(9, 10, 0) is False  # only the first message stays pinned


def test_collect_tool_calls_pairs_results_and_pins_the_recent_ones() -> None:
    messages = transcript()
    calls = collect_tool_calls(messages, 3)
    assert [
        (call.id, call.tool, call.call_index, call.result_index, call.pinned) for call in calls
    ] == [
        ("t1", "Read", 1, 2, False),
        ("t2", "Read", 4, 5, False),
        ("t3", "Bash", 6, 7, True),
    ]
    assert calls[2].is_error is True
    assert calls[0].is_error is False
    assert calls[0].result_chars == 1000
    assert calls[0].input == {"file_path": "src/a.ts"}


def test_collect_tool_calls_ignores_calls_without_a_result() -> None:
    from skaldir.jevcomp import ToolUse

    messages = [
        Message(role="user", text="hi"),
        Message(role="assistant", tool_uses=[ToolUse("x", "Read", {}, text="")]),
    ]
    assert collect_tool_calls(messages, 0) == []


def test_collect_tool_calls_uses_the_last_result_for_a_tool_use_id() -> None:
    from skaldir.jevcomp import ToolResult, ToolUse

    messages = [
        Message(role="assistant", tool_uses=[ToolUse("a", "Read", {}, text="1")]),
        Message(role="user", tool_results=[ToolResult("a", "first")]),
        Message(role="user", tool_results=[ToolResult("a", "second", is_error=True)]),
    ]
    calls = collect_tool_calls(messages, 0)
    assert len(calls) == 1
    assert calls[0].result_index == 2
    assert calls[0].result_chars == len("second")
    assert calls[0].is_error is True


def test_goal_from_messages_uses_the_last_three_user_prompts() -> None:
    messages = transcript()
    goal = goal_from_messages(messages)
    assert "Never edit anything under src/generated" in goal
    assert "go ahead" in goal
    assert goal.count("\n") == 1  # only the two real user prompts

    messages.append(Message(role="user", text="and make it fast"))
    assert goal_from_messages(messages).endswith("and make it fast")


def test_fit_state_full_stage_sends_the_history_with_results_replaced_by_a_note() -> None:
    messages = transcript()
    result = fit_state(
        messages,
        collect_tool_calls(messages, 0),
        {"maxStateTokens": 25_000, "preserveRecentMessages": 0, "goal": "fix the test"},
    )
    assert result.stage == "full"
    assert result.state.context == STATE_CONTEXT
    assert result.state.goal == "fix the test"
    serialized = wire_json(result.state.to_wire())
    # The result bodies are gone from the state, every text is there verbatim.
    assert "export const a = 1;" not in serialized
    assert "Never edit anything under src/generated" in serialized
    assert "go ahead" in serialized
    assert [entry.i for entry in result.state.history] == [0, 1, 3, 4, 6, 8, 9]
    first_call = result.state.history[1].tool_calls[0]
    assert first_call.to_wire() == {
        "id": "t1",
        "tool": "Read",
        "input": '{"file_path":"src/a.ts"}',
        "result": "ok, 1000 chars (omitted)",
    }
    assert result.state.history[4].tool_calls[0].result.startswith("error, ")


def test_fit_state_defaults_the_goal_to_the_last_user_prompts() -> None:
    messages = transcript()
    result = fit_state(messages, [], {"maxStateTokens": 25_000, "preserveRecentMessages": 0})
    assert "Fix the failing test" in result.state.goal
    assert "go ahead" in result.state.goal


def test_fit_state_truncates_tool_inputs_before_touching_message_text() -> None:
    messages = write_fixture()
    result = fit_state(
        messages,
        collect_tool_calls(messages, 0),
        {"maxStateTokens": 300, "preserveRecentMessages": 0, "goal": "g"},
    )
    assert result.stage == "inputs<=200"
    assert result.tokens <= 300
    assert result.state.history[0].text == "start"
    call_input = result.state.history[1].tool_calls[0].input
    assert isinstance(call_input, str)
    assert len(call_input) <= 200


def test_fit_state_walks_the_upstream_stage_order() -> None:
    # Each stage only runs when the previous one did not fit; the chain below is
    # what the TypeScript original produces for this fixture.
    assert first_stage(walk_stages(write_fixture(), 0)) == [
        "full",
        "inputs<=200",
        "inputs<=60",
        "old calls compacted",
        "old messages left out",
        "THROW",
    ]
    assert first_stage(walk_stages(transcript(), 1, goal="g"))[0] == "full"


def test_fit_state_abridges_long_texts_and_collapses_old_messages_last() -> None:
    messages = long_texts(4) + [Message(role="user", text="latest")]
    option = {"preserveRecentMessages": 1, "goal": "g"}
    abridged = fit_state(messages, [], {**option, "maxStateTokens": 1800})
    assert abridged.stage == "texts abridged"
    assert abridged.tokens <= 1800
    assert "chars omitted" in abridged.state.history[1].text
    assert abridged.state.history[0].text == messages[0].text  # pinned, untouched
    assert abridged.state.history[4].text == "latest"

    collapsed = fit_state(messages, [], {**option, "maxStateTokens": 420})
    assert collapsed.stage == "old messages collapsed"
    assert collapsed.tokens <= 420
    assert collapsed.state.history[1].text == f"[… {len(messages[1].text)} chars omitted …]"
    assert "lorem" in collapsed.state.history[0].text  # the first message is pinned
    assert collapsed.state.history[4].text == "latest"


def test_fit_state_compacts_old_calls_to_one_line_and_merges_runs() -> None:
    messages = call_chain(40)
    calls = collect_tool_calls(messages, 1)
    full = fit_state(messages, calls, {"maxStateTokens": 10**7, "preserveRecentMessages": 1})
    assert full.stage == "full"

    compacted = fit_state(
        messages,
        calls,
        {"maxStateTokens": int(full.tokens * 0.8), "preserveRecentMessages": 1},
    )
    assert compacted.stage == "old calls compacted"
    assert compacted.tokens <= int(full.tokens * 0.8)
    assert compacted.state.history[1].tool_calls[0] == (
        "t1 Read file_path=/repo/src/module-0.ts → ok 1ch"
    )
    assert compacted.state.history[-1].text == "done"

    merged = fit_state(
        messages,
        calls,
        {"maxStateTokens": int(full.tokens * 0.6), "preserveRecentMessages": 1},
    )
    assert merged.stage == "old calls merged"
    assert merged.tokens <= int(full.tokens * 0.6)
    assert len(merged.state.history) == 3
    assert len(merged.state.history[1].tool_calls) == 40
    assert merged.state.history[1].tool_calls[39].startswith("t40 Read ")
    assert merged.state.history[0].text == "start"
    assert merged.state.history[2].text == "done"

    with pytest.raises(StateFittingError, match="too large"):
        fit_state(messages, calls, {"maxStateTokens": 30, "preserveRecentMessages": 1})


def test_fit_state_throws_when_even_the_merged_state_is_too_large() -> None:
    messages = [Message(role="user", text="a" * 2000), Message(role="assistant", text="b")]
    with pytest.raises(StateFittingError, match="too large"):
        fit_state(messages, [], {"maxStateTokens": 50, "preserveRecentMessages": 0})


def test_fit_state_accepts_camel_case_options_and_dicts() -> None:
    messages = short_texts(10)
    camel = fit_state(messages, [], {"maxStateTokens": 100_000, "preserveRecentMessages": 1})
    snake = fit_state(
        messages,
        [],
        {"max_state_tokens": 100_000, "preserve_recent_messages": 1},
    )
    assert camel.tokens == snake.tokens
    assert camel.stage == snake.stage == "full"


def test_fit_state_keeps_pinned_messages_out_of_the_left_out_stage() -> None:
    messages = short_texts(60)
    fitted = fit_state(
        messages,
        [],
        {"maxStateTokens": int(state_tokens(messages, 1) * 0.3), "preserveRecentMessages": 1},
    )
    assert fitted.stage in {"old messages left out", "old calls merged"}
    assert fitted.state.history[0].text == messages[0].text
    assert fitted.state.history[-1].text == messages[-1].text


def test_fit_state_state_round_trips_as_json() -> None:
    messages = transcript()
    fitted = fit_state(messages, collect_tool_calls(messages, 0), {"maxStateTokens": 25_000})
    encoded = json.dumps(fitted.state.to_wire(), separators=(",", ":"), ensure_ascii=False)
    decoded = json.loads(encoded)
    assert decoded["history"][1]["tool_calls"][0]["result"] == "ok, 1000 chars (omitted)"
    assert estimate_tokens(encoded) == estimate_tokens(wire_json(decoded))


def test_merge_call_runs_folds_only_call_only_entries_of_the_same_role() -> None:
    entries = [
        HistoryEntry(i=1, role="assistant", text="", tool_calls=["t1 Read → ok 1ch"]),
        HistoryEntry(i=2, role="assistant", text="", tool_calls=["t2 Read → ok 1ch"]),
        HistoryEntry(i=3, role="user", text="", tool_calls=["t3 Read → ok 1ch"]),
        HistoryEntry(i=4, role="assistant", text="", tool_calls=None),
        HistoryEntry(i=5, role="assistant", text="text", tool_calls=["t4 Read → ok 1ch"]),
    ]
    merged = merge_call_runs(entries, pinned=lambda _entry: False)
    assert [entry.i for entry in merged] == [1, 3, 4, 5]
    assert merged[0].tool_calls == ["t1 Read → ok 1ch", "t2 Read → ok 1ch"]
    # The originals are copies: folding never mutates the input entries.
    assert entries[0].tool_calls == ["t1 Read → ok 1ch"]


def test_collect_tool_calls_reads_foreign_message_shapes() -> None:
    messages = [
        {
            "role": "assistant",
            "text": "",
            "toolUses": [{"toolUseId": "x", "tool": "Read", "input": {}}],
        },
        {
            "role": "user",
            "text": "",
            "toolResults": [{"toolUseId": "x", "text": "body", "isError": True}],
        },
    ]
    calls = collect_tool_calls(messages, 0)
    assert len(calls) == 1
    assert calls[0].tool_use_id == "x"
    assert calls[0].result_chars == 4
    assert calls[0].is_error is True
    assert tool_results_of(messages[1])[0]["text"] == "body"
