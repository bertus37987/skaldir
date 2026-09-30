"""The CLI *feel*: compact prompt, one-line status, message rhythm, palette, streaming.

What this file holds down — every claim is measured, never asserted by eye:

* the prompt is **one line** and never grows; the 2-3 facts live on one dim status line
  above it, which is redrawn **only when it changed** and clipped at 80 columns by
  dropping the least relevant fact;
* the message rhythm is one action line (``⏺``), the command's own output, one result
  line (``⎿``) and exactly one blank line between turns — no boxes, no rules;
* the palette is aligned, honest and filtered as you type; Esc dismisses it and runs
  nothing;
* output **streams** (the first byte is on the terminal long before the command ends) and
  Ctrl-C cancels only the running command, with one line, leaving the session alive;
* a pipe/log/``NO_COLOR`` session is byte-clean: no escape sequence and pure ASCII.

The end of the file is the real proof: a **real PTY** (80x24), real keystrokes and a real
SIGINT through the terminal's own line discipline — startup, palette, a real command, a
cancelled command, ``?`` help, clean exit — plus the raw capture.
"""

from __future__ import annotations

import fcntl
import io
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from skaldir import cli, complete, repl, slash

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SHELL_MODULE = "skaldir.repl"

#: \"\" or $TERM=… — the shell decides colour/glyphs from the stream, so a TTY needs none
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[=>()][A-Za-z0-9]?")

#: What a PTY session waits for before the next keystroke: the prompt mark, and the
#: action mark of a command that has really started (never a fixed sleep).
PROMPT_BYTES = repl.PROMPT_MARK.encode("utf-8")
ACTION_BYTES = "⏺".encode()

#: The verdict a cancelled command reports. Waiting for the *prompt* after a Ctrl-C only
#: says "the shell is asking again" — which is also what it says when the ``^C`` arrived
#: before the command ever started, and then the line under test never ran. This is the
#: shell's own cancel line, so a step that waits for it waits for the cancel to be real.
CANCELLED_BYTES = b"cancelled `skaldir scan -`"

#: The result line a **finished** command prints: ``  ⎿ exit 0 (OK) · …``.
#:
#: This is the marker the framework got wrong, and the measurement is the reason. The bare
#: prompt mark is *also* painted on the composer row while a line is being typed, so
#: ``until(PROMPT_BYTES)`` after a command is sent is satisfied by the **echo of the very
#: keystrokes the test just wrote** (measured: it matched in 0.2 s, on ``  ❯ s`` — the first
#: character echoed — while the command was still running and its result line arrived
#: seconds later). The next keystroke then landed before the shell was back at its prompt,
#: which is the whole class of load failure this work is about.
#:
#: So a step that means *"wait until this command has finished"* waits for the command's
#: own verdict, not for a glyph that appears in half a dozen other places. The prompt mark
#: stays for the steps that really only mean "the frame drew a row" (startup, the palette).
RESULT_LINE = re.compile("⎿ exit \\d+ \\([A-Z]+\\)".encode())

#: The history table's own header line — what ``history 3`` prints once it has really run.
#: The test asserts on the history that follows it, so the step has to wait for the table
#: rather than for the prompt mark (which the echo satisfies, see :data:`RESULT_LINE`).
HISTORY_BYTES = b"last 1 command(s)"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return tmp_path


def _shell(tmp_path, *, tty: bool = False, **kwargs):
    """A shell over captured streams; ``tty=True`` fakes a terminal (colour + unicode)."""
    class Stream(io.StringIO):
        def isatty(self):
            return tty

    out, err = Stream(), Stream()
    runner = kwargs.pop("runner", None) or (lambda argv: 0)
    options = repl.ShellOptions(probe=kwargs.pop("probe", False), **kwargs)
    session = repl.Session(runtime=tmp_path, runner=runner, out=out, err=err)
    shell = repl.Shell(options, session=session, out=out, err=err, stdin=Stream())
    return shell, out, err


def _feed(shell, text: str):
    shell.stdin = io.StringIO(text)
    return shell.run()


