"""The `timeout_s` chain: schema → received → validation → executor → feedback.

The user's transcript showed what breaks in between:

    "run_code scheitert nach etwa 38 Sekunden mit HTTPError."
    "ein weiterer run_code scheitert nach etwa 38 Sekunden mit TypeError."

and then: *"ein weiterer run_code"*. The model received a raw
``ValueError: could not convert string to float: '30 Sekunden'`` — a message with no
field, no expected type and no next step — and repeated the identical call three times.

These tests pin the whole chain instead of one layer of it.
"""

from __future__ import annotations

import pytest

from skaldir.code_exec_tool import CODE_INPUT_SCHEMA, _coerce_number, run_code_tool


def _tool(tmp_path):
    return run_code_tool(workspace=tmp_path, runtime=None, policy=None)


# --------------------------------------------------------------------------- #
# the schema that is actually sent
# --------------------------------------------------------------------------- #

def test_the_schema_declares_timeout_s_as_an_optional_number():
    props = CODE_INPUT_SCHEMA["properties"]
    assert props["timeout_s"]["type"] == "number", props["timeout_s"]
    # optional — the runner has its own default
    assert "timeout_s" not in CODE_INPUT_SCHEMA.get("required", [])
    assert "source" in CODE_INPUT_SCHEMA.get("required", [])


# --------------------------------------------------------------------------- #
# the single type violation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected_seconds", [
    ("30 Sekunden", 30.0),
    ("30s", 30.0),
    ("30 s", 30.0),
    ("2 minutes", 120.0),
    ("500ms", 0.5),
    ("30", 30.0),
    (30, 30.0),
    (2.5, 2.5),
])
def test_a_readable_duration_is_recovered_not_rejected(raw, expected_seconds):
    """A number with a stray unit is the model's typo, not a refusal."""
    assert _coerce_number(raw, "timeout_s") == expected_seconds


@pytest.mark.parametrize("raw", [
    "irgendwann", "so_bald_wie_möglich", "30 bitte", "", "  ",
    "viele", "30e", None,
])
def test_unreadable_text_is_a_named_error_not_a_raw_raise(raw):
    out = _coerce_number(raw, "timeout_s")
    assert isinstance(out, dict), f"{raw!r} was silently accepted"
    assert out["ok"] is False
    assert out["error"] == "invalid_argument"
    assert out["field"] == "timeout_s"


def test_readable_durations_are_recovered_but_unreadable_text_is_not():
    """"30 Sekunden" is the model's typo; "irgendwann" is not a number at all."""
    assert _coerce_number("30 Sekunden", "timeout_s") == 30.0
    out = _coerce_number("irgendwann", "timeout_s")
    assert isinstance(out, dict)
    assert out["field"] == "timeout_s"
    assert "number" in out["expected"]
    assert "usage_hint" in out
    assert "did_you_mean" in out["usage_hint"]


def test_a_boolean_is_not_a_duration():
    out = _coerce_number(True, "timeout_s")
    assert isinstance(out, dict) and out["error"] == "invalid_argument"


def test_a_negative_timeout_is_refused_with_a_reason():
    out = _coerce_number(-5, "timeout_s")
    assert isinstance(out, dict) and out["error"] == "invalid_argument"
    assert "greater than zero" in out["reason"]


# --------------------------------------------------------------------------- #
# the tool: no raw raise, ever
# --------------------------------------------------------------------------- #

def test_the_tool_never_raises_on_a_bad_argument(tmp_path):
    """The measured failure was an uncaught ``ValueError`` at the argument stage."""
    tool = _tool(tmp_path)
    out = tool.handler(source="result = 1", timeout_s="irgendwann")
    assert out["ok"] is False
    assert out["error"] == "invalid_argument"
    assert out["field"] == "timeout_s"
    # and the reason a model can use, not a bare exception name
    assert "usage_hint" in out or "expected" in out


def test_a_readable_duration_reaches_the_executor(tmp_path):
    tool = _tool(tmp_path)
    out = tool.handler(source="result = 6 * 7", timeout_s="30 Sekunden")
    assert out["ok"] is True, out
    # the runner returns the value of `result` (measured: `value`, not `result`)
    assert out["value"] == 42, out


def test_a_corrected_call_runs_normally_after_correction(tmp_path):
    """The acceptance: "korrigierte Aufrufe muessen anschliessend normal funktionieren"."""
    tool = _tool(tmp_path)
    bad = tool.handler(source="result = 1 + 1", timeout_s="irgendwann")
    assert bad["ok"] is False
    good = tool.handler(source="result = 1 + 1", timeout_s=30)
    assert good["ok"] is True
    assert good["value"] == 2


def test_the_same_unchanged_argument_gets_the_same_answer_not_a_second_run(tmp_path):
    """No new model round with the same unchanged invalid call."""
    tool = _tool(tmp_path)
    first = tool.handler(source="result = 1", timeout_s="irgendwann")
    second = tool.handler(source="result = 1", timeout_s="irgendwann")
    assert first == second
    assert first["error"] == second["error"] == "invalid_argument"
    # the code never ran, so no work happened twice
    assert first.get("result") is None and second.get("result") is None


# --------------------------------------------------------------------------- #
# the executor stage is still exercised
# --------------------------------------------------------------------------- #

def test_a_timeout_that_is_too_small_is_reported_as_a_timeout(tmp_path):
    """A real timeout must still be a timeout — not widened into success or silence."""
    tool = _tool(tmp_path)
    out = tool.handler(source="import time\ntime.sleep(5)", timeout_s=1)
    assert out["ok"] is False
    # the runner names the cause (measured contract: `timed_out_after_s` is set)
    assert out.get("timed_out_after_s") == 1.0, out
    assert out.get("duration_ms") >= 900, out


# --------------------------------------------------------------------------- #
# ACT/SKILL_ACT contract
# --------------------------------------------------------------------------- #

def test_the_act_schema_and_the_dispatch_agree():
    """The ACT contract: what the provider sends and what the executor accepts."""
    from skaldir import agent_toolset

    tools = agent_toolset.build_agent_toolset(
        workspace="/tmp", runtime=None, project_id="p", kind="builtin",
    )
    names = {t.name for t in tools.tools}
    for expected in ("skill_act", "run_code", "terminal", "skill_expand",
                     "skill_expand_build"):
        assert expected in names, sorted(names)
    act = next(t for t in tools.tools if t.name == "skill_act")
    assert act.input_schema.get("type") == "object", act.input_schema
    # an unknown action must be refused by name, not by a silent no-op
    out = act.handler(action="definitely_not_an_action")
    assert out.get("ok") is False
    assert "action" in str(out).lower()


def test_the_run_code_schema_is_number_typed_end_to_end():
    """Provider schema -> validation stay consistent: a number is the contract."""
    from skaldir import agent_toolset

    tools = agent_toolset.build_agent_toolset(
        workspace="/tmp", runtime=None, project_id="p", kind="builtin",
    )
    run_code = next(t for t in tools.tools if t.name == "run_code")
    schema = run_code.input_schema
    assert schema["properties"]["timeout_s"]["type"] == "number", schema
    assert schema["properties"]["source"]["type"] == "string", schema
