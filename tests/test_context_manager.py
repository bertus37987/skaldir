"""FINAL-7 — compaction wired into a live run: accounting, trigger, preserved set, fallback.

The port (:mod:`skaldir.jevcomp`) is proven by its own suite; this one proves the *live
wiring* around it: the two token estimates are labelled and never mixed with the
provider's own numbers, the trigger fires on the configured fraction, the preserved set
(system prompt, goal, criteria, current user turn, last tool result) survives byte for
byte, a compactor failure falls back to the uncompacted history instead of a fake
success, and both CLI verbs answer.

The history used throughout is the loop's own shape (``content`` / ``tool_calls`` /
``tool_call_id``), because that is what a live run hands to its client.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from skaldir import cli, cli_contract
from skaldir import context_manager as C
from skaldir.jevcomp import (
    CompactResult,
    CompactStats,
    JevAnswerError,
    JevRequestError,
    JevResponse,
)
from skaldir.jevcomp.fixture import constant_asker, replay_asker

GOAL = "Fix the failing test in a long run without breaking the build."
CRITERIA = ("pytest -q passes", "no new runtime dependency")

RESULT_CHARS = 3000


def _blob(seed: int, size: int = RESULT_CHARS) -> str:
    unit = f"line {seed}: some output with content; "
    return (unit * (size // len(unit) + 1))[:size]


def loop_history(turns: int = 15, *, result_chars: int = RESULT_CHARS) -> list[dict[str, Any]]:
    """A live-shaped history: system + task + ``turns`` tool round-trips + a new user turn."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Skaldir, a long-run agent."},
        {"role": "user", "content": GOAL},
    ]
    for index in range(turns):
        call_id = f"call_{index:02d}"
        messages.append(
            {
                "role": "assistant",
                "content": f"step {index}: reading the module",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "read_text",
                            "arguments": json.dumps({"path": f"src/mod_{index}.py"}),
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "read_text",
                "content": _blob(index, result_chars),
            }
        )
    messages.append({"role": "user", "content": "keep going: now run the tests"})
    return messages


def long_history() -> list[dict[str, Any]]:
    """The proof history: >= 60 messages, large tool results, one live-shaped run."""
    history = loop_history(turns=30)
    assert len(history) >= 60, len(history)
    return history


def estimate_chars(messages: list[Any]) -> int:
    """The exact character count a report's 'before' block must carry."""
    from skaldir.jevcomp import message_chars

    return sum(message_chars(item) for item in C.TranscriptView(messages).port)


def manager(**kwargs: Any) -> C.ContextManager:
    settings: dict[str, Any] = {
        "goal": GOAL,
        "criteria": CRITERIA,
        "budget_tokens": 4000,
        "trigger_fraction": 0.5,
        "asker": C.fixture_asker(),
    }
    settings.update(kwargs)
    return C.ContextManager(**settings)


# --------------------------------------------------------------------------- #
# 1. token accounting: measured facts, labelled estimates                       #
# --------------------------------------------------------------------------- #


def test_accounting_separates_measured_and_estimated() -> None:
    accounting = C.accounting_for(loop_history(2), budget_tokens=1000, trigger_fraction=0.5)
    measured = accounting["measured"]
    estimated = accounting["estimated"]
    assert measured["messages"] == len(loop_history(2))
    assert measured["chars"] > 0
    assert measured["provider"] == {} and measured["provider_reported"] is False
    assert estimated["exact"] is False
    assert estimated["tokens"] > 0 and estimated["tokens_chars4"] > 0
    assert "estimate" in estimated["method"]
    assert "estimate" in estimated["note"]
    # the two blocks never share a key
    assert not set(measured) & set(estimated)


def test_chars_are_measured_and_tokens_are_not() -> None:
    history = loop_history(1)
    accounting = C.accounting_for(history, budget_tokens=10_000)
    view = C.TranscriptView(history)
    from skaldir.jevcomp import message_chars

    assert accounting["measured"]["chars"] == sum(message_chars(m) for m in view.port)
    assert accounting["estimated"]["tokens_chars4"] == accounting["measured"]["chars"] // 4


def test_budget_state_reports_provenance_and_threshold() -> None:
    accounting = C.accounting_for(loop_history(2), budget_tokens=1000, trigger_fraction=0.25)
    budget = accounting["budget"]
    assert budget["tokens"] == 1000 and budget["threshold_tokens"] == 250
    assert budget["trigger_fraction"] == 0.25
    assert budget["used_source"] == "estimate"
    assert budget["state"] in {"below_trigger", "at_trigger", "over_budget"}
    assert budget["remaining_tokens"] == 1000 - budget["used_tokens"]


def test_provider_usage_is_measured_and_says_so() -> None:
    accounting = C.accounting_for(
        loop_history(2), measured={"prompt_tokens": 1234}, budget_tokens=2000, trigger_fraction=0.5
    )
    assert accounting["measured"]["provider"] == {"prompt_tokens": 1234}
    assert accounting["measured"]["provider_reported"] is True
    assert accounting["budget"]["used_tokens"] == 1234
    assert accounting["budget"]["used_source"] == "provider_measured"
    assert accounting["estimated"]["exact"] is False


def test_the_flag_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKALDIR_CONTEXT_BUDGET", "9999")
    from_flag = manager(budget_tokens=4000)
    assert from_flag.budget_tokens == 4000 and from_flag.budget_source == "--budget"
    from_env = C.ContextManager(budget_tokens=None)
    assert from_env.budget_tokens == 9999
    assert from_env.budget_source == "SKALDIR_CONTEXT_BUDGET"


