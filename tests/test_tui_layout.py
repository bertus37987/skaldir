"""UI-01..UI-08 and APR-01..APR-05 against the confirmed TUI decision.

``docs/masterplan/03-TUI-ENTSCHEIDUNGEN.md`` is the contract. These are render tests —
pure functions, a fake clock, no terminal, no display — which is what makes the layout
pinnable (03 §7: "Snapshot-/Fake-Clock-Tests für Renderer").
"""

from __future__ import annotations

import pytest

from skaldir.approval_modes import (
    ALWAYS_NEEDS_APPROVAL,
    ApprovalMode,
    ApprovalModeStore,
    policy_for,
    resolve_mode,
)
from skaldir.desktop_lease import IndicatorState, LeaseState
from skaldir.tui import (
    COMPACT_WIDTH,
    FIGURE_FRAMES,
    MIN_HEIGHT,
    MIN_WIDTH,
    CallRow,
    CallState,
    GoalPanel,
    Screen,
    TuiState,
    figure_frame,
    humor_at,
    layout,
    render,
    sanitize,
)

W, H = 100, 30


def state() -> TuiState:
    return TuiState(
        project="skaldir",
        run_id="42",
        chat=["You: Research orbital data centers", "", "Skaldir: Comparing sources …"],
        calls=[
            CallRow(call_id="#18", label="web_search", state=CallState.DONE, duration_s=0.78,
                    preview="Comparing sources …", args={"q": "orbital data centers"},
                    details="3 results\nhttps://example.org/a", exit_code=0),
            CallRow(call_id="#19", label="page.read", state=CallState.RUNNING,
                    preview="reading", args={"url": "https://example.org/a"}),
        ],
        goal=GoalPanel(
            title="Research orbital data centers",
            phase="compare",
            run_state="running",
            criteria=[(True, "Sources visited"), (False, "Compare claims"),
                      (False, "Verify report")],
            blockers=["no second source yet"],
            verification="unknown",
            revision=2,
        ),
        approval=ApprovalMode.APPROVE_FOR_ME,
        desktop=IndicatorState(state=LeaseState.CONTROLLING, task="Research", run_id="42"),
        input_text="",
        elapsed_s=84.0,
    )


def test_every_rendered_line_is_exactly_width_cells():
    lines = render(state(), W, H)
    assert len(lines) == H
    assert all(len(line) == W for line in lines)


def test_wide_terminal_splits_chat_left_tools_upper_right_goal_lower_right():
    geom = layout(W, H)
    assert geom.split is True
    cx, cy, cw, ch = geom.chat
    tx, ty, tw, th = geom.tools
    gx, gy, gw, gh = geom.goal
    # chat is on the left, tools and goal are on the right
    assert cx == 0 and cw > tw
    assert tx == cx + cw and gx == tx
    # tools are ABOVE goal
    assert ty < gy
    assert ty + th == gy
    assert gy + gh <= H - 3


def test_the_tools_pane_shows_a_short_preview_per_call():
    lines = render(state(), W, H)
    tools_block = "\n".join(lines)
    assert "#18 web_search" in tools_block
    assert "Comparing sources" in tools_block
    assert "#19 page.read" in tools_block


def test_a_done_call_is_marked_and_a_running_one_is_marked_differently():
    screen = Screen()
    done = CallRow(call_id="#1", label="t", state=CallState.DONE).line(60)
    running = CallRow(call_id="#1", label="t", state=CallState.RUNNING).line(60)
    failed = CallRow(call_id="#1", label="t", state=CallState.FAILED).line(60)
    assert "✓" in done and "✓" not in running
    assert "◐" in running
    assert "✖" in failed
    assert len(done) == len(running) == len(failed) == 60


def test_the_marks_fold_to_ascii_without_losing_the_state():
    done = CallRow(call_id="#1", label="t", state=CallState.DONE).line(60, unicode=False)
    failed = CallRow(call_id="#1", label="t", state=CallState.FAILED).line(60, unicode=False)
    assert "OK" in done and "XX" in failed
    assert all(ord(ch) < 128 for ch in done + failed)


