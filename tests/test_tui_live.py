"""The live TUI loop — Paket 5 (audit F10) and acceptance I.

The audit's point: the new feed was used by the *preview*, so reading a transcript proved
nothing about an interactive full-screen UI. These tests drive the loop itself: live events
in, frames out, keys handled.

The confirmed UI rule is asserted where it is implemented:

    "Tab at an empty prompt with no completion menu cycles the approval modes; active
     completion retains its Tab behavior."
"""

from __future__ import annotations

import pytest

from skaldir.tui import CallState
from skaldir.tui_live import LiveTui, LiveTuiConfig


@pytest.fixture()
def tui():
    return LiveTui(config=LiveTuiConfig(width=80, height=20, render_interval_s=0.01,
                                        project="demo"))


# --------------------------------------------------------------------------- #
# live events, not a transcript re-read
# --------------------------------------------------------------------------- #

def test_a_live_event_reaches_the_frame(tui):
    tui.push_seq(1, "run.started", run_id="run-1", task="the task")
    frame = tui.step()
    assert any("the task" in line for line in frame), (
        f"the event never reached the frame: {frame[:4]}"
    )


def test_a_tool_shows_up_while_it_runs(tui):
    tui.push_seq(1, "run.started", run_id="r", task="t")
    tui.push_seq(2, "tool.start", call_id="c1", tool="web_search")
    frame = tui.step()
    assert any("web_search" in line for line in frame)
    tui.push_seq(3, "tool.result", call_id="c1", duration_s=0.5, preview="found it")
    frame = tui.step()
    assert any("found it" in line or "web_search" in line for line in frame)
    assert tui.state.calls[0].state is CallState.DONE


def test_the_loop_draws_more_than_once_as_events_arrive(tui):
    for i in range(5):
        tui.push_seq(i + 1, "tool.start", call_id=f"c{i}", tool="read_file")
        tui.step()
    assert tui.frame_count == 5, "the loop must keep drawing"
    assert len(tui.state.calls) == 5


def test_the_loop_can_run_until_a_condition(tui):
    tui.push_seq(1, "run.started", run_id="r", task="t")
    seen = []

    def stop():
        seen.append(1)
        return len(seen) >= 3

    steps = tui.run(max_steps=10, timeout_s=2.0, stop_when=stop)
    assert steps >= 3
    assert tui.frame_count >= 3


# --------------------------------------------------------------------------- #
# the confirmed Tab rule
# --------------------------------------------------------------------------- #

def test_tab_at_an_empty_prompt_cycles_the_approval_mode(tui):
    """The confirmed UI rule, implemented exactly."""
    start = tui.state.approval
    outcome = tui.handle_key("TAB")
    assert outcome["action"] == "cycle_approval", outcome
    assert outcome["from"] == start.value
    assert outcome["to"] != start.value, "the mode did not actually change"
    assert tui.state.approval.value == outcome["to"]


def test_tab_cycles_through_all_three_modes_before_repeating(tui):
    seen = [tui.state.approval.value]
    for _ in range(3):
        tui.handle_key("TAB")
        seen.append(tui.state.approval.value)
    assert len(set(seen)) == 3, f"the cycle is not the three modes: {seen}"
    assert seen[0] == seen[3], "the cycle does not come back to where it started"


def test_an_active_completion_keeps_its_tab_behaviour(tui):
    """'active completion retains its Tab behavior'."""
    tui.set_completing(True)
    start = tui.state.approval
    outcome = tui.handle_key("TAB")
    assert outcome["action"] == "completion"
    assert tui.state.approval == start, "Tab was stolen from the completion menu"


def test_tab_is_ignored_when_the_input_is_not_empty(tui):
    tui.state.input_text = "half a sentence"
    start = tui.state.approval
    outcome = tui.handle_key("TAB")
    assert outcome["action"] == "tab_ignored"
    assert tui.state.approval == start


