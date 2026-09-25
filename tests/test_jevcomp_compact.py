"""Decisions, batching, rebuild and the fallback path — port of ``src/compact.ts``.

Proves every decision branch (keep / drop_result / drop_call), the pinned
messages, the batching limit, the token fitting on the request path, the
"raise so the caller can fall back" failure model, and — most importantly — that
the original messages that matter come back verbatim instead of being rewritten
into a summary.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import pytest
from test_jevcomp_fixtures import FILE_A, call_chain, call_ids, state_tokens, transcript

from skaldir.jevcomp import (
    DEFAULT_MODEL,
    DEFAULT_OPTIONS,
    SYSTEM_ONE_URL,
    JevAnswerError,
    JevAsker,
    JevCompactionError,
    JevRequestError,
    Message,
    StateFittingError,
    ToolResult,
    ToolUse,
    apply_decisions,
    batch_calls,
    build_jev_request,
    collect_tool_calls,
    compact,
    compact_async,
    decide_call,
    fit_state,
    keep_threshold,
    message_chars,
    parse_jev_response,
    questions_for,
    reduction_ratio,
    resolve_options,
    run_batches,
    truncated_result_text,
    wire_json,
)
from skaldir.jevcomp.fixture import (
    NoulAsker,
    ReplayAsker,
    constant_asker,
    keep_calls_drop_results,
    noul_asker,
    replay_asker,
)
from skaldir.jevcomp.request import noul_answer
from skaldir.jevcomp.types import CallAnswer

# --------------------------------------------------------------------------- options


def test_resolve_options_fills_the_upstream_defaults() -> None:
    resolved = resolve_options()
    assert resolved == DEFAULT_OPTIONS
    assert resolved.keep_threshold == 0.5
    assert resolved.preserve_recent_messages == 6
    assert resolved.max_state_tokens == 25_000
    assert resolved.max_request_tokens == 30_000
    assert resolved.truncate_head_chars == 300
    assert resolved.max_concurrency == 0


def test_resolve_options_ignores_non_finite_and_floors_the_rest() -> None:
    resolved = resolve_options(
        {
            "keepThreshold": math.nan,
            "preserveRecentMessages": 2.7,
            "truncateHeadChars": -1.2,
            "maxStateTokens": 0,
            "maxRequestTokens": -5,
        }
    )
    assert resolved.keep_threshold == 0.5
    assert resolved.preserve_recent_messages == 2
    assert resolved.truncate_head_chars == 0
    assert resolved.max_state_tokens == 1
    assert resolved.max_request_tokens == 1
    assert resolve_options({"goal": "fix it"}).goal == "fix it"
    assert resolve_options({"keep_threshold": 0.9}).keep_threshold == 0.9


def test_keep_threshold_accepts_a_number_or_an_options_object() -> None:
    assert keep_threshold(0.7) == 0.7
    assert keep_threshold({"keepThreshold": 0.25}) == 0.25
    assert keep_threshold(None) == 0.5


# --------------------------------------------------------------------------- questions


def test_questions_for_asks_the_two_upstream_questions() -> None:
    calls = collect_tool_calls(transcript(), 0)
    questions = questions_for(calls[2])
    assert list(questions) == ["call_t3", "result_t3"]
    assert questions["call_t3"]["type"] == "noul"
    assert questions["call_t3"]["instructions"] == (
        "Tool call t3 (Bash) should stay in the history: knowing this call was made, "
        "with its input, still matters for what the assistant does next"
    )
    assert questions["result_t3"]["instructions"].startswith(
        "The full output of tool call t3 (Bash, 34 chars)"
    )
    assert questions["result_t3"]["instructions"].endswith("re-running the tool would not do")


# --------------------------------------------------------------------------- batching


def test_batch_calls_keeps_everything_in_one_request_when_it_fits() -> None:
    calls = collect_tool_calls(transcript(), 0)
    assert batch_calls(calls, 1000, {"maxRequestTokens": 30_000}) == [calls]


def test_batch_calls_splits_when_the_state_leaves_little_room() -> None:
    calls = collect_tool_calls(call_chain(10), 0)
    assert len(calls) == 10
    batches = batch_calls(calls, 29_600, {"maxRequestTokens": 30_000})
    assert len(batches) > 1
    assert call_ids([call for batch in batches for call in batch]) == call_ids(calls)


def test_batch_calls_throws_when_one_question_does_not_fit() -> None:
    from skaldir.jevcomp.compact import questions_tokens

    calls = collect_tool_calls(transcript(), 0)
    with pytest.raises(StateFittingError, match="no room"):
        batch_calls(calls, 29_990, {"maxRequestTokens": 30_000})
    # The budget includes the 20-token request envelope: exactly enough still fits.
    one = calls[:1]
    need = questions_tokens(one[0])
    assert batch_calls(one, 30_000 - 20 - need, {"maxRequestTokens": 30_000}) == [one]
    with pytest.raises(StateFittingError, match="no room"):
        batch_calls(one, 30_000 - 19 - need, {"maxRequestTokens": 30_000})


# --------------------------------------------------------------------------- decisions


def test_decide_call_keeps_drops_the_result_or_drops_the_call() -> None:
    calls = collect_tool_calls(transcript(), 0)
    unpinned = calls[0]
    assert unpinned.pinned is False

    kept = decide_call(unpinned, CallAnswer(keep_call=0.9, keep_result=0.7), 0.5)
    assert (kept.action, kept.reason) == ("keep", "kept")
    assert kept.id == "t1" and kept.tool == "Read"
    assert (kept.keep_call, kept.keep_result) == (0.9, 0.7)

    shortened = decide_call(unpinned, CallAnswer(keep_call=0.9, keep_result=0.2), 0.5)
    assert (shortened.action, shortened.reason) == ("drop_result", "result_dropped")

    dropped = decide_call(unpinned, CallAnswer(keep_call=0.1, keep_result=0.2), 0.5)
    assert (dropped.action, dropped.reason) == ("drop_call", "call_dropped")


def test_decide_call_threshold_is_inclusive_and_pinned_wins() -> None:
    calls = collect_tool_calls(transcript(), 0)
    pinned = collect_tool_calls(transcript(), 3)[2]
    assert pinned.pinned is True
    decision = decide_call(pinned, CallAnswer(keep_call=0.0, keep_result=0.0), 0.5)
    assert (decision.action, decision.reason) == ("keep", "pinned")
    # Exactly at the threshold counts as kept, in both directions.
    assert decide_call(calls[0], CallAnswer(0.0, 0.5), 0.5).action == "keep"
    assert decide_call(calls[0], CallAnswer(0.5, 0.0), 0.5).action == "drop_result"
    # A pinned call is never dropped, whatever the probabilities say.
    assert decide_call(pinned, CallAnswer(keep_call=0.0, keep_result=0.0), 0.9).action == "keep"


def test_decide_call_reads_the_threshold_from_an_options_object() -> None:
    calls = collect_tool_calls(transcript(), 0)
    answer = CallAnswer(keep_call=0.8, keep_result=0.3)
    assert decide_call(calls[0], answer, {"keepThreshold": 0.5}).action == "drop_result"
    assert decide_call(calls[0], answer, {"keep_threshold": 0.2}).action == "keep"


# --------------------------------------------------------------------------- rebuild


def test_apply_decisions_removes_a_dropped_call_without_orphan_results() -> None:
    messages = transcript()
    calls = collect_tool_calls(messages, 0)
    decisions = [
        decide_call(calls[0], CallAnswer(0.1, 0.1), 0.5),  # drop_call
        decide_call(calls[1], CallAnswer(0.9, 0.1), 0.5),  # drop_result
        decide_call(calls[2], CallAnswer(0.9, 0.9), 0.5),  # keep
    ]
    kept = apply_decisions(messages, decisions, calls, 300)
    assert [
        message_text(message) or tool_use_id(message) or result_id(message) for message in kept
    ] == [
        "Never edit anything under src/generated. Fix the failing test.",
        "a.ts looks fine; checking b.ts",
        "tool-2",
        "tool-2",
        "tool-3",
        "tool-3",
        "The failure is in b.test.ts; fixing now.",
        "go ahead",
    ]
    # No result survives without its call, and no call without its result.
    surviving = {tool_use_id(message) for message in kept} | {
        result_id(message) for message in kept
    }
    assert "tool-1" not in surviving
    assert kept[5].tool_results[0].text == "FAIL b.test.ts: expected 2 to be 3"


def test_apply_decisions_reports_the_three_branch_order() -> None:
    messages = transcript()
    calls = collect_tool_calls(messages, 0)
    decisions = [
        decide_call(calls[0], CallAnswer(0.1, 0.1), 0.5),
        decide_call(calls[1], CallAnswer(0.9, 0.1), 0.5),
        decide_call(calls[2], CallAnswer(0.9, 0.9), 0.5),
    ]
    assert [(decision.action, decision.reason) for decision in decisions] == [
        ("drop_call", "call_dropped"),
        ("drop_result", "result_dropped"),
        ("keep", "kept"),
    ]


def message_text(message: Message) -> str:
    return message.text


def tool_use_id(message: Message) -> str:
    return message.tool_uses[0].tool_use_id if message.tool_uses else ""


def result_id(message: Message) -> str:
    return message.tool_results[0].tool_use_id if message.tool_results else ""


def test_apply_decisions_truncates_a_dropped_result_and_honours_a_zero_head() -> None:
    messages = transcript()
    calls = collect_tool_calls(messages, 0)
    decisions = [decide_call(calls[0], CallAnswer(0.9, 0.1), 0.5)]
    original = messages[2].tool_results[0].text
    total = len(original)

    kept = apply_decisions(messages, decisions, calls, 50)
    expected = (
        f"{original[:50]}\n[fast-jev-compaction truncated {total - 50} chars of this tool result; "
        "re-run the tool if needed]"
    )
    assert kept[2].tool_results[0].text == expected
    assert kept[1].tool_uses[0].text == expected  # the call's own copy is shortened too

    no_head = apply_decisions(messages, decisions, calls, 0)
    assert no_head[2].tool_results[0].text == (
        f"[fast-jev-compaction truncated {total} chars of this tool result; "
        "re-run the tool if needed]"
    )


def test_apply_decisions_never_touches_short_results_and_keeps_identity() -> None:
    messages = transcript()
    messages[4].tool_uses[0].text = "y" * 100
    messages[5].tool_results[0].text = "y" * 100
    calls = collect_tool_calls(messages, 0)
    decisions = [decide_call(calls[1], CallAnswer(0.9, 0.1), 0.5)]
    kept = apply_decisions(messages, decisions, calls, 300)
    assert kept[0] is messages[0]
    assert kept[4] is messages[4]
    assert kept[5] is messages[5]
    assert kept[6] is messages[6]


def test_apply_decisions_removes_a_message_that_loses_all_its_content() -> None:
    messages = [
        Message(role="user", text="keep me"),
        Message(role="assistant", text=""),
        Message(
            role="assistant", tool_uses=[ToolUse("a", "Read", {"file_path": "x.ts"}, text="body")]
        ),
        Message(role="user", tool_results=[ToolResult("a", "body")]),
    ]
    calls = collect_tool_calls(messages, 0)
    assert calls[0].pinned is False
    decisions = [decide_call(calls[0], CallAnswer(0.0, 0.0), 0.5)]
    kept = apply_decisions(messages, decisions, calls, 300)
    # The call message and its result message are gone ("keep me" and the already
    # empty message that was not touched stay, as upstream leaves them alone).
    assert [message.text for message in kept] == ["keep me", ""]
    assert kept[0] is messages[0]
    assert kept[1] is messages[1]
    assert all(not message.tool_uses and not message.tool_results for message in kept)


def test_apply_decisions_keeps_a_message_with_text_when_its_call_is_dropped() -> None:
    messages = [
        Message(role="user", text="start"),
        Message(
            role="assistant",
            text="I will read x.ts",
            tool_uses=[ToolUse("a", "Read", {"file_path": "x.ts"}, text="body")],
        ),
        Message(role="user", tool_results=[ToolResult("a", "body")]),
    ]
    calls = collect_tool_calls(messages, 0)
    assert calls[0].pinned is False
    decisions = [decide_call(calls[0], CallAnswer(0.0, 0.0), 0.5)]
    kept = apply_decisions(messages, decisions, calls, 300)
    assert len(kept) == 2
    assert kept[1].text == "I will read x.ts"
    assert kept[1].tool_uses == []


def test_truncated_result_text_boundaries() -> None:
    short = "x" * 420  # head(300) + 120 -> unchanged
    assert truncated_result_text(short, False, 300) == short
    long_text = "x" * 421
    assert truncated_result_text(long_text, False, 300) == (
        f"{'x' * 300}\n[fast-jev-compaction truncated 121 chars of this tool result; "
        "re-run the tool if needed]"
    )
    assert "(error)" in truncated_result_text(long_text, True, 300)
    assert truncated_result_text("y" * 500, False, 0).startswith(
        "[fast-jev-compaction truncated 500"
    )


# --------------------------------------------------------------------------- characters


def test_message_chars_counts_text_input_and_output() -> None:
    messages = transcript()
    assert message_chars(messages[0]) == len(messages[0].text)
    first_call = message_chars(messages[1])
    assert first_call == len('{"file_path":"src/a.ts"}')
    assert message_chars(messages[2]) == len(FILE_A)
    assert (
        message_chars(Message(role="user", text="x", tool_uses=[ToolUse("a", "Read", None)])) == 21
    )


def test_reduction_ratio_reports_the_removed_share() -> None:
    messages = transcript()
    result = compact(messages, keep_calls_drop_results(), {"preserveRecentMessages": 1})
    assert result.stats.chars_before == 2244
    assert result.stats.chars_after == 1022
    assert reduction_ratio(result) == pytest.approx((2244 - 1022) / 2244)
    assert result.reduction_ratio() == pytest.approx(reduction_ratio(result))
    assert reduction_ratio({"stats": {"charsBefore": 0, "charsAfter": 0}}) == 0.0


# --------------------------------------------------------------------------- compact


def test_compact_makes_no_request_when_nothing_is_a_candidate() -> None:
    asker = constant_asker(0.0)
    messages = [Message(role="user", text="hello"), Message(role="assistant", text="hi")]
    result = compact(messages, asker)
    assert asker.seen == []
    assert result.messages == messages
    assert (result.stats.requests, result.stats.state_stage, result.stats.calls) == (0, "", 0)
    assert result.decisions == []


def test_compact_keeps_everything_when_jev_wants_it() -> None:
    result = compact(transcript(), constant_asker(0.95), {"preserveRecentMessages": 1})
    assert all(decision.action == "keep" for decision in result.decisions)
    assert [decision.reason for decision in result.decisions] == ["kept", "kept", "kept"]
    assert result.stats.kept == 3
    assert reduction_ratio(result) == 0
    assert result.messages[0].text == transcript()[0].text


def test_compact_pins_the_first_and_the_newest_messages() -> None:
    # Newest 6 messages pinned -> t2 (indices 4/5) and t3 (indices 6/7) are pinned,
    # t1 is the only candidate.
    result = compact(transcript(), constant_asker(0.0), {"preserveRecentMessages": 6})
    assert (result.stats.pinned, result.stats.calls_dropped) == (2, 1)
    tool_use_ids = {tool.tool_use_id for message in result.messages for tool in message.tool_uses}
    assert tool_use_ids == {"tool-2", "tool-3"}
    assert result.stats.calls == 3

    # The first message is always pinned, even with nothing preserved.
    messages = [
        Message(role="user", tool_results=[ToolResult("a", "body")]),
        Message(
            role="assistant", tool_uses=[ToolUse("a", "Read", {"file_path": "x.ts"}, text="body")]
        ),
        Message(role="assistant", text="after"),
    ]
    pinned_result = compact(messages, constant_asker(0.0), {"preserveRecentMessages": 0})
    assert [decision.action for decision in pinned_result.decisions] == ["keep"]
    assert [decision.reason for decision in pinned_result.decisions] == ["pinned"]

    # With nothing preserved, every call is a candidate.
    loose = compact(transcript(), constant_asker(0.0), {"preserveRecentMessages": 1})
    assert loose.stats.pinned == 0
    assert loose.stats.calls_dropped == 3
    assert not any(message.tool_uses for message in loose.messages)


def test_compact_reports_the_three_branches_for_one_transcript() -> None:
    asker = replay_asker(
        {
            "call_t1": 0.9,
            "result_t1": 0.9,  # keep
            "call_t2": 0.9,
            "result_t2": 0.1,  # drop the result
            "call_t3": 0.1,
            "result_t3": 0.1,  # drop call and result
        }
    )
    result = compact(transcript(), asker, {"preserveRecentMessages": 0, "maxRequestTokens": 30_000})
    assert [(decision.id, decision.action) for decision in result.decisions] == [
        ("t1", "keep"),
        ("t2", "drop_result"),
        ("t3", "drop_call"),
    ]
    assert (result.stats.kept, result.stats.results_dropped, result.stats.calls_dropped) == (
        1,
        1,
        1,
    )
    assert result.stats.requests == 1


def test_compact_resends_the_full_state_with_every_batch_and_merges_the_answers() -> None:
    messages = transcript()
    tokens = state_tokens(messages, 1)
    asker = keep_calls_drop_results()
    result = compact(
        messages,
        asker,
        {"preserveRecentMessages": 1, "maxRequestTokens": tokens + 150},
    )
    assert result.stats.requests == len(asker.seen)
    assert result.stats.requests > 1
    assert sorted(name for _state, names in asker.seen for name in names) == [
        "call_t1",
        "call_t2",
        "call_t3",
        "result_t1",
        "result_t2",
        "result_t3",
    ]
    # The same complete state travels with every batch.
    states = {_wire_state(state) for state, _names in asker.seen}
    assert len(states) == 1
    assert [decision.action for decision in result.decisions] == [
        "drop_result",
        "drop_result",
        "drop_result",
    ]
    assert len(result.messages) == len(messages)
    assert result.stats.results_dropped == 3
    assert result.stats.kept == 0
    assert result.stats.calls_dropped == 0
    assert reduction_ratio(result) > 0


def _wire_state(state: dict) -> str:
    import json

    return json.dumps(state, separators=(",", ":"), ensure_ascii=False)


def test_compact_writes_the_camel_case_stats_object_of_the_upstream() -> None:
    result = compact(transcript(), keep_calls_drop_results(), {"preserveRecentMessages": 1})
    stats = result.stats.to_wire()
    assert list(stats) == [
        "messagesBefore",
        "messagesAfter",
        "charsBefore",
        "charsAfter",
        "calls",
        "kept",
        "resultsDropped",
        "callsDropped",
        "pinned",
        "stateTokens",
        "stateStage",
        "requests",
        "ms",
    ]
    assert stats["stateTokens"] == 531
    assert stats["stateStage"] == "full"
    assert stats["messagesBefore"] == 10
    assert stats["messagesAfter"] == 10
    assert stats["ms"] >= 0


# ------------------------------------------------------- the point of the port: verbatim


def test_compact_keeps_the_messages_that_matter_verbatim() -> None:
    messages = transcript()
    asker = replay_asker(
        {
            "call_t1": 0.9,
            "result_t1": 0.1,  # keep the call, shorten its result
            "call_t2": 0.1,
            "result_t2": 0.1,  # drop call and result
            "call_t3": 0.9,
            "result_t3": 0.9,  # keep call and result verbatim
        }
    )
    result = compact(messages, asker, {"preserveRecentMessages": 0})
    kept = result.messages

    # 1. User text is never rewritten: the constraint survives character for character.
    constraint = "Never edit anything under src/generated. Fix the failing test."
    assert kept[0] is messages[0]
    assert kept[0].text == constraint

    # 2. Assistant text survives verbatim too.
    kept_texts = [message.text for message in kept if message.text]
    assert "The failure is in b.test.ts; fixing now." in kept_texts
    assert "a.ts looks fine; checking b.ts" in kept_texts

    # 3. No text of the output is invented: every text comes from the input transcript.
    originals = {message.text for message in messages}
    assert set(kept_texts) <= originals

    # 4. Nothing in the output is a summary of the input.
    joined = "\n".join(
        message.text
        + "".join(tool.text or "" for tool in message.tool_uses)
        + "".join(block.text for block in message.tool_results)
        for message in kept
    )
    for forbidden in ("summary", "summarized", "TL;DR", "compacted transcript"):
        assert forbidden not in joined

    # 5. A shortened result keeps its verbatim head plus the note — never a rewrite.
    shortened = next(
        message
        for message in kept
        if message.tool_results and "truncated" in message.tool_results[0].text
    )
    assert shortened.tool_results[0].tool_use_id == "tool-1"
    text = shortened.tool_results[0].text
    assert text.startswith(FILE_A[:300])
    assert text.endswith("re-run the tool if needed]")
    assert "truncated 700 chars" in text  # 1000 chars of output, 300 kept as head
    # The call block carries the very same shortened text (its input stays untouched).
    call_message = next(
        message
        for message in kept
        if message.tool_uses and message.tool_uses[0].tool_use_id == "tool-1"
    )
    assert call_message.tool_uses[0].text == text
    assert call_message.tool_uses[0].input == {"file_path": "src/a.ts"}

    # 6. A kept result is byte-identical, including its exact error text.
    kept_error = next(
        message.tool_results[0].text
        for message in kept
        if message.tool_results and message.tool_results[0].tool_use_id == "tool-3"
    )
    assert kept_error == "FAIL b.test.ts: expected 2 to be 3"

    # 7. The dropped call is gone together with its result (no orphan, no stub).
    assert "tool-2" not in {
        block.tool_use_id
        for message in kept
        for block in list(message.tool_uses) + list(message.tool_results)
    }
    assert "src/b.ts" not in joined + "".join(
        wire_json(tool.input) for message in kept for tool in message.tool_uses
    )


def test_compact_keeps_a_kept_result_byte_identical() -> None:
    asker = replay_asker({"call_t1": 0.9, "result_t1": 0.9}, default=0.0)
    result = compact(transcript(), asker, {"preserveRecentMessages": 0})
    kept_result = next(
        message.tool_results[0].text
        for message in result.messages
        if message.tool_results and message.tool_results[0].tool_use_id == "tool-1"
    )
    assert kept_result == FILE_A
    assert len(kept_result) == 1000


# --------------------------------------------------------------------------- failures


def test_compact_raises_on_a_malformed_answer() -> None:
    class Broken:
        def ask(self, state: dict, questions: dict) -> dict:
            return {"answers": {"call_t1": {"noul": 0.5}}}

    with pytest.raises(JevAnswerError, match=r"Invalid Jev answer for result_t1"):
        compact(transcript(), Broken(), {"preserveRecentMessages": 0})


def test_compact_raises_when_an_answer_is_not_a_finite_number() -> None:
    class Raw:
        def __init__(self, answer: dict) -> None:
            self.answer = answer

        def ask(self, state: dict, questions: dict) -> dict:
            return {"answers": {name: self.answer for name in questions}}

    for bad in ({"noul": "0.5"}, {"noul": True}, {"noul": float("inf")}, {}, {"choice": "a"}):
        with pytest.raises(JevAnswerError):
            compact(transcript(), Raw(bad), {"preserveRecentMessages": 0})


def test_the_fixture_askers_satisfy_the_asker_protocol() -> None:
    assert isinstance(keep_calls_drop_results(), JevAsker)
    assert isinstance(replay_asker({"call_t1": 0.5}), JevAsker)
    assert isinstance(constant_asker(0.5), JevAsker)
    assert isinstance(NoulAsker(probability=lambda _name: 0.5), JevAsker)


def test_noul_answer_rejects_anything_but_a_finite_noul() -> None:
    assert noul_answer({"q": {"noul": 0.25}}, "q") == 0.25
    for bad in (
        {"q": {"noul": "0.25"}},
        {"q": {"noul": None}},
        {"q": {}},
        {},
        {"q": {"noul": True}},
    ):
        with pytest.raises(JevAnswerError):
            noul_answer(bad, "q")


def test_compact_leaves_the_transcript_alone_when_the_provider_fails() -> None:
    messages = transcript()
    before = [
        (message.text, list(message.tool_uses), list(message.tool_results)) for message in messages
    ]

    class Failing:
        def ask(self, state: dict, questions: dict) -> dict:
            raise JevRequestError("Jev request failed (503): provider down")

    with pytest.raises(JevRequestError):
        compact(messages, Failing(), {"preserveRecentMessages": 0})
    # The caller falls back: the input list is untouched, nothing was compacted.
    assert [
        (message.text, list(message.tool_uses), list(message.tool_results)) for message in messages
    ] == before


def test_compact_raises_when_the_history_cannot_be_fitted() -> None:
    asker = constant_asker(0.5)
    messages = [
        Message(role="user", text="start"),
        Message(
            role="assistant",
            tool_uses=[ToolUse("a", "Read", {"file_path": "x" * 3000}, text="body")],
        ),
        Message(role="user", tool_results=[ToolResult("a", "body")]),
    ]
    with pytest.raises(StateFittingError, match="too large"):
        compact(messages, asker, {"maxStateTokens": 40, "preserveRecentMessages": 0})
    assert asker.seen == []  # nothing was asked: it failed before the request


def test_compact_raises_when_a_batch_leaves_no_room_for_its_questions() -> None:
    messages = transcript()
    tokens = state_tokens(messages, 0)
    with pytest.raises(StateFittingError, match="no room"):
        compact(messages, constant_asker(0.5), {"maxRequestTokens": tokens + 5})


def test_compaction_errors_are_jevc_compaction_errors() -> None:
    assert issubclass(JevRequestError, JevCompactionError)
    assert issubclass(JevAnswerError, JevCompactionError)
    assert issubclass(StateFittingError, JevCompactionError)
    assert issubclass(JevCompactionError, RuntimeError)


def test_provider_errors_propagate_with_the_upstream_validation() -> None:
    from skaldir.jevcomp import parse_jev_response

    with pytest.raises(JevRequestError, match="500"):
        parse_jev_response(500, False, "boom")
    with pytest.raises(JevRequestError, match="malformed"):
        parse_jev_response(200, True, "not json")
    with pytest.raises(JevRequestError, match="missing answers"):
        parse_jev_response(200, True, "{}")
    assert parse_jev_response(200, True, '{"answers":{}}').answers == {}


# --------------------------------------------------------------------------- concurrency


def test_run_batches_can_be_serialised() -> None:
    calls = collect_tool_calls(transcript(), 0)
    asker = keep_calls_drop_results()
    batches = [calls[:1], calls[1:]]
    merged = run_batches(asker, {"context": "c"}, batches, 1)
    assert [set(mapping) for mapping in merged] == [{"t1"}, {"t2", "t3"}]
    assert len(asker.seen) == 2
    assert run_batches(asker, {"context": "c"}, [], 0) == []


def test_compact_honours_max_concurrency_one() -> None:
    messages = transcript()
    tokens = state_tokens(messages, 1)
    asker = keep_calls_drop_results()
    result = compact(
        messages,
        asker,
        {"preserveRecentMessages": 1, "maxRequestTokens": tokens + 150, "maxConcurrency": 1},
    )
    assert result.stats.requests > 1
    assert [decision.action for decision in result.decisions] == ["drop_result"] * 3


def test_compact_async_uses_the_async_asker_and_matches_the_sync_result() -> None:
    class AsyncNoul:
        def __init__(self) -> None:
            self.seen: list[list[str]] = []

        async def ask(self, state: dict, questions: dict) -> dict:
            self.seen.append(list(questions.keys()))
            return {
                "answers": {
                    name: {"noul": 0.9 if name.startswith("call_") else 0.1} for name in questions
                }
            }

    asker = AsyncNoul()
    messages = transcript()
    result = asyncio.run(compact_async(messages, asker, {"preserveRecentMessages": 1}))
    sync_result = compact(messages, keep_calls_drop_results(), {"preserveRecentMessages": 1})
    assert [decision.action for decision in result.decisions] == [
        decision.action for decision in sync_result.decisions
    ]
    assert result.stats.state_tokens == sync_result.stats.state_tokens
    assert result.stats.requests == sync_result.stats.requests == 1
    assert sorted(name for names in asker.seen for name in names) == [
        "call_t1",
        "call_t2",
        "call_t3",
        "result_t1",
        "result_t2",
        "result_t3",
    ]
    assert result.messages[0].text == messages[0].text


def test_compact_accepts_options_as_dataclass_and_leaves_input_objects_alone() -> None:
    from skaldir.jevcomp import CompactOptions

    messages = transcript()
    options = CompactOptions(preserve_recent_messages=6)
    result = compact(messages, constant_asker(0.0), options)
    # With 6 preserved messages the newest calls (indices 4/5 and 6/7) are pinned.
    assert result.stats.pinned == 2
    assert result.stats.calls == 3
    assert result.stats.calls_dropped == 1
    assert fit_state(messages, collect_tool_calls(messages, 6), options).tokens > 0


def test_replay_asker_raises_for_unknown_questions() -> None:
    assert isinstance(replay_asker({"call_t1": 0.5}), ReplayAsker)
    with pytest.raises(JevAnswerError):
        compact(transcript(), replay_asker({"call_t1": 0.5}), {"preserveRecentMessages": 0})


def test_noul_asker_records_what_was_asked() -> None:
    asker = noul_asker(lambda name: 0.4 if name.startswith("call_") else 0.6)
    result = compact(transcript(), asker, {"preserveRecentMessages": 0})
    assert len(asker.seen) == 1
    state, names = asker.seen[0]
    assert state["context"].startswith("A coding assistant conversation")
    assert names == ["call_t1", "result_t1", "call_t2", "result_t2", "call_t3", "result_t3"]
    assert result.stats.kept == 3


# ------------------------------------------------------------------ the provider seam


class ProviderAsker:
    """The documented integration seam: our LLM provider behind the upstream wire shape."""

    def __init__(self, probability: Any, status: int = 200) -> None:
        self.probability = probability
        self.status = status
        self.requests: list = []

    def ask(self, state: dict, questions: dict) -> Any:
        request = build_jev_request(state, questions, api_key="k", model="jev-test")
        self.requests.append(request)
        body = json.loads(request.body)
        answers = {
            name: {"type": "noul", "noul": self.probability(name)} for name in body["questions"]
        }
        return parse_jev_response(
            self.status,
            self.status == 200,
            json.dumps({"model": "jev-test", "answers": answers}),
        )


def test_a_provider_adapter_built_on_the_wire_helpers_drives_compaction() -> None:
    asker = ProviderAsker(lambda name: 0.9 if name.startswith("call_") else 0.1)
    result = compact(transcript(), asker, {"preserveRecentMessages": 1})
    assert [decision.action for decision in result.decisions] == ["drop_result"] * 3

    request = asker.requests[0]
    assert request.url == SYSTEM_ONE_URL
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer k"
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.body)
    assert body["model"] == "jev-test"
    assert body["state"]["context"].startswith("A coding assistant conversation")
    assert list(body["questions"]) == [
        "call_t1",
        "result_t1",
        "call_t2",
        "result_t2",
        "call_t3",
        "result_t3",
    ]
    assert "should stay in the history" in body["questions"]["call_t1"]["instructions"]


def test_default_model_and_endpoint_are_the_upstream_ones() -> None:
    request = build_jev_request({"a": 1}, {"q": {"type": "noul", "instructions": "x"}}, api_key="k")
    assert request.url == SYSTEM_ONE_URL == "https://api.typesafe.ai/v1/systemone"
    assert json.loads(request.body)["model"] == DEFAULT_MODEL == "jev-latest"


def test_a_failing_provider_makes_compaction_raise_so_the_caller_can_fall_back() -> None:
    with pytest.raises(JevRequestError, match="503"):
        compact(
            transcript(),
            ProviderAsker(lambda _name: 0.5, status=503),
            {"preserveRecentMessages": 0},
        )


def test_provider_answers_missing_a_question_raise_instead_of_defaulting() -> None:
    class Partial:
        def ask(self, state: dict, questions: dict) -> Any:
            return parse_jev_response(200, True, '{"answers": {"call_t1": {"noul": 0.5}}}')

    with pytest.raises(JevAnswerError, match=r"Invalid Jev answer for result_t1"):
        compact(transcript(), Partial(), {"preserveRecentMessages": 0})
