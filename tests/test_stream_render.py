"""The token-streaming path: the sink, the renderer, the loop wiring and the honesty rules.

Everything here is socket-free on purpose. The loop's own stub endpoint
(``tests/test_agent_loop.py::StubEndpoint``) is known to hang its teardown in this
environment (a keep-alive connection blocked in ``handle_one_request`` makes
``socketserver.shutdown()`` wait forever), so a test added *near* the loop must not open a
socket. The provider side is therefore exercised through a fake response object and the
loop through a fake client — same code path, no terminal, no network. The real end-to-end
run (a real pty at 80x24 against a stub that streams slowly) is
``tools/gv_stream_ttfb.py``; its numbers are in ``MVP/58-streaming.md``.
"""

from __future__ import annotations

import io
import json
import time
from typing import Any

from skaldir import agent_loop_cli, llm_provider, render, stream_render
from skaldir import progress as progress_mod
from skaldir import theme as theme_mod
from skaldir.agent_loop import AgentLoop, AgentTool, LoopLimits, ToolSet
from skaldir.llm_provider import ChatResult, ProviderConfig

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal (and records every byte written)."""

    def isatty(self) -> bool:  # noqa: D102 — the point of the double
        return True


class Clock:
    """A clock a test moves by hand — no sleeps, no flakiness."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def tool() -> AgentTool:
    return AgentTool(
        name="ping",
        description="ping",
        input_schema={"type": "object", "properties": {}},
        handler=lambda **kwargs: "pong",
    )


class StreamingClient:
    """A socket-free stand-in that streams like the real client does.

    It fills the *same* ``stream_stats`` contract ``OpenAICompatibleLLM`` fills, and calls
    ``on_delta`` for every chunk — which is exactly the seam the loop wires.
    """

    def __init__(self, chunks: list[str], *, sleep: float = 0.0, fail_after: int | None = None):
        self.on_delta = None
        self.config = None
        self.stream_stats = llm_provider._empty_stream_stats()
        self.chunks = chunks
        self.sleep = sleep
        self.fail_after = fail_after
        self.calls: list[dict] = []

    def chat(self, messages, *, tier="main", tools=None, tool_choice=None, stream=None):
        """Stream only when asked, exactly like ``OpenAICompatibleLLM`` does.

        A non-streaming call produces **no** deltas and returns the whole answer — which is
        what makes the "before" arm of the measurement the old behaviour and not a fiction.
        """
        self.calls.append({"stream": stream, "sink": self.on_delta is not None})
        self.stream_stats = llm_provider._empty_stream_stats()
        self.stream_stats["streaming_requested"] = bool(stream)
        text = "".join(self.chunks)
        if not stream:
            return ChatResult(
                text=text,
                finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            )
        started = time.monotonic()
        emitted = ""
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index >= self.fail_after:
                break
            if self.sleep:
                time.sleep(self.sleep)
            emitted += chunk
            self.stream_stats["deltas"] += 1
            self.stream_stats["chars"] += len(chunk)
            stamp = round((time.monotonic() - started) * 1000, 3)
            if self.stream_stats["first_content_ms"] is None:
                self.stream_stats["first_content_ms"] = stamp
            self.stream_stats["last_content_ms"] = stamp
            if self.on_delta is not None:
                try:
                    self.on_delta(chunk)
                except Exception:  # noqa: BLE001 — the real client counts this per delta
                    self.stream_stats["sink_errors"] += 1
        text = emitted
        return ChatResult(
            text=text,
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        )


class NonStreamingClient:
    """A client with no ``stream`` parameter at all — the old shape, unchanged."""

    def __init__(self, text: str = "whole answer"):
        self.text = text
        self.calls: list[dict] = []

    def chat(self, messages, *, tier="main", tools=None, tool_choice=None):
        self.calls.append({"tier": tier})
        return ChatResult(text=self.text, finish_reason="stop", usage={})


def run_loop(client, tmp_path, **kwargs) -> tuple[object, list]:
    """Run one step with tools, collecting the progress events the loop emits."""
    events: list[tuple[str, dict]] = []
    loop = AgentLoop(
        client,
        ToolSet.of([tool()]),
        state_dir=str(tmp_path / "state"),
        limits=LoopLimits(max_steps=2),
        progress=lambda kind, fields: events.append((kind, dict(fields))),
        **kwargs,
    )
    return loop.run("say something", run_id="stream-test"), events


def fake_sse(lines: list[str]):
    """A response object shaped like httpx's streamed response (``iter_lines``)."""

    class Response:
        status_code = 200
        text = ""

        def iter_lines(self):
            yield from lines

        def read(self):
            return b""

    return Response()