def test_expanded_calls_show_args_exit_code_verification_and_details():
    call = CallRow(call_id="#18", label="web_search", state=CallState.DONE, args={"q": "x"},
                   details="full output here", exit_code=0, verification="pass",
                   expanded=True)
    rows = call.detail_lines(60)
    joined = "\n".join(rows)
    assert "args:" in joined
    assert "exit: 0" in joined
    assert "verify: pass" in joined
    assert "full output here" in joined
    assert all(len(r) == 60 for r in rows)


def test_a_collapsed_call_does_not_dump_its_details():
    state_ = state()
    lines = render(state_, W, H)
    assert "https://example.org/a" not in "\n".join(lines)


# --------------------------------------------------------------------------- #
# UI-05 / T-50: control sequences and secrets never reach the terminal
# --------------------------------------------------------------------------- #


def test_sanitize_strips_terminal_control_sequences():
    dirty = "hello \x1b[2J\x1b[1;31mred\x1b[0m world\x07\x00end"
    assert sanitize(dirty) == "hello red worldend"


def test_a_control_sequence_inside_tool_data_is_never_drawn():
    call = CallRow(call_id="#1", label="t", state=CallState.DONE,
                   details="before\x1b[51;107mafter")
    rows = call.detail_lines(60)
    assert not any("\x1b" in r for r in rows)


def test_sanitize_truncates_before_it_can_leave_a_half_escape():
    assert sanitize("x" * 5000, limit=10).endswith("…[truncated]")
    assert len(sanitize("x" * 5000, limit=10)) < 40


def test_the_error_mark_is_a_symbol_and_a_word_not_colour_alone():
    state_ = state()
    state_.blocked = "the run is paused: desktop lease lost"
    lines = render(state_, W, H)
    blocker = [line for line in lines if "desktop lease lost" in line]
    assert blocker, "a blocker must be visible"
    assert "ERROR" in blocker[0]
    assert "paused" in blocker[0]


def test_ascii_mode_draws_no_non_ascii_at_all():
    lines = render(state(), W, H, unicode=False, motion=False)
    assert all(ord(ch) < 128 for line in lines for ch in line)


# --------------------------------------------------------------------------- #
# UI-02: the goal pane never claims success before VERIFY says PASS
# --------------------------------------------------------------------------- #


def test_the_goal_pane_shows_criteria_open_points_and_blockers():
    lines = render(state(), W, H)
    goal_block = "\n".join(lines)
    assert "Sources visited" in goal_block
    assert "Compare claims" in goal_block
    assert "no second source yet" in goal_block
    assert "verify: unknown" in goal_block


def test_no_success_mark_without_a_passing_verification():
    goal = GoalPanel(title="t", criteria=[(False, "a"), (False, "b")], verification="unknown")
    text = "\n".join(goal.lines(60))
    assert "○ a" in text and "○ b" in text
    assert "✓ a" not in text
    assert "verify: unknown" in text


def test_a_reached_criterion_is_marked_once_it_is_actually_met():
    goal = GoalPanel(title="t", criteria=[(True, "a")], verification="pass")
    text = "\n".join(goal.lines(60))
    assert "✓ a" in text
    assert "verify: pass" in text


# --------------------------------------------------------------------------- #
# UI-03: auto-follow and the event cursor
# --------------------------------------------------------------------------- #


def test_scrolling_up_reports_the_backlog_and_returns_to_live():
    state_ = state()
    state_.at_live_edge = False
    state_.new_events = 7
    lines = render(state_, W, H)
    assert any("7 new events" in line for line in lines)
    state_.at_live_edge = True
    state_.new_events = 7
    assert not any("new events" in line for line in render(state_, W, H))


def test_the_state_carries_a_sequence_cursor_for_reattach():
    state_ = state()
    assert state_.cursor == 0
    state_.cursor = 52
    assert state_.cursor == 52  # the renderer never renumbers calls


