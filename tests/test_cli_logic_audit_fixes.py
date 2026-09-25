"""Audit fixes 46 — items #1/#2/#4/#6/#8/#10/#15 (a dedicated regression file).

Every test here is the failing probe from the audit before the fix:
the executed-ALLOW render lie, the allow-vocabulary drift, the secret scan
that never saw ``text``, the tautological ``_unjudgeable``, the chat prose
route, and the two vocabulary copies.
"""

from __future__ import annotations

import io

import pytest

from skaldir import art, render, toolcall
from skaldir import theme as T


def bare(text: str) -> str:
    """The line without its escape sequences (the same strip the art tests use)."""
    return render.strip_ansi(text).strip()


def plain() -> T.Theme:
    return T.Theme(mode=T.MODE_PLAIN, depth=T.DEPTH_NONE, glyphs=T.GLYPHS_ASCII,
                   width=80, height=24, source="explicit")


# --------------------------------------------------------------------------- #
# #1 / #15 — executed vs declined: the renderer keyed on ``executed``          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("decision", ["ALLOW", "allow", "Allowed", "EXECUTED", "OK", "run"])
def test_an_executed_allow_that_failed_is_FAILED_never_DECLINED(decision: str):
    event = {"name": "write_text", "decision": decision, "ok": False, "executed": True}
    drawn = bare(art.from_tool_event(event, theme=plain()))
    assert "FAILED" in drawn, drawn
    assert "DECLINED" not in drawn, drawn
    assert decision in drawn, drawn


@pytest.mark.parametrize("decision", ["ALLOW", "allow", "Allowed"])
def test_an_executed_allow_with_no_ok_key_neither_fails_nor_declines(decision: str):
    event = {"name": "write_text", "decision": decision, "executed": True}
    drawn = bare(art.from_tool_event(event, theme=plain()))
    assert "FAILED" not in drawn, drawn
    assert "DECLINED" not in drawn, drawn


def test_a_nonexecuted_refusal_stays_DECLINED():
    event = {"name": "shell", "decision": "denied_approval", "executed": False}
    drawn = bare(art.from_tool_event(event, theme=plain()))
    assert "DECLINED" in drawn, drawn


def test_an_executed_refusal_is_never_worded_DECLINED():
    """The one lie the audit points at: executed, but the word says the gate refused."""
    event = {"name": "write_text", "decision": "ALLOW", "ok": False, "executed": True}
    drawn = bare(art.from_tool_event(event, theme=plain()))
    assert "DECLINED" not in drawn, drawn
    assert "FAILED" in drawn, drawn


def test_the_failed_word_is_in_the_art_vocabulary():
    from skaldir.art import KEYWORD_FAILED

    assert KEYWORD_FAILED == "FAILED"


# --------------------------------------------------------------------------- #
# #2 / #10 / #15 — one allow-vocabulary, exported from toolcall                #
# --------------------------------------------------------------------------- #


def test_toolcall_exports_the_allow_vocabulary_for_rendering():
    tokens = toolcall.ALLOW_TOKENS_FOR_RENDER
    for word in ("allow", "allowed", "executed", "ok", "run"):
        assert word in tokens, word
    spellings = {word.lower() for word in tokens}
    assert "allow" in spellings


def test_art_uses_the_one_exported_allow_set():
    """The renderer's allow-set is toolcall's, not a hand-copied twin."""
    from skaldir import toolcall as tc

    exported = {word.lower() for word in tc.ALLOW_TOKENS_FOR_RENDER}
    art_names = [name for name in dir(art) if name.startswith("_ALLOW")]
    assert art_names, "art no longer names its allow set at all"
    for name in art_names:
        value = getattr(art, name)
        try:
            members = {word.lower() for word in value}
        except TypeError:  # not a set of words
            continue
        assert members == exported, (name, members ^ exported)


def test_toolcall_write_vocabulary_includes_the_loop_builtins():
    for name in ("write_text", "edit_apply", "run_tests"):
        assert name in toolcall.WRITE_TOOLS, name


def test_the_role_vocabulary_derives_from_the_policy_write_set():
    """Audit 46 #10: the two WRITE_TOOLS copies cannot drift apart again."""
    from skaldir import subagent_roles as R
    from skaldir.tools_core import registry as core_registry

    classed = R.tool_classes()
    for name in sorted(toolcall.WRITE_TOOLS):
        if name in core_registry.names():
            continue  # the contracts already cover registry tools
        kind = classed.get(name)
        if kind == "write":
            assert name in R.WRITE_TOOLS, name


def test_the_short_loop_spellings_are_classed_once():
    from skaldir import subagent_roles as R

    classes = R.tool_classes()
    for name in ("write_text", "edit_apply"):
        assert classes.get(name) == "write", (name, classes.get(name))
        assert name in R.WRITE_TOOLS, name


# --------------------------------------------------------------------------- #
# #4 — the write content reaches the security scans                            #
# --------------------------------------------------------------------------- #


def test_a_secret_written_via_text_is_seen():
    intent = toolcall.ToolIntent(
        tool="write_text",
        args={"path": "notes.md", "text": "backup the key: /home/willy/.ssh/id_rsa"},
        workspace="/home/willy/projects/gods-voice",
    )
    decision = toolcall.judge_tool_call(intent)
    assert "secret path touched (credentials/keys)" in " ".join(decision.reasons), (
        decision.checks
    )


def test_write_text_text_parameter_must_be_a_string():
    intent = toolcall.ToolIntent(tool="write_text", args={"path": "a.py", "text": 3})
    decision = toolcall.judge_tool_call(intent)
    assert any("text" in reason for reason in decision.reasons), decision.checks


# --------------------------------------------------------------------------- #
# #6 — _unjudgeable: the tautology is gone, powershell is judged               #
# --------------------------------------------------------------------------- #


def test_powershell_call_without_any_command_or_target_is_unjudgeable():
    intent = toolcall.ToolIntent(tool="powershell", args={})
    assert toolcall._unjudgeable(intent), "an empty exec call slipped through"


def test_a_terminal_call_with_a_command_is_judgeable():
    intent = toolcall.ToolIntent(tool="terminal", args={"command": "ls"})
    assert not toolcall._unjudgeable(intent)


def test_a_write_tool_with_a_target_but_no_command_is_judgeable():
    intent = toolcall.ToolIntent(tool="write_text", args={"path": "a.py"})
    assert not toolcall._unjudgeable(intent)


# --------------------------------------------------------------------------- #
# #8 — chat prose ordering                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return tmp_path


def test_a_prose_question_reaches_the_agent_loop_not_the_command_parser(runtime):
    from skaldir import chatbar, repl

    out, err = io.StringIO(), io.StringIO()
    seen: list[tuple[str, ...]] = []

    def runner(argv):
        seen.append(tuple(argv))
        return 0

    session = repl.Session(runtime=runtime, runner=runner, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, quiet=True), session=session,
                       out=out, err=err, stdin=io.StringIO(""))
    bar = chatbar.ChatBar(out, fd=0, width=80, height=24, unicode=False,
                          model=lambda: chatbar.Fact("test-model", "test", ""),
                          goal=lambda: chatbar.Fact("none", "test", "no goal recorded"))
    shell._chatbar_front = chatbar.Front(shell, bar)  # type: ignore[attr-defined]
    shell.dispatch("what can you do?")  # type: ignore[attr-defined]
    assert seen and seen[0][:3] == ("agent", "run", "what can you do?"), seen
    assert "--chat-output" in seen[0]
    assert "invalid choice" not in err.getvalue(), err.getvalue()
    assert "usage" not in err.getvalue().lower(), err.getvalue()