def delta_line(text: str) -> str:
    return "data: " + json.dumps(
        {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
    )


def provider_config(**changes) -> ProviderConfig:
    base = {
        "provider": "openai-compatible",
        "base_url": "http://127.0.0.1:9/v1",
        "model": "stub-model",
        "api_key": "test-key",
    }
    base.update(changes)
    return ProviderConfig(**base)


# --------------------------------------------------------------------------- #
# the renderer: coalesced, incremental, honest                                 #
# --------------------------------------------------------------------------- #


def test_the_first_delta_is_rendered_at_once_and_the_rest_obey_the_bound():
    clock, tty = Clock(), FakeTTY()
    renderer = stream_render.StreamRenderer(tty, tty=True, interval_ms=60, clock=clock)
    renderer.arm()
    renderer.delta("Hel")            # first chunk: the felt latency, written immediately
    assert tty.getvalue() == "Hel"
    clock.advance(0.01)
    renderer.delta("lo")             # inside the interval: coalesced, not lost
    assert tty.getvalue() == "Hel"
    clock.advance(0.05)
    renderer.delta(" wo")
    assert tty.getvalue() == "Hel"
    clock.advance(0.06)
    renderer.delta("rld\n")
    assert "Hello world\n" in tty.getvalue()
    facts = renderer.finish()
    assert facts["deltas"] == 4
    assert facts["chars"] == 12
    assert facts["updates"] == 2, "one immediate plus one after the interval"
    assert facts["coalesced"] == 2
    assert facts["interval_ms"] == 60
    assert renderer.text == "Hello world\n"


def test_the_answer_is_only_ever_appended_never_reprinted():
    """The terminal's whole advantage: nothing that was written is written again."""
    clock, tty = Clock(), FakeTTY()
    renderer = stream_render.StreamRenderer(
        tty, tty=True, interval_ms=60, clock=clock, footer=False
    )
    renderer.arm()
    pieces = ["alpha ", "beta ", "gamma ", "delta\n"]
    for piece in pieces:
        renderer.delta(piece)
        clock.advance(0.07)
    renderer.finish()
    body = renderer.text
    plain = tty.getvalue().replace("\r\x1b[K", "")
    assert plain.count(body) == 1, "the whole answer appears exactly once"
    for piece in pieces:
        assert plain.count(piece) == 1, f"{piece!r} was written twice"
    assert body == "".join(pieces), "nothing was dropped between renders"


def test_the_footer_shows_measured_elapsed_time_and_tokens():
    clock, tty = Clock(), FakeTTY()
    renderer = stream_render.StreamRenderer(tty, tty=True, interval_ms=60, clock=clock)
    renderer.arm()
    clock.advance(0.5)
    renderer.delta("a line of text\n")
    out = tty.getvalue()
    assert "\r\x1b[K" in out, "the footer is drawn in place, after a line boundary"
    footer = out.split("\r\x1b[K", 1)[1]
    assert "0.5" in footer, "the elapsed time is measured, not estimated"
    assert "deltas" in footer and "tok" in footer
    assert stream_render.TOKEN_METHOD in footer, "the token number names its method"
    facts = renderer.finish()
    assert facts["footer_updates"] == 1
    assert facts["first_content_ms"] == 500.0
    assert facts["tokens_estimate"] == len("a line of text\n") // 4


def test_the_footer_never_lands_inside_a_line_the_model_is_writing():
    clock, tty = Clock(), FakeTTY()
    renderer = stream_render.StreamRenderer(tty, tty=True, interval_ms=60, clock=clock)
    renderer.arm()
    renderer.delta("half a line")
    clock.advance(0.5)
    renderer.delta(" still no newline")
    assert "\r\x1b[K" not in tty.getvalue(), "mid-line: the footer waits for a line boundary"
    clock.advance(0.5)
    renderer.delta(" and now it ends\n")
    assert "\r\x1b[K" in tty.getvalue()


def test_a_pipe_gets_no_escapes_and_no_per_chunk_noise():
    clock, buffer = Clock(), io.StringIO()
    renderer = stream_render.StreamRenderer(buffer, tty=False, interval_ms=60, clock=clock)
    renderer.arm()
    for piece in ("one ", "two ", "three"):
        renderer.delta(piece)
        clock.advance(1.0)
    assert buffer.getvalue() == "", "nothing is written per chunk on a pipe"
    facts = renderer.finish()
    assert buffer.getvalue() == "one two three", "the answer is written once, whole"
    assert "\x1b" not in buffer.getvalue()
    assert facts["paced"] is False and facts["tty"] is False
    assert facts["updates"] == 1
    assert facts["deltas"] == 3, "the measured stream facts are kept even when nothing renders"
    assert facts["first_content_ms"] == 0.0


def test_a_terminal_that_dies_mid_stream_does_not_break_the_run():
    class Broken(io.StringIO):
        def isatty(self) -> bool:
            return True

        def write(self, text: str) -> int:
            raise OSError("terminal gone")

    renderer = stream_render.StreamRenderer(Broken(), tty=True)
    renderer.arm()
    renderer.delta("text")
    renderer.delta(" more\n")
    facts = renderer.finish()
    assert facts["write_errors"] >= 1
    assert facts["chars"] == len("text more\n"), "the stream is still accounted for"


def test_closing_a_call_leaves_the_row_clean_on_a_terminal():
    """The defect this closes: the progress line landed *inside* the answer's last line."""
    clock, tty = Clock(), FakeTTY()
    renderer = stream_render.StreamRenderer(tty, tty=True, interval_ms=60, clock=clock)
    renderer.arm()
    renderer.delta("an answer without a trailing newline")
    clock.advance(0.1)
    renderer.close_call()
    assert tty.getvalue().endswith("an answer without a trailing newline\n"), (
        "a terminal gets a terminator so the next writer starts a fresh row"
    )
    renderer.delta(" and it continues\n")
    assert renderer.text.endswith("continues\n")


def test_a_pipe_is_never_given_a_byte_the_model_did_not_send():
    clock, buffer = Clock(), io.StringIO()
    renderer = stream_render.StreamRenderer(buffer, tty=False, interval_ms=60, clock=clock)
    renderer.arm()
    renderer.delta("no trailing newline here")
    renderer.close_call()
    assert buffer.getvalue() == "", "still nothing until finish()"
    renderer.finish()
    assert buffer.getvalue() == "no trailing newline here", "byte-identical to the model's text"


def test_closing_a_call_writes_the_tail_immediately():
    clock, tty = Clock(), FakeTTY()
    renderer = stream_render.StreamRenderer(tty, tty=True, interval_ms=60, clock=clock)
    renderer.arm()
    renderer.delta("tail that would have waited\n")
    clock.advance(0.01)
    renderer.delta("for the next interval")
    renderer.close_call()
    assert "for the next interval" in tty.getvalue(), "the tail is not held back"


def test_agent_run_hands_the_loop_the_three_streaming_hooks(tmp_path, monkeypatch):
    """The CLI wiring itself: a renderer that is built but not handed over would be a lie."""
    from skaldir.agent_loop import AgentRunResult

    captured: dict[str, Any] = {}

    class Recorder:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def run(self, task, *, run_id=""):
            return AgentRunResult(run_id="r", status="answered", answer="ok")

    monkeypatch.setattr(agent_loop_cli, "AgentLoop", Recorder)
    monkeypatch.setattr(
        agent_loop_cli,
        "_client_and_status",
        lambda args: (StreamingClient(["x"]), {"ready": True}),
    )
    parser = agent_loop_cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            "say something",
            "--tools", "builtin",
            "--workspace", str(tmp_path),
            "--runtime", str(tmp_path / "rt"),
            "--stream",
        ]
    )
    code = agent_loop_cli.cmd_run(args)
    assert code == 0
    assert callable(captured.get("on_delta")), "the content sink must reach the loop"
    assert callable(captured.get("on_request")), "the renderer's clock is armed by the loop"
    assert callable(captured.get("on_answer")), "and the tail is flushed by the loop"
    assert captured.get("stream") is True, "and the client is asked to stream"
    metrics = agent_loop_cli._answer_renderer(args).as_dict()
    assert metrics["token_method"] == "chars/4", "the token number names its method"
    assert metrics["token_source"] == "estimate"
    assert "first_content_ms" in metrics and "updates" in metrics