def _child_env(runtime: Path) -> dict:
    env = dict(os.environ)
    env["GODSV_HOME"] = str(runtime)
    env["HERMES_HOME"] = str(runtime / "hermes")
    env["PYTHONPATH"] = str(SRC)  # never a sibling scratch copy
    env["PYTHONUNBUFFERED"] = "1"
    env["TERM"] = env.get("TERM") or "xterm-256color"
    for key in ("NO_COLOR", "SKALDIR_COLOR", "FORCE_COLOR", "GODSV_COLOR", "COLUMNS",
                "SKALDIR_WIDTH", "GODSV_FORMAT"):
        env.pop(key, None)
    return env


# --------------------------------------------------------------------------- #
# 1. the compact prompt + the one-line status
# --------------------------------------------------------------------------- #


def test_prompt_is_one_line_and_never_grows(runtime):
    shell, _out, _err = _shell(runtime)
    prompt = shell.prompt()
    assert "\n" not in prompt
    assert prompt.startswith(repl.PROMPT_NAME)
    assert prompt.endswith(" ")
    assert prompt.strip() == "skaldir >"        # plain stream: the ASCII mark
    # nothing about goal/provider leaks into the prompt itself
    assert "goal" not in prompt and "provider" not in prompt
    tty, _out2, _err2 = _shell(runtime, tty=True)
    assert _strip_ansi(tty.prompt()).strip() == f"skaldir {repl.PROMPT_MARK}"


def test_status_line_carries_two_to_three_facts_and_never_a_wall(runtime):
    shell, _out, _err = _shell(runtime)
    facts = shell.status_facts()
    assert 2 <= len(facts) <= 4
    line = shell.status_line()
    assert "\n" not in line
    assert len(line) <= 80
    assert facts[0] == shell.workspace_label()
    assert "goal:" in line and "provider:" in line


def test_status_line_is_silent_when_there_is_nothing_honest_to_say(runtime, monkeypatch):
    shell, out, _err = _shell(runtime)
    monkeypatch.setattr(repl, "clip_facts", lambda *a, **k: "")
    assert shell.status_line() == ""
    assert shell.show_status() is False
    assert out.getvalue() == ""                # not even a bare separator
    assert shell.prompt().startswith("skaldir")  # the prompt still works


def test_status_line_updates_only_when_it_changed(runtime):
    shell, out, _err = _shell(runtime)
    assert shell.show_status() is True
    assert shell.show_status() is False
    assert out.getvalue().count("goal:none") == 1
    shell.session.probes.goal = lambda: repl.ProbeValue("7f3a working", "one open goal")
    assert shell.show_status() is True
    assert "goal:7f3a working" in out.getvalue()
    assert shell.show_status() is False        # and quiet again


def test_a_live_warm_session_is_a_fact_an_off_one_is_not(runtime, monkeypatch):
    shell, _out, _err = _shell(runtime)
    assert "warm:" not in shell.status_line()
    monkeypatch.setattr(
        repl.Probes, "warm",
        lambda self: repl.ProbeValue("on", "session answers (pid 42)", "warm_session", ok=True),
    )
    assert "warm:on" in shell.status_line()


# --------------------------------------------------------------------------- #
# 2. the status line truncates gracefully at 80 columns
# --------------------------------------------------------------------------- #


def test_clip_facts_keeps_the_relevant_and_drops_the_tail():
    facts = ["~/projects/gods-voice", "goal:7f3a1b2c working", "provider:deepseek",
             "warm:on"]
    short = repl.clip_facts(facts, 40, sep=" | ", ellipsis="...")
    assert short.startswith("~/projects/gods-voice")
    assert "warm" not in short                       # the least relevant goes first
    assert len(short) <= 40
    assert len(repl.clip_facts(facts, 80, sep=" | ")) <= 80
    # 80 columns keeps everything that fits, in order
    wide = repl.clip_facts(facts, 80, sep=" | ", ellipsis="...")
    assert wide == " | ".join(facts)


def test_clip_facts_clips_a_single_too_long_fact():
    line = repl.clip_facts(["~/" + "x" * 200], 20, ellipsis="...")
    assert len(line) == 20 and line.endswith("...")


def test_clip_facts_is_empty_rather_than_broken():
    assert repl.clip_facts([], 80) == ""
    assert repl.clip_facts(["", "   "], 80) == ""