# --------------------------------------------------------------------------- #
# input and geometry
# --------------------------------------------------------------------------- #

def test_enter_sends_and_clears_the_input(tui):
    sent: list[str] = []
    tui.on_send = sent.append
    for ch in "hello":
        tui.handle_key(ch)
    assert tui.state.input_text == "hello"
    outcome = tui.handle_key("ENTER")
    assert outcome == {"action": "send", "text": "hello"}
    assert sent == ["hello"]
    assert tui.state.input_text == ""


def test_backspace_edits_the_input(tui):
    for ch in "abc":
        tui.handle_key(ch)
    tui.handle_key("BACKSPACE")
    assert tui.state.input_text == "ab"


def test_a_resize_is_handled_without_restarting(tui):
    outcome = tui.resize(60, 12)
    assert outcome["to"] == (60, 12)
    assert outcome["compact"] is True
    frame = tui.step()
    assert all(len(line) == 60 for line in frame), (
        "the frame did not follow the new width"
    )
    assert tui.frame_count >= 1


def test_a_resize_to_a_tiny_terminal_is_clamped_not_broken(tui):
    outcome = tui.resize(3, 2)
    assert outcome["to"][0] >= 20 and outcome["to"][1] >= 6


def test_every_frame_is_exactly_the_requested_size(tui):
    for width, height in ((80, 20), (120, 40), (50, 12)):
        tui.resize(width, height)
        frame = tui.step()
        assert len(frame) == height
        assert all(len(line) == width for line in frame), (
            f"at {width}x{height} a line was the wrong width"
        )


# --------------------------------------------------------------------------- #
# reattach and scrollback
# --------------------------------------------------------------------------- #

def test_reattach_does_not_duplicate_calls(tui):
    """Reattach means the SAME events arrive again — the cursor must skip them.

    Sending three different events with one call id is not reattach; it is three events.
    What must never happen is the same sequence number applied twice.
    """
    for seq in (1, 2):
        tui.push_seq(seq, "tool.start", call_id=f"c{seq}", tool="read_file")
        tui.step()
    assert len(tui.state.calls) == 2

    # the same stream is re-delivered (a reattach, a refresh, a replay)
    for seq in (1, 2):
        tui.push_seq(seq, "tool.start", call_id=f"c{seq}", tool="read_file")
        tui.step()
    assert len(tui.state.calls) == 2, "reattach duplicated the calls"
    assert tui.state.cursor == 2


def test_one_call_is_one_row_however_often_it_is_updated(tui):
    """A result moves its row; it does not open a second one."""
    tui.push_seq(1, "tool.start", call_id="c1", tool="read_file")
    tui.step()
    tui.push_seq(2, "tool.result", call_id="c1", duration_s=0.2, preview="done")
    tui.step()
    assert len(tui.state.calls) == 1
    assert tui.state.calls[0].state is CallState.DONE


def test_scrolling_away_counts_new_events_instead_of_moving_the_view(tui):
    tui.push_seq(1, "run.started", run_id="r", task="t")
    tui.step()
    tui.feed.scroll_away()
    tui.push_seq(2, "tool.start", call_id="c1", tool="read_file")
    tui.step()
    assert tui.state.new_events >= 1
    waiting = tui.feed.return_to_live()
    assert tui.state.at_live_edge is True
    assert waiting >= 1


# --------------------------------------------------------------------------- #
# no second engine
# --------------------------------------------------------------------------- #

def test_the_loop_has_no_execution_engine_of_its_own(tui):
    """Keine zweite Ausfuehrungsengine in der Oberflaeche."""
    for forbidden in ("subprocess", "exec(", "eval(", "Popen"):
        assert forbidden not in str(type(tui)), ""
    import inspect

    source = inspect.getsource(type(tui))
    assert "subprocess" not in source, "the UI started a process of its own"
    assert "run_workflow" not in source, "the UI runs workflows itself"