def test_agent_run_creates_no_renderer_without_streaming(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agent_loop_cli,
        "_client_and_status",
        lambda args: (StreamingClient(["x"]), {"ready": True}),
    )
    parser = agent_loop_cli.build_parser()
    args = parser.parse_args(
        ["run", "say something", "--tools", "builtin", "--workspace", str(tmp_path),
         "--runtime", str(tmp_path / "rt"), "--no-stream"]
    )
    assert agent_loop_cli._answer_renderer(args) is None, "no sink, no --stream on the wire"


def test_the_loop_flushes_the_answer_before_any_progress_line(tmp_path):
    order: list[str] = []

    class Flush:
        def arm(self) -> None:
            order.append("armed")

        def close(self) -> None:
            order.append("flushed")

    client = StreamingClient(["x"])
    run_loop(
        client, tmp_path,
        on_delta=lambda text: None,
        on_request=Flush().arm,
        on_answer=Flush().close,
    )
    assert order == ["armed", "flushed"]
    kinds = [kind for kind, _ in run_loop(client, tmp_path, on_answer=Flush().close)[1]]
    assert kinds.index("model.response") == 1, "the flush happens before the note is emitted"


def test_the_loop_survives_an_answer_hook_that_raises(tmp_path):
    def boom() -> None:
        raise RuntimeError("no")

    client = StreamingClient(["fine"])
    result, _ = run_loop(client, tmp_path, on_delta=lambda text: None, on_answer=boom)
    assert result.status == "answered" and result.answer == "fine"


def test_the_token_number_is_an_estimate_and_says_so_never_a_zero():
    renderer = stream_render.StreamRenderer(io.StringIO(), tty=False)
    renderer.arm()
    renderer.delta("ab")
    assert renderer.metrics.tokens_estimate is None, "too little text to claim tokens"
    renderer.delta("cd")
    assert renderer.metrics.tokens_estimate == 1
    assert renderer.metrics.as_dict()["token_method"] == "chars/4"
    assert renderer.metrics.as_dict()["token_source"] == "estimate"


def test_a_stream_that_never_started_reports_the_reason_not_a_zero():
    renderer = stream_render.StreamRenderer(io.StringIO(), tty=False)
    renderer.arm()
    facts = renderer.finish()
    assert facts["streamed"] is False
    assert facts["deltas"] == 0
    assert facts["tokens_estimate"] is None
    assert "no content delta" in facts["reason"]


# --------------------------------------------------------------------------- #
# the gate: a terminal streams, a pipe/--json does not                         #
# --------------------------------------------------------------------------- #


def test_stream_hz_sets_the_bound_the_renderer_uses(tmp_path):
    parser = agent_loop_cli.build_parser()
    base = ["run", "t", "--tools", "builtin", "--workspace", str(tmp_path),
            "--runtime", str(tmp_path / "rt"), "--stream"]
    default = agent_loop_cli._answer_renderer(parser.parse_args(base))
    assert default.metrics.interval_ms == stream_render.DEFAULT_INTERVAL_MS == 60
    slow = agent_loop_cli._answer_renderer(parser.parse_args([*base, "--stream-hz", "5"]))
    assert slow.metrics.interval_ms == 200, "5 updates/s is a 200 ms bound"
    fast = agent_loop_cli._answer_renderer(parser.parse_args([*base, "--stream-hz", "1000"]))
    assert fast.metrics.interval_ms == stream_render.MIN_INTERVAL_MS, "and it is clamped"


