"""The interactive shell: session state, completion, streaming, Ctrl-C, history.

Nothing here talks to the network or to the developer's real runtime: the shell is
driven either with an injected runner (deterministic) or as a **real subprocess** with
scripted stdin (the end-to-end proof, including a real SIGINT cancelling a running
command).

The rule every test holds down: the shell may only do what the CLI can do — it hands
the argv to ``godsv.cli.main``, and a command group this build does not wire is refused
with its reason instead of guessing a verb.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from skaldir import cli, complete, repl, slash

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return tmp_path


def _shell(tmp_path, **kwargs):
    """A shell with captured streams; commands go to a stub runner unless one is given."""
    out, err = io.StringIO(), io.StringIO()
    runner = kwargs.pop("runner", None) or (lambda argv: 0)
    options = repl.ShellOptions(probe=kwargs.pop("probe", False), **kwargs)
    session = repl.Session(runtime=tmp_path, runner=runner, out=out, err=err)
    shell = repl.Shell(options, session=session, out=out, err=err, stdin=io.StringIO(""))
    return shell, out, err


def _feed(shell, text: str):
    shell.stdin = io.StringIO(text)
    return shell.run()


# --------------------------------------------------------------------------- #
# the completion engine reads the real parser
# --------------------------------------------------------------------------- #


def test_tree_comes_from_the_real_parser():
    tree = complete.build_tree(cli.build_parser())
    paths = set(complete.known_paths(tree))
    assert "selftest" in paths
    assert "tools.list" in paths
    assert "jev.selftest" in paths
    assert complete.path_for(tree, ["tools", "list"]) == "tools.list"
    node, leftovers = complete.node_for(tree, ["tools", "nope"])
    assert node.path == "tools" and leftovers == ["nope"]


def test_completion_completes_commands_options_and_inserts_the_first_match():
    tree = complete.build_tree(cli.build_parser())
    # `sel` is not unambiguous any more: `shell` is a real command of this build, so the
    # engine inserts the best *prefix* match and says which entry it picked
    only = complete.complete_line(tree, "sel")
    assert only.insert == "selftest"
    assert [item.text for item in only.suggestions][0] == "selftest"
    assert not only.unique, "the first menu entry wins when the prefix has rivals"
    assert "first menu entry" in only.reason
    assert only.completed_line().startswith("selftest")

    # a prefix only one command answers is inserted as the unique candidate
    unique = complete.complete_line(tree, "selftes")
    assert unique.insert == "selftest" and unique.unique

    option = complete.complete_line(tree, "tools --j")
    assert option.insert == "--json"

    menu = complete.complete_line(tree, "tools l")
    assert [item.text for item in menu.suggestions][0] == "list"
    assert menu.insert == "list"
    assert "first menu entry" in menu.reason


def test_fuzzy_ranking_prefers_prefixes_and_word_starts():
    prefix = complete.fuzzy_score("sel", "selftest") or 0
    other = complete.fuzzy_score("sel", "skills") or 0
    assert prefix > other
    assert complete.fuzzy_score("pt", "provider test") is not None
    assert complete.fuzzy_score("zzz", "selftest") is None
    ranked = complete.fuzzy_filter("act", ["completion", "act.subtool", "pact"])
    assert ranked[0] == "act.subtool"
    assert complete.longest_common_prefix(["list", "logs"]) == "l"
    assert complete.longest_common_prefix(["list", "show"]) == ""


def test_completion_routes_a_slash_line_to_the_palette():
    tree = complete.build_tree(cli.build_parser())
    completion = complete.complete_line(
        tree, "/je", 4, slash=lambda query: slash.suggestions(query, tree)
    )
    assert completion.suggestions[0].text == "/jev"
    assert completion.insert == "/jev"
    assert completion.suggestions[0].kind == "slash"


def test_help_overlay_shows_real_parser_facts():
    tree = complete.build_tree(cli.build_parser())
    text = complete.help_overlay(tree, ["tools", "list"])
    assert "tools.list" in text
    assert "usage:" in text
    assert "options:" in text
    assert "exit codes:" in text  # read from the contract, not invented
    unknown = complete.help_overlay(tree, ["nope", "nope"])
    assert "no command" in unknown
    assert "closest:" in unknown


# --------------------------------------------------------------------------- #
# the shell: prompt, banner, palette honesty
# --------------------------------------------------------------------------- #


def test_prompt_is_one_line_and_the_status_line_carries_the_facts(runtime):
    """The prompt never grows; the one dim line above it carries 2-3 facts, and no more."""
    shell, _out, _err = _shell(runtime)
    prompt = shell.prompt()
    assert "\n" not in prompt
    assert prompt.startswith("skaldir")
    assert repl.PROMPT_NAME in prompt
    assert repl.PROMPT_MARK in prompt or ">" in prompt  # the prompt is a shell mark
    status = shell.status_line()
    assert status.count("\n") == 0
    assert "goal:none" in status
    # the label is the probe's own, unshortened: `provider()` reads the real config and says
    # `not configured` when there is none. The status line quotes the fact, it does not
    # prettify it (measured at 80x24: `… | goal:none | provider:not configured`, 60 cells).
    assert "provider:not configured" in status
    assert shell.workspace_label() in status
    assert len(status) <= 80
    assert "warm:" not in status  # a session that is off is not one of the facts
    # the long form is still what the probes report (the banner and `?` show it)
    assert shell.session.probes.provider().label == "not configured"


def test_status_line_updates_between_commands_without_spamming(runtime):
    """Drawn when it changed, silent when it did not — never a repeated line."""
    shell, out, _err = _shell(runtime)
    assert shell.show_status() is True
    assert shell.show_status() is False          # unchanged: no second line
    _feed(shell, "selftest\n/quit\n")           # a whole turn must not re-print it
    assert out.getvalue().count("goal:none") == 1
    # when the fact really changed, the line is drawn again
    shell.session.probes.goal = lambda: repl.ProbeValue("7f3a1b2c working", "1 open goal")
    assert shell.show_status() is True
    assert "goal:7f3a1b2c working" in out.getvalue()


def test_banner_reports_reality_and_the_probe_state(runtime):
    shell, out, _err = _shell(runtime, probe=False)
    shell.banner()
    text = out.getvalue()
    assert "Skaldir" in text
    assert "capability probe did not run" in text  # never an invented capability
    assert "--no-probe" not in text
    assert "Tab completes" in text

    shell2, out2, _err2 = _shell(runtime, probe=True)
    shell2.banner()
    probed = out2.getvalue()
    assert "selftest:" in probed
    assert "layer(s)" in probed


def test_an_unwired_group_is_refused_and_never_executed(runtime, monkeypatch):
    """Every slash group is wired in this build, so the refusal is measured against a
    parser that really lacks the group — the code path a slimmer build takes."""
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0

    real = complete.build_tree(cli.build_parser())
    # every group this build *carries* is runnable; the one verb with no command behind it
    # (`/plan`, CHATBAR) is marked planned and takes this same refusal path
    assert all(item.runnable for item in slash.entries(real) if item.command.argv), (
        "this build wires every slash group it declares; that is why the tree below is synthetic"
    )
    # a build whose parser has no `provider` group at all: the row is marked unwired
    monkeypatch.setattr(
        repl.Session,
        "tree",
        property(lambda self: complete.build_tree(argparse.ArgumentParser(prog="skaldir"))),
    )
    shell, out, _err = _shell(runtime, runner=runner)
    shell.run_slash("/provider")
    text = out.getvalue()
    assert calls == [], "an unwired group must not be executed"
    assert "/provider" in text
    assert "not wired here" in text or "cannot do" in text


def test_a_slash_command_runs_the_real_argv(runtime):
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        print("pretend output from the real command")
        return 0

    shell, out, _err = _shell(runtime, runner=runner)
    if not next(item for item in shell.palette() if item.command.name == "tools").runnable:
        pytest.skip("this build does not wire the tools group")
    shell.run_slash("/tools")
    assert calls == [["tools", "list"]]
    # the command's own output is streamed AND recorded
    assert "pretend output from the real command" in out.getvalue()
    assert "pretend output from the real command" in shell.session.history.entries[0].line or (
        "pretend output" in "\n".join(shell.transcript)
    )
    assert shell.session.history.entries[0].source == "slash"


def test_bare_line_runs_as_a_skaldir_command(runtime):
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0

    shell, _out, _err = _shell(runtime, runner=runner)
    _feed(shell, "tools list --json\n/quit\n")
    assert calls == [["tools", "list", "--json"]]
    assert shell.session.history.entries[0].line == "tools list --json"


def test_quoted_arguments_survive_the_shell(runtime):
    calls: list[list[str]] = []
    shell, _out, _err = _shell(runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1])
    _feed(shell, 'scan "my file.txt"\n/quit\n')
    assert calls == [["scan", "my file.txt"]]


def test_history_builtin_shows_the_verdicts(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 2)
    _feed(shell, "selftest --layer ACT\nhistory 3\n/quit\n")
    assert "exit 2" in out.getvalue()


# --------------------------------------------------------------------------- #
# Ctrl-C: the running command is cancelled, the session survives
# --------------------------------------------------------------------------- #


def test_ctrl_c_cancels_the_command_and_keeps_the_session(runtime):
    state = {"n": 0}

    def runner(argv):
        state["n"] += 1
        if state["n"] == 1:
            raise KeyboardInterrupt
        return 0

    shell, _out, err = _shell(runtime, runner=runner)
    summary = _feed(shell, "scan -\nselftest\n/quit\n")
    assert summary["commands"] == 2
    assert summary["cancelled"] == 1
    assert "cancelled" in err.getvalue()
    entries = shell.session.history.entries
    assert entries[0].cancelled is True
    assert entries[0].exit_code == repl.EXIT_CANCELLED
    assert entries[1].cancelled is False


def test_ctrl_c_when_nothing_is_running_clears_the_line(runtime):
    shell, _out, err = _shell(runtime, runner=lambda argv: 0)
    reads = {"n": 0}

    def readline():
        reads["n"] += 1
        if reads["n"] == 1:
            raise KeyboardInterrupt
        return "" if reads["n"] > 2 else "/quit\n"

    class FakeStdin:
        def readline(self):
            return readline()

        def isatty(self):
            return False

    shell.stdin = FakeStdin()
    summary = shell.run()
    assert "input cleared" in err.getvalue()
    assert summary["commands"] == 0


def test_a_crashing_command_is_reported_not_hidden(runtime):
    class Boom(Exception):
        pass

    def runner(argv):
        raise Boom("kaboom")

    shell, _out, err = _shell(runtime, runner=runner)
    summary = _feed(shell, "demo\n/quit\n")
    assert summary["commands"] == 1
    assert summary["failures"] == 1
    assert "Boom: kaboom" in err.getvalue()
    assert shell.session.history.entries[0].exit_code == repl.EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# completion in a scripted session, and the ? overlay
# --------------------------------------------------------------------------- #


def test_tab_completes_and_prints_the_menu(runtime):
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0

    shell, out, _err = _shell(runtime, runner=runner)
    _feed(shell, "tools l\t\n/quit\n")
    text = out.getvalue()
    # plain output (a pipe is not a TTY) is ASCII-only — the keystroke is visible too
    assert "<TAB>" in text
    assert "candidate(s)" in text  # the menu
    assert calls and calls[0][:2] == ["tools", "list"]
    assert "insert 'list'" in text


def test_tab_completes_a_slash_command(runtime):
    calls: list[list[str]] = []
    shell, out, _err = _shell(runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1])
    _feed(shell, "/sta\t\n/quit\n")
    assert "insert '/status'" in out.getvalue()


def test_a_bare_slash_only_opens_the_menu(runtime):
    """Tab on `/` must show the palette, not commit to the first entry.

    The palette shows one page (``repl.MENU_ROWS`` rows) and names the rest — with the
    standard vocabulary woven in, ``/jev`` and ``/quit`` are in the named remainder rather
    than on the first page. Both are reachable by typing their name.
    """
    calls: list[list[str]] = []
    shell, out, _err = _shell(runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1])
    _feed(shell, "/\t\n/quit\n")
    text = out.getvalue()
    assert "candidate(s)" in text
    for name in ("/help", "/goal", "/status", "/tools"):
        assert name in text
    assert "more" in text  # the rest of the table is named, not silently dropped
    assert calls == [], "a bare / must not run anything"


def test_tab_without_candidates_says_so(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "tools nosuchthing\t\n/quit\n")
    assert "no completion" in out.getvalue() or "candidate" in out.getvalue()


def test_overlay_for_a_command_path_and_for_the_session(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "? tools list\n?\n/quit\n")
    text = out.getvalue()
    assert "tools.list" in text
    assert "usage: skaldir tools list" in text
    assert "skaldir skaldir" not in text  # the prog name is not doubled
    assert "slash commands" in text
    assert "runtime" in text


def test_overlay_at_the_end_of_a_line(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "tools list ?\n/quit\n")
    assert "tools.list" in out.getvalue()


def test_unknown_slash_suggests_the_closest(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "/verif\n/quit\n")
    text = out.getvalue()
    # a near miss shows the filtered palette (the same renderer as the `/` menu)
    assert "no exact match for '/verif'" in text
    assert "/verify" in text
    # the honest mark travels with the row — whatever this build's real state is
    verify = next(item for item in shell.palette() if item.command.name == "verify")
    assert verify.mark() in text


def test_a_slash_name_that_matches_nothing_says_so(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "/zzzz\n/quit\n")
    text = out.getvalue()
    assert "no slash command '/zzzz'" in text
    assert "closest rows" not in text  # nothing fuzzy-matched: no empty header


def test_help_with_an_argument_runs_the_real_help(runtime):
    calls: list[list[str]] = []
    shell, out, _err = _shell(runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1])
    _feed(shell, "/help tools list\n/quit\n")
    assert calls == [["tools", "list", "--help"]]


def test_help_for_a_slash_command_describes_it(runtime):
    calls: list[list[str]] = []
    shell, out, _err = _shell(runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1])
    _feed(shell, "/help /runs\n/quit\n")
    text = out.getvalue()
    assert "runs:  skaldir runs status" in text
    assert "state:" in text
    assert calls == [], "describing a slash command must not run anything"


# --------------------------------------------------------------------------- #
# history, resume, transcript, limit
# --------------------------------------------------------------------------- #


def test_history_persists_and_resume_reports_it(runtime):
    shell, _out, _err = _shell(runtime, runner=lambda argv: 0)
    _feed(shell, "selftest\n/quit\n")
    path = runtime / "shell" / "history.jsonl"
    assert path.exists()
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["line"] == "selftest"
    assert record["argv"] == ["selftest"]

    resumed, out2, _err2 = _shell(runtime, runner=lambda argv: 0, resume=True)
    _feed(resumed, "/quit\n")
    assert "resumed: 1 earlier command(s)" in out2.getvalue()


def test_resume_without_a_history_says_so(runtime):
    shell, out, _err = _shell(runtime, runner=lambda argv: 0, resume=True)
    _feed(shell, "/quit\n")
    assert "no earlier session" in out.getvalue()


def test_transcript_is_written(runtime):
    target = runtime / "session.txt"
    shell, _out, _err = _shell(runtime, runner=lambda argv: 0, transcript=str(target))
    summary = _feed(shell, "selftest\n/quit\n")
    assert summary["transcript"] == str(target)
    assert "selftest" in target.read_text(encoding="utf-8")


def test_limit_stops_a_scripted_session(runtime):
    calls: list[list[str]] = []
    shell, _out, _err = _shell(
        runtime, runner=lambda argv: (calls.append(list(argv)), 0)[1], limit=1
    )
    summary = _feed(shell, "selftest\nselftest\nselftest\n")
    assert summary["commands"] == 1
    assert len(calls) == 1


def test_eof_ends_the_session_cleanly(runtime):
    shell, _out, _err = _shell(runtime, runner=lambda argv: 0)
    summary = _feed(shell, "")
    assert summary["commands"] == 0


# --------------------------------------------------------------------------- #
# streaming, colour, theme
# --------------------------------------------------------------------------- #


def test_interactive_mode_installs_readline_and_uses_input(runtime, monkeypatch):
    """The TTY branch: readline wired with our completer, the prompt passed to input()."""
    installed: list[object] = []
    prompts: list[str] = []
    hooks: list[object] = []

    monkeypatch.setattr(
        repl._complete, "install_readline",
        lambda completer, history_file=None, display=None: (
            installed.append(completer), hooks.append(display), True)[1],
    )
    answers = iter(["/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": (prompts.append(prompt),
                                                             next(answers))[1])

    class TTY(io.StringIO):
        def isatty(self):
            return True

    options = repl.ShellOptions(probe=False, interactive=True)
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(options, session=session, out=out, err=err, stdin=TTY())
    assert shell.interactive is True
    assert shell.echo is False  # the terminal echoes the keys itself
    summary = shell.run()
    assert installed and isinstance(installed[0], complete.LineCompleter)
    assert prompts and prompts[0].startswith("skaldir")
    assert hooks and callable(hooks[0])  # readline draws OUR menu, not its column list
    assert summary["interactive"] is True
    assert summary["commands"] == 0


def test_tee_streams_and_records():
    sink = io.StringIO()
    primary = io.StringIO()
    tee = repl.Tee(primary, sink)
    assert tee.write("hello ") == 6
    assert tee.write("world") == 5
    tee.flush()
    assert primary.getvalue() == "hello world"
    assert sink.getvalue() == "hello world"
    assert tee.writable() and tee.encoding


def test_tee_survives_a_closed_primary():
    class Dead:
        def write(self, text):
            raise ValueError("closed")

        def flush(self):
            raise ValueError("closed")

        def isatty(self):
            return False

    sink = io.StringIO()
    tee = repl.Tee(Dead(), sink)
    assert tee.write("still recorded") == len("still recorded")
    tee.flush()
    assert sink.getvalue() == "still recorded"


def test_colour_only_on_a_tty_unless_forced():
    class Stream:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    assert repl.color_enabled(Stream(True)) is True
    assert repl.color_enabled(Stream(False)) is False
    assert repl.color_enabled(Stream(False), forced=True) is True
    assert repl.color_enabled(Stream(True), no_color=True) is False


def test_painter_uses_the_theme_module_when_it_exists(monkeypatch):
    """The real ``godsv.theme`` object API is the first choice (it decides the depth)."""
    import types

    calls: list[tuple[str, str]] = []

    class FakeTheme:
        color = True

        def style(self, text, token="default"):
            calls.append((text, token))
            return f"<{token}>{text}</{token}>"

    fake = types.ModuleType("godsv.theme")
    fake.detect = lambda stream=None, **kwargs: FakeTheme()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "godsv.theme", fake)
    monkeypatch.setattr(repl, "load_theme", lambda: fake)

    painter = repl.Painter(color=True)
    assert painter("hi", "bold") == "<strong>hi</strong>"
    assert painter("bye", "green") == "<ok>bye</ok>"
    assert painter.used_theme is True
    assert calls == [("hi", "strong"), ("bye", "ok")]


def test_painter_falls_back_to_a_function_style_theme(monkeypatch):
    import types

    fake = types.ModuleType("godsv.theme")
    fake.paint = lambda text, role="": f"[{role}]{text}"  # type: ignore[attr-defined]
    monkeypatch.setattr(repl, "load_theme", lambda: fake)
    painter = repl.Painter(color=True)
    assert painter("hi", "bold") == "[bold]hi"
    assert painter.used_theme is True


def test_painter_degrades_to_ansi_then_plain(monkeypatch):
    monkeypatch.setattr(repl, "load_theme", lambda: None)  # no theme at all
    painted = repl.Painter(color=True)
    assert painted("hi", "green") == "\033[32mhi\033[0m"
    assert painted.used_theme is False
    plain = repl.Painter(color=False)
    assert plain("hi", "green") == "hi"


def test_painter_and_the_real_theme_agree(monkeypatch):
    """Integration with the parallel ``godsv.theme``: a TTY gets styled text."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("GODSV_COLOR", raising=False)

    class TTY(io.StringIO):
        def isatty(self):
            return True

    if repl.load_theme() is None:  # pragma: no cover — the module ships with the CLI
        pytest.skip("this build has no godsv.theme module")
    painter = repl.Painter(color=repl.color_enabled(TTY()), stream=TTY())
    painted = painter("ok", "green")
    assert painter.used_theme is True
    assert painted != "ok" or not painter.color  # styled, or colour is off entirely


