"""The TUI must be fed by real run/tool events — and reattach must not double anything.

GOAL §4: "Vorhandenen Renderer an echte Run-/Tool-Events anschließen. Vorschau und
aufklappbare Details, funktionierender Live-Cursor/Reattach."

`03-TUI-ENTSCHEIDUNGEN.md` §4 is explicit about the reattach hazard: "duplicates never
double a call". That is the first test here.
"""

from __future__ import annotations

import json

from skaldir.tui import CallState, TuiState
from skaldir.tui_feed import TuiEventFeed, apply_events, state_from_transcript


def _write(tmp_path, events):
    path = tmp_path / "run.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


SAMPLE = [
    {"seq": 1, "kind": "run.started", "run_id": "run-7", "task": "search the docs"},
    {"seq": 2, "kind": "user.message", "text": "find the answer"},
    {"seq": 3, "kind": "tool.start", "call_id": "c1", "tool": "web_search", "args": {"q": "x"}},
    {"seq": 4, "kind": "tool.result", "call_id": "c1", "duration_s": 0.78,
     "preview": "Comparing sources …", "exit_code": 0},
    {"seq": 5, "kind": "assistant.message", "text": "Here is what I found."},
    {"seq": 6, "kind": "usage", "tokens": 1200, "tok_per_s": 41.5, "cost_usd": 0.0021},
]


def test_real_events_reach_the_state(tmp_path):
    state = state_from_transcript(_write(tmp_path, SAMPLE))
    assert state.run_id == "run-7"
    assert state.goal.title == "search the docs"
    assert state.chat == ["> find the answer", "Here is what I found."]
    assert len(state.calls) == 1
    row = state.calls[0]
    assert row.label == "web_search"
    assert row.state is CallState.DONE
    assert row.duration_s == 0.78
    assert row.preview == "Comparing sources …"
    assert row.exit_code == 0
    assert state.ctx == "1200"
    assert state.cost == "0.0021"        # the status line adds the currency symbol


def test_a_tool_result_moves_the_row_it_does_not_add_one(tmp_path):
    """One call is one row, whatever happens to it."""
    state = state_from_transcript(_write(tmp_path, SAMPLE))
    assert len(state.calls) == 1
    assert state.calls[0].state is CallState.DONE


def test_a_failed_call_shows_the_error_and_is_not_called_done(tmp_path):
    events = [
        {"seq": 1, "kind": "run.started", "run_id": "r", "task": "t"},
        {"seq": 2, "kind": "tool.start", "call_id": "c1", "tool": "write_file"},
        {"seq": 3, "kind": "tool.failed", "call_id": "c1", "error": "PermissionError"},
    ]
    state = state_from_transcript(_write(tmp_path, events))
    assert len(state.calls) == 1
    assert state.calls[0].state is CallState.FAILED
    assert "PermissionError" in state.calls[0].error


def test_reattach_does_not_double_any_call(tmp_path):
    """`03-TUI-ENTSCHEIDUNGEN.md` §4: duplicates never double a call."""
    path = _write(tmp_path, SAMPLE)
    feed = TuiEventFeed(path)
    feed.feed()
    feed.feed()                       # a refresh
    feed.feed()                       # and another
    assert len(feed.state.calls) == 1, "reattach duplicated a call"
    assert len(feed.state.chat) == 2, "reattach duplicated the conversation"
    assert feed.state.cursor == 6


def test_new_events_beyond_the_cursor_are_applied_once(tmp_path):
    path = _write(tmp_path, SAMPLE)
    feed = TuiEventFeed(path)
    feed.feed()
    more = SAMPLE + [
        {"seq": 7, "kind": "tool.start", "call_id": "c2", "tool": "read_file"},
        {"seq": 8, "kind": "tool.result", "call_id": "c2", "duration_s": 0.1},
    ]
    path.write_text("\n".join(json.dumps(e) for e in more) + "\n", encoding="utf-8")
    feed.feed()
    assert len(feed.state.calls) == 2
    assert feed.state.cursor == 8


def test_scrolling_away_counts_new_events_instead_of_moving_the_view(tmp_path):
    """Scrollback must not lose events — they are counted until the reader returns."""
    path = _write(tmp_path, SAMPLE)
    feed = TuiEventFeed(path)
    feed.feed()
    feed.scroll_away()
    assert feed.state.at_live_edge is False

    more = SAMPLE + [{"seq": 7, "kind": "tool.start", "call_id": "c2", "tool": "read_file"}]
    path.write_text("\n".join(json.dumps(e) for e in more) + "\n", encoding="utf-8")
    feed.feed()
    assert feed.state.new_events >= 1, "events while scrolled away were silently absorbed"

    waiting = feed.return_to_live()
    assert feed.state.at_live_edge is True
    assert feed.state.new_events == 0
    assert waiting >= 1


def test_an_unreadable_transcript_is_an_empty_stream_not_a_crash(tmp_path):
    state = state_from_transcript(tmp_path / "missing.jsonl")
    assert state.calls == [] and state.chat == []


def test_a_broken_line_does_not_lose_the_rest_of_the_run(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text(
        json.dumps({"seq": 1, "kind": "run.started", "run_id": "r", "task": "t"}) + "\n"
        + "{ this is not json\n"
        + json.dumps({"seq": 2, "kind": "tool.start", "call_id": "c1", "tool": "read_file"}) + "\n",
        encoding="utf-8",
    )
    state = state_from_transcript(path)
    assert state.run_id == "r"
    assert len(state.calls) == 1


def test_an_unknown_tool_state_is_unknown_not_success():
    """`CallState` says "done never implies fachlich success" — the feeder keeps that."""
    state = TuiState()
    apply_events(state, [
        {"seq": 1, "kind": "tool.start", "call_id": "c1", "tool": "weird"},
        {"seq": 2, "kind": "tool.something_unexpected", "call_id": "c1"},
    ])
    assert state.calls[-1].state is not CallState.DONE
