"""Telemetry — the standard displays, and the honesty rules that make them usable.

Every test here drives the **real** agent loop (``skaldir.agent_loop.AgentLoop``) against a
real HTTP endpoint on localhost whose ``/v1/chat/completions`` answers are scripted: one
tool call, then a final answer. Two variants matter more than the rest, and they are the
reason this module exists at all:

* a provider that **reports** ``usage`` — every number in the record must be labelled
  ``provider``;
* a provider that reports **nothing** — every number must be labelled ``estimate``, the
  label must name the method, and the cost must be ``null`` with a reason. The loop's own
  ``run.finished`` event carries ``cost_usd: 0.0`` in that case, which reads as "free";
  a record that copies it would be a fabricated number.

The record's shape is also pinned where a consumer depends on it: tokens/second is measured
over a named window, the context block carries the compaction trigger, the record is filed
beside the transcript, and ``skaldir telemetry show|live|export`` answer through the product
CLI contract (exit 0/1/3/5/7).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skaldir import telemetry as T
from skaldir import telemetry_cli
from skaldir.agent_loop import AgentLoop, LoopLimits, builtin_tools
from skaldir.llm_provider import OpenAICompatibleLLM, ProviderConfig

# --------------------------------------------------------------------------- #
# the stub provider (a real endpoint, scripted answers)                        #
# --------------------------------------------------------------------------- #

ANSWER = (
    "The workspace was listed with the built-in list_dir tool and it holds nothing else."
)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class _Quiet(ThreadingHTTPServer):
    """A threaded, silent server.

    Threaded on purpose: a *single* threaded server blocking in ``readinto`` on a
    keep-alive connection makes ``shutdown()`` wait forever (measured — the first
    version of this fixture hung the whole suite). ``daemon_threads`` keeps a lingering
    connection from holding the interpreter open.
    """

    allow_reuse_address = True
    daemon_threads = True

    def log_message(self, *args: Any) -> None:  # pragma: no cover — silence
        return


class StubProvider:
    """``/v1/chat/completions`` with a scripted first tool call, then a final answer.

    ``usage=False`` sends no ``usage`` block at all — the case a real provider produces
    when it does not report accounting, and the one telemetry must label as an estimate.
    """

    def __init__(self, *, usage: bool = True, delay_ms: int = 120) -> None:
        self.usage = usage
        self.delay_ms = delay_ms
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # pragma: no cover — silence
                return

            def do_POST(self) -> None:  # noqa: N802 — http.server's name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                with stub._lock:
                    stub.requests.append(body)
                    turn = len(stub.requests)
                time.sleep(max(0.0, stub.delay_ms / 1000.0))
                self._json(200, stub.answer(body, turn))

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = _Quiet(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )

    def answer(self, body: dict[str, Any], turn: int) -> dict[str, Any]:
        prompt_text = json.dumps(body.get("messages") or [], ensure_ascii=False)
        if turn == 1 and body.get("tools"):
            call = {
                "id": "call_stub_1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            }
            message: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": [call]}
            finish = "tool_calls"
            completion_text = json.dumps(call)
        else:
            message = {"role": "assistant", "content": ANSWER}
            finish = "stop"
            completion_text = ANSWER
        payload: dict[str, Any] = {
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }
        if self.usage:
            payload["usage"] = {
                "prompt_tokens": _tokens(prompt_text),
                "completion_tokens": _tokens(completion_text),
                "total_tokens": _tokens(prompt_text) + _tokens(completion_text),
            }
        return payload

    def __enter__(self) -> StubProvider:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    @property
    def client(self) -> OpenAICompatibleLLM:
        return OpenAICompatibleLLM(
            ProviderConfig(
                provider="openai-compatible",
                base_url=f"http://127.0.0.1:{self._server.server_address[1]}/v1",
                model="stub-model",
                timeout_s=10.0,
                max_retries=0,
            )
        )


@dataclass
class Ran:
    """One finished run, with everything the tests need to check its record."""

    run_id: str
    state_dir: Path
    transcript: Path
    result: Any
    record: T.TelemetryRecord = field(default=None)  # type: ignore[assignment]


def run_agent(
    tmp_path: Path,
    *,
    usage: bool = True,
    delay_ms: int = 120,
    limits: LoopLimits | None = None,
    run_id: str = "",
) -> Ran:
    """Run the real loop against the stub and derive the telemetry record."""
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    state_dir = tmp_path / "agent"
    with StubProvider(usage=usage, delay_ms=delay_ms) as stub:
        loop = AgentLoop(
            stub.client,
            builtin_tools(workspace),
            state_dir=state_dir,
            workspace=workspace,
            limits=limits or LoopLimits(max_steps=4),
            goal="list the workspace",
        )
        result = loop.run("List the workspace and report what you find.", run_id=run_id)
    transcript = Path(result.transcript_path)
    record = T.build_record(transcript, context_budget=2000)
    return Ran(
        run_id=result.run_id,
        state_dir=state_dir,
        transcript=transcript,
        result=result,
        record=record,
    )


def write_events(path: Path, events: list[dict[str, Any]]) -> Path:
    """A transcript built by hand — for the cases a live run cannot be made to produce."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def header(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "run.started",
        "at": "2026-09-18T12:00:00+00:00",
        "run_id": "run-hand-made",
        "provider": {"provider": "stub", "model": "hand-model"},
        "limits": {"max_steps": 4},
        "workspace": "/tmp/ws",
    }
    return {**base, **fields}


