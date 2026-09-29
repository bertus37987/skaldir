"""The repeat loop: no new model round with the same unchanged invalid call.

The user's transcript, second case:

    "run_code scheitert nach etwa 38 Sekunden mit HTTPError."
    "ein weiterer run_code scheitert nach etwa 38 Sekunden mit TypeError."

and the same shape observed live in the `timeout_s` case: the model received a bare
`ValueError: could not convert string to float: '30 Sekunden'` and then **repeated the
identical call three times**. Each round is another 38-second provider round.

These tests pin the fix: the FIRST invalid call is answered with actionable feedback,
and an UNCHANGED repeat is refused without executing anything — naming the offending
argument, the schema contract and the call that would actually be valid.
"""

from __future__ import annotations

import pytest

from skaldir.agent_loop import (
    AgentLoop,
    AgentTool,
    LoopLimits,
    ToolCall,
    ToolSet,
    Transcript,
    _argument_problem,
    _matches_type,
    _readable_number,
    _repeat_refusal,
    _repeat_signature,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "timeout_s": {"type": "number"},
    },
    "required": ["source"],
    "additionalProperties": False,
}

INVALID_CALL = {
    "id": "c1",
    "function": {
        "name": "run_code",
        "arguments": '{"source": "print(41+1)", "timeout_s": "irgendwann"}',
    },
}


@pytest.fixture()
def loop(tmp_path):
    """A loop whose tools record every call they actually execute."""
    executed: list[dict] = []

    def handler(**kw):
        executed.append(kw)
        return {"ok": True, "value": 42}

    tools = ToolSet.of([AgentTool(
        name="run_code", description="run python", input_schema=SCHEMA,
        handler=handler, parallel_safe=False, mutating=True,
    )])

    loop = AgentLoop(
        client=object(),  # never used: _run_one is driven directly
        tools=tools,
        state_dir=tmp_path / "state",
        workspace=tmp_path / "ws",
        limits=LoopLimits(max_steps=8),
    )
    return loop, executed


def _call_after(index: int) -> ToolCall:
    """The model's tool call for round ``index`` — always the same invalid one."""
    return ToolCall(
        id="c1", name="run_code", index=index,
        arguments_raw=INVALID_CALL["function"]["arguments"],
        arguments={"source": "print(41+1)", "timeout_s": "irgendwann"},
        parse_error="",
    )


# --------------------------------------------------------------------------- #
# the type contract
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,expected,ok", [
    (30, "number", True),
    (30.5, "number", True),
    (True, "number", False),     # a bool is not a number
    ("30", "number", False),
    ("text", "string", True),
    (5, "string", False),
    ([1], "array", True),
    ({}, "object", True),
    (5, "integer", True),
    (5.5, "integer", False),
    # an explicit None is a violation: the model sent a null for a required number
    (None, "number", False),
])
def test_the_type_check_matches_the_schema(value, expected, ok):
    assert _matches_type(value, expected) is ok


def test_a_type_violation_is_named_with_the_field_and_the_expected_type():
    """The root fix: a type violation is caught before the executor ever runs."""
    problem = _argument_problem(SCHEMA, {"source": "x", "timeout_s": "irgendwann"})
    assert problem == (
        "argument 'timeout_s' has type str, but the schema expects number"
    ), problem


def test_a_type_violation_is_not_reported_as_a_missing_argument():
    problem = _argument_problem(SCHEMA, {"source": "x", "timeout_s": True})
    assert "missing required" not in problem
    assert "timeout_s" in problem, problem


def test_a_valid_call_has_no_problem():
    assert _argument_problem(SCHEMA, {"source": "x", "timeout_s": 30}) == ""


def test_an_enum_violation_is_named():
    schema = {
        "type": "object",
        "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
        "required": ["mode"],
        "additionalProperties": False,
    }
    assert "must be one of" in _argument_problem(schema, {"mode": "c"})


def test_missing_required_is_still_named():
    problem = _argument_problem(SCHEMA, {"timeout_s": 30})
    assert "missing required argument(s): source" in problem, problem


def test_an_unknown_argument_is_named_with_the_allowed_set():
    problem = _argument_problem(SCHEMA, {"source": "x", "bogus": 1})
    assert "unexpected argument(s): bogus" in problem, problem


def test_a_readable_number_is_extracted_for_the_suggestion():
    assert _readable_number("30 Sekunden") == 30.0
    assert _readable_number("don't") == 30