def test_the_legacy_environment_name_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKALDIR_CONTEXT_BUDGET", raising=False)
    monkeypatch.setenv("GODSV_CONTEXT_BUDGET", "2048")
    resolved = C.ContextManager(budget_tokens=None)
    assert resolved.budget_tokens == 2048
    assert resolved.budget_source == "GODSV_CONTEXT_BUDGET"


def test_a_nonsense_budget_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKALDIR_CONTEXT_BUDGET", "not-a-number")
    assert C.ContextManager(budget_tokens=None).budget_tokens == C.DEFAULT_BUDGET_TOKENS
    monkeypatch.setenv("SKALDIR_CONTEXT_BUDGET", "-5")
    assert C.ContextManager(budget_tokens=None).budget_tokens == C.DEFAULT_BUDGET_TOKENS


def test_a_nonsense_trigger_fraction_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKALDIR_CONTEXT_TRIGGER_FRACTION", "7")
    assert C.ContextManager(trigger_fraction=None).trigger_fraction == C.DEFAULT_TRIGGER_FRACTION
    monkeypatch.setenv("SKALDIR_CONTEXT_TRIGGER_FRACTION", "0.5")
    assert C.ContextManager(trigger_fraction=None).trigger_fraction == 0.5


def test_note_usage_keeps_the_last_prompt_count_and_ignores_nonsense() -> None:
    live = manager()
    live.note_usage({"prompt_tokens": 100, "completion_tokens": 5})
    live.note_usage({})
    live.note_usage({"prompt_tokens": 250})
    live.note_usage({"prompt_tokens": "nope"})  # type: ignore[dict-item]
    assert live.last_prompt_tokens == 250
    assert live.usage_snapshots == [
        {"prompt_tokens": 100, "completion_tokens": 5},
        {"prompt_tokens": 250},
    ]


# --------------------------------------------------------------------------- #
# 2. the automatic trigger                                                      #
# --------------------------------------------------------------------------- #


def test_the_trigger_fires_on_the_configured_fraction() -> None:
    history = loop_history(10)
    tight = manager(budget_tokens=500, trigger_fraction=0.5)
    decision = tight.trigger(history)
    assert decision.fire is True
    assert decision.used_source == "estimate"
    assert "at trigger" in decision.reason

    roomy = manager(budget_tokens=10_000_000, trigger_fraction=0.5)
    decision = roomy.trigger(history)
    assert decision.fire is False and "below trigger" in decision.reason


def test_the_measured_count_decides_the_trigger_when_there_is_one() -> None:
    history = loop_history(2)
    live = manager(budget_tokens=100_000, trigger_fraction=0.5)
    assert live.trigger(history).fire is False
    live.note_usage({"prompt_tokens": 60_000})
    decision = live.trigger(history)
    assert decision.fire is True
    assert decision.used_source == "provider_measured" and decision.used_tokens == 60_000


def test_compaction_can_be_switched_off_by_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKALDIR_CONTEXT_COMPACTION", "0")
    assert C.ContextManager().enabled is False
    off = C.ContextManager(enabled=None)
    assert off.trigger(loop_history(10)).fire is False
    assert "disabled" in off.trigger(loop_history(10)).reason


def test_maybe_compact_does_nothing_below_the_trigger() -> None:
    history = loop_history(3)
    live = manager(budget_tokens=10_000_000)
    outgoing, report = live.maybe_compact(history)
    assert report is None and outgoing == history
    assert live.reports == []


def test_maybe_compact_compacts_when_the_trigger_fires() -> None:
    history = loop_history(10)
    live = manager(budget_tokens=300, trigger_fraction=0.5)
    outgoing, report = live.maybe_compact(history)
    assert report is not None and report.applied is True
    assert len(outgoing) <= len(history)
    assert live.summary()["compactions"] == 1


# --------------------------------------------------------------------------- #
# 3. the preserved set                                                          #
# --------------------------------------------------------------------------- #


def test_preserved_lists_the_five_things_and_their_digests() -> None:
    history = loop_history(4)
    items = C.preserved_items(history, goal=GOAL, criteria=CRITERIA, system_prompt="sys")
    by_label = {item.label: item for item in items}
    assert set(by_label) == {
        "goal",
        "criterion:0",
        "criterion:1",
        "system_prompt",
        "current_user_turn",
        "last_tool_result",
    }
    assert by_label["goal"].kind == "out_of_band"
    assert by_label["system_prompt"].kind == "message" and by_label["system_prompt"].index == 0
    assert by_label["current_user_turn"].index == len(history) - 1
    assert by_label["last_tool_result"].index == len(history) - 2
    assert by_label["goal"].sha256 == hashlib.sha256(GOAL.encode()).hexdigest()


def test_preserved_goal_is_out_of_band_when_the_transcript_has_none() -> None:
    items = C.preserved_items(loop_history(1), goal=GOAL, criteria=CRITERIA)
    system = next(item for item in items if item.label == "system_prompt")
    assert system.kind == "message"  # the transcript carries one
    no_system = C.preserved_items([{"role": "user", "content": "hi"}], system_prompt="s")
    assert next(item for item in no_system if item.label == "system_prompt").kind == "out_of_band"
    assert not [item for item in no_system if item.label == "last_tool_result"]


def test_the_pinning_window_is_widened_to_cover_the_preserved_set() -> None:
    history = loop_history(10)
    live = manager(preserve_recent_messages=0)
    window = live.preserve_window(history, live.preserved(history))
    assert window["configured"] == 0
    assert window["reason"] == "widened to pin the preserved set"
    assert window["counted"] == ["last_tool_result"]
    # the last tool result sits at len-2, so a window of 2 covers it
    assert window["effective"] == 2 and window["pinned_from_index"] == len(history) - 2