# --------------------------------------------------------------------------- #
# a provider that reports usage                                                 #
# --------------------------------------------------------------------------- #


def test_a_real_run_reports_provider_numbers(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    record = ran.record

    assert record.status == "answered"
    assert record.turn_count == 2
    assert record.model == "stub-model"
    assert record.provider["client"] == "OpenAICompatibleLLM"
    assert record.tokens["prompt"]["value"] == ran.result.usage["prompt_tokens"]
    assert record.tokens["completion"]["value"] == ran.result.usage["completion_tokens"]
    assert record.tokens["total"]["value"] == ran.result.usage["total_tokens"]
    for name in ("prompt", "completion", "total"):
        assert record.tokens[name]["source"] == "provider", "the provider reported these"
    assert record.estimates == (), "nothing in this record is an estimate"
    assert record.duration_ms == ran.result.duration_ms
    assert record.tool_calls["count"] == 1
    assert record.tool_calls["calls"][0]["name"] == "list_dir"


def test_tokens_per_second_is_measured_over_a_named_window(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    speed = ran.record.tokens_per_second
    turns = ran.record.turns

    window = sum(int(turn.duration_ms or 0) for turn in turns)
    completion = sum(int(turn.completion_tokens or 0) for turn in turns)

    assert window > 0, "the stub really takes time to answer"
    assert speed["window_ms"] == window
    assert speed["window_ms"] <= ran.result.duration_ms, "tool time is not generation time"
    assert speed["turns_measured"] == 2
    assert speed["completion_tokens"] == completion
    assert speed["value"] == pytest.approx(completion / (window / 1000.0))
    assert speed["source"] == "provider"
    assert "duration_ms" in speed["method"] and "monotonic" in speed["method"]


def test_each_turn_carries_its_own_rate_and_tool_durations(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    first, second = ran.record.turns

    assert first.tokens_source == "provider"
    assert first.duration_ms and first.completion_tokens
    assert first.tokens_per_second == pytest.approx(
        first.completion_tokens / (first.duration_ms / 1000.0)
    )
    assert [span.name for span in first.tool_calls] == ["list_dir"]
    assert first.tool_calls[0].duration_ms >= 0
    assert second.tool_calls == ()
    assert second.finish_reason == "stop"


# --------------------------------------------------------------------------- #
# a provider that reports nothing: estimates and a null cost                    #
# --------------------------------------------------------------------------- #


def test_no_usage_yields_labelled_estimates(tmp_path: Path) -> None:
    ran = run_agent(tmp_path, usage=False)
    record = ran.record

    assert ran.result.usage_estimated is True, "the loop's own flag says estimate"
    for name in ("prompt", "completion", "total"):
        block = record.tokens[name]
        assert block["source"] == "estimate"
        assert block["estimate"] is True
        assert block["value"] and block["value"] > 0
    assert record.tokens["prompt"]["method"].startswith("summed over the run's turns")
    assert "tokens.prompt (estimate)" in record.estimates
    assert "tokens_per_second (estimate: the completion count is one)" in record.estimates
    assert "context.used_tokens (estimate)" in record.estimates
    assert record.tokens_per_second["value"] > 0


def test_an_unpriced_run_reports_null_never_zero(tmp_path: Path) -> None:
    ran = run_agent(tmp_path, usage=False)
    record = ran.record

    # the loop's own events say 0.0 — a record that copied that would read as "free"
    events = [json.loads(line) for line in ran.transcript.read_text().splitlines()]
    finished = next(event for event in events if event["kind"] == "run.finished")
    started = next(event for event in events if event["kind"] == "run.started")
    assert finished["cost_usd"] == 0.0
    assert started["limits"]["price_per_1k_tokens"] == 0.0

    assert record.cost["usd"] is None
    assert "no price was declared" in record.cost["reason"]
    assert {"field": "cost.usd", "reason": record.cost["reason"]} in list(record.nulls)
    assert "$0.000000" not in "\n".join(record.lines())


def test_a_price_the_run_declared_is_used_and_named(tmp_path: Path) -> None:
    ran = run_agent(
        tmp_path, limits=LoopLimits(max_steps=4, price_per_1k_tokens=2.0)
    )
    record = ran.record

    expected = (record.tokens["total"]["value"] / 1000.0) * 2.0
    assert record.cost["usd"] == pytest.approx(expected)
    assert record.cost["price_source"] == "transcript:run.started.limits.price_per_1k_tokens"
    assert record.cost["price_per_1k_tokens"] == 2.0


def test_a_price_flag_wins_over_the_run_and_is_named(tmp_path: Path) -> None:
    ran = run_agent(tmp_path, limits=LoopLimits(max_steps=4, price_per_1k_tokens=2.0))
    record = T.build_record(ran.transcript, price_per_1k_tokens=5.0, context_budget=2000)

    assert record.cost["price_per_1k_tokens"] == 5.0
    assert record.cost["price_source"] == "flag:--price-per-1k-tokens"
    assert record.cost["usd"] == pytest.approx(
        (record.tokens["total"]["value"] / 1000.0) * 5.0
    )


def test_an_estimated_token_total_makes_the_cost_an_estimate_too(tmp_path: Path) -> None:
    ran = run_agent(tmp_path, usage=False)
    record = T.build_record(ran.transcript, price_per_1k_tokens=1.0, context_budget=2000)

    assert record.cost["usd"] is not None
    assert record.cost["estimate"] is True
    assert "cost.usd (derived from an estimated token total)" in record.estimates
    assert "so this cost is one too" in record.cost["method"]


# --------------------------------------------------------------------------- #
# context usage and the compaction trigger                                      #
# --------------------------------------------------------------------------- #


def test_context_usage_names_its_source_and_the_compaction_trigger(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    context = ran.record.context

    assert context["total_tokens"] == 2000
    assert context["total_source"] == "flag:--context-budget"
    assert context["trigger_fraction"] == 0.75
    assert context["trigger_tokens"] == 1500
    assert context["used_source"] == "provider_measured"
    assert context["used_tokens"] == ran.record.turns[-1].prompt_tokens, (
        "the context block reports the context in use at the end of the run"
    )
    assert context["used_tokens"] != ran.result.usage["prompt_tokens"], (
        "which is the last turn's prompt, not the sum over all turns"
    )
    assert context["percent"] == pytest.approx(100.0 * context["used_tokens"] / 2000.0)
    assert context["state"] == "below_trigger"
    assert context["estimate"]["messages"] >= 2


def test_a_budget_the_context_exceeds_is_reported_as_over_budget(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    record = T.build_record(ran.transcript, context_budget=200)

    assert record.context["state"] == "over_budget"
    assert record.context["trigger_tokens"] == 150
    assert record.context["percent"] > 100
    assert record.context["percent_display"] == f"{record.context['percent']:.0f}%"
    assert record.context["remaining_tokens"] < 0, "the record says how far over it is"

    footer = record.footer()
    assert "ctx " in footer and record.context["percent_display"] in footer


def test_a_declared_budget_from_the_environment_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = run_agent(tmp_path)
    monkeypatch.setenv("SKALDIR_CONTEXT_BUDGET", "8000")
    record = T.build_record(ran.transcript)

    assert record.context["total_tokens"] == 8000
    assert record.context["total_source"] == "env:SKALDIR_CONTEXT_BUDGET"
    assert record.context["trigger_tokens"] == 6000


# --------------------------------------------------------------------------- #
# nulls where nothing can be measured                                          #
# --------------------------------------------------------------------------- #


def test_a_zero_generation_window_is_null_not_a_division(tmp_path: Path) -> None:
    transcript = write_events(
        tmp_path / "agent" / "runs" / "run-zero.jsonl",
        [
            header(),
            {"kind": "model.request", "step": 1, "body": {"messages": [{"role": "user"}]}},
            {
                "kind": "model.response",
                "step": 1,
                "duration_ms": 0,
                "text": "hi",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "usage_estimated": False,
            },
            {
                "kind": "run.finished",
                "at": "2026-09-18T12:00:01+00:00",
                "status": "answered",
                "duration_ms": 10,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "usage_estimated": False,
            },
        ],
    )
    record = T.build_record(transcript, context_budget=1000)

    assert record.turns[0].duration_ms == 0
    assert record.turns[0].tokens_per_second is None
    assert record.tokens_per_second["value"] is None
    assert "no turn has both" in record.tokens_per_second["reason"]
    assert any(item["field"] == "tokens_per_second" for item in record.nulls)
    assert "tok/s n/a" in record.footer()


def test_a_run_without_an_end_event_is_reported_as_running(tmp_path: Path) -> None:
    transcript = write_events(
        tmp_path / "agent" / "runs" / "run-open.jsonl",
        [
            header(),
            {
                "kind": "model.request",
                "step": 1,
                "body": {"messages": [{"role": "user", "content": "x"}]},
            },
            {
                "kind": "model.response",
                "step": 1,
                "at": "2026-09-18T12:00:02+00:00",
                "duration_ms": 1000,
                "text": "working",
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
                "usage_estimated": False,
            },
        ],
    )
    record = T.build_record(transcript, context_budget=1000)

    assert record.status == "running"
    assert record.duration_ms == 2000, "measured to the last recorded event"
    assert "still running or was killed" in record.duration_source
    assert any("no `run.finished` event" in warning for warning in record.warnings)


def test_a_request_with_no_answer_is_one_row_with_a_reason(tmp_path: Path) -> None:
    transcript = write_events(
        tmp_path / "agent" / "runs" / "run-torn.jsonl",
        [
            header(),
            {
                "kind": "model.request",
                "step": 1,
                "body": {"messages": [{"role": "user", "content": "x"}]},
            },
        ],
    )
    record = T.build_record(transcript, context_budget=1000)

    assert record.turn_count == 1
    assert record.turns[0].answered is False
    assert record.turns[0].completion_tokens is None
    assert "interrupted before the model replied" in record.turns[0].tokens_per_second_reason
    assert any(
        item["field"] == "turns[1].tokens_per_second" for item in record.nulls
    ), "an unmeasurable rate is listed, not quietly dropped"


def test_a_pending_request_does_not_move_the_context_reading(tmp_path: Path) -> None:
    """The footer reports the last **answered** turn, not a request still in flight.

    A pending request has no provider count, so reading it would switch estimator mid-run
    and report two different percentages for the same context (measured: 78% then 40%).
    """
    transcript = write_events(
        tmp_path / "agent" / "runs" / "run-pending.jsonl",
        [
            header(),
            {
                "kind": "model.request",
                "step": 1,
                "body": {"messages": [{"role": "user", "content": "small"}]},
            },
            {
                "kind": "model.response",
                "step": 1,
                "duration_ms": 100,
                "text": "ok",
                "usage": {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
                "usage_estimated": False,
            },
            {  # a much bigger request the model has not answered yet
                "kind": "model.request",
                "step": 2,
                "body": {
                    "messages": [
                        {"role": "user", "content": "x" * 4000},
                        {"role": "tool", "content": "y" * 4000},
                    ]
                },
            },
        ],
    )
    record = T.build_record(transcript, context_budget=1000)

    assert record.context["used_tokens"] == 20
    assert record.context["used_source"] == "provider_measured"
    assert record.context["percent"] == pytest.approx(2.0)
    assert record.turn_count == 2 and record.turns[1].answered is False
    assert record.footer().startswith("hand-model · ctx 2% ·")


def test_an_empty_transcript_is_blocked_not_an_empty_success(tmp_path: Path) -> None:
    empty = tmp_path / "agent" / "runs" / "run-empty.jsonl"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("", encoding="utf-8")

    with pytest.raises(T.TelemetryError) as excinfo:
        T.build_record(empty)
    assert excinfo.value.code == "blocked"

    with pytest.raises(T.TelemetryError) as missing:
        T.build_record(tmp_path / "nope.jsonl")
    assert missing.value.code == "not_found"


# --------------------------------------------------------------------------- #
# the displays                                                                  #
# --------------------------------------------------------------------------- #


def test_the_footer_matches_the_documented_shape(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    record = T.build_record(ran.transcript, context_budget=2000)

    footer = record.footer()
    model, context, speed, duration = footer.split(" · ")
    assert model == "stub-model"
    assert context.startswith("ctx ") and context.endswith("%")
    assert speed.endswith(" tok/s")
    assert len(duration.split(":")) == 2, "mm:ss"
    assert "~" not in footer, "nothing here is an estimate — so nothing is marked"


def test_an_estimated_footer_is_marked_and_a_missing_part_says_so(tmp_path: Path) -> None:
    ran = run_agent(tmp_path, usage=False)
    footer = T.build_record(ran.transcript, context_budget=2000).footer()

    assert "ctx ~" in footer
    assert "tok/s" in footer and "~" in footer.split(" · ")[2]


def test_turn_lines_carry_the_source_of_every_number(tmp_path: Path) -> None:
    ran = run_agent(tmp_path, usage=False)
    record = T.build_record(ran.transcript, context_budget=2000)
    lines = record.turn_lines()

    assert len(lines) == 2
    assert lines[0].startswith("turn 1/2 ·")
    assert "out ~" in lines[0] and "(estimate)" in lines[0]
    assert "tool call(s) [list_dir]" in lines[0]
    assert "no tool call" in lines[1]


# --------------------------------------------------------------------------- #
# persistence                                                                   #
# --------------------------------------------------------------------------- #


def test_the_record_is_filed_beside_the_transcript(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    record, notes = T.show_record(transcript=ran.transcript, context_budget=2000)

    filed = T.record_path_for(ran.transcript)
    assert filed.is_file(), "a later `telemetry show <run-id>` must work"
    assert filed.name == f"{ran.run_id}.telemetry.json"
    assert any("record written to" in note for note in notes)
    assert T.load_record(filed).run_id == record.run_id


def test_a_filed_record_is_served_when_the_transcript_is_gone(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    T.show_record(transcript=ran.transcript, context_budget=2000)
    ran.transcript.unlink()

    record, notes = T.show_record(run_id=ran.run_id, state_dir=ran.state_dir)
    assert record.source == "persisted record"
    assert any("was not re-derived" in note for note in notes)
    assert record.tokens["total"]["value"] == ran.result.usage["total_tokens"]
    assert "from persisted record" in "\n".join(record.lines())


def test_show_refuses_a_run_it_does_not_have(tmp_path: Path) -> None:
    with pytest.raises(T.TelemetryError) as excinfo:
        T.show_record(run_id="run-does-not-exist", state_dir=tmp_path / "agent")
    assert excinfo.value.code == "not_found"


# --------------------------------------------------------------------------- #
# export                                                                        #
# --------------------------------------------------------------------------- #


def test_export_jsonl_has_one_line_per_turn_plus_the_run(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    text = T.export_text(ran.record, fmt="jsonl")
    rows = [json.loads(line) for line in text.splitlines()]

    assert [row["row"] for row in rows] == ["turn", "turn", "run"]
    assert rows[-1]["tokens"]["total"]["value"] == ran.result.usage["total_tokens"]
    assert rows[0]["tokens_source"] == "provider"


def test_export_csv_has_a_run_row_then_one_row_per_turn(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    lines = T.export_text(ran.record, fmt="csv").splitlines()

    assert lines[0].startswith("row,run_id,index")
    assert len(lines) == ran.record.turn_count + 2
    assert lines[1].startswith("run,")
    assert f",{ran.result.usage['total_tokens']}," in lines[1]


def test_export_refuses_an_unknown_format(tmp_path: Path) -> None:
    ran = run_agent(tmp_path)
    with pytest.raises(T.TelemetryError) as excinfo:
        T.export_text(ran.record, fmt="yaml")
    assert excinfo.value.code == "usage"


# --------------------------------------------------------------------------- #
# the CLI                                                                       #
# --------------------------------------------------------------------------- #


def test_show_prints_the_record_as_one_json_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = run_agent(tmp_path)
    code = telemetry_cli.main(
        ["show", ran.run_id, "--state-dir", str(ran.state_dir), "--json"]
    )
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["run_id"] == ran.run_id
    assert out["footer"].startswith("stub-model · ctx ")
    assert out["cost"]["usd"] is None, "no price was declared anywhere in this test"
    assert T.record_path_for(ran.transcript).is_file()


def test_show_text_prints_the_human_view(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = run_agent(tmp_path, usage=False)
    code = telemetry_cli.main(
        ["show", ran.run_id, "--state-dir", str(ran.state_dir), "--text", "--no-persist"]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "model    stub-model" in out
    assert "duration 00:0" in out
    assert "tokens   prompt ~" in out
    assert "cost     null (" in out


def test_show_exits_5_for_an_unknown_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = telemetry_cli.main(["show", "run-nope", "--state-dir", str(tmp_path / "agent")])
    assert code == 5


def test_show_needs_a_run_argument(capsys: pytest.CaptureFixture[str]) -> None:
    assert telemetry_cli.main(["show", "--json"]) == 1
    assert telemetry_cli.main(["show", "--transcript", "/nope.jsonl"]) == 5
    capsys.readouterr()


def test_live_replays_a_finished_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = run_agent(tmp_path)
    code = telemetry_cli.main(
        ["live", ran.run_id, "--state-dir", str(ran.state_dir), "--json"]
    )
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert len(out["lines"]) == 3, "two turns and one footer"
    assert out["lines"][0].startswith("turn 1/2 ·")
    assert out["lines"][-1] == ran.record.footer() or out["lines"][-1].count(" · ") == 3
    assert out["followed"] is False


def test_live_follows_a_transcript_as_it_grows(tmp_path: Path) -> None:
    transcript = tmp_path / "agent" / "runs" / "run-growing.jsonl"
    write_events(transcript, [header(run_id="run-growing")])
    watcher = T.TelemetryWatcher(transcript, run_id="run-growing", context_budget=2000)

    watcher.poll()
    assert watcher.lines([]) == [], "a header alone is not a turn"

    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "kind": "model.request", "step": 1,
            "body": {"messages": [{"role": "user", "content": "hi"}]},
        }) + "\n")
    watcher.poll()
    assert watcher.lines([]) == [], "a request with no answer is not a turn line yet"

    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "kind": "model.response", "step": 1, "duration_ms": 1000, "text": "hello",
            "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
            "usage_estimated": False,
        }) + "\n")
        handle.write(json.dumps({
            "kind": "run.finished", "status": "answered", "duration_ms": 1200,
            "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
            "usage_estimated": False,
        }) + "\n")

    lines = watcher.lines()
    assert len(lines) == 2, "one turn line and the footer"
    assert lines[0].startswith("turn 1/1 · 1000 ms · out 10 tok (provider)")
    assert lines[1] == "hand-model · ctx 2% · 10 tok/s · 00:01"
    assert watcher.finished is True
    assert watcher.record().status == "answered"


def test_the_watcher_notices_a_transcript_that_was_truncated(tmp_path: Path) -> None:
    transcript = write_events(
        tmp_path / "agent" / "runs" / "run-reused.jsonl",
        [
            header(run_id="run-reused"),
            {"kind": "model.request", "step": 1, "body": {"messages": []}},
        ],
    )
    watcher = T.TelemetryWatcher(transcript, run_id="run-reused", context_budget=2000)
    watcher.poll()
    assert watcher.lines([]) == []

    write_events(transcript, [header(run_id="run-reused")])  # a new run under the same id
    watcher.poll()
    lines = watcher.lines([])
    assert any("truncated" in line for line in lines)
    assert watcher.truncated is False, "the note is printed once"


def test_export_writes_the_file_and_reports_its_size(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = run_agent(tmp_path)
    target = tmp_path / "out" / "run.jsonl"
    code = telemetry_cli.main([
        "export", ran.run_id, "--state-dir", str(ran.state_dir),
        "--as", "jsonl", "--out", str(target), "--json",
    ])
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["path"] == str(target)
    assert out["bytes"] == target.stat().st_size
    assert len(target.read_text().splitlines()) == ran.record.turn_count + 1


def test_export_to_stdout_keeps_the_document_parseable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = run_agent(tmp_path)
    code = telemetry_cli.main([
        "export", ran.run_id, "--state-dir", str(ran.state_dir), "--as", "json",
        "--out", "-", "--text",
    ])
    out = capsys.readouterr().out

    assert code == 0
    assert json.loads(out)["run_id"] == ran.run_id


def test_export_refuses_a_bad_format_through_the_parser(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = run_agent(tmp_path)
    assert telemetry_cli.main([
        "export", ran.run_id, "--state-dir", str(ran.state_dir), "--as", "yaml",
    ]) == 1


# --------------------------------------------------------------------------- #
# the contract and the wiring                                                   #
# --------------------------------------------------------------------------- #


def test_register_cli_installs_three_contract_entries_on_a_bare_parser() -> None:
    added = telemetry_cli.register_contract_entries()
    from skaldir import cli_contract

    for path in ("telemetry.show", "telemetry.live", "telemetry.export"):
        assert path in cli_contract.COMMANDS, f"{path} is not registered"
        entry = cli_contract.COMMANDS[path]
        assert entry.layer == "PERSIST"
        assert entry.data_schema and entry.data_schema["type"] == "object"
        assert entry.evidence, "a command may not ship without an evidence source"
    assert set(added) <= {"telemetry.show", "telemetry.live", "telemetry.export"}
    assert telemetry_cli.register_contract_entries() == [], "installing twice adds nothing"


def _sub_choices(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """The verbs of a parser's sub-command action (argparse keeps them on the action)."""
    action = next(
        item for item in parser._actions if isinstance(item, argparse._SubParsersAction)
    )
    return dict(action.choices)


def test_register_cli_is_idempotent_and_never_dies_on_a_conflict() -> None:
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    group = telemetry_cli.register_cli(sub)
    again = telemetry_cli.register_cli(sub)  # must not raise `conflicting subparser`

    assert group is again
    assert set(_sub_choices(group)) == {"show", "live", "export"}


def test_the_standalone_parser_exposes_every_documented_flag() -> None:
    parser = telemetry_cli.build_parser()
    subs = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    for verb in ("show", "live", "export"):
        options = {
            option
            for action in subs.choices[verb]._actions
            for option in action.option_strings
        }
        assert {"--json", "--text", "--state-dir", "--context-budget",
                "--trigger-fraction", "--price-per-1k-tokens", "--no-persist"} <= options
        assert getattr(subs.choices[verb], "choices", None) is None, "a verb has no sub-verbs"
    export_options = {
        option for action in subs.choices["export"]._actions for option in action.option_strings
    }
    assert {"--as", "--out", "--format"} <= export_options
    live_options = {
        option for action in subs.choices["live"]._actions for option in action.option_strings
    }
    assert {"--follow", "--interval-s", "--timeout-s"} <= live_options


def test_a_handler_that_would_crash_is_reported_as_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A TelemetryError reaching ``main`` is still a refusal, not a traceback."""
    def explode(_args: argparse.Namespace) -> int:
        raise T.TelemetryError("nothing to report", code="blocked")

    monkeypatch.setattr(telemetry_cli, "cmd_show", explode)
    parser = telemetry_cli.build_parser()
    subs = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    subs.choices["show"].set_defaults(func=explode)
    assert telemetry_cli.main(["show", "run-x", "--transcript", str(tmp_path / "x.jsonl")]) == 3
    capsys.readouterr()
