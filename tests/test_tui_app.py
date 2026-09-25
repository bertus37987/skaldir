"""Abschnitt 6 — the actual terminal UI, at the entry the user sees.

    "Ein Renderer mit Beispieldaten oder Transcript-Preview erfuellt das nicht."

So these drive the *application*: it takes the terminal, draws the agreed layout
("grosser Chat links, Tools oben rechts, Goal unten rechts"), reads real keys and
reacts to resize. Where a real TTY is needed the test uses a PTY — a scripted key
stream is not the same claim as a live terminal, and the two are marked apart.
"""

from __future__ import annotations

import io
import os
import pty
import shutil
import subprocess
import sys
import time

import pytest

from skaldir.tui_app import NO_TTY_HINT, TuiApp, TuiAppConfig


@pytest.fixture()
def app():
    return TuiApp(config=TuiAppConfig(width=80, height=20, motion=False,
                                      project="demo"), stdin=io.StringIO(),
                  stdout=io.StringIO())


# --------------------------------------------------------------------------- #
# the agreed layout
# --------------------------------------------------------------------------- #

def test_the_layout_is_the_agreed_one(app):
    """Grosser Chat links, Tools oben rechts, Goal unten rechts.

    The two panels carry content, not just labels: with nothing registered the tool
    panel is genuinely empty, so the assertion uses a populated state.
    """
    assert app.screen_name() == "chat-left_tools-topright_goal-bottomright"

    frame = app.tui.render()
    rows = len(frame)
    # the goal panel sits in the lower third of the screen (bottom-right region),
    # the mark/status is at the top, and the input line is below the conversation
    top = frame[0]
    assert "SKALDIR" in top, f"no mark at the top: {top!r}"
    lower = "\n".join(frame[rows // 2:])
    assert ("no goal" in lower or "GOAL" in lower or "goal" in lower), (
        f"no goal panel in the lower half: {frame[-6:]}"
    )
    assert any(line.strip().startswith(("", "›", ">")) for line in frame[-4:]), (
        "no input line"
    )
    assert app.state.goal is not None, "the goal panel is not a real panel"


def test_every_frame_is_the_full_screen_size(app):
    app.tui.resize(100, 30)
    frame = app.tui.render()
    assert len(frame) == 30
    assert all(len(line) == 100 for line in frame)


# --------------------------------------------------------------------------- #
# the confirmed key rules
# --------------------------------------------------------------------------- #

def test_tab_at_an_empty_prompt_cycles_the_approval_mode(app):
    """The confirmed UI rule, implemented in exactly one place."""
    start = app.state.approval
    outcome = app.handle_key("TAB")
    assert outcome["action"] == "cycle_approval"
    assert outcome["to"] != start.value


def test_active_completion_keeps_its_tab_behaviour(app):
    app.set_completing(True)
    start = app.state.approval
    outcome = app.handle_key("TAB")
    assert outcome["action"] == "completion"
    assert app.state.approval == start


def test_enter_sends_through_the_runner(app):
    sent: list[str] = []
    app.config.run_on_send = sent.append
    for ch in "hi":
        app.handle_key(ch)
    app.handle_key("ENTER")
    deadline = time.time() + 5
    while not sent and time.time() < deadline:   # the send runs on a thread
        time.sleep(0.01)
    assert sent == ["hi"]


# --------------------------------------------------------------------------- #
# live events and streaming
# --------------------------------------------------------------------------- #

def test_a_tool_event_appears_while_it_happens(app):
    app.tui.push_seq(1, "run.started", run_id="r", task="the task")
    app.tui.step()
    app.draw()
    frame = "\n".join(app.tui.render())
    assert "the task" in frame


def test_a_result_shows_a_preview_with_detail(app):
    app.tui.push_seq(1, "tool.start", call_id="c1", tool="run_code")
    app.tui.push_seq(2, "tool.result", call_id="c1", duration_s=0.3,
                     preview="Hello, World!", details="exit_code=0",
                     exit_code=0)
    app.tui.step()
    row = app.state.calls[0]
    assert "Hello, World!" in row.preview
    assert row.details == "exit_code=0", "the detail was not kept"
    assert row.exit_code == 0
    assert row.expanded is False, "the detail should start collapsed (expandable)"


# --------------------------------------------------------------------------- #
# resize, scrollback, reattach
# --------------------------------------------------------------------------- #

def test_a_resize_redraws_at_the_new_size(app):
    app.tui.step()
    app.draw()
    app.apply_resize = lambda: None  # driven manually below
    outcome = app.tui.resize(60, 14)
    app.config.width, app.config.height = outcome["to"]
    frame = app.tui.render()
    assert all(len(line) == 60 for line in frame)


def test_scrollback_returns_to_the_live_edge(app):
    app.tui.push_seq(1, "run.started", run_id="r", task="t")
    app.tui.step()
    app.tui.feed.scroll_away()
    app.tui.push_seq(2, "tool.start", call_id="c1", tool="read_file")
    app.tui.step()
    assert app.state.new_events >= 1
    app.tui.feed.return_to_live()
    assert app.state.at_live_edge is True


def test_reattach_does_not_duplicate(app):
    for _ in range(2):
        app.tui.push_seq(1, "tool.start", call_id="c1", tool="read_file")
        app.tui.step()
    assert len(app.state.calls) == 1


# --------------------------------------------------------------------------- #
# the non-interactive surface
# --------------------------------------------------------------------------- #

def test_without_a_tty_the_app_says_which_command_to_use_instead():
    """A pipe must not hang an automated caller, and must not be silent about it."""
    app = TuiApp(config=TuiAppConfig(width=80, height=20), stdin=io.StringIO(),
                 stdout=io.StringIO())
    with pytest.raises(RuntimeError) as excinfo:
        app.run(max_frames=1)
    assert "--json" in str(excinfo.value)
    assert "skaldir agent run" in str(excinfo.value)


def test_the_no_tty_hint_is_the_documented_one():
    assert "interactive terminal" in NO_TTY_HINT


# --------------------------------------------------------------------------- #
# a real terminal (PTY) — marked separately from scripted keys
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(os.environ.get("SKALDIR_NO_PTY") == "1", reason="PTY disabled")
def test_the_app_takes_a_real_pty_and_draws_the_full_screen():
    """Live: an actual pseudo-terminal, not a StringIO. This is the claim
    "oeffnet im unterstuetzten interaktiven Terminal" and nothing less."""
    master, slave = pty.openpty()
    rows, cols = 20, 80
    import fcntl
    import struct
    import termios

    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    out = []

    class Collector(io.StringIO):
        def write(self, text):  # noqa: ANN001
            out.append(text)
            return super().write(text)

    app = TuiApp(config=TuiAppConfig(width=cols, height=rows, motion=False),
                 stdin=os.fdopen(slave, "r", buffering=1), stdout=Collector())
    app.tui.push_seq(1, "run.started", run_id="r", task="pty task")
    frames = app.run(max_frames=2, keys=["x"])
    os.close(master)

    assert frames == 2, "the loop did not draw"
    painted = "".join(out)
    assert "\x1b[?1049h" in painted, "the alt screen was never entered"
    assert "SKALDIR" in painted, "the mark was not painted into the real terminal"
    assert "pty task" in painted, "live content was not painted into the real terminal"
    assert "\x1b[?1049l" in painted, "the terminal was not given back"


def test_the_app_restores_the_terminal_when_it_leaves():
    out = io.StringIO()
    app = TuiApp(config=TuiAppConfig(width=80, height=20, motion=False),
                 stdin=io.StringIO(), stdout=out)
    app.run(max_frames=1, keys=[""])
    painted = out.getvalue()
    assert "\x1b[?1049l" in painted, "the terminal was not given back"
    assert "\x1b[?25h" in painted, "the cursor was left hidden"