def test_the_gate_decides_from_the_stream_and_the_flags():
    tty, pipe = FakeTTY(), io.StringIO()
    assert stream_render.enabled_for(tty) is True
    assert stream_render.enabled_for(pipe) is False
    assert stream_render.enabled_for(tty, json_mode=True) is False, "one envelope, one stdout"
    assert stream_render.enabled_for(tty, quiet=True) is False
    assert stream_render.enabled_for(pipe, requested=True) is True, "an explicit ask wins"
    assert stream_render.enabled_for(tty, requested=False) is False


def test_agent_run_declares_the_streaming_flags():
    parser = agent_loop_cli.build_parser()
    action = next(
        item
        for item in parser._actions
        if isinstance(item, __import__("argparse")._SubParsersAction)
    )
    run = action.choices["run"]
    flags = {option for item in run._actions for option in item.option_strings}
    assert {"--stream", "--no-stream", "--stream-hz"} <= flags
    assert run.parse_args(["task", "--no-stream"]).stream is False
    assert run.parse_args(["task", "--stream"]).stream is True
    assert run.parse_args(["task"]).stream is None, "the default is decided at run time"


# --------------------------------------------------------------------------- #
# the provider: the sink is called where the bytes arrive                      #
# --------------------------------------------------------------------------- #


def test_the_streamed_reader_hands_every_content_delta_to_the_sink():
    seen: list[str] = []
    client = llm_provider.OpenAICompatibleLLM(provider_config(stream=True), on_delta=seen.append)
    client._request_started = time.monotonic()
    result = client._read_chat_stream(
        fake_sse(
            [
                ": keep-alive",
                delta_line("Hel"),
                "",
                delta_line("lo"),
                "data: [DONE]",
            ]
        )
    )
    assert result.text == "Hello"
    assert seen == ["Hel", "lo"], "each delta reaches the sink as it arrives, in order"
    stats = client.stream_stats
    assert stats["deltas"] == 2 and stats["chars"] == 5
    assert stats["first_content_ms"] is not None and stats["first_content_ms"] >= 0
    assert stats["last_content_ms"] >= stats["first_content_ms"]


def test_a_role_only_chunk_is_not_a_token():
    seen: list[str] = []
    client = llm_provider.OpenAICompatibleLLM(provider_config(stream=True), on_delta=seen.append)
    client._request_started = time.monotonic()
    role = "data: " + json.dumps({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})
    client._read_chat_stream(fake_sse([role, delta_line("x"), "data: [DONE]"]))
    assert seen == ["x"], "a role-only chunk carries no content and must not be counted"
    assert client.stream_stats["deltas"] == 1
    assert client.first_byte_ms() is not None


def test_a_raising_sink_is_counted_and_never_breaks_the_answer():
    def boom(text: str) -> None:
        raise RuntimeError("renderer exploded")

    client = llm_provider.OpenAICompatibleLLM(provider_config(stream=True), on_delta=boom)
    client._request_started = time.monotonic()
    result = client._read_chat_stream(fake_sse([delta_line("ok"), "data: [DONE]"]))
    assert result.text == "ok", "the answer survives a broken renderer"
    assert client.stream_stats["sink_errors"] == 1
    assert client.stream_stats["deltas"] == 1


def test_a_sink_that_mutates_nothing_leaves_the_old_path_intact():
    """No sink ⇒ the accumulation path is byte-identical to what it always was."""
    client = llm_provider.OpenAICompatibleLLM(provider_config(stream=True))
    client._request_started = time.monotonic()
    result = client._read_chat_stream(fake_sse([delta_line("a"), delta_line("b"), "data: [DONE]"]))
    assert result.text == "ab"
    assert client.stream_stats["deltas"] == 2, "the stream is still measured"
    assert client.on_delta is None


def test_the_request_body_carries_stream_only_when_it_is_asked_for():
    cfg = provider_config(stream=False)
    plain = llm_provider.chat_request_body(cfg, [{"role": "user", "content": "hi"}], tier="main")
    assert "stream" not in plain, "the configured value is respected"
    forced = llm_provider.chat_request_body(
        cfg, [{"role": "user", "content": "hi"}], tier="main", stream=True
    )
    assert forced["stream"] is True, "a caller with a sink can ask for this one call"
    off = llm_provider.chat_request_body(
        provider_config(stream=True), [{"role": "user", "content": "hi"}], tier="main", stream=False
    )
    assert "stream" not in off


def test_the_client_learns_to_stream_from_the_constructor_too():
    client = llm_provider.OpenAICompatibleLLM(provider_config(), on_delta=lambda text: None)
    assert client.on_delta is not None
    assert client.stream_stats == llm_provider._empty_stream_stats()
    assert client.stream_stats["first_content_ms"] is None


# --------------------------------------------------------------------------- #
# the loop: it owns the seam, not the pace                                     #
# --------------------------------------------------------------------------- #