# --------------------------------------------------------------------------- #
# the repeat signature
# --------------------------------------------------------------------------- #

def test_the_same_call_in_a_different_key_order_is_the_same_repeat():
    a = _repeat_signature("run_code", {"source": "x", "timeout_s": "irgendwann"},
                          "bad argument")
    b = _repeat_signature("run_code", {"timeout_s": "irgendwann", "source": "x"},
                          "bad argument")
    assert a == b


def test_a_changed_argument_is_not_a_repeat():
    a = _repeat_signature("run_code", {"source": "x", "timeout_s": "irgendwann"}, "bad")
    b = _repeat_signature("run_code", {"source": "x", "timeout_s": 30}, "bad")
    assert a != b


def test_a_changed_problem_is_not_a_repeat():
    a = _repeat_signature("run_code", {"source": "x"}, "missing required argument")
    b = _repeat_signature("run_code", {"source": "x"}, "unexpected argument")
    assert a != b


# --------------------------------------------------------------------------- #
# the refusal itself
# --------------------------------------------------------------------------- #

def test_the_refusal_names_the_offending_argument_and_the_valid_call():
    text = _repeat_refusal(
        "run_code", "argument timeout_s has the wrong type", 3,
        arguments={"source": "print(41+1)", "timeout_s": "irgendwann"},
        schema=SCHEMA,
    )
    assert "timeout_s" in text
    assert "str" in text and "number" in text, text
    assert "NOT executed again" in text, text
    # the corrected call is spelled out, so the model can actually use it
    assert "timeout_s=30" in text, text


def test_the_refusal_offers_to_drop_the_optional_argument():
    text = _repeat_refusal(
        "run_code", "bad", 2,
        arguments={"source": "x", "timeout_s": "irgendwann"},
        schema=SCHEMA,
    )
    assert "drop the offending argument" in text


# --------------------------------------------------------------------------- #
# end to end: the loop refuses the unchanged repeat
# --------------------------------------------------------------------------- #

class _Transcript:
    def event(self, *args, **kwargs):
        return None


def test_the_first_invalid_call_is_answered_and_refused_on_repeat(loop, tmp_path):
    """Two identical invalid calls: the first is answered, the second is NOT executed."""
    agent, executed = loop
    transcript = Transcript(tmp_path / "t1.jsonl")

    first = agent._run_one(_call_after(1), transcript, step=1, parallel=False)
    assert first.executed is False, "the first invalid call must not run either"
    assert first.decision == "invalid_arguments"
    assert first.repeat_attempt == 1

    # the unchanged identical call a second time
    second = agent._run_one(_call_after(1), transcript, step=2, parallel=False)
    assert second.decision == "repeated_invalid_arguments"
    assert second.repeat_attempt == 2
    assert second.executed is False
    assert "NOT executed again" in second.error
    assert "timeout_s" in second.error

    # nothing ran in either round
    assert executed == [], "an invalid call was executed anyway"


def test_a_corrected_call_runs_normally_after_the_repeat(loop, tmp_path):
    """The model's correction must work — the refusal is not a dead end."""
    agent, executed = loop
    transcript = Transcript(tmp_path / "t2.jsonl")

    agent._run_one(_call_after(1), transcript, step=1, parallel=False)
    refused = agent._run_one(_call_after(1), transcript, step=2, parallel=False)
    assert refused.decision == "repeated_invalid_arguments"

    corrected = ToolCall(
        id="c2", name="run_code", index=2,
        arguments_raw='{"source": "print(41+1)", "timeout_s": 30}',
        arguments={"source": "print(41+1)", "timeout_s": 30},
    )
    good = agent._run_one(corrected, transcript, step=3, parallel=False)
    assert good.executed is True, good.error
    assert executed, "the corrected call never reached the handler"


def test_a_different_tool_is_not_treated_as_a_repeat(loop, tmp_path):
    """The signature includes the tool name."""
    agent, executed = loop
    transcript = Transcript(tmp_path / "t3.jsonl")
    agent._run_one(_call_after(1), transcript, step=1, parallel=False)
    other = ToolCall(
        id="c9", name="terminal", index=2,
        arguments_raw='{"command": "echo hi"}', arguments={"command": "echo hi"},
    )
    outcome = agent._run_one(other, transcript, step=2, parallel=False)
    assert outcome.decision != "repeated_invalid_arguments"