def test_the_first_message_does_not_inflate_the_window() -> None:
    """messages[0] is pinned by the port itself — counting it would pin everything."""
    history = loop_history(10)
    live = manager()
    window = live.preserve_window(history, live.preserved(history))
    assert window["effective"] < len(history)
    assert window["effective"] >= 2  # the newest tool result is always pinned


def test_an_autonomous_run_does_not_pin_the_whole_history() -> None:
    """The bug the window rule exists for: one human turn at index 1, results piling up.

    Counting every preserved message would demand a window of ``count - 1`` (the human
    turn sits near the front), pin the entire history and turn the compaction into a
    silent no-op — which is exactly what the first live run measured (51 of 52 messages
    pinned, 0 tokens freed).
    """
    history = [message for message in loop_history(12) if not (
        message["role"] == "user" and message["content"] == "keep going: now run the tests"
    )]
    assert history[-1]["role"] == "tool"
    live = manager(preserve_recent_messages=0)
    window = live.preserve_window(history, live.preserved(history))
    assert window["effective"] <= 2
    assert window["effective"] < len(history) // 2  # not the whole history
    assert window["counted"] == ["last_tool_result"]
    compacted, report = live.compact(history)
    assert report.reason == "compacted"
    assert report.freed["chars_freed"] > 0
    assert len(compacted) <= len(history)


def test_a_configured_window_survives_when_it_is_already_wide_enough() -> None:
    history = loop_history(3)
    live = manager(preserve_recent_messages=50)
    window = live.preserve_window(history, live.preserved(history))
    assert window["effective"] == 50 and window["reason"] == "configured"


def test_a_real_compaction_keeps_the_preserved_messages_byte_identical() -> None:
    history = loop_history(12)
    live = manager()
    items = C.preserved_items(history, goal=GOAL, criteria=CRITERIA)
    compacted, report = live.compact(history)
    assert report.applied is True
    digests = [C.message_digest(message) for message in compacted]
    for label in ("system_prompt", "current_user_turn", "last_tool_result"):
        item = next(entry for entry in items if entry.label == label)
        assert item.sha256 in digests, label
        assert any(entry["label"] == label and entry["ok"] for entry in report.preserved)
        # equality, not just the digest: the exact bytes came through
        original = history[item.index]
        assert any(message == original for message in compacted)


def test_the_preserved_check_reports_every_item() -> None:
    history = loop_history(12)
    live = manager()
    _, report = live.compact(history)
    labels = {entry["label"] for entry in report.preserved}
    assert {"system_prompt", "goal", "current_user_turn", "last_tool_result"} <= labels
    assert all(entry["ok"] for entry in report.preserved)


def test_a_compaction_that_would_drop_a_preserved_item_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hard rule bites: a compactor result that loses the last tool result is discarded."""
    history = loop_history(12)

    def bad_compactor(messages: list[Any], asker: Any, options: Any = None) -> CompactResult:
        return CompactResult(
            messages=list(messages[:-2]),  # drops the newest tool result
            decisions=[],
            stats=CompactStats(
                messages_before=len(messages),
                messages_after=len(messages) - 2,
                chars_before=0,
                chars_after=0,
                calls=0,
                kept=0,
                results_dropped=0,
                calls_dropped=0,
                pinned=0,
                state_tokens=0,
                state_stage="full",
                requests=1,
                ms=1,
            ),
        )

    monkeypatch.setattr(C, "compact", bad_compactor)
    live = manager()
    outgoing, report = live.compact(history)
    assert report.applied is False and report.refused is True
    assert report.reason == "refused_preserved_violation"
    assert "last_tool_result" in report.error
    assert outgoing == history
    assert all(message is original for message, original in zip(outgoing, history, strict=True))


# --------------------------------------------------------------------------- #
# 4. the fallback                                                               #
# --------------------------------------------------------------------------- #


class _RaisingAsker:
    """A provider that fails exactly the way the upstream model fails."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def ask(self, state: Any, questions: Any) -> JevResponse:
        self.calls += 1
        raise self.error


def test_a_compactor_failure_falls_back_to_the_uncompacted_history() -> None:
    history = loop_history(12)
    asker = _RaisingAsker(JevRequestError("Jev request failed (500): upstream broke"))
    live = manager(asker=asker)
    outgoing, report = live.compact(history)
    assert report.fallback is True and report.applied is False
    assert report.reason == "fallback_compactor_error"
    assert report.error_type == "JevRequestError"
    assert "500" in report.error
    assert asker.calls == 1
    assert outgoing == history
    assert all(message is original for message, original in zip(outgoing, history, strict=True))
    assert any("uncompacted" in note.lower() for note in report.notes)
    assert live.summary()["fallbacks"] == 1


def test_the_fallback_also_survives_an_unexpected_error() -> None:
    live = manager(asker=_RaisingAsker(RuntimeError("a bug in the compactor")))
    _, report = live.compact(loop_history(8))
    assert report.fallback is True and report.error_type == "RuntimeError"
    assert report.reason == "fallback_unexpected_error"


def test_a_malformed_answer_never_compacts_on_a_guess() -> None:
    live = manager(asker=replay_asker({}, default=None))
    _, report = live.compact(loop_history(8))
    assert report.fallback is True and report.error_type == "JevAnswerError"
    assert report.applied is False


