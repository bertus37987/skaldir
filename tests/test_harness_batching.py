"""HARNESS batching/streaming — the behaviors behind VAL-HARNESS-005/006/007.

The loop offers several tool calls per model answer; the harness decides how they
run. Pinned here, behavior by behavior:

* **Discovery keeps its valid schemas** (VAL-HARNESS-005): argument validation
  rejects an invalid call *before any side effect* — the file is never written.
* **Parallel with dependencies** (VAL-HARNESS-006): three read-only calls of
  different durations overlap in time (measured with real timestamps); a
  Write→Read pair runs ordered, so the read sees what the write put there.
* **Streaming per call** (VAL-HARNESS-007): one completed call's result reaches
  the caller while its slow sibling is still running — never collected at the end
  of the batch.
* **Partial failure**: one call of three failing does not destroy the other two
  results; they are preserved and reported to the model.
* **Cancellation mid-batch**: stopping the run means no new call starts; the
  side effects that did execute happen exactly once.
* **Result states** (TOOL-03 class): completed is not the same as success — a
  tool's own ``exit_code`` survives the transport ``ok``; a write over an
  existing file reports ``created: false`` as ``updated``.
* **Provider errors recover bounded and state-aware**: 429 recovers through the
  shared retry, then fails with a *named* state when bounded; an auth refusal is
  a different state (needs_credentials) than a provider error; a stream abort
  ends the run instead of burning rounds.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from skaldir.agent_loop import (
    EXIT_BLOCKED,
    EXIT_NEEDS_CREDENTIALS,
    REASON_CANCELLED,
    REASON_NEEDS_CREDENTIALS,
    REASON_PROVIDER_ERROR,
    STATUS_ANSWERED,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    AgentLoop,
    AgentTool,
    LoopLimits,
    ToolOutcome,
    ToolSet,
    builtin_tools,
    read_transcript,
)
from skaldir.llm import LLMError
from tests.test_llm_provider import FAKE_KEY, Step, StubProvider, http_error, ok


def _config(stub: StubProvider, **changes: Any):
    from skaldir.llm_provider import ProviderConfig

    base = ProviderConfig(
        provider="openai-compatible",
        base_url=stub.base_url,
        model="stub-model",
        timeout_s=10.0,
        max_retries=0,
        api_key=FAKE_KEY,
    )
    return ProviderConfig(**{**base.__dict__, **changes}) if changes else base


def _llm(stub: StubProvider, **changes: Any):
    from skaldir.llm_provider import OpenAICompatibleLLM

    return OpenAICompatibleLLM(_config(stub, **changes))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "runtime" / "agent"
    directory.mkdir(parents=True)
    return directory


def answer(text: str) -> dict[str, Any]:
    from tests.test_agent_loop import answer as _answer

    return _answer(text)


def tool_step(*calls: tuple[str, str, Any], text: str = "") -> dict[str, Any]:
    from tests.test_agent_loop import tool_step as _tool_step

    return _tool_step(*calls, text=text)


#: (name, phase, monotonic timestamp, worker thread id) — the measurement data.
Log = list[tuple[str, str, float, int]]


def _timed_tool(
    name: str,
    delay: float,
    log: Log,
    *,
    parallel_safe: bool = True,
    effect: Any = None,
) -> AgentTool:
    """A sleep-then-act tool that records (name, phase, t, thread) around the sleep."""

    def handler(**_kwargs: Any) -> dict[str, Any]:
        start = time.monotonic()
        log.append((name, "start", start, threading.get_ident()))
        time.sleep(delay)
        ran = effect() if effect is not None else True
        end = time.monotonic()
        log.append((name, "end", end, threading.get_ident()))
        return {"tool": name, "ran": bool(ran)}

    return AgentTool(
        name=name,
        description=f"a {delay:.2f}s call ({name})",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
        parallel_safe=parallel_safe,
        mutating=not parallel_safe,
    )


def _intervals(log: Log, name: str) -> tuple[float, float]:
    starts = [record[2] for record in log if record[0] == name and record[1] == "start"]
    ends = [record[2] for record in log if record[0] == name and record[1] == "end"]
    assert starts and ends, f"{name} never measured: {log}"
    return starts[0], ends[0]


def _result_for(body: dict[str, Any], call_id: str) -> dict[str, Any]:
    """The parsed ``role: tool`` payload fed back for one call id."""
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            return json.loads(str(message.get("content") or ""))
    raise AssertionError(f"no tool result for call {call_id!r}")


def _named(calls: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [call for call in calls if call["name"] == name]
    assert matches, f"{name} missing among {[c['name'] for c in calls]}"
    return matches[0]


# --------------------------------------------------------------------------- #
# VAL-HARNESS-006: parallel batches with dependencies                          #
# --------------------------------------------------------------------------- #


def test_three_independent_calls_of_different_durations_overlap_in_time(
    workspace: Path, state: Path
):
    """Three read-only calls (0.3/0.2/0.1 s) really overlap — with timestamps."""
    log: Log = []
    steps = [
        tool_step(
            ("c_a", "read_a", {}),
            ("c_b", "read_b", {}),
            ("c_c", "read_c", {}),
        ),
        answer("all three came back"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(
                [
                    _timed_tool("read_a", 0.30, log),
                    _timed_tool("read_b", 0.20, log),
                    _timed_tool("read_c", 0.10, log),
                ]
            ),
            state_dir=state,
            workspace=workspace,
        )
        started = time.monotonic()
        result = loop.run("read three things in one batch")
        elapsed = time.monotonic() - started

    assert result.status == STATUS_ANSWERED, result.message
    a0, a1 = _intervals(log, "read_a")
    b0, b1 = _intervals(log, "read_b")
    c0, _ = _intervals(log, "read_c")
    # every pair of intervals overlaps — they really run side by side
    assert b0 < a1, "b started only after a ended — the batch ran serially"
    assert c0 < a1, "c started only after a ended — the batch ran serially"
    assert c0 < b1, "c started only after b ended — the batch ran serially"
    # three different worker threads: separate workers, not one reused thread
    starters = {record[3] for record in log if record[1] == "start"}
    assert len(starters) == 3, starters
    # overlap saves the serial time: about max(delays) + margin, never their sum
    assert elapsed < 0.55, f"elapsed {elapsed:.3f}s — the batch did not overlap"
    by_call = {call["name"]: call for call in read_transcript(result.transcript_path)["tool_calls"]}
    for name in ("read_a", "read_b", "read_c"):
        assert by_call[name]["parallel"] is True, name
        assert by_call[name]["ok"] is True, name


def test_write_then_read_pair_runs_ordered_and_the_read_sees_the_write(
    workspace: Path, state: Path
):
    """Dependency sequencing: the read runs after the write it depends on."""
    steps = [
        tool_step(
            ("c_w", "write_text", {"path": "ordered.txt", "text": "written first"}),
            ("c_r", "read_text", {"path": "ordered.txt"}),
        ),
        answer("the pair executed in order"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("write then read what you wrote")

    assert result.status == STATUS_ANSWERED, result.message
    read_payload = _result_for(stub.requests[1]["body"], "c_r")
    assert read_payload["ok"] is True
    assert read_payload["result"]["text"] == "written first"

    calls = read_transcript(result.transcript_path)["tool_calls"]
    write_call, read_call = _named(calls, "write_text"), _named(calls, "read_text")
    assert write_call["seq"] < read_call["seq"], calls
    assert write_call["executed"] and read_call["executed"]
    assert write_call["parallel"] is False  # a write is never scheduled beside a sibling


# --------------------------------------------------------------------------- #
# streaming per call (VAL-HARNESS-007) + partial failure                       #
# --------------------------------------------------------------------------- #


def test_results_stream_per_call_as_they_complete_not_collected_at_the_end(
    workspace: Path, state: Path
):
    """The fast call's result arrives while the slow sibling is still running."""
    log: Log = []
    receipts: list[tuple[str, float, bool]] = []

    with StubProvider(
        [
            Step(body=tool_step(("c_s", "slow_tool", {}), ("c_f", "fast_tool", {}))),
            Step(body=answer("got both")),
        ]
    ) as stub:

        def on_result(outcome: ToolOutcome) -> None:
            receipts.append((outcome.name, time.monotonic(), outcome.ok))

        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(
                [
                    _timed_tool("slow_tool", 0.30, log),
                    _timed_tool("fast_tool", 0.05, log),
                ]
            ),
            state_dir=state,
            workspace=workspace,
            on_result=on_result,
        )
        result = loop.run("run one fast and one slow call")

    assert result.status == STATUS_ANSWERED, result.message
    _, slow_end = _intervals(log, "slow_tool")
    assert [name for name, _, _ in receipts] == ["fast_tool", "slow_tool"], receipts
    assert receipts[0][1] < slow_end, (
        "the fast call's result was collected only at the end of the batch"
    )
    assert receipts[0][2] is True and receipts[1][2] is True


