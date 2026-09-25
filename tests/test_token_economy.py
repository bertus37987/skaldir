"""TOKEN-ECONOMY + PROGRESS-GUARD (§94) — the four quick wins, each really exercised.

The module under test is three small ones plus the seams they were wired into:

1. **durable progress signals** — ``ProgressSignals`` written into the real ``runs``
   table and read back, so a resumed run knows it was stuck (``runs_store``);
2. **no-progress as a runtime decision** — the four conditions, in one place
   (``progress_guard.ProgressGuard``), acting instead of logging;
3. **tool-result archiving** — full result to a content-addressed artifact, id +
   digest + summary in the prompt, artifact retrievable **byte-identically**
   (``tool_archive.ToolArchive`` through a real ``AgentLoop`` run);
4. **retry only before the visible commit point** — ``RetryGate`` consulted by the
   provider's own attempt loop, and the refusal named on the record.

Plus the savings block the owner asked for: per-mechanism, never one combined number.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_agent_loop import StubEndpoint, answer, tool_step

from skaldir import telemetry
from skaldir.agent_loop import AgentLoop, AgentTool, LoopLimits, ToolSet
from skaldir.progress_guard import (
    ACTION_CONTINUE,
    ACTION_STOP,
    ACTION_STRATEGY_CHANGE,
    CommitPoint,
    ProgressGuard,
    RetryGate,
    TurnObservation,
    reply_fingerprint,
)
from skaldir.runs_store import RunsStore
from skaldir.tool_archive import ToolArchive


def _client(base_url: str):
    """A real provider client pointed at the loopback stub (no network, no key)."""
    from skaldir.llm_provider import provider_for

    return provider_for(
        {
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "stub-token-economy",
            "api_key": "stub-local-key",
        }
    )


# --------------------------------------------------------------------------- #
# 1 — durable progress signals                                                 #
# --------------------------------------------------------------------------- #


def test_progress_signals_survive_the_store(tmp_path: Path):
    """The signals land in the ``runs`` row and read back — a resumed run is not blind."""
    store = RunsStore(str(tmp_path / "runs.db"))
    try:
        supervisor = store.supervisor()
        assert supervisor is not None
        run = supervisor.create_run("s1", state="ACTIVE")
        guard = ProgressGuard()
        first = guard.observe(TurnObservation(turn=1, answer_text="nothing changed"))
        assert first.action == ACTION_CONTINUE
        assert store.record_progress(run.run_id, decision=first) is True

        stalled = guard.observe(
            TurnObservation(turn=2, answer_text="NOTHING   changed", verification="INCOMPLETE")
        )
        assert stalled.action == ACTION_STRATEGY_CHANGE
        assert store.record_progress(run.run_id, decision=stalled) is True

        back = store.progress(run.run_id)
        assert back is not None
        assert back.turn == 2
        assert back.no_tool_streak == 2
        assert back.reply_fingerprint == reply_fingerprint("nothing changed")
        assert back.last_action == ACTION_STRATEGY_CHANGE
        assert back.last_signal == "pattern_no_new_evidence"
    finally:
        store.close()


def test_the_progress_columns_are_really_in_the_runs_table(tmp_path: Path):
    """Not just on the dataclass: the sqlite table carries them (the durable part)."""
    import sqlite3

    store = RunsStore(str(tmp_path / "runs.db"))
    store.close()
    con = sqlite3.connect(tmp_path / "runs.db")
    try:
        columns = {row[1] for row in con.execute("PRAGMA table_info(runs)")}
    finally:
        con.close()
    assert {
        "turn",
        "no_progress_streak",
        "no_tool_streak",
        "last_verification",
        "reply_fingerprint",
        "progress_action",
        "progress_signal",
        "tokens_used",
        "token_budget",
    } <= columns


# --------------------------------------------------------------------------- #
# 2 — no-progress is a decision                                                #
# --------------------------------------------------------------------------- #


def _stall(turn: int, text: str = "everything is done", **kw) -> TurnObservation:
    return TurnObservation(turn=turn, answer_text=text, **kw)


def test_the_four_conditions_are_each_required():
    """Same answer + no tools + no evidence + no state delta — all four, or not stuck."""
    guard = ProgressGuard()
    guard.observe(_stall(1))
    decision = guard.observe(_stall(2))
    assert decision.stuck is True
    assert decision.conditions == {
        "same_answer": True,
        "no_tool_calls": True,
        "no_new_evidence": True,
        "no_state_delta": True,
    }

    # ...and breaking any single one of them is progress, not a stall
    for label, observation in (
        ("a different answer", TurnObservation(turn=2, answer_text="a new idea entirely")),
        ("a tool that ran", TurnObservation(turn=2, answer_text="everything is done",
                                            tool_calls=("read_file",), executed=1)),
        ("new evidence", TurnObservation(turn=2, answer_text="everything is done",
                                         evidence="pytest:exit=1")),
        ("a state delta", TurnObservation(turn=2, answer_text="everything is done",
                                          state="revision:3")),
    ):
        fresh = ProgressGuard()
        fresh.observe(_stall(1))
        out = fresh.observe(observation)
        assert out.stuck is False, label
        assert out.action == ACTION_CONTINUE, label


def test_a_blank_answer_never_matches_another_blank_answer():
    """``""`` must not fingerprint to a digest — "said nothing" is not "said the same"."""
    assert reply_fingerprint("") == ""
    assert reply_fingerprint("   \n ") == ""
    guard = ProgressGuard()
    guard.observe(TurnObservation(turn=1, answer_text=""))
    assert guard.observe(TurnObservation(turn=2, answer_text="")).stuck is False


def test_the_first_stall_demands_a_strategy_change_and_the_second_stops():
    """The action, not a log line: one directive, then a **named** stop."""
    guard = ProgressGuard()
    guard.observe(_stall(1))
    demanded = guard.observe(_stall(2))
    assert demanded.action == ACTION_STRATEGY_CHANGE
    assert demanded.signal == "pattern_no_new_evidence"
    assert demanded.reason == "no_progress"

    stopped = guard.observe(_stall(3))
    assert stopped.action == ACTION_STOP
    assert stopped.reason == "no_strategy_change_after_directive"
    assert stopped.signal == "pattern_retry_without_strategy_change"
    assert stopped.stop is True


def test_a_real_step_clears_the_demand_counter():
    """A nudge that worked is not held against the run forever."""
    guard = ProgressGuard()
    guard.observe(_stall(1))
    assert guard.observe(_stall(2)).action == ACTION_STRATEGY_CHANGE
    progressed = guard.observe(
        TurnObservation(turn=3, answer_text="done", tool_calls=("run_tests",), executed=1,
                        evidence="pytest:exit=0", state="revision:2")
    )
    assert progressed.action == ACTION_CONTINUE
    guard.observe(_stall(4))
    # still the *first* demand of the new stall, so a directive, not a stop
    assert guard.observe(_stall(5)).action == ACTION_STRATEGY_CHANGE


def test_a_repeated_search_is_named_as_a_search():
    guard = ProgressGuard()
    guard.observe(TurnObservation(turn=1, answer_text="looking", tool_calls=("search",),
                                  executed=1))
    decision = guard.observe(
        TurnObservation(turn=2, answer_text="looking", tool_calls=("search",), executed=1)
    )
    # a search that runs is *activity*, not a stall — the guard must not cry wolf
    assert decision.action == ACTION_CONTINUE
    assert decision.conditions["no_tool_calls"] is False


def test_the_guard_can_be_switched_off():
    guard = ProgressGuard(enabled=False)
    guard.observe(_stall(1))
    out = guard.observe(_stall(2))
    assert out.stuck is False and out.action == ACTION_CONTINUE
    assert "disabled" in out.reason


# --------------------------------------------------------------------------- #
# 3 — tool-result archiving                                                    #
# --------------------------------------------------------------------------- #


def _oversized_tool(workspace: Path) -> AgentTool:
    """A tool whose result is a real dump: 40 000 chars, deterministic."""
    body = "\n".join(f"line {index:05d} of the container log" for index in range(1000))

    def dump() -> dict:
        return {"logs": body, "lines": 1000}

    return AgentTool(
        name="container_logs",
        description="the container's log dump",
        handler=dump,
        input_schema={"type": "object", "properties": {}},
        source="docker",
    )


def test_an_oversized_result_is_archived_and_retrievable_byte_identically(tmp_path: Path):
    archive = ToolArchive(tmp_path / "state", threshold_chars=1000)
    payload = {"logs": "x" * 5000, "lines": 1}
    result = archive.put(payload, source="docker")

    assert result.digest == hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    as_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode()
    assert result.bytes == len(as_bytes)
    # the artifact is byte-identical to what was archived...
    assert archive.retrieve(result.ref) == as_bytes
    # ...and its name IS its digest, so retrieval cannot return something else
    assert Path(result.path).stem == result.digest
    # the hot form is bounded, and says what it is
    assert result.token_estimate_after < result.token_estimate_before
    assert "container output" in result.summary
    assert len(result.summary) < 1200


def test_archiving_is_idempotent_and_content_addressed(tmp_path: Path):
    archive = ToolArchive(tmp_path / "state", threshold_chars=10)
    first = archive.put({"a": "y" * 500}, source="tests")
    second = archive.put({"a": "y" * 500}, source="tests")
    assert first.ref == second.ref
    assert first.path == second.path
    assert archive.stats()["deduped"] == 1


def test_a_corrupted_artifact_is_refused_not_returned(tmp_path: Path):
    from skaldir.tool_archive import ArchiveError

    archive = ToolArchive(tmp_path / "state", threshold_chars=10)
    result = archive.put("z" * 400, source="browser")
    Path(result.path).write_text("tampered", encoding="utf-8")
    with pytest.raises(ArchiveError):
        archive.retrieve(result.ref)


def test_an_oversized_tool_result_never_reaches_the_prompt_whole(tmp_path: Path):
    """End to end through a REAL ``AgentLoop``: the model sees id + digest + summary."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    state = tmp_path / "state"
    archive = ToolArchive(state, threshold_chars=1000)
    tool = _oversized_tool(workspace)

    with StubEndpoint(
        [
            tool_step(("c1", "container_logs", {})),
            answer("read the archive id, done"),
        ]
    ) as stub:
        loop = AgentLoop(
            _client(stub.base_url),
            ToolSet.of([tool]),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=4),
            max_result_chars=4000,
            archive=archive,
        )
        result = loop.run("inspect the container logs")

    assert result.status == "answered", result.message
    assert result.executed_calls == 1
    # the prompt the model received carries the archive, never the 34 000-char dump
    second = stub.requests[1]["body"]["messages"]
    tool_messages = [item for item in second if item.get("role") == "tool"]
    assert len(tool_messages) == 1
    content = tool_messages[0]["content"]
    assert "tool_archive:" in content
    assert '"sha256"' in content
    assert len(content) < 4000, "the hot form must stay under the result budget"
    assert "line 00999" not in content, "the dump itself must not be in the prompt"

    # and the artifact is byte-identically retrievable from what the loop stored
    ref = json.loads(content)["archived"]["ref"]
    blob = archive.retrieve(ref)
    assert b"line 00999" in blob
    assert len(blob) > 30000
    assert result.archive["puts"] == 1