def test_without_an_asker_the_result_is_a_refusal_not_a_silent_pass() -> None:
    live = manager(asker=None)
    outgoing, report = live.compact(loop_history(8))
    assert report.reason == "no_asker" and report.applied is False
    assert report.error_type == "AskerUnavailable"
    assert outgoing == loop_history(8)


def test_maybe_compact_never_raises_out_of_a_run() -> None:
    """A live call site must not have to wrap this: the failure is in the report."""
    live = manager(asker=_RaisingAsker(JevRequestError("nope")))
    outgoing, report = live.maybe_compact(loop_history(12), force=True)
    assert report is not None and report.fallback is True
    assert len(outgoing) == len(loop_history(12))


def test_a_fallback_never_claims_the_preserved_set_was_checked() -> None:
    """``null`` beats a green tick with no referent: nothing was applied, nothing checked."""
    live = manager(asker=_RaisingAsker(JevRequestError("nope")))
    _, report = live.compact(loop_history(12))
    assert report.preserved  # the digests were still recorded before the attempt
    assert all(item["checked"] is False for item in report.preserved)
    assert all("ok" not in item for item in report.preserved)
    assert C._preserved_ok(report) is None
    assert any("NOT re-checked" in note for note in report.notes)


def test_a_successful_compaction_marks_the_check_as_done() -> None:
    live = manager()
    _, report = live.compact(loop_history(12))
    assert report.applied is True
    assert all(item["checked"] is True and item["ok"] is True for item in report.preserved)
    assert C._preserved_ok(report) is True


# --------------------------------------------------------------------------- #
# 5. what a compaction actually changes (and what it must not)                   #
# --------------------------------------------------------------------------- #


def test_an_untouched_message_is_the_same_object_it_came_in_as() -> None:
    history = loop_history(10)
    live = manager()
    compacted, report = live.compact(history)
    assert report.applied is True
    for message, original in zip(compacted, history, strict=False):
        if message is original:
            continue
        assert message["role"] in {"assistant", "tool"}
    assert compacted[0] is history[0]
    assert compacted[-1] is history[-1]


def test_the_report_says_what_was_shortened_and_what_stayed_verbatim() -> None:
    live = manager()
    _, report = live.compact(loop_history(12))
    assert report.shortened and report.kept_verbatim
    assert {row["action"] for row in report.shortened} == {"drop_result"}
    assert {row["action"] for row in report.kept_verbatim} == {"keep"}
    assert all("tool" in row and "id" in row for row in report.shortened)
    assert report.before["tokens_estimate_jevcomp"] > report.after["tokens_estimate_jevcomp"]
    assert report.freed["tokens_estimate_freed"] > 0
    assert report.freed["estimate"] is True


def test_a_removal_branch_drops_call_and_result_together() -> None:
    """``noul`` below the threshold removes the call *and* its result — no orphan."""
    live = manager(asker=constant_asker(0.1), preserve_recent_messages=1)
    history = loop_history(6)
    compacted, report = live.compact(history)
    assert report.dropped
    dropped_ids = {row["tool_use_id"] for row in report.dropped}
    surviving_calls = [
        call["id"]
        for message in compacted
        for call in (message.get("tool_calls") or [])
    ]
    assert not dropped_ids & set(surviving_calls)
    surviving_results = [
        message.get("tool_call_id") for message in compacted if message["role"] == "tool"
    ]
    assert not dropped_ids & set(surviving_results)


def test_the_translated_history_still_speaks_the_loop_shape() -> None:
    history = loop_history(8)
    live = manager(asker=constant_asker(0.1), preserve_recent_messages=1)
    compacted, report = live.compact(history)
    assert report.applied is True
    for message in compacted:
        assert set(message) <= {"role", "content", "tool_calls", "tool_call_id", "name"}
        if message["role"] == "assistant":
            for call in message.get("tool_calls") or []:
                assert call["type"] == "function"
                json.loads(call["function"]["arguments"])  # arguments stay parseable
        if message["role"] == "tool":
            assert message["tool_call_id"] and "name" in message


def test_a_shortened_result_keeps_its_head_and_its_tool_identity() -> None:
    history = loop_history(5)
    live = manager(preserve_recent_messages=1)
    compacted, report = live.compact(history)
    by_id = {row["tool_use_id"]: row for row in report.shortened}
    assert by_id
    for message in compacted:
        call_id = message.get("tool_call_id")
        if call_id in by_id:
            assert message["content"].startswith("line ") or message["content"].startswith("step ")
            assert len(message["content"]) < RESULT_CHARS
            assert message["name"] == "read_text"


def test_a_compaction_with_nothing_to_decide_says_so() -> None:
    live = manager(preserve_recent_messages=1000)
    outgoing, report = live.compact(loop_history(6))
    assert report.reason == "no_candidate"
    assert report.stats["requests"] == 0
    assert any("no tool call outside the pinning window" in note for note in report.notes)
    assert outgoing == loop_history(6)


# --------------------------------------------------------------------------- #
# the proof history: >= 60 messages, large results, trigger + preserved + fallback #
# --------------------------------------------------------------------------- #