def test_every_shell_line_fits_the_width(runtime):
    shell, _out, _err = _shell(runtime)
    long_facts = ["~/" + "d" * 90, "goal:" + "g" * 90, "provider:" + "p" * 90]
    shell.status_facts = lambda: long_facts  # type: ignore[method-assign]
    assert len(shell.status_line()) <= 80
    assert len(shell.status_line()) <= shell.line_width()


# --------------------------------------------------------------------------- #
# 3. ASCII when colour/glyphs are off, unicode on a terminal
# --------------------------------------------------------------------------- #


def test_glyphs_degrade_to_ascii_off_a_terminal(runtime):
    plain, _out, _err = _shell(runtime, tty=False)
    assert plain.glyph("prompt") == ">"
    assert plain.glyph("action") == "*"
    assert plain.glyph("result") == "\\"
    assert plain.glyph("tab") == "<TAB>"
    assert plain.glyph("ellipsis") == "..."
    assert plain.mark("ok") == "+"

    tty, _out2, _err2 = _shell(runtime, tty=True)
    assert tty.glyph("prompt") == "❯"
    assert tty.glyph("action") == "⏺"
    assert tty.glyph("result") == "⎿"
    assert tty.glyph("tab") == "⇥"
    assert tty.glyph("ellipsis") == "…"
    assert tty.mark("ok") == "✓"


def test_a_plain_prompt_and_status_are_pure_ascii(runtime):
    shell, _out, _err = _shell(runtime)
    for text in (shell.prompt(), shell.status_line(), shell.action_line(["tools", "list"]),
                 shell.result_line("exit 0 (OK) | 12 ms")):
        text.encode("ascii")   # raises when a glyph leaked into plain mode


# --------------------------------------------------------------------------- #
# 4. the message rhythm
# --------------------------------------------------------------------------- #


def test_one_turn_is_action_then_output_then_one_result_line(runtime):
    def runner(argv):
        print("several lines")
        print("of the command's own output")
        return 0

    shell, out, _err = _shell(runtime, runner=runner)
    _feed(shell, "tools list\n/quit\n")
    text = out.getvalue()
    lines = text.splitlines()
    action = next(i for i, line in enumerate(lines) if line.startswith("* skaldir tools list"))
    assert lines[action + 1] == "several lines"
    assert lines[action + 2] == "of the command's own output"
    assert lines[action + 3].startswith("  \\ exit 0 (OK)")
    assert "ms" in lines[action + 3]


def test_one_blank_line_between_turns_and_never_two_in_a_row(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "tools list\ntools list\n/quit\n")
    text = out.getvalue()
    lines = text.splitlines()
    assert "\n\n\n" not in text, lines        # never two blank lines in a row
    turns = [i for i, line in enumerate(lines) if line.startswith("* skaldir tools list")]
    assert len(turns) == 2
    between = lines[turns[0]:turns[1]]
    assert between.count("") == 1, between     # exactly one, between the two turns
    blank = between.index("")
    assert between[blank - 1].startswith("  \\ exit 0 (OK)")   # after the result line
    # the status line is NOT repeated (it did not change) — the next prompt follows
    assert between[blank + 1].startswith("skaldir >")
    assert sum("goal:" in line for line in lines) == 1


def test_no_boxes_or_rules_in_the_session(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "tools list\nhistory 3\nhelp\n/quit\n")
    text = out.getvalue()
    for forbidden in ("┌", "┐", "└", "┘", "│", "╭", "╰", "══"):
        assert forbidden not in text


def test_a_slash_command_is_shown_as_the_action_that_really_runs(runtime):
    calls: list[list[str]] = []
    shell, out, _err = _shell(runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1])
    if not next(item for item in shell.palette() if item.command.name == "tools").runnable:
        pytest.skip("this build does not wire the tools group")
    _feed(shell, "/tools\n/quit\n")
    assert calls == [["tools", "list"]]
    assert "* skaldir tools list" in out.getvalue()   # the action line names the real argv
    assert "→" not in out.getvalue() and "->" not in out.getvalue()


# --------------------------------------------------------------------------- #
# 5. streaming and cancel
# --------------------------------------------------------------------------- #