# --------------------------------------------------------------------------- #
# the real subprocess proof: scripted session, then a real SIGINT
# --------------------------------------------------------------------------- #


def _child_env(runtime: Path) -> dict:
    env = dict(os.environ)
    env["GODSV_HOME"] = str(runtime)
    env["HERMES_HOME"] = str(runtime / "hermes")
    env["PYTHONPATH"] = str(SRC)  # never a sibling scratch copy
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("GODSV_FORMAT", None)
    # env.py prefers SKALDIR_HOME over GODSV_HOME — a leaked value would send the
    # child's runtime (history, transcript) outside the runtime this test names.
    env.pop("SKALDIR_HOME", None)
    return env


#: What a **piped** shell prints once it is up and about to read stdin. A pipe is not a
#: TTY, so the frame stands down and the shell reads with ``readline()`` on a non-echoing
#: stream: it draws its banner and its one status line, and its plain ``skaldir > `` prompt
#: only appears together with the *echo of the line it just read* (measured). The status
#: line is therefore the last thing printed before the shell blocks for input — which makes
#: it the honest readiness signal, and a fixed sleep after the spawn is not: on a loaded host
#: the interpreter is still importing at 1.0 s, the SIGINT lands mid-import and kills the
#: child (measured: 13 of 14 runs, ``rc=-2``).
PIPE_READY = b"goal:none"

