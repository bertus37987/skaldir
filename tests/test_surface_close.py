"""SURFACE-CLOSE: the plumbing defects the reachability and chat-model-calls audits measured.

One test per finding, each naming the number it closes:

* **D1** — the status line read ``getattr(entry, "ready")`` on a ``ProviderEntry``, which is a
  *method*: the probe therefore said ``provider:llm (not ready)`` forever, whatever the config.
* **D2** — every early return out of ``agent run`` now closes the live progress row: a row that
  outlives its command kept writing ``still working · no new event for N s`` after a refusal.
* **D3** — a chat prose turn continues the **session**: ``transcript_history`` + ``--previous``
  put the previous exchange in the next request body, so context accumulates.
* **D4** — ``/context --run-id <id>`` resolves against the state dir the chat's own runs live
  in (``<runtime>/agent``) instead of the current directory.
* **D5** — ``art.from_tool_event`` has a product call site: the frame's tool row names the tool
  the model really called, out of the run's own transcript events.
* **F2** — a run that completes inside a frame is reported: the frame owns the terminal, so
  ``skaldir run`` does not open a second chat inside it and the command returns.
* **F4** — a quoted value in the slash path is ONE token, exactly as the CLI receives it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from skaldir import agent_loop as al
from skaldir import agent_loop_cli as acli
from skaldir import chatbar, cli, goal_run_cli, repl, slash
from skaldir import provider_config as pc
from skaldir.llm_provider import ChatResult


class AnsweringClient:
    """A client that answers every request with one sentence and never calls a tool."""

    name = "answering"

    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
        self.requests.append({"messages": [dict(item) for item in messages], **kwargs})
        return ChatResult(text=self.text, tool_calls=[], finish_reason="stop")


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sample.py").write_text("x = 1\n", encoding="utf-8")
    return ws


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- #
# D1 — the provider probe reads the real readiness signal                       #
# --------------------------------------------------------------------------- #


def _write_config(entries: dict[str, dict[str, Any]], default: str) -> None:
    path = Path(pc.config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"godsv": {"providers": {"default": default, "entries": entries}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_the_provider_probe_does_not_read_a_bound_method_as_readiness(runtime):
    from skaldir import provider_config as pc_mod

    _write_config(
        {"llm": {"kind": "openai-compatible", "base_url": "http://127.0.0.1:8080/v1",
                 "model": "stub-model"}},
        "llm",
    )
    entry = pc_mod.load_store(pc_mod.config_path()).providers["llm"]
    assert callable(entry.ready), "the trap: `ready` is a method, so its truth is never False"
    probes = repl.Probes(runtime)
    value = probes.provider()
    assert value.label == "llm", "a complete local provider is ready, and the line says so"
    assert "(not ready)" not in value.label
    assert "model is unset" not in value.detail


def test_the_provider_probe_names_what_is_missing_when_it_is_not_ready(runtime):
    _write_config(
        {"llm": {"kind": "openai-compatible", "base_url": "http://127.0.0.1:8080/v1",
                 "model": ""}},
        "llm",
    )
    value = repl.Probes(runtime).provider()
    assert value.label == "llm (not ready)"
    assert "model is unset" in value.detail, "the reason is on the line, not hidden"


# --------------------------------------------------------------------------- #
# D2 — every early return out of `agent run` closes the live row                 #
# --------------------------------------------------------------------------- #


class Recorder:
    """A progress row that records what was done to it."""

    def __init__(self) -> None:
        self.begun: list[str] = []
        self.settled: list[tuple[str, str]] = []
        self.closed = 0

    def begin(self, action: str, **_facts: Any) -> str:
        self.begun.append(action)
        return "line"

    def event(self, kind: str, fields: Any = None, **extra: Any) -> str:
        return "line"

    def settle(self, verdict: str, text: str = "") -> str:
        self.settled.append((str(verdict), str(text)))
        return "line"

    def close(self) -> dict[str, Any]:
        self.closed += 1
        return {"closed": self.closed}


def _agent_args(*extra: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["agent", "run", "do the thing", *extra])


def test_a_refused_run_closes_its_progress_row(monkeypatch, runtime):
    """No provider configured: exit 4 — and the row is closed before the refusal is printed."""
    from skaldir.llm_provider import ProviderConfigError

    recorder = Recorder()
    monkeypatch.setattr(acli, "_progress_for", lambda args: recorder)
    monkeypatch.setattr(acli, "_client_and_status",
                        lambda args: (_ for _ in ()).throw(
                            ProviderConfigError("no provider configured")))

    assert acli.cmd_run(_agent_args()) == 4
    assert recorder.begun, "the row was begun (the felt-latency line)"
    assert recorder.closed == 1, "and it was closed — the row never outlives its command"


def test_a_run_that_hits_a_bound_closes_its_row_too(monkeypatch, workspace, runtime):
    recorder = Recorder()
    monkeypatch.setattr(acli, "_progress_for", lambda args: recorder)

    class Boom(AnsweringClient):
        def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
            raise al.LLMError("provider exploded")

    monkeypatch.setattr(acli, "_client_and_status",
                        lambda args: (Boom(), {"ready": True}))
    args = _agent_args("--workspace", str(workspace))
    assert acli.cmd_run(args) != 0
    assert recorder.closed == 1, "a failed run's row is closed exactly like a successful one"


def test_a_successful_run_closes_its_row_once(monkeypatch, workspace, runtime):
    recorder = Recorder()
    monkeypatch.setattr(acli, "_progress_for", lambda args: recorder)
    monkeypatch.setattr(acli, "_client_and_status",
                        lambda args: (AnsweringClient("all done"), {"ready": True}))
    args = _agent_args("--workspace", str(workspace), "--no-policy")
    assert acli.cmd_run(args) == 0
    assert recorder.closed == 1
    assert recorder.settled, "the verdict line was written"


# --------------------------------------------------------------------------- #
# D3 — a prose turn continues the session                                       #
# --------------------------------------------------------------------------- #


def _loop(state: Path, workspace: Path, client: Any, history: Any = ()) -> al.AgentLoop:
    return al.AgentLoop(
        client, al.builtin_tools(workspace), state_dir=state, workspace=workspace,
        history=history, limits=al.LoopLimits(max_steps=2),
    )


def test_transcript_history_is_the_exchange_the_run_left_behind(tmp_path, workspace):
    state = tmp_path / "agent"
    first = _loop(state, workspace, AnsweringClient("the first answer"))
    result = first.run("what is one?")
    history = al.transcript_history(result.transcript_path)
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "what is one?"
    assert history[1]["content"] == "the first answer"
    assert all(item["role"] != "system" for item in history), "the next run writes its own"


def test_the_second_request_body_visibly_contains_the_first_exchange(tmp_path, workspace):
    """The proof the audit asked for: two consecutive turns, one growing request."""
    state = tmp_path / "agent"
    alone = AnsweringClient("the first answer")
    first = _loop(state, workspace, alone).run("what is one?")
    assert [item["role"] for item in alone.requests[-1]["messages"]] == ["system", "user"], (
        "an independent run sends one user turn and nothing before it"
    )

    history = al.transcript_history(first.transcript_path)
    client = AnsweringClient("the second answer")
    second = _loop(state, workspace, client, history=history).run("and what is two?")

    body = client.requests[-1]["messages"]
    assert [item["role"] for item in body] == ["system", "user", "assistant", "user"]
    assert body[1]["content"] == "what is one?", "the first question is in the second request"
    assert body[2]["content"] == "the first answer"
    assert body[3]["content"] == "and what is two?"

    header = next(event for event in al.read_transcript(second.transcript_path)["events"]
                  if event.get("kind") == "run.started")
    assert header["history_messages"] == 2, "the transcript records how much it continued"
    # and the chain keeps going: the second run's own history is the whole exchange
    assert [item["role"] for item in al.transcript_history(second.transcript_path)] == [
        "user", "assistant", "user", "assistant",
    ]


def test_a_transcript_without_a_recorded_body_continues_nothing(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps({"kind": "run.started", "run_id": "r"}) + "\n",
                    encoding="utf-8")
    assert al.transcript_history(path) == []


def test_previous_is_the_way_the_cli_carries_the_conversation(tmp_path, workspace):
    state = tmp_path / "agent"
    first = _loop(state, workspace, AnsweringClient("hello there")).run("say hello")
    args = _agent_args("--previous", str(first.transcript_path))
    history = acli._history_from(args)
    assert [item["content"] for item in history] == ["say hello", "hello there"]


def test_a_previous_path_that_cannot_be_read_is_reported(tmp_path):
    args = _agent_args("--previous", str(tmp_path / "nope.jsonl"))
    with pytest.raises(al.AgentLoopError, match="could not be read"):
        acli._history_from(args)


def test_the_chat_hands_the_previous_turn_to_the_next_one(monkeypatch, runtime):
    """``route_plain`` passes ``--previous`` once a turn has produced a transcript."""
    seen: list[tuple[str, ...]] = []

    class Shell:
        session = type("S", (), {"tree": object(), "runtime": str(runtime)})()

        def run_command(self, text, argv=None, source_line=None):
            seen.append(tuple(argv or ()))
            return False

    front = type("F", (), {"routing": True, "history": "/tmp/earlier.jsonl",
                           "bar": object(), "begin_turn": lambda self: None,
                           "end_turn": lambda self: None})()
    monkeypatch.setattr(chatbar, "front_of", lambda shell: front)
    monkeypatch.setattr(chatbar, "looks_like_prose", lambda text, known=None: True)
    monkeypatch.setattr(chatbar, "root_names", lambda tree: ())
    monkeypatch.setattr(chatbar, "_transcript_from_envelope", lambda shell: "")
    monkeypatch.setattr(chatbar, "_transcript_since", lambda shell, since: "")
    monkeypatch.setattr(chatbar, "_stamp_tool_rows", lambda front, path: 0)

    assert chatbar.route_plain(Shell(), "what does this repo do?") is False
    assert seen == [("agent", "run", "what does this repo do?", "--chat-output",
                     "--previous", "/tmp/earlier.jsonl")], (
        "the previous turn rides into the request that follows it"
    )


# --------------------------------------------------------------------------- #
# D4 — `/context --run-id` resolves against the chat's own state dir            #
# --------------------------------------------------------------------------- #


def _shell(runtime_dir: Path, **kwargs: Any) -> repl.Shell:
    out, err = io.StringIO(), io.StringIO()
    options = repl.ShellOptions(probe=False, **kwargs)
    session = repl.Session(runtime=runtime_dir, runner=lambda argv: 0, out=out, err=err)
    return repl.Shell(options, session=session, out=out, err=err, stdin=io.StringIO(""))


def test_a_run_id_from_the_chat_resolves_against_the_agents_state_dir(runtime):
    shell = _shell(runtime)
    resolved = shell.context_state_dir(("context", "usage", "--run-id", "run-abc"))
    assert resolved == ("context", "usage", "--run-id", "run-abc",
                        "--state-dir", str(runtime / "agent"))


def test_an_explicit_state_dir_is_left_alone(runtime):
    shell = _shell(runtime)
    given = ("context", "usage", "--run-id", "run-abc", "--state-dir", "/tmp/elsewhere")
    assert shell.context_state_dir(given) == given


def test_other_verbs_and_other_forms_are_untouched(runtime):
    shell = _shell(runtime)
    assert shell.context_state_dir(("agent", "run", "hi")) == ("agent", "run", "hi")
    assert shell.context_state_dir(("context", "usage")) == ("context", "usage")
    assert shell.context_state_dir(()) == ()


# --------------------------------------------------------------------------- #
# D5 — the frame's tool row names the tool the model really called              #
# --------------------------------------------------------------------------- #


class Rows:
    def __init__(self) -> None:
        self.blocks: list[list[str]] = []

    def stamp(self, rows: Any, **kwargs: Any) -> int:
        self.blocks.append([str(item) for item in rows])
        return len(self.blocks[-1])


def _transcript_with_a_tool_call(path: Path) -> None:
    events = [
        {"kind": "run.started", "run_id": "run-1", "task": "what is in this directory"},
        {"kind": "model.request", "step": 1, "body": {"messages": [
            {"role": "system", "content": "system"}, {"role": "user", "content": "list"}]}},
        {"kind": "tool.call", "step": 1, "name": "shell", "arguments": {"command": "ls -la"},
         "decision": "allowed", "executed": True, "ok": True, "duration_ms": 41},
        {"kind": "model.response", "step": 2, "text": "36 entries"},
        {"kind": "run.finished", "status": "answered", "answer": "36 entries"},
    ]
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")


def test_the_tool_row_is_drawn_from_the_runs_own_events(tmp_path):
    path = tmp_path / "run-1.jsonl"
    _transcript_with_a_tool_call(path)
    rows = Rows()
    front = type("F", (), {"bar": rows})()
    assert chatbar._stamp_tool_rows(front, str(path)) == 1
    assert len(rows.blocks) == 1
    drawn = rows.blocks[0][0]
    assert "shell" in drawn, "the tool the model called, not the command the shell ran"
    assert "41 ms" in drawn, "and the duration the loop measured, not an estimate"
    assert "agent run" not in drawn


def test_a_file_tool_draws_the_file_it_touched(tmp_path):
    path = tmp_path / "run-read.jsonl"
    path.write_text(json.dumps({
        "kind": "tool.call", "name": "read_text", "arguments": {"path": "src/x.py"},
        "decision": "allowed", "executed": True, "ok": True, "duration_ms": 2,
    }) + "\n", encoding="utf-8")
    rows = Rows()
    front = type("F", (), {"bar": rows})()
    assert chatbar._stamp_tool_rows(front, str(path)) == 1
    assert "src/x.py" in rows.blocks[0][0], "a read row names what was read"


def test_a_refused_tool_call_draws_the_blocked_mark(tmp_path):
    path = tmp_path / "run-2.jsonl"
    path.write_text(json.dumps({
        "kind": "tool.call", "name": "write_text", "arguments": {}, "decision": "denied",
        "executed": False, "ok": False, "duration_ms": 0,
    }) + "\n", encoding="utf-8")
    rows = Rows()
    front = type("F", (), {"bar": rows})()
    assert chatbar._stamp_tool_rows(front, str(path)) == 1
    assert "write_text" in rows.blocks[0][0]
    assert "denied" in rows.blocks[0][0]


def test_a_transcript_with_no_tool_call_draws_nothing(tmp_path):
    path = tmp_path / "run-3.jsonl"
    path.write_text(json.dumps({"kind": "run.started", "run_id": "r"}) + "\n",
                    encoding="utf-8")
    rows = Rows()
    front = type("F", (), {"bar": rows})()
    assert chatbar._stamp_tool_rows(front, str(path)) == 0
    assert rows.blocks == []


# --------------------------------------------------------------------------- #
# F2 — a run that completes inside a frame is reported                          #
# --------------------------------------------------------------------------- #


class _TTY:
    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:  # pragma: no cover — never written to
        return len(text)

    def flush(self) -> None:  # pragma: no cover
        return None


def test_a_frame_takes_the_terminal_away_from_a_nested_run(monkeypatch):
    args = argparse.Namespace(no_chat=False, json=False, quiet=False)
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr(sys, "stdout", _TTY())
    monkeypatch.delenv("SKALDIR_FRAME", raising=False)
    assert goal_run_cli._chat_wanted(args) is True, "a bare terminal still gets the chat"

    with chatbar.frame_owns_terminal(True):
        assert os.environ["SKALDIR_FRAME"] == "1"
        assert goal_run_cli._chat_wanted(args) is False, (
            "a second frame inside the first is not a chat: it is a command that never returns"
        )
    assert "SKALDIR_FRAME" not in os.environ, "and the marker is given back"


def test_the_marker_nests_without_leaking(monkeypatch):
    monkeypatch.setenv("SKALDIR_FRAME", "from-outside")
    with chatbar.frame_owns_terminal(True):
        assert os.environ["SKALDIR_FRAME"] == "1"
    assert os.environ["SKALDIR_FRAME"] == "from-outside"


def test_without_a_frame_the_marker_changes_nothing(monkeypatch):
    monkeypatch.delenv("SKALDIR_FRAME", raising=False)
    with chatbar.frame_owns_terminal(False):
        assert "SKALDIR_FRAME" not in os.environ
    assert "SKALDIR_FRAME" not in os.environ


def test_a_frame_closes_the_live_row_when_the_command_ends(runtime):
    """The visible half of F2: `front_end` puts the row back to idle and stamps the result."""
    out = io.StringIO()
    bar = chatbar.ChatBar(out, fd=0, width=80, height=24, unicode=False,
                          model=lambda: chatbar.Fact("m", "t", ""),
                          goal=lambda: chatbar.Fact("none", "t", ""))
    front = chatbar.Front(None, bar)
    shell = _shell(runtime)
    bar.begin_turn("working")
    bar.set_activity("tool", "run")
    assert bar.activity[0] == "tool"
    shell.front_end(("run", "make it clean"),
                    repl.RunResult(argv=("run", "make it clean"), exit_code=0,
                                   duration_ms=110, bytes_streamed=1120),
                    front)
    assert bar.activity[0] == "idle", "no finished command leaves a moving display behind"
    assert "exit 0" in out.getvalue() or "OK" in out.getvalue()


# --------------------------------------------------------------------------- #
# F4 — a quoted value is one token, everywhere on the slash path                #
# --------------------------------------------------------------------------- #


def test_a_quoted_value_reaches_the_cli_as_one_token():
    resolved = slash.resolve('/spawn --task "hi there" --parallel 1 --max-turns 1')
    assert resolved is not None
    assert resolved.extra == ("--task", "hi there", "--parallel", "1", "--max-turns", "1")
    assert resolved.argv == ("agent", "spawn", "--task", "hi there", "--parallel", "1",
                             "--max-turns", "1")


def test_the_action_row_is_the_argv_it_prints():
    resolved = slash.resolve('/find "two words"')
    assert resolved is not None
    assert resolved.argv[2:] == ("two words",), "the row joins the argv; the argv is tokenised"


def test_the_rest_slot_still_takes_a_quoted_goal_as_one_value():
    resolved = slash.resolve('/locking "fix the parser" --max-turns 2')
    assert resolved is not None
    assert resolved.argv == ("run", "fix the parser", "--progress", "--max-turns", "2")
    plain = slash.resolve("/locking fix the parser")
    assert plain is not None and plain.argv == ("run", "fix the parser", "--progress")


def test_an_unbalanced_quote_still_resolves():
    resolved = slash.resolve('/find "unterminated')
    assert resolved is not None, "a half-typed line is a line, not a crash"