def test_the_loop_hands_every_delta_to_the_sink_and_reports_where_it_came_from(tmp_path):
    renderer = stream_render.StreamRenderer(io.StringIO(), tty=False)
    client = StreamingClient(["Hel", "lo ", "wor", "ld\n"])
    result, events = run_loop(
        client, tmp_path, on_delta=renderer.delta, on_request=renderer.arm, stream=True
    )
    assert result.answer == "Hello world\n"
    assert renderer.text == "Hello world\n", "the renderer saw the answer as it was written"
    assert client.calls == [{"stream": True, "sink": True}], "the client was asked to stream"
    streaming = result.streaming
    assert streaming["streamed"] is True
    assert streaming["deltas"] == 4 and streaming["chars"] == 12
    assert streaming["source"] == "provider", "the deltas come from the provider client"
    assert streaming["deltas_rendered"] == 4
    assert streaming["first_content_ms"] is not None
    assert streaming["token_method"] == "chars/4"
    assert any(kind == "model.tokens" for kind, _ in events), (
        "the live-progress renderer is told while the stream flows"
    )
    assert client.on_delta is None, "the sink is detached again — the client outlives the run"
    assert result.to_dict()["streaming"]["streamed"] is True


def test_the_loop_arms_the_clock_before_the_request_is_sent(tmp_path):
    order: list[str] = []
    client = StreamingClient(["x"])
    original = client.chat

    def chat(*args, **kwargs):
        order.append("call")
        return original(*args, **kwargs)

    client.chat = chat  # type: ignore[method-assign]
    run_loop(
        client, tmp_path, on_delta=lambda text: None,
        on_request=lambda: order.append("armed"), stream=True,
    )
    assert order == ["armed", "call"], "the renderer's clock starts at the request, not later"


def test_a_streaming_renderer_that_raises_cannot_change_the_run(tmp_path):
    def boom(text: str) -> None:
        raise RuntimeError("boom")

    client = StreamingClient(["a", "b"])
    result, _ = run_loop(client, tmp_path, on_delta=boom, stream=True)
    assert result.status == "answered", "a broken renderer is not a run failure"
    assert result.answer == "ab"
    assert result.streaming["streamed"] is False, "no delta reached a *working* sink"
    assert result.streaming["deltas"] == 2, "but the provider's own count is still reported"
    assert result.streaming["deltas_rendered"] == 0
    assert result.streaming["sink_errors"] == 2
    assert "raised on every delta" in result.streaming["reason"]


def test_without_a_sink_nothing_is_asked_for_and_the_old_path_stays(tmp_path):
    client = StreamingClient(["whole answer"])
    result, events = run_loop(client, tmp_path)
    assert client.calls == [{"stream": None, "sink": False}], "no stream= was passed"
    assert result.streaming["streamed"] is False
    assert result.streaming["reason"] == "no content sink was attached"
    assert [kind for kind, _ in events] == ["model.request", "model.response"]
    assert events[-1][1]["streaming"]["streamed"] is False
    assert events[-1][1]["streaming"]["reason"] == "no content sink was attached"


def test_a_client_without_a_stream_parameter_is_called_exactly_as_before(tmp_path):
    client = NonStreamingClient("the whole answer")
    result, _ = run_loop(client, tmp_path, on_delta=lambda text: None, stream=True)
    assert result.answer == "the whole answer", "no TypeError from a keyword it cannot take"
    assert result.streaming["streamed"] is False
    assert result.streaming["reason"] == "the provider config did not stream (stream: false)"
    assert client.calls == [{"tier": "main"}]


def test_a_provider_that_was_not_asked_to_stream_says_so(tmp_path):
    client = StreamingClient(["text"], )
    result, _ = run_loop(client, tmp_path, on_delta=lambda text: None, stream=False)
    assert result.streaming["streamed"] is False
    assert result.streaming["streaming_requested"] is False
    assert result.streaming["deltas"] == 0, "a whole-answer call carries no delta"
    assert result.streaming["reason"] == "the provider config did not stream (stream: false)"
    assert result.answer == "text", "the answer still arrives whole"


# --------------------------------------------------------------------------- #
# live progress: bounded, and silent while content flows                       #
# --------------------------------------------------------------------------- #


def test_progress_stays_silent_while_the_answer_is_flowing():
    sink = io.StringIO()
    reporter = progress_mod.RunProgress(
        sink, theme=None, keepalive_thread=False, min_interval_ms=0
    )
    reporter.begin("agent run")
    reporter.model_call(1)
    before = sink.getvalue()
    reporter.event("model.tokens", step=1, deltas=10, chars=100, tokens_estimate=25)
    assert reporter.tick() == "", "no repaint while tokens are reaching the terminal"
    assert sink.getvalue() == before, "and nothing extra was written either"
    line = reporter.event(
        "model.response",
        step=1,
        duration_ms=1200,
        text_chars=100,
        streaming={
            "streamed": True,
            "deltas": 10,
            "chars": 100,
            "tokens_estimate": 25,
            "first_content_ms": 300.0,
            "token_method": "chars/4",
        },
    )
    assert "first token 0.30 s" in line, "the measured time to first token is on the line"
    assert "10 deltas" in line and "~25 tok (est. chars/4)" in line


