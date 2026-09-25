"""Shared fixtures for the ``jevcomp`` tests (port of fast-jev-compaction).

One transcript, one wire form, one stage walker. The transcript lives in its
wire form so ``tests/test_jevcomp_oracle.py`` can hand the *same* data to the
upstream TypeScript implementation through Node and diff the results.
"""

from __future__ import annotations

from typing import Any

from skaldir.jevcomp import (
    Message,
    ToolCall,
    ToolResult,
    ToolUse,
    collect_tool_calls,
    fit_state,
)
from skaldir.jevcomp.errors import StateFittingError

#: Two "files" of 1000 characters each, as a Read tool reports them.
FILE_A = "export const a = 1;\n" * 50
FILE_B = "export const b = 2;\n" * 50

#: The upstream test transcript: a user instruction with a "never edit X" constraint,
#: two Reads, a failing test run and a short follow-up.
TRANSCRIPT_WIRE: list[dict[str, Any]] = [
    {
        "role": "user",
        "text": "Never edit anything under src/generated. Fix the failing test.",
        "toolUses": [],
    },
    {
        "role": "assistant",
        "text": "",
        "toolUses": [
            {
                "tool_use_id": "tool-1",
                "tool": "Read",
                "input": {"file_path": "src/a.ts"},
                "text": FILE_A,
            }
        ],
    },
    {
        "role": "user",
        "text": "",
        "toolUses": [],
        "toolResults": [{"tool_use_id": "tool-1", "text": FILE_A}],
    },
    {"role": "assistant", "text": "a.ts looks fine; checking b.ts", "toolUses": []},
    {
        "role": "assistant",
        "text": "",
        "toolUses": [
            {
                "tool_use_id": "tool-2",
                "tool": "Read",
                "input": {"file_path": "src/b.ts"},
                "text": FILE_B,
            }
        ],
    },
    {
        "role": "user",
        "text": "",
        "toolUses": [],
        "toolResults": [{"tool_use_id": "tool-2", "text": FILE_B}],
    },
    {
        "role": "assistant",
        "text": "",
        "toolUses": [
            {
                "tool_use_id": "tool-3",
                "tool": "Bash",
                "input": {"command": "npm test"},
                "text": "FAIL b.test.ts",
            }
        ],
    },
    {
        "role": "user",
        "text": "",
        "toolUses": [],
        "toolResults": [
            {"tool_use_id": "tool-3", "text": "FAIL b.test.ts: expected 2 to be 3", "isError": True}
        ],
    },
    {"role": "assistant", "text": "The failure is in b.test.ts; fixing now.", "toolUses": []},
    {"role": "user", "text": "go ahead", "toolUses": []},
]


def transcript() -> list[Message]:
    """The shared transcript as ``Message`` objects (fresh copies every call)."""
    return [Message.from_any(dict(entry)) for entry in TRANSCRIPT_WIRE]


def wire_transcript() -> list[dict[str, Any]]:
    """The shared transcript in wire form (JSON-ready, same data as ``transcript()``)."""
    import json

    return json.loads(json.dumps(TRANSCRIPT_WIRE))


def call_ids(calls: list[ToolCall]) -> list[str]:
    return [call.id for call in calls]


def state_tokens(messages: list[Message], preserve_recent_messages: int = 1, goal: str = "") -> int:
    """Estimated tokens of the fitted state for a transcript."""
    calls = collect_tool_calls(messages, preserve_recent_messages)
    return fit_state(
        messages,
        calls,
        {
            "maxStateTokens": 10**7,
            "preserveRecentMessages": preserve_recent_messages,
            "goal": goal,
        },
    ).tokens


def write_fixture() -> list[Message]:
    """A Write with a 5000-character body: the state only fits once inputs shrink."""
    return [
        Message(role="user", text="start"),
        Message(
            role="assistant",
            tool_uses=[
                ToolUse("w", "Write", {"file_path": "x.ts", "content": "x" * 5000}, text="ok")
            ],
        ),
        Message(role="user", tool_results=[ToolResult("w", "ok")]),
        Message(role="assistant", text="written"),
    ]


def long_texts(count: int = 4) -> list[Message]:
    """Alternating messages with long texts (abridge and collapse stages)."""
    return [
        Message(
            role="user" if index % 2 == 0 else "assistant",
            text=f"{index} " + "lorem ipsum " * 300,
        )
        for index in range(count)
    ]


def short_texts(count: int = 100) -> list[Message]:
    """Many short text messages: only leaving the old ones out makes the state fit."""
    return [
        Message(role="user" if index % 2 == 0 else "assistant", text=f"note {index} " + "a" * 10)
        for index in range(count)
    ]


def call_chain(count: int = 40) -> list[Message]:
    """``count`` Read calls with one-character results and no long texts."""
    messages = [Message(role="user", text="start")]
    for index in range(count):
        messages.append(
            Message(
                role="assistant",
                tool_uses=[
                    ToolUse(
                        f"c{index}", "Read", {"file_path": f"/repo/src/module-{index}.ts"}, text="x"
                    )
                ],
            )
        )
        messages.append(Message(role="user", tool_results=[ToolResult(f"c{index}", "x")]))
    messages.append(Message(role="assistant", text="done"))
    return messages


def walk_stages(
    messages: list[Message], preserve_recent_messages: int = 1, goal: str = "g", limit: int = 60
) -> list[str]:
    """The fitting stages a transcript walks through as the budget shrinks.

    Starts with an unlimited budget and repeats with ``tokens - 1``, so the
    returned list is exactly the stage chain the algorithm produces, ending in
    ``"THROW"`` once the history cannot be fitted at all.
    """
    calls = collect_tool_calls(messages, preserve_recent_messages)
    stages: list[str] = []
    budget = 10**7
    for _ in range(limit):
        try:
            fitted = fit_state(
                messages,
                calls,
                {
                    "maxStateTokens": budget,
                    "preserveRecentMessages": preserve_recent_messages,
                    "goal": goal,
                },
            )
        except StateFittingError:
            stages.append("THROW")
            break
        stages.append(fitted.stage)
        budget = fitted.tokens - 1
        if budget < 20:
            break
    return stages


def first_stage(stages: list[str]) -> list[str]:
    """Collapse consecutive duplicates, to assert the *order* of the stages."""
    ordered: list[str] = []
    for stage in stages:
        if not ordered or ordered[-1] != stage:
            ordered.append(stage)
    return ordered