def test_output_streams_before_the_command_ends(runtime):
    """The first byte is on the terminal while the command is still running."""
    seen: list[str] = []

    def runner(argv):
        print("first byte")
        seen.append(shell_out.getvalue())      # mid-command: what the terminal has
        time.sleep(0.25)
        print("much later")
        return 0

    shell, out, _err = _shell(runtime, runner=runner)
    shell_out = out
    result = shell.session.execute(["demo"])
    assert "first byte" in seen[0], "the output was buffered until the end"
    assert result.duration_ms >= 200
    assert 0 <= result.first_byte_ms < 100     # measured, not claimed
    assert "much later" in result.output


def test_the_footer_only_mentions_the_first_byte_when_it_matters():
    fast = repl.RunResult(argv=("x",), duration_ms=12, first_byte_ms=2)
    assert fast.footer() == "exit 0 (OK) · 12 ms"
    slow = repl.RunResult(argv=("x",), duration_ms=800, first_byte_ms=7)
    assert slow.footer() == "exit 0 (OK) · first output at 7 ms · 800 ms"


def test_ctrl_c_cancels_the_command_in_one_line_and_the_prompt_returns(runtime):
    def runner(argv):
        if argv[0] == "scan":
            raise KeyboardInterrupt
        return 0

    shell, out, err = _shell(runtime, runner=runner)
    summary = _feed(shell, "scan -\ntools list\n/quit\n")
    notes = [line for line in err.getvalue().splitlines() if line.strip()]
    cancel = [line for line in notes if line.startswith("^C cancelled")]
    assert len(cancel) == 1, notes
    assert cancel[0].count("the session is still here") == 1
    assert "`skaldir scan -`" in cancel[0]
    assert summary["cancelled"] == 1 and summary["commands"] == 2
    # no result line for a cancelled command, and the next turn still ran
    assert "* skaldir tools list" in out.getvalue()
    assert "\\ exit 0" in out.getvalue()


def test_ctrl_c_at_the_prompt_clears_the_line_without_leaving(runtime):
    shell, _out, err = _shell(runtime, runner=lambda argv: 0)
    reads = {"n": 0}

    class Stdin:
        def readline(self):
            reads["n"] += 1
            if reads["n"] == 1:
                raise KeyboardInterrupt
            return "" if reads["n"] > 2 else "/quit\n"

        def isatty(self):
            return False

    shell.stdin = Stdin()
    summary = shell.run()
    assert "input cleared" in err.getvalue()
    assert "the session is still here" in err.getvalue()
    assert summary["commands"] == 0


# --------------------------------------------------------------------------- #
# 6. the palette: aligned, filtered as you type, dismissed with Esc
# --------------------------------------------------------------------------- #


def test_the_palette_menu_filters_as_you_type(runtime):
    tree = complete.build_tree(cli.build_parser())
    palette = slash.entries(tree)
    menu = slash.PaletteMenu()
    everything = menu.update("/", palette)
    assert len(everything) == len(palette) and menu.open
    narrowed = menu.update("/je", palette)
    assert [item.command.name for item in narrowed] == ["jev"]
    assert menu.query == "/je"
    assert menu.update("/zzz", palette) == [] and menu.open is False


def test_esc_dismisses_the_palette_and_runs_nothing(runtime):
    calls: list[list[str]] = []
    shell, out, _err = _shell(runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1])
    _feed(shell, "/\t\n\x1b\n/quit\n")
    text = out.getvalue()
    assert "candidate(s)" in text              # the palette was open
    assert "palette dismissed" in text
    assert not shell.menu_state.open
    assert calls == [], "Esc must not run anything"
    # a second Esc with nothing open stays quiet
    shell.dismiss_menu()
    before = out.getvalue()
    shell.dismiss_menu()
    assert out.getvalue() == before


def test_a_palette_row_is_one_line_that_fits_and_stays_aligned(runtime):
    tree = complete.build_tree(cli.build_parser())
    palette = slash.entries(tree, reachable={}, report=None)
    text = slash.render(palette, width=80, ellipsis="...")
    rows = [line for line in text.splitlines() if line.startswith("    /")]
    assert len(rows) == len(palette)
    assert {len(line) <= 80 for line in rows} == {True}
    marks = set()
    for entry in palette:
        row = next(line for line in rows if line.strip().startswith(entry.command.slash))
        marks.add(row.index(entry.mark()))
    assert len(marks) == 1, marks
    for entry in palette:
        row = next(line for line in rows if line.strip().startswith(entry.command.slash))
        assert row.strip().endswith(entry.mark())


