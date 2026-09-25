"""TDD ``/computer-use <task>``: free text STARTS THE AGENT, the bare verb reports.

Defect (confirmed in the old mapping): every ``/computer-use …`` line folded onto
``act check``, so ``/computer-use open chrome`` *reported* the capability surface
instead of acting on the task. The mapping now splits three ways — measured here
against the real parser tree, exactly like ``tests/test_slash_palette.py``:

* no free text            → ``act check``   (the honest report — unchanged),
* a word naming a sibling → that sibling    (``run <action-list>`` → ``act run …``),
* free-text task words    → ``agent run --chat-output --agent-tools "<task>"``,
  so the agent loop (with the layer tools, ``skill_act``/``skill_act_check``) drives
  the real GUI instead of a report being printed.

The shell side of the seam is proven too: ``Shell.run_slash`` hands the resolved
argv to the runner verbatim (the fake-frame pattern of ``tests/test_repl_shell.py``),
and the produced argv parses with this build's real ``agent run`` parser.
"""
from __future__ import annotations

import io

import pytest
from skaldir import cli, complete, repl, slash


def _tree():
    """The real parser tree — the ground truth the sibling rule reads."""
    return complete.build_tree(cli.build_parser())


TREE = _tree()


# --------------------------------------------------------------------------- #
# resolve: the three shapes of the verb
# --------------------------------------------------------------------------- #


def test_the_bare_verb_is_still_the_honest_act_check_report():
    """No argument means the capability report — the documented no-args form."""
    for spelling in ("/computer-use", "/cu"):
        invocation = slash.resolve(spelling, TREE)
        assert invocation is not None
        assert invocation.argv == ("act", "check")
        assert invocation.mode == "default"


def test_a_sibling_word_still_becomes_the_verb():
    """`run <action-list>` and `subtool …` keep their documented routing."""
    run = slash.resolve("/computer-use run actions.json", TREE)
    assert run.argv == ("act", "run", "actions.json")
    assert run.mode == "verb"
    sub = slash.resolve("/computer-use subtool list", TREE)
    assert sub.argv == ("act", "subtool", "list")
    assert sub.mode == "verb"
    one = slash.resolve("/computer-use check", TREE)
    assert one.argv == ("act", "check") and one.mode == "verb"
    # a flag-only line is the report with its flag — never a task
    flagged = slash.resolve("/computer-use --probe-image", TREE)
    assert flagged.argv == ("act", "check", "--probe-image")
    assert flagged.mode == "default"


def test_free_text_starts_the_agent_loop_on_the_task():
    """The defect: `open chrome` fed `act check` as arguments. Now it is ONE task."""
    invocation = slash.resolve("/computer-use open chrome", TREE)
    assert invocation is not None
    assert invocation.argv == (
        "agent", "run", "--chat-output", "--agent-tools", "open chrome",
    )
    assert invocation.mode == "task"
    # a quoted sentence stays one argument (shlex, not split)
    quoted = slash.resolve('/computer-use "open the browser and read the title"', TREE)
    assert quoted.argv[-1] == "open the browser and read the title"
    # the alias takes the same path
    alias = slash.resolve("/cu open chrome", TREE)
    assert alias is not None and alias.argv == invocation.argv
    # the task words end at the first flag; the flags pass through as flags
    flagged = slash.resolve("/computer-use open chrome --max-steps 3", TREE)
    assert flagged.argv == (
        "agent", "run", "--chat-output", "--agent-tools", "open chrome",
        "--max-steps", "3",
    )


def test_the_produced_argv_parses_with_this_builds_real_agent_run_parser():
    """Not a shape argparse refuses: the task is `agent run`'s own positional."""
    parsed = repl.parse_invocation(
        ("agent", "run", "--chat-output", "--agent-tools", "open chrome")
    )
    assert parsed is not None
    assert parsed.task == "open chrome"
    assert parsed.agent_tools is True and parsed.chat_output is True


# --------------------------------------------------------------------------- #
# the routing seam: the shell runs the resolved argv verbatim
# --------------------------------------------------------------------------- #


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return tmp_path


def _shell(tmp_path, runner):
    out, err = io.StringIO(), io.StringIO()
    options = repl.ShellOptions(probe=False)
    session = repl.Session(runtime=tmp_path, runner=runner, out=out, err=err)
    shell = repl.Shell(options, session=session, out=out, err=err, stdin=io.StringIO(""))
    return shell, out, err


def test_the_shell_dispatches_a_task_line_to_the_agent_loop(runtime):
    """run_slash is the seam: the resolved argv is what reaches the runner."""
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0

    shell, out, _err = _shell(runtime, runner)
    shell.run_slash("/computer-use open chrome")
    assert calls == [["agent", "run", "--chat-output", "--agent-tools", "open chrome"]]
    # the frame treats it as a chat turn (the shape `chatbar` dispatches)
    assert "You:" in out.getvalue()


def test_the_shell_still_runs_the_report_and_the_siblings(runtime):
    """The two preserved forms go to `act`, untouched by the task dispatch."""
    calls: list[list[str]] = []

    def runner(argv):
        calls.append(list(argv))
        return 0

    shell, _out, _err = _shell(runtime, runner)
    shell.run_slash("/computer-use")
    shell.run_slash("/computer-use run actions.json")
    assert calls[0] == ["act", "check"]
    assert calls[1] == ["act", "run", "actions.json"]