def test_progress_can_draw_again_once_the_flow_stops():
    sink = io.StringIO()
    reporter = progress_mod.RunProgress(
        sink, theme=None, keepalive_thread=False, min_interval_ms=0, keepalive_s=0
    )
    reporter.begin("agent run")
    reporter.event("model.tokens", step=1, deltas=1, chars=4, tokens_estimate=1)
    assert reporter.tick() == "", "suppressed: content is flowing"
    reporter.event("model.response", step=1, duration_ms=10, tool_calls=0, text_chars=4)
    reporter.model_call(2)  # a new wait is open, no content flowing
    drawn = reporter.tick()
    assert drawn != "", "the flag was cleared: the wait line is drawn again"
    assert "still working" in drawn
    assert reporter.tick() == "", "an unchanged line costs no write at all"


def test_the_wait_line_carries_the_tokens_it_has_really_seen():
    """While the stream flows the facts are kept; the wait line shows them if it repaints."""
    sink = io.StringIO()
    reporter = progress_mod.RunProgress(
        sink, theme=None, keepalive_thread=False, min_interval_ms=0, keepalive_s=99
    )
    reporter.begin("agent run")
    reporter.event("model.tokens", step=1, deltas=42, chars=421, tokens_estimate=105)
    reporter.model_call(2)
    reporter.model_answer(1, duration_ms=900)
    text = sink.getvalue()
    assert "42 deltas" in text and "~105 tok (est. chars/4)" in text


# --------------------------------------------------------------------------- #
# the result block: an answer that is on screen is not printed twice           #
# --------------------------------------------------------------------------- #


def test_a_fully_streamed_answer_is_not_reprinted(capsys):
    agent_loop_cli._render_result(
        {
            "run_id": "r1",
            "status": "answered",
            "reason": "",
            "steps": 1,
            "tool_calls": 0,
            "executed_calls": 0,
            "duration_ms": 1200,
            "usage": {},
            "answer": "the streamed answer",
            "transcript_path": "/tmp/x.jsonl",
            "streaming": {"deltas": 30, "streamed": True},
        },
        streamed_text="preamble\n the streamed answer",
    )
    out = capsys.readouterr().out
    assert "streamed above" in out, "the block says where the answer is instead of repeating it"
    assert "the streamed answer" not in out, "the answer is not printed a second time"
    assert "30 deltas" in out


def test_an_answer_that_did_not_stream_is_printed_complete(capsys):
    agent_loop_cli._render_result(
        {
            "run_id": "r2",
            "status": "answered",
            "reason": "",
            "steps": 1,
            "tool_calls": 0,
            "executed_calls": 0,
            "duration_ms": 1200,
            "usage": {},
            "answer": "line one\nline two",
            "transcript_path": "/tmp/x.jsonl",
            "streaming": {},
        }
    )
    out = capsys.readouterr().out
    assert "answer:" in out
    assert "    line one" in out and "    line two" in out


def test_the_activity_line_separator_keeps_its_spaces_in_both_modes():
    from skaldir import theme as theme_mod

    plain = theme_mod.detect(io.StringIO(), mode="plain", no_color=True)
    line = render.activity_line("streaming", {"deltas": 12, "tok": "~30 (est. chars/4)"},
                                theme=plain)
    assert "12 deltas - ~30 (est. chars/4) tok" in line
    assert line.isascii()


# --------------------------------------------------------------------------- #
# markdown-lite while streaming: the look, the guarantee, the flicker number    #
# --------------------------------------------------------------------------- #


def unicode_theme() -> Any:
    """The product's glyphs with no escape sequences — the look, readable in a test."""
    return theme_mod.Theme(mode=theme_mod.MODE_PLAIN, depth=theme_mod.DEPTH_NONE,
                           glyphs=theme_mod.GLYPHS_UNICODE)


def rich_theme() -> Any:
    """The product's glyphs *with* weight — what proves bold is bold and not just gone."""
    return theme_mod.Theme(mode=theme_mod.MODE_RICH, depth=theme_mod.DEPTH_BASIC,
                           glyphs=theme_mod.GLYPHS_UNICODE)


def markdown_renderer(tty, *, theme=None, clock=None, **kwargs) -> Any:
    options: dict[str, Any] = {
        "tty": True,
        "theme": theme if theme is not None else unicode_theme(),
        "interval_ms": 0,
        "footer": False,
        "columns": 60,
    }
    options.update(kwargs)
    if clock is not None:
        options["clock"] = clock
    return stream_render.MarkdownStream(tty, **options)


def feed(renderer, text: str, *, clock=None, step: float = 0.0) -> None:
    for char in text:
        renderer.delta(char)
        if clock is not None and step:
            clock.advance(step)


MARKDOWN_SAMPLE = (
    "# Report\n"
    "\n"
    "Here is **bold** and `inline code` in one line.\n"
    "\n"
    "- first item\n"
    "- second item\n"
    "\n"
    "```python\n"
    "print('hello')\n"
    "```\n"
)


def test_bold_and_inline_code_are_drawn_and_their_markers_are_consumed():
    tty = FakeTTY()
    renderer = markdown_renderer(tty, theme=rich_theme())
    renderer.arm()
    feed(renderer, "Here is **bold** and `code`.\n")
    renderer.finish()
    out = tty.getvalue()
    assert "\x1b[1mbold\x1b[0m" in out, "bold is drawn, not just stripped"
    assert "\x1b[1;2mcode\x1b[0m" in out, "inline code is drawn"
    assert "**" not in out and "`" not in out, "the markers themselves are consumed"