def test_the_proof_history_fires_compacts_and_preserves() -> None:
    history = long_history()
    assert len(history) >= 60
    live = manager(budget_tokens=6000, trigger_fraction=0.5)
    decision = live.trigger(history)
    assert decision.fire is True, decision.reason

    kept_before = {
        message.get("tool_call_id"): message for message in history if message["role"] == "tool"
    }
    compacted, report = live.compact(history)
    assert report.applied is True
    assert report.before["tokens_estimate_jevcomp"] > report.after["tokens_estimate_jevcomp"]
    assert report.freed["chars_freed"] > 0

    digests_after = {C.message_digest(message) for message in compacted}
    for label in ("system_prompt", "current_user_turn", "last_tool_result"):
        item = next(entry for entry in report.preserved if entry["label"] == label)
        assert item["ok"] is True, label
        assert item["sha256"] in digests_after
    surviving_results = [
        message["tool_call_id"] for message in compacted if message["role"] == "tool"
    ]
    assert max(surviving_results) == max(kept_before)


def test_the_proof_fallback_path_keeps_the_long_history_intact() -> None:
    history = long_history()
    live = manager(
        budget_tokens=6000, trigger_fraction=0.5, asker=_RaisingAsker(JevRequestError("boom"))
    )
    outgoing, report = live.maybe_compact(history)
    assert report is not None and report.fallback is True
    assert len(outgoing) == len(history) == len(history)
    assert all(new is old for new, old in zip(outgoing, history, strict=True))


# --------------------------------------------------------------------------- #
# the provider asker                                                            #
# --------------------------------------------------------------------------- #