# --------------------------------------------------------------------------- #
# UI-08 / 03 §4: the status line, and honest n/a instead of 0
# --------------------------------------------------------------------------- #


def test_the_status_line_names_approval_desktop_and_telemetry():
    lines = render(state(), W, H)
    status = [line for line in lines if "Approval:" in line]
    assert status
    assert "Approval: Approve for me" in status[0]
    assert "Desktop: 42" in status[0]
    assert "elapsed 01:24" in status[0]


def test_missing_measurements_render_as_na_never_as_zero():
    state_ = state()
    state_.ctx = "n/a"
    state_.cost = "n/a"
    state_.tok_per_s = "n/a"
    status = [line for line in render(state_, W, H) if "Approval:" in line][0]
    assert "ctx n/a" in status
    assert "$ n/a" in status
    assert "n/a tok/s" in status
    assert "ctx 0" not in status and "$ 0" not in status


def test_the_status_line_reports_the_real_elapsed_clock():
    state_ = state()
    state_.elapsed_s = 3725.0
    status = [line for line in render(state_, W, H) if "Approval:" in line][0]
    assert "elapsed 62:05" in status


# --------------------------------------------------------------------------- #
# UI-06: one subtle figure, deterministic, and no fake progress
# --------------------------------------------------------------------------- #


def test_the_figure_is_deterministic_in_time():
    assert figure_frame(0.0) == figure_frame(0.0)
    assert figure_frame(1.25) == figure_frame(1.25)
    assert figure_frame(0.0) in FIGURE_FRAMES


def test_the_figure_never_shows_a_percentage_or_invented_progress():
    for at in (0.0, 1.0, 2.5, 100.0):
        frame = figure_frame(at)
        assert "%" not in frame
        assert not any(ch.isdigit() for ch in frame)


def test_motion_off_still_draws_a_static_figure():
    assert figure_frame(1.0, motion=False) == "[._]"


def test_humor_is_rare_and_empty_most_of_the_time():
    assert humor_at(0.0) == ""
    assert humor_at(10.0) == ""
    assert humor_at(46.0) != ""
    assert humor_at(46.0) == humor_at(47.0)


# --------------------------------------------------------------------------- #
# narrow terminals: stacked panes with the same data, then compact
# --------------------------------------------------------------------------- #


def test_a_narrow_terminal_stacks_the_panes_with_the_same_data():
    lines = render(state(), 60, 28)
    joined = "\n".join(lines)
    assert all(len(line) == 60 for line in lines)
    assert "Research orbital data centers" in joined   # goal data still there
    assert "#18 web_search" in joined                  # tool data still there
    assert "You: Research orbital" in joined            # chat data still there


def test_below_the_minimum_size_a_compact_view_is_drawn_not_a_broken_frame():
    lines = render(state(), 20, 6)
    assert len(lines) == 6
    assert all(len(line) == 20 for line in lines)
    assert not any("┌" in line or "└" in line for line in lines)


def test_the_split_regime_is_only_taken_when_there_is_room_for_it():
    assert layout(COMPACT_WIDTH, 24).split is True
    assert layout(COMPACT_WIDTH - 1, 24).split is False
    assert layout(100, 23).split is False
    assert layout(MIN_WIDTH - 1, MIN_HEIGHT).compact is True


def test_the_input_line_is_preserved_and_rendered():
    state_ = state()
    state_.input_text = "draft text that must survive a resize"
    lines = render(state_, W, H)
    assert any("draft text that must survive a resize" in line for line in lines)
    lines2 = render(state_, 70, 25)
    assert any("draft text that must survive" in line for line in lines2)


# --------------------------------------------------------------------------- #
# the desktop corner indicator (DESK-03) — shared with the host layer
# --------------------------------------------------------------------------- #


def test_the_corner_indicator_is_the_confirmed_three_lines():
    from skaldir.tui import desktop_indicator
    lines = desktop_indicator(IndicatorState(state=LeaseState.CONTROLLING,
                                             task="Edit video", run_id="42"), width=34)
    assert lines[0].rstrip() == "Skaldir controls the desktop"
    assert lines[2].rstrip() == "Task: Edit video"
    assert lines[3].rstrip() == "Esc, Esc to pause"