def test_lists_headings_and_blocks_take_their_form():
    tty = FakeTTY()
    renderer = markdown_renderer(tty)
    renderer.arm()
    feed(renderer, "# Title\n\n- one\n- two\n\n1. first\n2. second\n\n> quoted\n")
    renderer.finish()
    lines = [line.strip() for line in tty.getvalue().splitlines() if line.strip()]
    assert lines[0] == "Title", "a heading loses its hashes"
    assert "• one" in tty.getvalue() and "• two" in tty.getvalue()
    assert "1. first" in tty.getvalue() and "2. second" in tty.getvalue()
    assert "│ quoted" in tty.getvalue(), "a quote keeps a rule, not a chevron"


def test_a_fence_becomes_a_ruled_block_and_the_fences_themselves_are_consumed():
    tty = FakeTTY()
    renderer = markdown_renderer(tty)
    renderer.arm()
    feed(renderer, MARKDOWN_SAMPLE)
    renderer.finish()
    out = tty.getvalue()
    assert "```" not in out, "the fence lines are scaffolding and cost no row"
    assert "│ print('hello')" in out, "the block's left rule carries its shape"
    # the fence costs no row at all: the row before it is the model's blank line
    assert "\n\n\n" not in out, "a fence must not leave blank rows behind"


def test_partial_markup_is_held_not_guessed():
    assert stream_render.safe_prefix("plain") == 5
    assert stream_render.safe_prefix("a **bold") == 2, "an unclosed bold is undecided"
    assert stream_render.safe_prefix("a **bold**") == 10
    assert stream_render.safe_prefix("a `code") == 2, "an unclosed code span is undecided"
    assert stream_render.safe_prefix("a `code` done") == 13
    assert stream_render.safe_prefix("a * b") == 5, "a lone star is literal, never italic"
    assert stream_render.safe_prefix("") == 0


def test_a_chunk_that_ends_inside_a_marker_prints_nothing_broken():
    """The hard guarantee: half a pair is worse than nothing, so it is held."""
    tty = FakeTTY()
    renderer = markdown_renderer(tty, theme=rich_theme())
    renderer.arm()
    renderer.delta("the answer is **bo")
    assert tty.getvalue() == "", "a line still inside a marker is buffered, not printed"
    renderer.delta("ld**\n")
    out = tty.getvalue()
    assert "**" not in out and "\x1b[1mbold\x1b[0m" in out
    renderer.finish()


def test_no_prefix_of_a_markdown_answer_can_ever_print_broken_markup():
    """Every prefix of the sample: appended text only, and no marker ever leaks.

    This is the strongest statement the renderer makes. If rendering prefix *k* were not a
    prefix of rendering prefix *k+1*, then some byte the user already read would have been
    rewritten — the exact defect the flicker number exists to catch.
    """
    previous = ""
    for cut in range(len(MARKDOWN_SAMPLE) + 1):
        tty = FakeTTY()
        renderer = markdown_renderer(tty)
        renderer.arm()
        feed(renderer, MARKDOWN_SAMPLE[:cut])
        visible = tty.getvalue()
        assert visible.startswith(previous), (
            f"prefix {cut} rewrote a line the reader had already seen"
        )
        previous = visible
        assert "**" not in visible, "half a bold pair reached the screen"
        assert "`" not in visible, "half a code span reached the screen"


def test_a_marker_that_never_closes_is_printed_as_the_character_it_is():
    """At a line end the question is settled: an unclosed marker is literal, not lost."""
    tty = FakeTTY()
    renderer = markdown_renderer(tty)
    renderer.arm()
    feed(renderer, "a line with **no partner\n")
    renderer.finish()
    assert "**no partner" in tty.getvalue(), "the answer never loses a byte"


def test_a_line_is_written_once_and_the_answer_does_not_flicker():
    """The measurement the whole change is for: written rows vs rows that changed."""
    clock, tty = Clock(), FakeTTY()
    renderer = markdown_renderer(tty, clock=clock, footer=True, tail_flush_ms=250)
    renderer.arm()
    for line in MARKDOWN_SAMPLE.splitlines(keepends=True):
        renderer.delta(line)
        clock.advance(0.05)  # every line lands well inside the stall grace period
    facts = renderer.end_turn()
    assert facts["screen_measured"] is True
    assert facts["lines_written"] >= 6
    assert facts["lines_changed"] == 0, "a finished row was appended to after it was shown"
    assert facts["lines_redrawn"] == 0
    assert facts["flicker_ratio"] == 0.0


def test_batching_obeys_the_bound_and_drops_no_character():
    clock, tty = Clock(), FakeTTY()
    renderer = markdown_renderer(tty, clock=clock, interval_ms=60, footer=False, columns=80)
    renderer.arm()
    document = "".join(f"line {n} with **bold** text\n" for n in range(24))
    feed(renderer, document, clock=clock, step=0.004)   # 250 deltas a second
    facts = renderer.finish()

    reference = FakeTTY()
    whole = markdown_renderer(reference, interval_ms=0, footer=False, columns=80)
    whole.arm()
    feed(whole, document)
    whole.finish()

    assert facts["deltas"] == len(document), "every delta was accepted"
    assert facts["chars"] == len(document), "no character was dropped between renders"
    assert facts["coalesced"] > 0, "the bound really folded deltas together"
    bound = facts["elapsed_ms"] / 60 + 1
    assert facts["updates"] <= bound, f"{facts['updates']} updates over a {bound:.0f} bound"
    assert tty.getvalue() == reference.getvalue(), (
        "batched and unbatched renders must be byte-identical — batching may not be visible"
    )


