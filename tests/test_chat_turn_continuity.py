"""D3 in the real layout: a prose turn continues the session, not just the unit's mocks.

``chatbar.route_plain`` hands the previous turn's transcript to the next one through
``--previous``. The handle is measured, not invented — but it is looked up in a *directory*,
and that lookup is the half a mocked test cannot see.

Measured in a real 100x30 pty (two prose turns against an OpenAI-compatible stub): the second
request went out as ``roles=[system,user]`` with no trace of the first exchange, because
``chatbar._transcript_since`` searched ``<runtime>/runs`` while the chat's own ``agent run``
writes ``<runtime>/agent/runs`` (``agent_loop.agent_state_dir`` is the identity; the *caller*
appends the group directory — ``agent_loop_cli._state_dir``). These tests hold both halves:
the directory, and the request body the user actually pays for.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from skaldir import agent_loop as al
from skaldir import chatbar, cli, repl
from skaldir.llm_provider import ChatResult


class AnsweringClient:
    """A client that answers every request with one sentence and never calls a tool."""

    name = "answering"

    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.requests: list[list[dict[str, Any]]] = []

    def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
        self.requests.append([dict(item) for item in messages])
        return ChatResult(text=self.text, tool_calls=[], finish_reason="stop")


def _shell(runtime: Path) -> repl.Shell:
    import io

    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    return repl.Shell(repl.ShellOptions(probe=False), session=session, out=out, err=err,
                      stdin=io.StringIO(""))


def test_the_turn_handle_is_found_where_the_group_really_writes_it(tmp_path):
    """``agent run`` writes ``<runtime>/agent/runs/<id>.jsonl`` — that is where the handle is."""
    runs = tmp_path / "agent" / "runs"
    runs.mkdir(parents=True)
    transcript = runs / "run-20260101T000000Z-abc123.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    shell = _shell(tmp_path)
    assert chatbar._transcript_since(shell, time.time() - 30.0) == str(transcript)


def test_a_turn_that_wrote_nothing_leaves_the_handle_empty(tmp_path):
    """The honest empty: no transcript in the window, so the next line is independent again."""
    import os

    runs = tmp_path / "agent" / "runs"
    runs.mkdir(parents=True)
    old = runs / "run-20200101T000000Z-old.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 3600.0
    os.utime(old, (stale, stale))          # a transcript from an earlier session, not this turn

    shell = _shell(tmp_path)
    assert chatbar._transcript_since(shell, time.time()) == ""


def test_the_second_turn_goes_out_with_the_first_exchange(tmp_path):
    """The request body a user's second question produces: system, user, assistant, user."""
    state = tmp_path / "agent"
    first = AnsweringClient("the first answer")
    result = al.AgentLoop(
        first, al.builtin_tools(tmp_path), state_dir=state, workspace=tmp_path,
        limits=al.LoopLimits(max_steps=2),
    ).run("what is one?")
    history = al.transcript_history(result.transcript_path)
    assert [item["role"] for item in history] == ["user", "assistant"]

    second = AnsweringClient("the second answer")
    al.AgentLoop(
        second, al.builtin_tools(tmp_path), state_dir=state, workspace=tmp_path,
        history=history, limits=al.LoopLimits(max_steps=2),
    ).run("and what is two?")

    messages = second.requests[0]
    assert [item["role"] for item in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "what is one?"
    assert messages[2]["content"] == "the first answer"
    assert messages[3]["content"] == "and what is two?"


def test_the_shell_hands_the_found_handle_to_the_next_prose_line(tmp_path, monkeypatch):
    """``route_plain`` + the real lookup: the second line carries ``--previous <real path>``."""
    runs = tmp_path / "agent" / "runs"
    runs.mkdir(parents=True)
    transcript = runs / "run-20260101T000000Z-abc123.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    shell = _shell(tmp_path)
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(shell, "run_command",
                        lambda text, argv=None, source_line=None: seen.append(tuple(argv or ())))
    front = type("F", (), {
        "routing": True, "history": "", "bar": object(),
        "begin_turn": lambda self: None, "end_turn": lambda self: None,
    })()
    # the handle is the *real* lookup, against the real runtime layout — not a literal
    front.history = chatbar._transcript_since(shell, time.time() - 30.0)
    assert front.history == str(transcript), "the turn's own transcript is the handle"
    monkeypatch.setattr(shell, "_chatbar_front", front, raising=False)
    monkeypatch.setattr(chatbar, "_stamp_tool_rows", lambda front, path: 0)

    assert chatbar.route_plain(shell, "how do I add tests?") is False
    assert seen == [("agent", "run", "how do I add tests?", "--chat-output",
                     "--previous", str(transcript))]


def test_the_parser_still_takes_previous(tmp_path):
    """``--previous`` is a real flag of the real command (never a chat-only invention)."""
    args = cli.build_parser().parse_args(["agent", "run", "hi", "--previous", "/tmp/x.jsonl"])
    assert args.previous == "/tmp/x.jsonl"