def test_the_tui_reuses_the_host_indicator_rather_than_rewording_it():
    import inspect
    from skaldir import tui
    assert "indicator_lines" in inspect.getsource(tui.desktop_indicator)


# --------------------------------------------------------------------------- #
# APR-01: exactly three modes, and Tab cycles them
# --------------------------------------------------------------------------- #


def test_there_are_exactly_three_modes():
    assert [m.value for m in ApprovalMode] == ["ask", "auto", "always"]


def test_tab_cycles_through_all_three_and_wraps():
    assert ApprovalMode.ASK_EVERYTHING.next() is ApprovalMode.APPROVE_FOR_ME
    assert ApprovalMode.APPROVE_FOR_ME.next() is ApprovalMode.APPROVE_EVERYTHING
    assert ApprovalMode.APPROVE_EVERYTHING.next() is ApprovalMode.ASK_EVERYTHING


def test_the_labels_are_the_confirmed_words():
    assert ApprovalMode.ASK_EVERYTHING.label == "Ask Everything"
    assert ApprovalMode.APPROVE_FOR_ME.label == "Approve for me"
    assert ApprovalMode.APPROVE_EVERYTHING.label == "Approve Everything"


def test_approve_nichts_is_an_alias_for_ask_everything_not_a_fourth_mode():
    assert resolve_mode("Approve nichts") is ApprovalMode.ASK_EVERYTHING
    assert resolve_mode("approve-nothing") is ApprovalMode.ASK_EVERYTHING
    assert len(list(ApprovalMode)) == 3


def test_an_unknown_mode_is_refused_rather_than_guessed():
    with pytest.raises(ValueError) as excinfo:
        resolve_mode("yolo")
    assert "unknown approval mode" in str(excinfo.value)
    assert "Ask Everything" in str(excinfo.value)


def test_an_empty_value_falls_back_to_the_default_mode():
    assert resolve_mode("") is ApprovalMode.APPROVE_FOR_ME
    assert resolve_mode(None) is ApprovalMode.APPROVE_FOR_ME
    assert resolve_mode("", default=ApprovalMode.ASK_EVERYTHING) is ApprovalMode.ASK_EVERYTHING


# --------------------------------------------------------------------------- #
# APR-02: spending and irreversible effects are asked in EVERY mode
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", list(ApprovalMode))
def test_money_and_irreversible_effects_need_approval_in_every_mode(mode):
    policy = policy_for(mode)
    assert policy["requires_explicit_approval"] == list(ALWAYS_NEEDS_APPROVAL)
    assert "spending and irreversible effects are `prompt` in every mode" in policy["note"]


@pytest.mark.parametrize("mode", list(ApprovalMode))
def test_the_policy_declares_its_mode_so_the_engine_cannot_drift(mode):
    policy = policy_for(mode)
    assert policy["mode"] == mode.value
    assert policy["mode_label"] == mode.label
    assert policy["schema"] == "skaldir.approval-policy/1"


def test_ask_everything_never_auto_allows_anything():
    policy = policy_for(ApprovalMode.ASK_EVERYTHING)
    assert policy["default_decision"] == "prompt"
    assert policy["auto_allow_readonly"] is False
    assert policy["subtool_autoplay"] is False


def test_approve_for_me_auto_allows_only_the_harmless_and_validated_subtools():
    policy = policy_for(ApprovalMode.APPROVE_FOR_ME)
    assert policy["auto_allow_readonly"] is True
    assert policy["default_decision"] == "prompt"
    assert policy["subtool_autoplay"] == "validated_only"


def test_approve_everything_still_defaults_to_allow_but_not_for_the_exceptions():
    policy = policy_for(ApprovalMode.APPROVE_EVERYTHING)
    assert policy["default_decision"] == "allow"
    assert policy["requires_explicit_approval"]  # the exceptions survive