def test_the_turn_ends_on_a_fresh_line_with_one_status_band():
    clock, tty = Clock(), FakeTTY()
    renderer = markdown_renderer(tty, clock=clock, footer=True)
    renderer.arm()
    renderer.delta("an answer whose last line never ended")
    clock.advance(0.2)
    facts = renderer.end_turn()
    out = tty.getvalue()
    assert out.endswith("\n"), "the cursor lands on a fresh line"
    assert facts["status_band_updates"] == 1, "the final numbers appear exactly once"
    assert facts["chrome_rows"] == 1, "the band reused the footer's row instead of adding one"
    assert facts["lines_changed"] == 0, "the answer itself was still written once"
    band = out.splitlines()[-1]
    assert "streamed" in band and "tok" in band and stream_render.TOKEN_METHOD in band


def test_a_stalled_line_is_shown_after_the_grace_period_and_the_cost_is_counted():
    """A model stuck inside one enormous line must not look like a hang — and must be billed."""
    clock, tty = Clock(), FakeTTY()
    renderer = markdown_renderer(tty, clock=clock, footer=False, columns=200, tail_flush_ms=250)
    renderer.arm()
    renderer.delta("a very long single line that never ends " * 2)
    clock.advance(0.30)
    renderer.delta("and keeps going " * 3)
    clock.advance(0.30)
    renderer.delta("until it finally does\n")
    facts = renderer.finish()
    assert facts["lines_written"] == 1
    assert facts["lines_changed"] == 1, "the row grew after it was visible: that is a change"
    assert facts["lines_redrawn"] == 0, "it was grown, never taken back"

    strict = markdown_renderer(FakeTTY(), clock=Clock(), footer=False, columns=200,
                               tail_flush_ms=0)
    strict.arm()
    feed(strict, "a very long single line that never ends " * 5)
    strict.finish()
    assert strict.metrics.lines_changed == 0, "holding the tail strictly costs no change"


def test_a_pipe_gets_the_models_own_markdown_byte_for_byte():
    """Copy-paste safety: a code block bound for a paste may not carry a single added byte."""
    document = "# title\n\nA **bold** and `code`.\n\n```python\nx = '`' + \"**\"\n```\n"
    buffer = io.StringIO()
    renderer = stream_render.MarkdownStream(buffer, tty=False, theme=rich_theme(),
                                            interval_ms=60)
    renderer.arm()
    feed(renderer, document)
    renderer.end_turn()
    assert buffer.getvalue() == document, "the model's bytes, unchanged"
    assert "\x1b" not in buffer.getvalue(), "no escape sequence on a pipe"
    assert "│" not in buffer.getvalue(), "no rule on a pipe"


def test_a_wide_code_row_wraps_without_breaking_the_block_left_rule():
    body = "y" * 70 + " end"
    block = stream_render.render_code_block(f"x = 1\n{body}\n", columns=40, theme=unicode_theme())
    rows = block.splitlines()
    assert all(row.startswith("│ ") for row in rows), "every visual row keeps the rule"
    assert "".join(row[2:] for row in rows[1:]) == body, "wrapping loses nothing"


def test_a_wrapped_code_row_breaks_at_a_space_and_loses_nothing():
    body = "value = compute(alpha, beta) + compute(gamma, delta)  # a comment past the edge"
    block = stream_render.render_code_block(body + "\n", columns=44, theme=unicode_theme())
    rows = block.splitlines()
    assert "".join(row[2:] for row in rows) == body, "wrapping must lose nothing"
    assert all(len(row) <= 44 for row in rows), "no visual row may exceed the width"
    for row in rows[:-1]:
        content = row[2:]
        assert content.endswith(" ") or len(content) == 42, (
            "a break must fall on a space unless there is none to use"
        )


def test_line_numbers_are_optional_and_a_wrapped_row_is_not_renumbered():
    body = "z" * 60
    numbered = stream_render.render_code_block(f"a\n{body}\n", columns=30,
                                               line_numbers=True, theme=unicode_theme())
    rows = numbered.splitlines()
    assert rows[0].startswith("│   1 ")
    assert rows[1].startswith("│   2 ")
    assert rows[2].startswith("│     "), "a continuation row must not claim a line number"
    assert all(stream_render.render_code_block("a\nb\n", columns=30, line_numbers=False,
                                               theme=unicode_theme()).splitlines()[n][2] in "ab"
               for n in (0, 1))


def test_a_huge_tool_return_prints_a_head_and_names_exactly_what_it_hid():
    body = "\n".join(f"row {n}" for n in range(1, 1001))
    shown = stream_render.bounded_output(body, max_lines=5, theme=unicode_theme())
    lines = shown.splitlines()
    assert len(lines) == 6, "five rows of head and one row that says what was left out"
    assert lines[0].strip() == "row 1" and lines[4].strip() == "row 5"
    assert "… +995 lines" in lines[5], "the count is the measurement, not a rounding"
    assert stream_render.LONG_OUTPUT_HINT_TAIL in lines[5]
    assert "/export" in lines[5]


def test_a_small_tool_return_is_not_hinted_at():
    shown = stream_render.bounded_output("a\nb", max_lines=5, theme=unicode_theme())
    assert shown.splitlines() == ["  a", "  b"]
    assert "lines" not in shown