def test_archiving_can_be_switched_off(tmp_path: Path):
    """``archive=None`` is exactly the old behaviour: inline, truncated, no artifact."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    state = tmp_path / "state"
    with StubEndpoint(
        [tool_step(("c1", "container_logs", {})), answer("done")]
    ) as stub:
        loop = AgentLoop(
            _client(stub.base_url),
            ToolSet.of([_oversized_tool(workspace)]),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=4),
            max_result_chars=2000,
        )
        result = loop.run("inspect the container logs")
    assert result.status == "answered"
    second = stub.requests[1]["body"]["messages"]
    content = [item for item in second if item.get("role") == "tool"][0]["content"]
    assert "tool_archive:" not in content
    assert "[truncated" in content
    assert result.archive == {}


# --------------------------------------------------------------------------- #
# 4 — retry only before the visible commit point                               #
# --------------------------------------------------------------------------- #


def test_the_commit_point_is_the_line():
    point = CommitPoint(turn=1)
    assert point.committed is False
    point.visible_output = True
    point.visible_chars = 40
    assert point.committed is True
    assert "user-visible output" in point.blocker

    other = CommitPoint(turn=1, tool_executed=True)
    assert other.committed is True
    assert "tool call has already executed" in other.blocker


def test_a_retry_before_the_commit_point_is_allowed_exactly_once():
    gate = RetryGate(max_attempts=1)
    gate.begin(turn=1)
    allowed, why = gate.allow_retry(attempt=1)
    assert allowed is True and "nothing has been committed" in why
    # ...and only once: the second attempt is refused even with nothing committed
    allowed, why = gate.allow_retry(attempt=2)
    assert allowed is False and "already made" in why


def test_a_retry_after_the_commit_point_is_refused_and_named():
    gate = RetryGate(max_attempts=3)
    gate.begin(turn=1)
    gate.note_visible_output(120)
    decision = gate.decide(attempt=1, error="ReadError: connection reset")
    assert decision.retry is False
    assert decision.action == "replan"
    assert decision.commit["committed"] is True
    assert "user-visible output was already emitted (120 chars)" in decision.reason
    assert "Re-plan instead" in decision.replan
    assert gate.refusals == 1


def test_a_turn_that_ran_a_tool_can_never_be_retried_gate_wise():
    gate = RetryGate(max_attempts=3)
    gate.begin(turn=2)
    gate.note_tool_executed(1)
    allowed, why = gate.allow_retry(attempt=1)
    assert allowed is False
    assert "a tool call has already executed" in why
    decision = gate.decide(attempt=1)
    assert "run a tool a second time" in decision.replan


def test_the_provider_really_consults_the_gate(tmp_path: Path):
    """The hook in ``llm_provider.chat``: one retry allowed, refused once committed."""
    from skaldir import llm_provider as lp

    client = lp.provider_for(
        {
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:9/v1",
            "model": "stub",
            "api_key": "stub-local-key",
            "max_retries": 3,
        }
    )
    attempts = {"n": 0}

    def failing_send(_client, _url, _body, *, timeout=None, stream=None):  # noqa: ARG001
        attempts["n"] += 1
        raise lp.ProviderTimeoutError("simulated disconnect")

    client._send_chat = failing_send  # type: ignore[method-assign]

    # (a) nothing committed yet → the §43 policy stands, so exactly one retry happens
    gate = RetryGate(max_attempts=1)
    gate.begin(turn=1)
    client.retry_gate = gate
    with pytest.raises(lp.LLMError):
        client.chat([{"role": "user", "content": "hi"}])
    assert attempts["n"] == 2, "one retry must really have been attempted"

    # (b) a delta already went out → the gate refuses the retry, so NO second attempt
    attempts["n"] = 0
    gate = RetryGate(max_attempts=3)
    gate.begin(turn=2)
    gate.note_visible_output(64)
    client.retry_gate = gate
    with pytest.raises(lp.LLMError):
        client.chat([{"role": "user", "content": "hi"}])
    assert attempts["n"] == 1, "a committed attempt must never be re-sent"


def test_a_mid_stream_disconnect_past_the_commit_point_never_runs_a_tool_twice(tmp_path: Path):
    """The end-to-end case: the user saw text, the tool ran, the retry is named."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    state = tmp_path / "state"
    ran: list[str] = []

    def tool_handler() -> dict:
        ran.append("once")
        return {"ok": True}

    tool = AgentTool(
        name="do_the_thing", description="does the thing", handler=tool_handler,
        input_schema={"type": "object", "properties": {}},
    )
    gate = RetryGate(max_attempts=3)
    client = DisconnectingClient(
        [
            tool_step(("c1", "do_the_thing", {})),
            {"__stream_then_disconnect__": "Working on it — "},
            answer("recovered after the re-plan"),
        ],
        gate=gate,
    )
    deltas: list[str] = []
    loop = AgentLoop(
        client,
        ToolSet.of([tool]),
        state_dir=state,
        workspace=workspace,
        limits=LoopLimits(max_steps=5),
        retry_gate=gate,
        on_delta=deltas.append,
    )
    result = loop.run("do the thing once")

    # the tool ran exactly once — a retried turn would have run it twice
    assert ran == ["once"], ran
    # the disconnect landed past the visible commit point, so the gate refused it
    assert result.retry, "the case must be named on the record"
    refusal = result.retry[-1]
    assert refusal["retry"] is False
    assert refusal["action"] == "replan"
    assert "user-visible output was already emitted" in refusal["reason"]
    # ...and the run continued through an explicit re-plan instead of a duplicate answer
    assert result.status == "answered", result.message
    assert result.commit["committed"] is True
    assert result.commit["tool_executed"] is True