# --------------------------------------------------------------------------- #
# APR-04 / APR-05: the mode is per run, and the default is configurable
# --------------------------------------------------------------------------- #


def test_the_mode_is_per_run_and_never_a_silent_global(tmp_path):
    store = ApprovalModeStore(tmp_path / "approval-modes.json")
    store.set_mode("run-1", ApprovalMode.ASK_EVERYTHING)
    store.set_mode("run-2", ApprovalMode.APPROVE_EVERYTHING)
    assert store.mode_for("run-1") is ApprovalMode.ASK_EVERYTHING
    assert store.mode_for("run-2") is ApprovalMode.APPROVE_EVERYTHING


def test_a_new_run_adopts_the_configured_default(tmp_path):
    store = ApprovalModeStore(tmp_path / "m.json")
    assert store.mode_for("new-run") is ApprovalMode.APPROVE_FOR_ME
    store.set_default(ApprovalMode.ASK_EVERYTHING)
    assert store.mode_for("brand-new") is ApprovalMode.ASK_EVERYTHING
    # setting a default does NOT rewrite the mode of an existing run (APR-04)
    store.set_mode("existing", ApprovalMode.APPROVE_EVERYTHING)
    store.set_default(ApprovalMode.ASK_EVERYTHING)
    assert store.mode_for("existing") is ApprovalMode.APPROVE_EVERYTHING


def test_tab_persists_the_cycle_per_run(tmp_path):
    store = ApprovalModeStore(tmp_path / "m.json")
    # a fresh run starts on the configured default (Approve for me), then Tab walks the
    # confirmed three-mode order and wraps
    assert store.cycle("run-1") is ApprovalMode.APPROVE_EVERYTHING
    assert store.cycle("run-1") is ApprovalMode.ASK_EVERYTHING
    assert store.cycle("run-1") is ApprovalMode.APPROVE_FOR_ME
    assert store.cycle("run-2") is ApprovalMode.APPROVE_EVERYTHING  # run-2 is independent


def test_the_mode_survives_a_restart(tmp_path):
    path = tmp_path / "m.json"
    ApprovalModeStore(path).set_mode("run-1", ApprovalMode.APPROVE_EVERYTHING)
    assert ApprovalModeStore(path).mode_for("run-1") is ApprovalMode.APPROVE_EVERYTHING


def test_a_corrupt_mode_file_falls_back_to_the_default(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{ nope", encoding="utf-8")
    store = ApprovalModeStore(path)
    assert store.mode_for("run-1") is ApprovalMode.APPROVE_FOR_ME
    assert store.default() is ApprovalMode.APPROVE_FOR_ME


def test_a_malformed_stored_mode_is_not_trusted(tmp_path):
    path = tmp_path / "m.json"
    path.write_text('{"runs": {"r": {"mode": "nonsense"}}}', encoding="utf-8")
    assert ApprovalModeStore(path).mode_for("r") is ApprovalMode.APPROVE_FOR_ME


def test_no_temporary_file_is_left_behind(tmp_path):
    store = ApprovalModeStore(tmp_path / "m.json")
    store.set_mode("run-1", ApprovalMode.ASK_EVERYTHING)
    assert list(tmp_path.glob("*.tmp")) == []


# --------------------------------------------------------------------------- #
# UI-04: Tab behaviour is decided by whether a completion menu is open
# --------------------------------------------------------------------------- #


def test_tab_at_an_empty_prompt_cycles_approval_while_completion_keeps_tab():
    """03 §4: 'Tab bei leerem Prompt und geschlossenem Menü wechselt den sichtbaren Modus;
    während aktiver Completion bedient Tab die Completion.'"""
    from skaldir.tui import tab_action

    assert tab_action(prompt="", completion_open=False) == "cycle_approval"
    assert tab_action(prompt="skal", completion_open=True) == "accept_completion"
    assert tab_action(prompt="skal", completion_open=False) == "cycle_approval"
    assert tab_action(prompt="", completion_open=True) == "accept_completion"