def test_the_completion_menu_is_aligned_and_clipped(runtime):
    rows = complete.menu_lines(
        [complete.Suggestion("/provider", "x" * 400), complete.Suggestion("/quit", "bye")],
        width=80, ellipsis="...",
    )
    assert all(len(row) <= 80 for row in rows)
    assert rows[0].index("x") == rows[1].index("bye")   # one description column


# --------------------------------------------------------------------------- #
# 7. the pipe path: no escapes, pure ASCII
# --------------------------------------------------------------------------- #


def test_a_piped_session_has_no_escapes_and_is_ascii(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    script = "?\n/help\nhelp\nhistory 3\ntools l\t\n/je\n/quit\n"
    for extra in ([], ["--no-color"]):
        proc = subprocess.run(
            [sys.executable, "-m", SHELL_MODULE, "--no-probe", *extra],
            cwd=str(REPO), env=_child_env(runtime), input=script,
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        assert "\x1b" not in proc.stdout, "a pipe must never carry an escape sequence"
        assert "\x1b" not in proc.stderr
        _assert_ascii(proc.stdout, "the piped session's stdout")
        _assert_ascii(proc.stderr, "the piped session's stderr")
        assert "slash commands" in proc.stdout         # the palette rendered
        assert "skaldir >" in proc.stdout              # ASCII prompt
        assert "<TAB>" in proc.stdout                  # the keystroke, ASCII
        assert "candidate(s)" in proc.stdout
        assert "\x1b" not in proc.stdout


def test_colour_only_when_a_terminal_is_there(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    plain = subprocess.run(
        [sys.executable, "-m", SHELL_MODULE, "--no-probe"], cwd=str(REPO),
        env=_child_env(runtime), input="/quit\n", capture_output=True, text=True,
        timeout=120,
    )
    assert "\x1b" not in plain.stdout
    loud = subprocess.run(
        [sys.executable, "-m", SHELL_MODULE, "--no-probe", "--color"], cwd=str(REPO),
        env=_child_env(runtime), input="/quit\n", capture_output=True, text=True,
        timeout=120,
    )
    assert "\x1b[" in loud.stdout, "an explicit --color must still paint a pipe"


# --------------------------------------------------------------------------- #
# 8. the real thing: a PTY, real keystrokes, a real SIGINT
# --------------------------------------------------------------------------- #


def pty_session(
    steps,
    *,
    argv=(),
    cols: int = 80,
    rows: int = 24,
    runtime: Path,
    settle: float = 0.1,
    timeout: float = 300.0,
    expect_timeout: float = 60.0,
):
    """Run the shell on a real 80x24 PTY and send real keystrokes; return (code, raw).

    ``steps`` is a list of ``(bytes, seconds, expected_bytes_or_None)``: send the bytes,
    wait until the expected bytes appear (the prompt, the action mark) and then read
    for the given time. The
    capture is raw: what a terminal would really show, escapes and readline redraws
    included — that is the point of a PTY proof.

    ``pty.fork()`` and not ``subprocess`` on purpose: the child must be a **session leader
    with the pty as its controlling terminal**, otherwise the line discipline has no
    foreground process group to signal and a Ctrl-C keystroke is swallowed instead of
    becoming SIGINT (measured: the shell then hangs on the blocked command).

    ``expect_timeout`` is a **bound**, not a guess: the condition each step waits for is
    unchanged, only the ceiling moved, because the child's *start* is what a loaded host
    stretches (measured on this one: the first prompt at 1.6 s idle, 13-40 s with the
    cores saturated — the old 15 s ceiling could not cover a slow start, and a session that
    was merely slow looked exactly like a cancelled command that never ran).
    """
    env = _child_env(runtime)
    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover — the child image
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.chdir(str(REPO))
            os.execvpe(sys.executable, [sys.executable, "-m", SHELL_MODULE, *argv], env)
        finally:  # pragma: no cover — exec never returns on success
            os._exit(127)
    chunks: list[bytes] = []

    def captured() -> bytes:
        return b"".join(chunks)

    def drain(seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            ready, _w, _x = select.select([master], [], [], left)
            if not ready:
                return
            try:
                data = os.read(master, 65536)
            except OSError:      # the child is gone; the capture is complete
                return
            if not data:
                return
            chunks.append(data)

    def until(needle, *, start: int = 0, seconds: float = 10.0) -> bool:
        """Wait for ``needle`` (searching from ``start``); ``False`` on timeout.

        A fixed sleep is a guess about a loaded machine; waiting for the prompt is not
        (measured: under a parallel test run a fixed 0.6 s let a keystroke arrive before
        readline was ready, and the completion was simply lost).

        ``needle`` is bytes or a compiled **bytes** pattern. Which marker a step waits for is
        the substance, not a detail: the bare prompt mark is painted on the composer row
        while a line is being *typed* as well, so a step that means "this command has
        finished" has to wait for that command's own verdict — see :data:`RESULT_LINE`.
        """
        def seen(blob: bytes) -> bool:
            if hasattr(needle, "search"):
                return bool(needle.search(blob))
            return needle in blob

        end = time.monotonic() + seconds
        while not seen(captured()[start:]):
            if time.monotonic() > end:
                return False
            drain(0.2)
        return True

    status = 0
    killed = False
    try:
        drain(settle)
        for step in steps:
            payload, wait = step[0], step[1]
            expect = step[2] if len(step) > 2 else None
            mark = len(captured())
            if payload:
                os.write(master, payload)
            if expect:
                # only what happened *after* this point counts (the first step waits for
                # the very first prompt, which is also what a marker-only step means)
                until(expect, start=mark, seconds=expect_timeout)
            drain(wait)
        deadline = time.monotonic() + timeout
        while True:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                break
            if time.monotonic() > deadline:  # pragma: no cover — a hung shell
                killed = True
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                break
            drain(0.1)
        drain(0.3)
    finally:
        os.close(master)
    if killed:
        return -9, b"".join(chunks)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status), b"".join(chunks)
    return -os.WTERMSIG(status), b"".join(chunks)


def _strip_ansi(text: str) -> str:
    """The text without SGR sequences (for asserting on what a human reads)."""
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def _assert_ascii(text: str, what: str) -> None:
    """A pipe/log session must be pure ASCII (theme.py's plain-mode contract)."""
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:  # pragma: no cover — only on a regression
        raise AssertionError(f"{what} is not ASCII: {exc}") from exc


def _clean(raw: bytes) -> str:
    """The capture as a human would read it: no SGR, and every redrawn line keeps its
    last state (readline redraws a line with ``\\r``; the previous state is noise)."""
    text = _ANSI.sub(b"", raw).decode("utf-8", "replace")
    lines: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        lines.append(line.split("\r")[-1])
    return "\n".join(lines)


#: One CSI sequence: parameters and its final byte (SGR included — it moves nothing).
_CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")
#: The non-CSI escapes (charset selection, keypad mode) — no cursor effect either.
_ESC_OTHER = re.compile(r"\x1b[=>()][A-Za-z0-9]?")


def _screen(raw: bytes, *, cols: int = 80, rows: int = 24) -> str:
    """The capture as the *screen* shows it: what scrolled off, then the final 80x24 screen.

    ``_clean`` is a byte reader — it keeps every painted run — so content the frame takes
    back with a cursor-up + erase is counted twice: a repaint, and (the one that bites a
    count) an open streamed line that is rewritten in full when its terminating newline
    arrives. A line the user sees **once** therefore looks like two lines there. Claims
    about what a person reads — a line that must appear exactly once — use this reader: it
    honours ``\\r``/``\\n``, ``\\x1b[<n>F`` (start of the n-th line up, which is what
    ``Frame.erase`` moves from), ``\\x1b[<n>A/B/C/D``, ``\\x1b[J``/``\\x1b[K`` (erase to the
    end of the screen / of the line), ``\\x1b[H``, ``\\x1b[2J`` and scrolling. Cross-checked
    against a real terminal emulator (``tmux``, ``capture-pane -S -``) running the same
    journey.
    """
    grid = [[" "] * cols for _ in range(rows)]
    scrolled: list[str] = []
    row = col = 0

    def scroll() -> None:
        nonlocal row
        scrolled.append("".join(grid[0]).rstrip())
        grid.pop(0)
        grid.append([" "] * cols)
        row = rows - 1

    def down() -> None:
        nonlocal row
        if row + 1 >= rows:
            scroll()
        else:
            row += 1

    def size_of(params: str) -> int:
        head = params.split(";")[0]
        return int(head) if head.isdigit() else 1

    text = _ESC_OTHER.sub("", raw.decode("utf-8", "replace"))
    index = 0
    while index < len(text):
        match = _CSI.match(text, index)
        if match:
            params, final = match.group(1), match.group(2)
            size = size_of(params)
            if final in ("F", "A"):
                row = max(0, row - size)
                if final == "F":
                    col = 0
            elif final == "B":
                row = min(rows - 1, row + size)
            elif final == "C":
                col = min(cols, col + size)
            elif final == "D":
                col = max(0, col - size)
            elif final == "G":
                col = max(0, min(cols, size - 1))
            elif final == "J":
                if params == "2":
                    grid[:] = [[" "] * cols for _ in range(rows)]
                else:
                    for cell in range(col, cols):
                        grid[row][cell] = " "
                    for lower in range(row + 1, rows):
                        grid[lower] = [" "] * cols
            elif final == "K":
                for cell in range(col, cols):
                    grid[row][cell] = " "
            elif final == "H":
                row, col = max(0, min(rows - 1, size - 1)), 0
            index = match.end()
            continue
        char = text[index]
        index += 1
        if char == "\r":
            col = 0
        elif char == "\n":
            down()
            col = 0
        elif char == "\b":
            col = max(0, col - 1)
        elif char >= " ":
            if col >= cols:  # a full row: the next cell wraps, as the terminal's own does
                col = 0
                down()
            grid[row][col] = char
            col += 1
    return "\n".join(scrolled + ["".join(line).rstrip() for line in grid])


def test_real_pty_session_at_80x24(tmp_path):
    """startup -> Tab completion -> palette -> a real command -> a cancelled one -> ? -> quit."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    code, raw = pty_session(
        [
            (None, 0.2, PROMPT_BYTES),        # startup: banner, status, prompt
            (b"/h", 0.2),                     # a slash line, half typed
            (b"\t", 0.4),                     # Tab: completes the common prefix
            (b"\x15", 0.2),                   # C-u: clear the line
            (b"/\r", 0.3, PROMPT_BYTES),      # `/` + Enter: the palette
            (b"/je\r", 0.3, PROMPT_BYTES),    # a filtered palette (the query filters)
            # a real command: the wait is for the command's own verdict line, because the
            # prompt mark is also painted while a line is being typed (see RESULT_LINE)
            (b"selftest --layer ACT\r", 0.3, RESULT_LINE),
            (b"scan -\r", 0.3, ACTION_BYTES),  # blocks on stdin (the action started)
            # Ctrl-C: cancel just this command. The wait is for the shell's own cancel
            # verdict, then for the prompt it returns to — a prompt on its own is also what
            # a `^C` that arrived *before* the command started leaves behind, and then the
            # line this test is about never ran at all.
            (b"\x03", 0.3, CANCELLED_BYTES),
            (None, 0.3, PROMPT_BYTES),
            (b"?\r", 0.3, PROMPT_BYTES),      # the help overlay
            (b"/quit\r", 0.5),                # leave
        ],
        runtime=runtime,
    )
    text = _clean(raw)
    assert code == 0, text[-3000:]
    assert "interactive shell" in text
    # The prompt on a real terminal is the frame's *own* composer row: ``read_line()`` hands
    # the input line to the chatbar front, so the bare ``skaldir ❯ `` prompt is what a pipe
    # or a script gets. Measured on a real 80x24 PTY (and again in tmux): every redraw paints
    # ``  ❯ <typed text>`` between the two bars, and ``skaldir ❯`` is painted nowhere — a
    # terminal with both would be two places to type.
    assert f"  {repl.PROMPT_MARK} " in text      # the composed frame's composer row
    assert "skaldir ❯" not in text
    assert "goal:none" in text                  # the status line above it
    # Tab really completes (the libedit binding is pinned: without it a literal tab lands).
    # ``/h`` becomes ``/help`` in the composer: a palette row carries a description after the
    # verb, so a row that is *only* ``❯ /help`` can be nothing but the line being edited.
    assert re.search(r"^  ❯ /h\s*$", text, re.M), text[-2000:]
    assert re.search(r"^  ❯ /help\s*$", text, re.M), text[-2000:]
    # the palette, both unfiltered and filtered as you type
    assert "slash commands" in text and "/provider" in text
    assert "slash commands — /je" in text and "/jev" in text
    assert "not wired here" in text or "ready" in text        # an honest state mark
    assert "⏺ skaldir selftest --layer ACT" in text            # the action line
    assert "exit 0 (OK)" in text                               # a real command really ran
    assert "cancelled `skaldir scan -`" in text
    # one line, no stack trace — counted on the *screen*, not on the byte stream: the frame
    # rewrites an open line in full when its newline arrives (that is how a streamed answer
    # grows in place), so the bytes carry the text twice and the terminal shows it once.
    assert _screen(raw).count("the session is still here") == 1
    assert "? help" in text and "warm" in text                # the overlay
    assert "\t" not in text.split("? help")[-1]               # Tab never leaked a raw tab


def test_real_pty_tab_completes_a_unique_command(tmp_path):
    """Tab on an unambiguous prefix inserts it — measured, not assumed (libedit vs GNU)."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    code, raw = pty_session(
        [
            (None, 0.2, PROMPT_BYTES),
            (b"selftest --lay\t", 0.4, PROMPT_BYTES),   # Tab: the prefix completes
            (b"ACT\r", 0.3, RESULT_LINE),               # continue typing, then run
            (b"/quit\r", 0.5),
        ],
        runtime=runtime,
    )
    text = _clean(raw)
    # the one command of this session runs against the real build: the ACT layer is
    # reachable now, so the shell honestly reports OK (a blocked verdict would be `exit 2`)
    assert code in (0, repl.EXIT_BLOCKED), text[-1500:]
    assert "selftest --lay" in text and "--layerACT" not in text
    assert "⏺ skaldir selftest --layer ACT" in text, text[-1500:]
    assert "exit 0 (OK)" in text


def test_real_pty_cancel_keeps_the_session_and_the_raw_capture_is_readable(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    code, raw = pty_session(
        [
            (None, 0.2, PROMPT_BYTES),
            (b"scan -\r", 0.3, ACTION_BYTES),
            (b"\x03", 0.3, CANCELLED_BYTES),   # the shell's own cancel verdict, not the prompt
            (None, 0.3, PROMPT_BYTES),         # and the prompt it hands the turn back at
            (b"history 3\r", 0.3, HISTORY_BYTES),   # the history table, before the shell leaves
            (b"/quit\r", 0.5),
        ],
        runtime=runtime,
    )
    text = _clean(raw)
    assert code == 0, text[-2000:]
    # one line, no stack trace — on the screen (see ``_screen``): the frame rewrites its open
    # line in full when the newline arrives, so the byte stream carries the text twice while
    # the terminal shows it once.
    assert _screen(raw).count("the session is still here") == 1
    assert "cancelled" in text
    assert raw.decode("utf-8", "replace").strip(), "the raw capture must not be empty"
    history = (runtime / "shell" / "history.jsonl").read_text(encoding="utf-8")
    assert "scan -" in history and "cancelled" in history


# --------------------------------------------------------------------------- #
# R-2: invalid UTF-8 on piped stdin is replaced, never a UnicodeDecodeError     #
# --------------------------------------------------------------------------- #


def test_tolerant_stdin_replaces_invalid_bytes_with_the_replacement_char(monkeypatch):
    """`printf 'hello\\xff\\xfe\\x81 world\\n' | skaldir chat` must keep going (R-2)."""
    import io as _io

    raw = _io.BufferedReader(_io.BytesIO(b"hello\xff\xfe\x81 world\n"))
    monkeypatch.setattr(sys, "stdin", _io.TextIOWrapper(raw, encoding="utf-8"))
    stream = repl._tolerant_stdin()
    line = stream.readline()
    assert line == "hello\ufffd\ufffd\ufffd world\n"
    assert stream.readline() == ""  # EOF stays EOF


def test_tolerant_stdin_leaves_normal_text_alone(monkeypatch):
    import io as _io

    raw = _io.BufferedReader(_io.BytesIO("skál hëlp\n".encode()))
    monkeypatch.setattr(sys, "stdin", _io.TextIOWrapper(raw, encoding="utf-8"))
    stream = repl._tolerant_stdin()
    assert stream.readline() == "skál hëlp\n"