#: The action mark of a command that has really started, in the shell's own vocabulary.
#: On a pipe the action glyph is the plain ``*`` (``_OWN_GLYPHS["action"]``).
PIPE_ACTION = re.compile(rb"^\* skaldir scan -$", re.M)

#: How long a readiness wait may take. A loaded host starts this interpreter slowly
#: (measured: 1.6 s idle, 13-40 s with the host's cores saturated), so the bound has to
#: cover a slow *start*, not a slow assertion. It is a bound, not a substitute for waiting:
#: every wait below returns as soon as its readiness condition holds.
READY_TIMEOUT = 120.0


class _PipedShell:
    """A real child shell on a pipe, read **continuously** while the test drives it.

    Two measured facts make this shape necessary, and both are load symptoms:

    * an unread ``stdout`` pipe is a second clock. The child blocks on it once the buffer
      fills, and a blocked child cannot answer the SIGINT it was sent;
    * ``SIGINT`` before the shell is up does not cancel a command — it kills the
      interpreter mid-import (measured: 13 of 14 runs under load died with ``rc=-2`` and a
      ``KeyboardInterrupt`` traceback out of ``dataclasses``), because there is no session
      yet to survive it.

    So: read on a thread (the capture is the same bytes ``communicate()`` would return),
    and gate every write on the readiness of what the child last printed.
    """

    def __init__(self, runtime: Path):
        self.child = subprocess.Popen(
            [sys.executable, "-m", "godsv.repl", "--no-probe"],
            cwd=str(REPO),
            env=_child_env(runtime),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._out = bytearray()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        """Drain stdout into the capture — the child's own write side never blocks."""
        stream = self.child.stdout
        assert stream is not None
        while not self._stop.is_set():
            try:
                ready, _w, _x = select.select([stream], [], [], 0.1)
                if not ready:
                    continue
                data = os.read(stream.fileno(), 65536)
            except (OSError, ValueError):  # the child is gone; the capture is complete
                return
            if not data:
                return
            self._out.extend(data)

    def text(self) -> str:
        return bytes(self._out).decode("utf-8", "replace")

    def until(self, needle, *, start: int = 0, seconds: float = READY_TIMEOUT) -> bool:
        """Wait for ``needle`` in what the child has written since ``start``.

        ``needle`` is bytes or a compiled **bytes** pattern. Returns ``False`` on timeout
        **or** if the child died — the caller asserts on the real exit code and the real
        output, so a hang and a crash are both reported honestly instead of as a mysterious
        missing string.
        """
        def seen(blob: bytes) -> bool:
            if needle is None:
                return False
            if hasattr(needle, "search"):  # a compiled bytes pattern
                return bool(needle.search(blob))
            return needle in blob

        end = time.monotonic() + seconds
        while not seen(bytes(self._out)[start:]):
            if self.child.poll() is not None:
                return False
            if time.monotonic() > end:
                return False
            time.sleep(0.05)
        return True

    def write(self, line: str) -> bool:
        """Type one line; ``False`` (never an exception) if the shell is no longer there."""
        if self.child.stdin is None or self.child.poll() is not None:
            return False
        try:
            self.child.stdin.write(line)
            self.child.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            return False
        return True

    def finish(self, seconds: float = 90.0) -> str:
        """Close stdin, wait for the exit, return everything the child wrote."""
        self._stop.set()
        if self.child.stdin is not None:
            try:
                self.child.stdin.close()
            except (BrokenPipeError, ValueError, OSError):
                pass
            self.child.stdin = None
        try:
            self.child.wait(timeout=seconds)
        except subprocess.TimeoutExpired:  # pragma: no cover — a hung shell
            self.child.kill()
            self.child.wait()
        self._reader.join(timeout=5.0)
        return self.text()


def test_real_session_subprocess_transcript(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-m", "godsv.repl", "--no-probe", "--limit", "6"],
        cwd=str(REPO),
        env=_child_env(runtime),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    script = "tools l\t\n? tools list\n/quit\n"
    try:
        output, _ = child.communicate(script, timeout=90)
    except subprocess.TimeoutExpired:  # pragma: no cover
        child.kill()
        output, _ = child.communicate()
        pytest.fail("the shell did not exit after /quit")
    assert child.returncode == 0, output
    assert "Skaldir" in output
    assert "capability probe did not run" in output
    assert "insert 'list'" in output  # tab completion
    assert "tools.list" in output  # the ? overlay
    assert "exit 0 (OK)" in output  # a real command ran
    assert (runtime / "shell" / "history.jsonl").exists()


def test_real_sigint_cancels_the_running_command(tmp_path):
    """A real SIGINT to a real child cancels the running command; the session survives.

    Every wait here is a readiness wait on what the child actually printed. The fixed
    ``sleep(1.0)`` this replaces was a guess, and the guess is what the test's own failure
    was: on a loaded host the interpreter is still importing at 1.0 s, so the SIGINT killed
    the shell mid-import (measured: 13 of 14 runs, ``rc=-2``, a ``KeyboardInterrupt``
    traceback out of ``dataclasses``) instead of cancelling a command, and the child was
    gone before ``history 3`` could be typed. The SIGINT is sent from exactly the same
    place in the journey — after the command has really started — and the assertions are
    byte-for-byte the same.
    """
    runtime = tmp_path / "rt"
    runtime.mkdir()
    shell = _PipedShell(runtime)
    child = shell.child
    try:
        # readiness 1: the shell is up and about to read stdin
        assert shell.until(PIPE_READY), (
            f"the shell never reached its status line (rc={child.poll()}):\n"
            f"{shell.text()[-2000:]}"
        )
        mark = len(shell._out)
        assert shell.write("scan -\n"), "the shell was already gone before the command"
        # readiness 2: the action line — the command really started and is blocked on stdin
        assert shell.until(PIPE_ACTION, start=mark), (
            f"`scan -` never started (rc={child.poll()}):\n{shell.text()[-2000:]}"
        )
        child.send_signal(signal.SIGINT)
        # readiness 3: the shell reported the cancel itself
        assert shell.until(b"cancelled", start=mark), (
            f"the SIGINT was never answered (rc={child.poll()}):\n{shell.text()[-2000:]}"
        )
        assert shell.write("history 3\n"), "the shell did not survive the SIGINT"
        # readiness 4: the history table itself, so the recording is *observed* before the
        # shell is asked to leave. `finish()` only returns what was written by then.
        history_mark = len(shell._out)
        assert shell.until(b"last 1 command(s)", start=history_mark), (
            f"`history 3` never answered (rc={child.poll()}):\n{shell.text()[-2000:]}"
        )
        assert shell.write("/quit\n"), "the shell did not survive the SIGINT"
        output = shell.finish()
    finally:
        if child.poll() is None:  # pragma: no cover — only on a failed assertion above
            child.kill()
            child.wait()
    assert child.returncode == 0, output
    assert "cancelled `skaldir scan -`" in output
    assert "the session is still here" in output
    assert "cancelled" in output.split("history 3")[-1]  # recorded in the history
