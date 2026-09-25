"""The approval modes must differ at the *dispatch*, not only inside the gate.

GOAL §4: "Ask Everything, Approve for me und Approve Everything müssen wirksam verschieden
sein." A gate that nothing consults would pass its own unit tests and change nothing. These
tests drive the real agent loop and count what the tool actually did.
"""

from __future__ import annotations

import json

from skaldir.agent_loop import AgentLoop, AgentTool
from skaldir.approval_gate import ApprovalGate
from skaldir.approval_modes import ApprovalMode


class _Answer:
    """The loop reads `answer.tool_calls` as an attribute, not from a dict."""

    def __init__(self, content: str, tool_calls: list) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments

    def to_dict(self) -> dict:
        return {"name": self.name, "arguments": self.arguments}


class _Call:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = "call-1"
        self.type = "function"
        self.name = name
        # the loop reads `call.arguments` as a mapping and `function.arguments` as the
        # JSON text — give each the shape its reader expects
        self.arguments = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        self.function = _Fn(name, arguments)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "function": self.function.to_dict(),
        }

    def to_wire(self) -> dict:
        return self.to_dict()


def _calls(name: str, args: dict) -> list:
    """Build the calls with the runtime's *own* parser, not a hand-rolled stand-in.

    A hand-made call object drifts from the real shape one attribute at a time (measured:
    ``to_dict``, then ``name``, then ``parse_error``). The parser the loop uses is the one
    that knows the shape.
    """
    from skaldir.llm_provider import tool_calls_from_message

    message = {
        "content": "",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }
    return tool_calls_from_message(message)


class FakeClient:
    """Answers with one tool call, then with a final answer."""

    def __init__(self, tool_name: str, args: dict) -> None:
        self.tool_name = tool_name
        self.args = args
        self.turn = 0

    def chat(self, messages, *, tier="", tools=None, tool_choice=None, **_):
        self.turn += 1
        if self.turn == 1:
            return _Answer("", _calls(self.tool_name, self.args))
        return _Answer("done", [])


def _loop(mode: ApprovalMode, calls: list, *, tool_name: str = "write_file",
          args: dict | None = None, state_dir=None) -> AgentLoop:
    def handler(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "written": kwargs}

    tool = AgentTool(
        name=tool_name,
        description="writes a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler=handler,
        mutating=tool_name not in {"read_file", "status"},
        parallel_safe=tool_name in {"read_file", "status"},
    )
    return AgentLoop(
        FakeClient(tool_name, args or {"path": "out.txt"}),
        [tool],
        state_dir=state_dir,
        goal="write the file",
        policy=True,
        approval_gate=ApprovalGate(mode),
        limits=__import__("skaldir.agent_loop", fromlist=["LoopLimits"]).LoopLimits(max_steps=3),
    )


def test_approve_everything_runs_the_tool_ask_everything_does_not(tmp_path):
    """Same input, same tool, two modes — the behaviour must differ."""
    ran: list = []
    loop = _loop(ApprovalMode.APPROVE_EVERYTHING, ran, state_dir=tmp_path / "a")
    loop.run(task="write the file")
    assert ran, "Approve Everything did not execute the tool"

    ran.clear()
    loop = _loop(ApprovalMode.ASK_EVERYTHING, ran, state_dir=tmp_path / "b")
    loop.run(task="write the file")
    assert not ran, "Ask Everything executed a tool without asking"


def test_approve_for_me_lets_a_read_through_but_not_a_write(tmp_path):
    ran: list = []
    loop = _loop(ApprovalMode.APPROVE_FOR_ME, ran, tool_name="read_file",
                args={"path": "a.txt"}, state_dir=tmp_path / "read")
    loop.run(task="write the file")
    assert ran, "Approve for me did not run a read-only tool"

    ran.clear()
    loop = _loop(ApprovalMode.APPROVE_FOR_ME, ran, tool_name="write_file",
                args={"path": "a.txt"}, state_dir=tmp_path / "write")
    loop.run(task="write the file")
    assert not ran, "Approve for me ran a state-changing tool without asking"


def test_spending_is_never_auto_approved_even_by_approve_everything(tmp_path):
    ran: list = []
    loop = _loop(ApprovalMode.APPROVE_EVERYTHING, ran, tool_name="purchase",
                args={"item": "stock"}, state_dir=tmp_path / "buy")
    loop.run(task="write the file")
    assert not ran, "Approve Everything spent money without asking (APR-02)"


def test_the_refusal_of_the_judge_survives_every_mode(tmp_path):
    """A mode is not a way around a refusal — measured at the dispatch."""
    for mode in ApprovalMode:
        ran: list = []
        loop = _loop(mode, ran, tool_name="write_file", args={"path": "/etc/passwd"},
                     state_dir=tmp_path / f"refuse-{mode.value}")
        loop.run(task="write the file")
        assert not ran, f"{mode} executed a call the judge refused"