class DisconnectingClient:
    """A client that emits a delta and then drops the connection, mid-answer.

    It behaves the way a real streaming client does — the delta reaches the sink
    **before** anything is known about the rest of the answer — and it consults the
    retry gate exactly where ``OpenAICompatibleLLM.chat`` does.
    """

    def __init__(self, script: list[dict], *, gate: RetryGate) -> None:
        self.script = list(script)
        self.gate = gate
        self.on_delta = None
        self.calls = 0

    def chat(self, messages, *, tier="main", tools=None, tool_choice=None, **_kw):
        from skaldir.llm_provider import ChatResult, LLMError, ToolCall, parse_tool_arguments

        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        partial = str(step.get("__stream_then_disconnect__") or "")
        if partial:
            # the user has SEEN text before anything else is known — this is the
            # mid-stream disconnect the rule is about
            if self.on_delta is not None:
                self.on_delta(partial)
            allowed, _why = self.gate.allow_retry(attempt=1)
            if allowed:
                return self.chat(messages)
            raise LLMError("ConnectionResetError: the stream dropped mid-answer")
        text = str((step.get("choices") or [{}])[0].get("message", {}).get("content") or "")
        if text and self.on_delta is not None and not step.get("tool_calls"):
            self.on_delta(text)
        calls = []
        for raw in (step.get("choices") or [{}])[0].get("message", {}).get("tool_calls") or []:
            fn = raw.get("function") or {}
            arguments, error = parse_tool_arguments(fn.get("arguments"))
            calls.append(
                ToolCall(id=raw.get("id") or "c", name=fn.get("name") or "",
                         arguments_raw=str(fn.get("arguments") or ""),
                         arguments=arguments, parse_error=error)
            )
        return ChatResult(text=text, tool_calls=calls, finish_reason="stop",
                          usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})