class _FakeChatClient:
    """A client shaped like ``OpenAICompatibleLLM``: ``chat()`` returning text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.seen: list[Any] = []

    def chat(self, messages: Any, **kwargs: Any) -> Any:
        self.seen.append((messages, kwargs))

        class _Answer:
            text = self.text  # noqa: N805 - a tiny stand-in, not a real class body issue

        return _Answer()


class _FakeCompleteClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.calls.append({"prompt_id": prompt_id, "prompt": prompt, "tier": tier})
        return self.text


def _questions() -> dict[str, Any]:
    return {"call_t1": {"type": "noul"}, "result_t1": {"type": "noul"}}


def test_the_provider_asker_parses_a_well_formed_answer() -> None:
    client = _FakeChatClient('{"answers": {"call_t1": {"noul": 0.9}, "result_t1": {"noul": 0.1}}}')
    asker = C.ProviderJevAsker(client, tier="main")
    response = asker.ask({"goal": "g", "history": []}, _questions())
    assert response.answers["call_t1"]["noul"] == 0.9
    assert client.seen[0][1] == {"tier": "main", "tools": None, "tool_choice": None}


def test_the_provider_asker_tolerates_prose_and_fences() -> None:
    body = '{"answers": {"call_t1": {"noul": 1}, "result_t1": {"noul": 0}}}'
    client = _FakeChatClient(f"Sure!\n```json\n{body}\n```")
    response = C.ProviderJevAsker(client).ask({}, _questions())
    assert response.answers["result_t1"]["noul"] == 0.0


def test_the_provider_asker_works_with_a_complete_only_client() -> None:
    client = _FakeCompleteClient(
        '{"answers": {"call_t1": {"noul": 0.4}, "result_t1": {"noul": 0.4}}}'
    )
    response = C.ProviderJevAsker(client).ask({}, _questions())
    assert response.answers["call_t1"]["noul"] == 0.4
    assert client.calls[0]["prompt_id"] == "jev_compaction"


def test_the_provider_asker_raises_instead_of_guessing() -> None:
    with pytest.raises(JevRequestError):
        C.ProviderJevAsker(_FakeChatClient("no json here")).ask({}, _questions())
    with pytest.raises(JevRequestError):
        C.ProviderJevAsker(_FakeChatClient("")).ask({}, _questions())
    with pytest.raises(JevRequestError):
        C.ProviderJevAsker(_FakeChatClient('{"nope": 1}')).ask({}, _questions())
    with pytest.raises(JevAnswerError):
        C.ProviderJevAsker(
            _FakeChatClient('{"answers": {"call_t1": {"noul": 5}}}')
        ).ask({}, _questions())
    with pytest.raises(JevAnswerError):
        C.ProviderJevAsker(_FakeChatClient('{"answers": {}}')).ask({}, _questions())
    with pytest.raises(JevRequestError):
        C.ProviderJevAsker(object()).ask({}, _questions())


def test_a_provider_backed_compaction_runs_end_to_end_offline() -> None:
    """The production asker answers from a scripted provider — no network, no credential."""
    answers = {
        name: {"noul": 0.9 if name.startswith("call_") else 0.1}
        for name in (
            f"{kind}_t{index}" for index in range(1, 40) for kind in ("call", "result")
        )
    }
    client = _FakeChatClient(json.dumps({"answers": answers}))
    live = manager(asker=C.ProviderJevAsker(client))
    compacted, report = live.compact(loop_history(12))
    assert report.applied is True and report.fallback is False
    assert report.shortened
    assert client.seen, "the provider was actually asked"


def test_build_provider_asker_refuses_loudly_without_a_provider(tmp_path: Any) -> None:
    """No decision block and no general provider ⇒ a refusal naming the exports to set.

    The seam is the runtime's own resolution (``godsv.jev`` → ``godsv.providers.entries.
    jev`` → ``godsv.llm``), so the isolated environment *is* the setup: an empty env and a
    config file that does not exist is exactly "nothing is configured". The old version of
    this test patched ``skaldir.llm.provider_for``, which the asker no longer goes through.
    """
    with pytest.raises(C.AskerUnavailable) as failure:
        C.build_provider_asker(env={}, config_path=tmp_path / "missing.yaml")
    assert "SKALDIR_LLM_BASE_URL" in str(failure.value)


def test_build_provider_asker_takes_an_explicit_client() -> None:
    asker = C.build_provider_asker(client=_FakeChatClient("{}"))
    assert isinstance(asker, C.ProviderJevAsker)


def test_the_fixture_asker_is_labelled_as_a_simulation() -> None:
    asker = C.fixture_asker()
    response = asker.ask({}, {"call_t1": {}, "result_t1": {}})
    assert response.answers["call_t1"]["noul"] == 0.9
    assert response.answers["result_t1"]["noul"] == 0.1
    flat = C.fixture_asker(0.5)
    assert flat.ask({}, {"call_t1": {}}).answers["call_t1"]["noul"] == 0.5


# --------------------------------------------------------------------------- #
# the live seam: a wrapped client                                                #
# --------------------------------------------------------------------------- #


class _RecordingClient:
    def __init__(self, usage: dict[str, int] | None = None) -> None:
        self.batches: list[list[Any]] = []
        self.usage = usage or {}
        self.config = "provider-config-object"

    def chat(self, messages: Any, **kwargs: Any) -> Any:
        self.batches.append(list(messages))

        class _Answer:
            text = "answered"
            usage = self.usage

        return _Answer()


def test_the_wrapped_client_compacts_the_request_it_sends() -> None:
    inner = _RecordingClient({"prompt_tokens": 5000})
    live = manager(budget_tokens=1000, trigger_fraction=0.5)
    wrapper = C.wrap_client(inner, live)
    history = long_history()
    wrapper.chat(history)
    assert len(inner.batches) == 1
    sent = inner.batches[0]
    assert len(sent) <= len(history)
    assert wrapper.reports and wrapper.reports[0].applied is True
    # the request that went out is materially smaller (the fixture asker shortens results)
    before = sum(len(str(item.get("content") or "")) for item in history)
    after = sum(len(str(item.get("content") or "")) for item in sent)
    assert after < before
    assert wrapper.reports[0].freed["chars_freed"] > 0
    assert live.last_prompt_tokens == 5000  # the provider's own number was recorded


def test_the_wrapped_client_passes_a_small_history_through_untouched() -> None:
    inner = _RecordingClient()
    live = manager(budget_tokens=10_000_000)
    wrapper = C.wrap_client(inner, live)
    history = loop_history(2)
    wrapper.chat(history)
    assert inner.batches[0] == history
    assert wrapper.reports == []


def test_the_wrapped_client_survives_a_failing_compactor() -> None:
    inner = _RecordingClient()
    live = manager(
        budget_tokens=1000, trigger_fraction=0.5, asker=_RaisingAsker(JevRequestError("down"))
    )
    wrapper = C.wrap_client(inner, live)
    history = long_history()
    wrapper.chat(history)
    assert len(inner.batches[0]) == len(history)  # uncompacted, and it still answered
    assert wrapper.reports[0].fallback is True


def test_the_wrapper_delegates_everything_else() -> None:
    wrapper = C.wrap_client(_RecordingClient(), manager())
    assert wrapper.config == "provider-config-object"


def test_attach_swaps_the_client_of_a_live_loop() -> None:
    class _Loop:
        def __init__(self) -> None:
            self.client = _RecordingClient()

    loop = _Loop()
    original = loop.client
    wrapper = C.attach(loop, goal=GOAL, asker=C.fixture_asker())
    assert isinstance(loop.client, C.CompactingClient) and loop.client is wrapper
    assert wrapper.client is original
    assert loop.client.manager.goal == GOAL


def test_the_run_summary_counts_what_happened() -> None:
    live = manager(budget_tokens=300, trigger_fraction=0.5)
    live.maybe_compact(loop_history(10))
    summary = live.summary()
    assert summary["compactions"] == 1 and summary["attempts"] == 1
    assert summary["fallbacks"] == 0
    assert summary["tokens_estimate_freed"] > 0
    assert "estimate" in summary["estimate_note"]


# --------------------------------------------------------------------------- #
# dry run and the report contract                                                #
# --------------------------------------------------------------------------- #


def test_a_dry_run_changes_nothing_but_shows_the_plan() -> None:
    history = long_history()
    live = manager(budget_tokens=6000, trigger_fraction=0.5)
    outgoing, report = live.compact(history, dry_run=True)
    assert report.dry_run is True and report.applied is False
    assert outgoing == history
    assert all(new is old for new, old in zip(outgoing, history, strict=True))
    assert report.shortened and report.dropped is not None
    assert report.before["chars_measured"] == estimate_chars(history)
    # the after/freed numbers are the projection, and say so
    assert report.freed["projected"] is True
    assert report.after["tokens_estimate_jevcomp"] < report.before["tokens_estimate_jevcomp"]
    assert any("dry run" in note for note in report.notes)


def test_a_dry_run_plan_matches_what_a_real_compaction_does() -> None:
    history = long_history()
    plan = manager(budget_tokens=6000, trigger_fraction=0.5)
    _, dry = plan.compact(history, dry_run=True)
    real = manager(budget_tokens=6000, trigger_fraction=0.5)
    compacted, applied = real.compact(history)
    assert [row["id"] for row in dry.shortened] == [row["id"] for row in applied.shortened]
    assert [row["id"] for row in dry.kept_verbatim] == [row["id"] for row in applied.kept_verbatim]
    assert dry.after["messages"] == len(compacted)


def test_the_report_is_json_serialisable_and_names_its_estimates() -> None:
    history = loop_history(8)
    live = manager()
    _, report = live.compact(history)
    document = json.loads(json.dumps(report.to_wire()))
    assert document["reason"] == "compacted"
    assert document["before"]["exact"] is False
    assert "estimate" in document["before"]["note"]
    assert document["preserve"]["effective"] >= 1
    assert document["stats"]["messagesBefore"] == len(history)
    assert all(item["sha256"] for item in document["preserved"])


def test_a_dry_run_that_would_violate_a_rule_is_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = loop_history(8)

    def bad_compactor(messages: list[Any], asker: Any, options: Any = None) -> CompactResult:
        return CompactResult(
            messages=[message for message in messages if message is not messages[0]],
            decisions=[],
            stats=CompactStats(
                messages_before=len(messages),
                messages_after=len(messages) - 1,
                chars_before=1,
                chars_after=1,
                calls=0,
                kept=0,
                results_dropped=0,
                calls_dropped=0,
                pinned=0,
                state_tokens=0,
                state_stage="full",
                requests=1,
                ms=1,
            ),
        )

    monkeypatch.setattr(C, "compact", bad_compactor)
    _, report = manager().compact(history, dry_run=True)
    assert report.refused is True and report.applied is False
    assert "system_prompt" in report.error


# --------------------------------------------------------------------------- #
# reading a history                                                             #
# --------------------------------------------------------------------------- #


def test_load_history_reads_a_message_list(tmp_path: Path) -> None:
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(loop_history(2)), encoding="utf-8")
    messages, source = C.load_history(transcript=str(path))
    assert len(messages) == len(loop_history(2))
    assert "a JSON message list" in source


def test_load_history_reads_an_object_with_messages(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({"model": "m", "messages": loop_history(1)}), encoding="utf-8")
    messages, source = C.load_history(transcript=str(path))
    assert messages and "messages" in source


def test_load_history_reads_a_run_transcript(tmp_path: Path) -> None:
    path = tmp_path / "run-abc.jsonl"
    lines = [
        {"kind": "run.started", "run_id": "run-abc", "task": GOAL},
        {"kind": "model.request", "step": 1, "body": {"model": "m", "messages": loop_history(3)}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    messages, source = C.load_history(transcript=str(path))
    assert len(messages) == len(loop_history(3))
    assert "JSONL run transcript" in source


def test_load_history_takes_the_newest_request_of_a_run(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    lines = [
        {
            "kind": "model.request",
            "step": 1,
            "body": {"messages": [{"role": "user", "content": "old"}]},
        },
        {"kind": "model.request", "step": 2, "body": {"messages": loop_history(2)}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    messages, _ = C.load_history(transcript=str(path))
    assert len(messages) == len(loop_history(2))


def test_load_history_reports_a_transcript_that_recorded_only_a_digest(tmp_path: Path) -> None:
    path = tmp_path / "digest.jsonl"
    lines = [
        {"kind": "run.started", "run_id": "r"},
        {"kind": "model.request", "step": 1, "body_sha256": "abc"},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    with pytest.raises(C.HistoryError) as failure:
        C.load_history(transcript=str(path))
    assert failure.value.code == "blocked"


def test_load_history_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(C.HistoryError) as failure:
        C.load_history(transcript=str(tmp_path / "nope.json"))
    assert failure.value.code == "not_found"


def test_load_history_needs_a_source() -> None:
    with pytest.raises(C.HistoryError):
        C.load_history()


def test_load_history_reads_a_run_of_a_state_dir(tmp_path: Path) -> None:
    runs = tmp_path / "agent" / "runs"
    runs.mkdir(parents=True)
    (runs / "run-20260918T000000Z-abc123.jsonl").write_text(
        json.dumps({"kind": "model.request", "step": 1, "body": {"messages": loop_history(2)}}),
        encoding="utf-8",
    )
    messages, source = C.load_history(
        run_id="run-20260918T000000Z-abc123", state_dir=str(tmp_path / "agent")
    )
    assert len(messages) == len(loop_history(2))
    assert "run transcript" in source
    with pytest.raises(C.HistoryError) as failure:
        C.load_history(run_id="no-such-run", state_dir=str(tmp_path / "agent"))
    assert failure.value.code == "not_found"


# --------------------------------------------------------------------------- #
# the CLI: ``context usage`` and ``context compact``                            #
# --------------------------------------------------------------------------- #


def run_cli(*argv: str) -> int:
    """``skaldir context …`` through the product CLI (the real parser)."""
    return cli.main([str(item) for item in argv])


@pytest.fixture
def history_file(tmp_path: Path) -> str:
    path = tmp_path / "history.json"
    path.write_text(json.dumps(long_history()), encoding="utf-8")
    return str(path)


def test_context_usage_json_reports_the_budget_state(
    history_file: str, capsys: pytest.CaptureFixture
) -> None:
    code = run_cli("context", "usage", "--transcript", history_file, "--budget", "6000", "--json")
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["command"] == "context.usage"
    data = document["data"]
    assert data["accounting"]["measured"]["messages"] >= 60
    assert data["accounting"]["estimated"]["exact"] is False
    assert data["accounting"]["budget"]["tokens"] == 6000
    assert data["trigger"]["fire"] is True
    assert document["evidence"]


def test_context_usage_accepts_a_huge_budget_and_says_nothing_would_happen(
    history_file: str, capsys: pytest.CaptureFixture
) -> None:
    code = run_cli(
        "context", "usage", "--transcript", history_file, "--budget", "9000000", "--json"
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["trigger"]["fire"] is False


def test_context_usage_text_mode_is_human(history_file: str, capsys: pytest.CaptureFixture) -> None:
    code = run_cli("context", "usage", "--transcript", history_file, "--budget", "6000", "--text")
    assert code == 0
    out = capsys.readouterr().out
    assert "tokens (estimate" in out and "would compact: True" in out


def test_context_usage_missing_transcript_exits_5(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(["context", "usage", "--transcript", str(tmp_path / "gone.json"), "--json"])
    assert code == cli_contract.EXIT_NOT_FOUND
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not_found"


def test_context_compact_dry_run_shows_the_plan_and_applies_nothing(
    history_file: str, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(
        [
            "context",
            "compact",
            "--transcript",
            history_file,
            "--budget",
            "6000",
            "--trigger-fraction",
            "0.5",
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    data = document["data"]
    assert data["dry_run"] is True and data["applied"] is False and data["fired"] is True
    assert data["messages_before"] == data["messages_after"] >= 60
    report = data["report"]
    assert report["shortened"] or report["dropped"]
    assert report["before"]["tokens_estimate_jevcomp"] > report["after"]["tokens_estimate_jevcomp"]
    assert data["preserved_ok"] is True
    assert document["evidence"]


def test_context_compact_below_the_trigger_changes_nothing(
    history_file: str, capsys: pytest.CaptureFixture
) -> None:
    code = run_cli(
        "context", "compact", "--transcript", history_file, "--budget", "9000000", "--json"
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["fired"] is False and data["applied"] is False and data["report"] == {}


def test_context_compact_force_compacts_below_the_trigger(
    history_file: str, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(
        ["context", "compact", "--transcript", history_file, "--budget", "9000000",
         "--force", "--json"]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["fired"] is False and data["forced"] is True and data["applied"] is True


def test_context_compact_writes_the_compacted_history_only_with_out(
    history_file: str, capsys: pytest.CaptureFixture, tmp_path: Path
) -> None:
    target = tmp_path / "compacted.json"
    code = cli.main(
        [
            "context",
            "compact",
            "--transcript",
            history_file,
            "--budget",
            "6000",
            "--force",
            "--out",
            str(target),
            "--json",
        ]
    )
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert len(written) <= len(long_history())
    assert str(target) in [item["ref"] for item in document["evidence"]]
    assert document["data"]["written_to"] == str(target)


def test_context_compact_without_out_writes_nothing(
    history_file: str, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    before = sorted(item.name for item in tmp_path.iterdir())
    assert run_cli(
        "context", "compact", "--transcript", history_file, "--budget", "6000",
        "--force", "--json",
    ) == 0
    capsys.readouterr()
    assert sorted(item.name for item in tmp_path.iterdir()) == before


def test_context_compact_without_a_provider_is_blocked(
    history_file: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import skaldir.llm as llm

    def _fail(*args: Any, **kwargs: Any) -> Any:
        raise llm.LLMError("no provider configured")

    monkeypatch.setattr(llm, "provider_for", _fail)
    code = run_cli(
        "context", "compact", "--transcript", history_file, "--asker", "provider", "--json"
    )
    assert code == cli_contract.EXIT_BLOCKED
    out = capsys.readouterr().out
    document = json.loads(out)
    assert document["error"]["code"] == "blocked"
    assert "no live asker" in json.dumps(document)


def test_context_compact_text_mode_names_the_actions(
    history_file: str, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(
        ["context", "compact", "--transcript", history_file, "--budget", "6000",
         "--dry-run", "--text"]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "KEEP verbatim" in captured.out or "SHORTEN result" in captured.out
    assert "SIMULATION" in captured.err  # the fixture asker is never sold as the model


def test_the_standalone_entry_point_answers(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = tmp_path / "h.json"
    path.write_text(json.dumps(loop_history(2)), encoding="utf-8")
    assert C.main(["usage", "--transcript", str(path), "--budget", "1000", "--text"]) == 0
    assert "would compact" in capsys.readouterr().out


def test_the_standalone_entry_point_refuses_a_missing_history(
    capsys: pytest.CaptureFixture,
) -> None:
    assert C.main(["usage"]) == cli_contract.EXIT_USAGE
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# contract + wiring                                                             #
# --------------------------------------------------------------------------- #


def test_both_verbs_are_in_the_real_parser() -> None:
    paths = [path for path, _sub in cli_contract.leaf_commands(cli.build_parser())]
    assert "context.compact" in paths
    assert "context.usage" in paths


def test_the_group_registers_idempotently() -> None:
    import argparse

    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    first = C.register_cli(sub)
    second = C.register_cli(sub)
    assert first is second
    verbs = sorted((getattr(first, "_actions", []),))  # keep the object alive
    action = next(a for a in first._actions if isinstance(a, argparse._SubParsersAction))
    assert {"compact", "usage"} <= set(action.choices)
    assert verbs


def test_the_contract_entries_declare_the_layer_and_evidence() -> None:
    entries = C.contract_entries()
    for path in ("context.compact", "context.usage"):
        entry = entries[path]
        assert entry.layer == "ROUTE"
        assert entry.evidence and entry.data_schema
        assert entry.notes
        assert entries[path] is not None
    inventory = cli_contract.dump(cli.build_parser())
    assert inventory["unregistered"] == []


def test_the_dumped_contract_carries_both_verbs() -> None:
    inventory = cli_contract.dump(cli.build_parser())
    records = {item["command"]: item for item in inventory["commands"]}
    for path in ("context.compact", "context.usage"):
        record = records[path]
        assert record["registered"] is True
        assert record["has_json_flag"] is True
        assert record["default_evidence"]


def test_a_dry_run_is_reachable_from_the_product_cli_help(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["context", "compact", "--help"])
    assert exit_info.value.code == 0
    assert "--dry-run" in capsys.readouterr().out