def test_one_failing_call_of_three_preserves_the_other_results(
    workspace: Path, state: Path
):
    """A failing call does not destroy the batch: the other two arrive intact."""
    def fail() -> dict[str, Any]:
        raise RuntimeError("boom in tool b")

    marker: dict[str, Any] = {"kept": True}
    log: Log = []
    steps = [
        tool_step(
            ("c_keep1", "tool_keep1", {}),
            ("c_fail", "tool_fail", {}),
            ("c_keep2", "tool_keep2", {}),
        ),
        answer("reported the failure"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(
                [
                    _timed_tool("tool_keep1", 0.02, log),
                    _timed_tool("tool_fail", 0.02, log, effect=fail),
                    _timed_tool("tool_keep2", 0.02, log, effect=lambda: marker.pop("kept")),
                ]
            ),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("three calls, one fails")

    assert result.status == STATUS_ANSWERED, result.message
    body = stub.requests[1]["body"]
    keep1 = _result_for(body, "c_keep1")
    keep2 = _result_for(body, "c_keep2")
    failed = _result_for(body, "c_fail")
    assert keep1["ok"] is True and keep1["result"]["ran"] is True
    assert keep2["ok"] is True and keep2["result"]["ran"] is True
    assert failed["ok"] is False and "boom" in failed["error"]
    transcript = read_transcript(result.transcript_path)
    by_name = {c["name"]: c for c in transcript["tool_calls"]}
    assert by_name["tool_fail"]["ok"] is False
    assert by_name["tool_keep1"]["ok"] is True
    assert by_name["tool_keep2"]["ok"] is True
    # all three carry a digest — a failed call is recorded, never dropped
    for call in transcript["tool_calls"]:
        assert call["result_sha256"]


# --------------------------------------------------------------------------- #
# cancellation mid-batch                                                       #
# --------------------------------------------------------------------------- #


def test_cancel_during_a_batch_stops_new_starts_and_side_effects_run_once(
    workspace: Path, state: Path
):
    """Cancellation mid-batch: no new call starts; the executed effect is not re-run."""
    cancel_event = threading.Event()
    log: Log = []
    effects: list[str] = []

    def stopper_effect() -> bool:
        # the side effect of the first call: happens exactly once, then cancels
        effects.append("one")
        (workspace / "side-one.txt").write_text("once", encoding="utf-8")
        cancel_event.set()
        return True

    def late_effect() -> bool:
        effects.append("late")
        return True

    steps = [
        tool_step(
            ("c1", "stopper", {}),
            ("c2", "phase_two", {}),
            ("c3", "phase_two", {}),
        ),
        answer("never reached — the stop ends the run first"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(
                [
                    _timed_tool("stopper", 0.15, log, effect=stopper_effect),
                    _timed_tool("phase_two", 0.15, log, parallel_safe=False,
                                effect=late_effect),
                ]
            ),
            state_dir=state,
            workspace=workspace,
            cancel_event=cancel_event,
        )
        result = loop.run("run a batch and stop it halfway")

    assert result.status == STATUS_INCOMPLETE
    assert result.reason == REASON_CANCELLED
    assert result.answer is None
    assert result.exit_code == EXIT_BLOCKED
    # the first call's side effect happened exactly once and was never restarted
    assert effects == ["one"], effects
    assert (workspace / "side-one.txt").read_text(encoding="utf-8") == "once"
    # the mutating calls after the stop never started
    assert not (workspace / "side-two.txt").exists()
    assert not (workspace / "side-three.txt").exists()
    transcript = read_transcript(result.transcript_path)
    stop_call = _named(transcript["tool_calls"], "stopper")
    assert stop_call["executed"] is True
    phases = [c for c in transcript["tool_calls"] if c["name"] == "phase_two"]
    assert len(phases) == 2
    for call in phases:
        assert call["executed"] is False
        assert "cancel" in str(call["error"]).lower()
    assert len(stub.requests) == 1  # the stop held before the next model request


# --------------------------------------------------------------------------- #
# argument validation before side effects (VAL-HARNESS-005)                     #
# --------------------------------------------------------------------------- #


def test_invalid_arguments_are_rejected_before_any_side_effect(
    workspace: Path, state: Path
):
    """An invalid call never runs: whatever its shape, no file appears."""
    effects: list[str] = []

    def side_effect(path: str) -> dict[str, Any]:
        effects.append(path)
        (workspace / str(path)).write_text(path, encoding="utf-8")
        return {"wrote": path}

    side_tool = AgentTool(
        name="side_tool",
        description="writes a file when it runs",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=side_effect,
        parallel_safe=False,
        mutating=True,
    )
    steps = [
        tool_step(
            ("c_bad", "side_tool", {"path": 7}),      # wrong type
            ("c_missing", "side_tool", {}),           # missing required
        ),
        answer("both refused without touching anything"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of([side_tool]),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("write with a wrong argument type")

    assert result.status == STATUS_ANSWERED, result.message
    assert effects == [], effects
    assert not list(workspace.glob("*.txt"))
    calls = read_transcript(result.transcript_path)["tool_calls"]
    bad = [c for c in calls if c["call_id"] == "c_bad"][0]
    missing = [c for c in calls if c["call_id"] == "c_missing"][0]
    for call in (bad, missing):
        assert call["executed"] is False
        assert call["decision"] == "invalid_arguments"
    assert "type" in bad["error"] and "expects string" in bad["error"]
    assert "missing required" in missing["error"]


# --------------------------------------------------------------------------- #
# the result states: completed != success (TOOL-03 class)                       #
# --------------------------------------------------------------------------- #


def test_exit_code_is_a_field_of_the_result_not_a_renamed_ok(workspace: Path, state: Path):
    """ok is transport; exit_code is the command's own number — 7 stays 7."""
    reporting_tool = AgentTool(
        name="status_report",
        description="reports transport ok with a real exit code",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=lambda: {"ok": True, "exit_code": 7, "stderr": "the command failed"},
    )
    refusing_tool = AgentTool(
        name="status_refuse",
        description="reports ok false with a real exit code",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=lambda: {"ok": False, "exit_code": 2, "error": "bad usage"},
    )
    plain_tool = AgentTool(
        name="status_plain",
        description="a normal success",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=lambda: {"value": 1},
    )
    steps = [
        tool_step(
            ("c_ok7", "status_report", {}),
            ("c_ok2", "status_refuse", {}),
            ("c_plain", "status_plain", {}),
        ),
        answer("both states recorded"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of([reporting_tool, refusing_tool, plain_tool]),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("record the real exit codes")

    assert result.status == STATUS_ANSWERED, result.message
    calls = read_transcript(result.transcript_path)["tool_calls"]
    assert _named(calls, "status_report")["exit_code"] == 7
    assert _named(calls, "status_report")["ok"] is True  # transport ok, exit 7 stays 7
    assert _named(calls, "status_refuse")["exit_code"] == 2
    assert _named(calls, "status_refuse")["ok"] is False
    assert _named(calls, "status_plain")["exit_code"] == 0


def test_write_text_reports_created_and_updated_state(workspace: Path, state: Path):
    """A write to an existing path is an update: created:false means updated."""
    steps = [
        tool_step(("c_first", "write_text", {"path": "state.txt", "text": "one"})),
        tool_step(("c_second", "write_text", {"path": "state.txt", "text": "two"})),
        answer("both writes recorded"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("write twice to the same file")

    assert result.status == STATUS_ANSWERED, result.message
    first = _result_for(stub.requests[1]["body"], "c_first")
    second = _result_for(stub.requests[2]["body"], "c_second")
    assert first["result"]["created"] is True and first["result"]["state"] == "created"
    assert second["result"]["created"] is False and second["result"]["state"] == "updated"
    assert (workspace / "state.txt").read_text(encoding="utf-8") == "two"


# --------------------------------------------------------------------------- #
# provider errors recover bounded and state-aware (VAL-HARNESS-007)             #
# --------------------------------------------------------------------------- #


def test_a_429_recovers_through_the_shared_retry_and_the_run_answers(
    workspace: Path, state: Path
):
    """429 → bounded retry inside the client (§43) → the answer, inside one run."""
    with StubProvider([http_error(429, "slow down"), ok("after retry")]) as stub:
        loop = AgentLoop(
            _llm(stub, max_retries=1, backoff_s=0.01),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("ask and retry once")
    assert result.status == STATUS_ANSWERED, result.message
    assert result.answer == "after retry"
    assert stub.request_count == 2


def test_a_429_exhausts_to_a_named_provider_error_not_an_endless_loop(
    workspace: Path, state: Path
):
    """Exhausted 429 → failed/provider_error, bounded attempts, the status is named."""
    with StubProvider([http_error(429, "rate limited")]) as stub:
        loop = AgentLoop(
            _llm(stub, max_retries=1, backoff_s=0.01),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("ask against a rate limit")
    assert result.status == STATUS_FAILED
    assert result.reason == REASON_PROVIDER_ERROR
    assert "429" in result.message
    assert stub.request_count == 2  # initial + one bounded retry — no endless loop
    assert result.exit_code == EXIT_BLOCKED


def test_an_auth_refusal_is_a_state_of_its_own_not_a_provider_voice(
    workspace: Path, state: Path
):
    """401 → needs_credentials, not provider_error: the caller can react differently."""
    with StubProvider([http_error(401, "wrong key")]) as stub:
        loop = AgentLoop(
            _llm(stub, max_retries=0),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("ask against a bad key")
    assert result.status == STATUS_FAILED
    assert result.reason == REASON_NEEDS_CREDENTIALS
    assert result.exit_code == EXIT_NEEDS_CREDENTIALS
    assert "401" in result.message
    assert stub.request_count == 1  # an auth refusal is never retried (§43)


def test_a_stream_abort_fails_bounded_and_state_aware(workspace: Path, state: Path):
    """A truncated stream is one bounded provider error — no hang, no extra rounds."""
    calls = {"n": 0}

    class AbortingStreamClient:
        name = "aborting-stream"

        def chat(self, messages: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if kwargs.get("stream"):
                raise LLMError("stream aborted by the provider mid-answer (RemoteProtocolError)")
            raise AssertionError("this client only ever streams")

    loop = AgentLoop(
        AbortingStreamClient(),
        builtin_tools(workspace),
        state_dir=state,
        workspace=workspace,
        limits=LoopLimits(max_steps=5),
        stream=True,
    )
    result = loop.run("read a stream that dies mid-answer")

    assert result.status == STATUS_FAILED
    assert result.reason == REASON_PROVIDER_ERROR
    assert "RemoteProtocolError" in result.message
    assert calls["n"] == 1  # one attempt, no retry storm
    assert result.answer is None