# --------------------------------------------------------------------------- #
# the owner's question: savings, PER MECHANISM                                 #
# --------------------------------------------------------------------------- #


def test_the_savings_report_names_every_mechanism_separately():
    events = [
        {"kind": "run.started", "run_id": "r1"},
        {
            "kind": "model.response",
            "step": 1,
            "text": "",
            "usage": {
                "prompt_tokens": 4096,
                "completion_tokens": 4096,
                "total_tokens": 8192,
                "reasoning_tokens": 3703,
                "prompt_cache_hit_tokens": 3000,
            },
        },
        {
            "kind": "tool_archived",
            "name": "container_logs",
            "ref": "tool_archive:abc",
            "sha256": "abc",
            "chars": 40000,
            "tokens_before": 10000,
            "tokens_after": 120,
            "tokens_saved": 9880,
            "source": "docker",
        },
    ]
    record = telemetry.build_record_from_events(events, transcript="/tmp/r1.jsonl")
    savings = record.savings

    # every mechanism is its own block, with its own source — never one hopeful sum
    assert set(savings) >= {
        "prompt_cache",
        "archived_tool_results",
        "compaction",
        "brief_vs_transcript",
        "reasoning",
    }
    assert savings["prompt_cache"]["value"] == 3000
    assert savings["prompt_cache"]["source"] == "provider"
    assert savings["archived_tool_results"]["tokens_saved"] == 9880
    assert savings["archived_tool_results"]["source"] == "estimate/chars-4"
    assert savings["compaction"]["tokens_freed"] is None
    assert savings["compaction"]["reason"] == "no compaction fired on this run"

    # the 3 703-of-4 096 case is exactly what this exists to surface
    assert savings["reasoning"]["value"] == 3703
    assert savings["reasoning"]["share_of_completion"] == pytest.approx(3703 / 4096, abs=1e-4)
    assert savings["reasoning"]["reasoning_only_answers"] == 1

    # ...and it is visible in the human view, with the share spelled out
    rendered = "\n".join(record.lines())
    assert "savings" in rendered
    assert "archived-tool-results" in rendered
    assert "90% of completion" in rendered
    assert "1 answer(s) with no content" in rendered


def test_a_run_with_no_reasoning_counters_reports_unknown_not_zero():
    events = [
        {"kind": "run.started", "run_id": "r2"},
        {
            "kind": "model.response",
            "step": 1,
            "text": "done",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        },
    ]
    record = telemetry.build_record_from_events(events, transcript="/tmp/r2.jsonl")
    reasoning = record.savings["reasoning"]
    assert reasoning["value"] is None, "absence must not be reported as a zero spend"
    assert "no reasoning-token counter" in reasoning["reason"]
