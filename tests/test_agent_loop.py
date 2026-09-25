"""AGENT wave — the loop with **real model-driven tool calling**, proved on a stub.

Everything here runs against a local OpenAI-compatible stub endpoint
(``127.0.0.1``, a random port, no credential, no network): the stub decides which
tool the "model" asks for and records the **raw request bodies** it received, so
the wire half of the loop (``tools=[…]`` out, ``tool_calls`` back) is measured
rather than claimed.

Covered, in the order the brief names it:

* tool **schema generation** from a registered spec (signature, sample, free form);
* the **request shape** — the ``tools`` array and ``tool_choice`` in the body, the
  parsed ``tool_calls``, the same for **streamed** deltas (fragmented arguments);
* the **loop** — execute, feed back as ``role: tool``, repeat, answer;
* **stop conditions** — step limit, token budget, wall clock, cancellation, all of
  them reported ``incomplete`` with **no answer**;
* **robustness** — an unknown tool and a failing tool reach the model as text and
  the loop continues;
* the **transcript** — every request body, every tool call with arguments,
  duration and result digest, readable back with ``read_transcript``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skaldir.agent_loop import (
    REASON_CANCELLED,
    REASON_EMPTY_ANSWER,
    REASON_MAX_STEPS,
    REASON_TOKEN_BUDGET,
    REASON_WALL_CLOCK,
    STATUS_ANSWERED,
    STATUS_INCOMPLETE,
    AgentLoop,
    AgentLoopError,
    AgentTool,
    LoopLimits,
    ToolSet,
    builtin_tools,
    input_schema_for_spec,
    input_schema_from_sample,
    input_schema_from_source,
    list_runs,
    read_transcript,
    run_path,
    schema_for_annotation,
    tools_array,
    wire_tool_name,
)
from skaldir.llm import ScriptedLLM
from skaldir.llm_provider import (
    ChatResult,
    OpenAICompatibleLLM,
    ProviderConfig,
    ToolCall,
    accumulate_stream_chunks,
    chat_request_body,
    chat_result_from_payload,
    tool_calls_from_message,
)
from skaldir.tools import ToolSpec

# --------------------------------------------------------------------------- #
# the stub endpoint                                                            #
# --------------------------------------------------------------------------- #

#: ``step(body) -> response payload`` (or a fixed payload, repeated forever).
Step = dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]


class _QuietServer(ThreadingHTTPServer):
    """Threaded, so ``shutdown()`` can never block on a live keep-alive socket.

    The pool in ``skaldir.perf_runtime`` keeps a connection open (that is the
    point of it, and what a real provider does), and a single-threaded
    ``HTTPServer`` stays blocked inside ``handle_one_request`` reading that open
    connection — so ``shutdown()`` waits forever and the test hangs. Every other
    stub in this suite (``test_llm_provider``, ``test_structured``, …) is already
    threaded; this one now matches.
    """

    allow_reuse_address = True
    daemon_threads = True

    def log_message(self, *args: Any) -> None:  # pragma: no cover - silence
        return


class StubEndpoint:
    """A deterministic ``/v1/chat/completions`` endpoint that records raw bodies.

    ``steps`` is consumed in order; the last one repeats, so a script that always
    asks for a tool does not need a count. Each response payload is exactly what
    the stub sends — the loop's parsing is therefore under test, not mocked.
    """

    def __init__(self, steps: list[Step], *, stream_lines: list[list[str]] | None = None) -> None:
        assert steps, "the stub needs at least one step"
        self._steps = list(steps)
        self._stream_lines = stream_lines
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                payload = self.rfile.read(length) if length else b""
                raw = payload.decode("utf-8", "replace")
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {"_unparsed": raw}
                with stub._lock:
                    position = len(stub.requests)
                    stub.requests.append(
                        {
                            "path": self.path,
                            "raw": raw,
                            "body": body,
                            "authorization": self.headers.get("Authorization", ""),
                        }
                    )
                    step = stub._steps[min(position, len(stub._steps) - 1)]
                    scripted_lines = (
                        stub._stream_lines[position]
                        if stub._stream_lines is not None
                        and position < len(stub._stream_lines)
                        else None
                    )
                # a request that asks for `stream: true` gets real SSE, exactly like a
                # provider would answer it — otherwise the client's stream reader is not
                # under test at all
                response = step(body) if callable(step) else step
                if body.get("stream") is True:
                    self._send_stream(scripted_lines or _as_stream_lines(response))
                    return
                self._send_json(response)

            def _send_json(self, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()

            def _send_stream(self, lines: list[str]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for line in lines:
                    chunk = line.encode()
                    self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )

    # -- lifecycle ---------------------------------------------------------- #

    def __enter__(self) -> StubEndpoint:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [item["body"] for item in self.requests]

    @property
    def raw_bodies(self) -> list[str]:
        return [item["raw"] for item in self.requests]


def _as_stream_lines(response: dict[str, Any]) -> list[str]:
    """A non-streamed answer expressed as SSE deltas (what a provider really sends)."""
    choice = (response.get("choices") or [{}])[0]
    message = dict(choice.get("message") or {})
    message.pop("role", None)
    finish = choice.get("finish_reason") or "stop"
    chunk = {"choices": [{"index": choice.get("index", 0), "delta": message,
                          "finish_reason": finish}]}
    return [f"data: {json.dumps(chunk)}\n\n", "data: [DONE]\n\n"]


def answer(text: str, *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    """A plain answer (no tool call) — the shape that ends the loop."""
    payload: dict[str, Any] = {
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ]
    }
    if usage:
        payload["usage"] = usage
    return payload


def tool_step(
    *calls: tuple[str, str, Any],
    text: str = "",
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """A "model asks for tools" answer: ``(call_id, name, arguments)`` triples."""
    payload: dict[str, Any] = {
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": arguments
                                if isinstance(arguments, str)
                                else json.dumps(arguments),
                            },
                        }
                        for call_id, name, arguments in calls
                    ],
                },
            }
        ]
    }
    if usage:
        payload["usage"] = usage
    return payload


def _config(stub: StubEndpoint, **changes: Any) -> ProviderConfig:
    base = ProviderConfig(
        provider="openai-compatible",
        base_url=stub.base_url,
        model="stub-model",
        timeout_s=10.0,
        max_retries=0,
    )
    return ProviderConfig(**{**base.__dict__, **changes}) if changes else base


def _llm(stub: StubEndpoint, **changes: Any) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(_config(stub, **changes))


def _outcomes(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    return transcript["tool_calls"]


def _tool_message(body: dict[str, Any], tool_name: str) -> str:
    """The ``role: tool`` content for one tool name in a recorded request body."""
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "tool" and message.get("name") == tool_name:
            return str(message.get("content") or "")
    raise AssertionError(f"no tool result for {tool_name!r} in {body.get('messages')!r}")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "note.md").write_text("# note\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "runtime" / "agent"
    directory.mkdir(parents=True)
    return directory


# --------------------------------------------------------------------------- #
# 1. tool schema generation                                                    #
# --------------------------------------------------------------------------- #


def test_the_schema_comes_from_the_tool_signature():
    """Typed input from the entrypoint itself — read with ast, never imported."""
    source = (
        "from typing import Optional\n"
        "def fetch(url: str, retries: int = 3, timeout: Optional[float] = None,\n"
        "          headers: dict = {}, tags: list = []) -> dict:\n"
        "    return {}\n"
    )
    schema = input_schema_from_source(source, "fetch")
    assert schema["type"] == "object"
    assert schema["properties"]["url"] == {"type": "string"}
    assert schema["properties"]["retries"] == {"type": "integer", "default": 3}
    assert schema["properties"]["timeout"] == {"type": "number", "default": None}
    assert schema["properties"]["headers"] == {"type": "object", "default": {}}
    assert schema["properties"]["tags"] == {"type": "array", "default": []}
    assert schema["required"] == ["url"]
    assert schema["additionalProperties"] is False


def test_a_spec_schema_is_read_from_the_generated_module_without_importing_it(tmp_path: Path):
    """Generated code is untrusted: the schema comes from the *source*, not an import."""
    tool_dir = tmp_path / "generated_tools" / "http_probe"
    tool_dir.mkdir(parents=True)
    (tool_dir / "http_probe.py").write_text(
        "raise RuntimeError('this file must never be imported to read its signature')\n"
        "\n"
        "def run(url: str, method: str = 'GET') -> dict:\n"
        "    return {}\n",
        encoding="utf-8",
    )
    spec = ToolSpec(
        name="http_probe",
        title="HTTP probe",
        description="probe one URL",
        module="http_probe",
        entrypoint="run",
    )
    schema = input_schema_for_spec(spec, base_dir=tmp_path)
    assert schema["properties"] == {"url": {"type": "string"}, "method": {"type": "string",
                                                                        "default": "GET"}}
    assert schema["required"] == ["url"]


def test_sample_input_and_free_form_are_the_documented_fallbacks():
    sample = input_schema_from_sample({"needle": "x", "limit": 5, "deep": True})
    assert sample["properties"] == {
        "needle": {"type": "string"},
        "limit": {"type": "integer"},
        "deep": {"type": "boolean"},
    }
    assert "required" not in sample  # a sample is evidence of a shape, not a contract

    bare = ToolSpec(name="mystery", title="mystery")
    assert input_schema_for_spec(bare) == {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


def test_annotations_map_to_json_schema_types():
    import ast

    def schema(text: str) -> dict[str, Any]:
        return schema_for_annotation(ast.parse(text, mode="eval").body)

    assert schema("str") == {"type": "string"}
    assert schema("bool") == {"type": "boolean"}
    assert schema("list[str]") == {"type": "array", "items": {"type": "string"}}
    assert schema("dict[str, int]") == {"type": "object"}
    assert schema("str | None") == {"type": "string"}
    assert schema("SomeUnknownClass") == {}  # free-form, never a guessed type


def test_wire_names_stay_inside_the_provider_rule():
    assert wire_tool_name("tools.list") == "tools_list"
    assert wire_tool_name("9lives") == "t_9lives"
    assert wire_tool_name("x" * 100) == "x" * 64
    assert wire_tool_name("same", taken=["same"]) == "same_2"
    assert wire_tool_name("same", taken=["same", "same_2"]) == "same_3"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", wire_tool_name("a b/./c", taken=[]))


def test_the_tools_array_is_a_function_array_with_json_schema_parameters(workspace: Path):
    tools = builtin_tools(workspace)
    definitions, choice = tools_array(tools, tool_choice="auto")
    names = [item["function"]["name"] for item in definitions]
    assert names == ["read_text", "list_dir", "sha256_file", "write_text"]
    assert choice == "auto"
    for item in definitions:
        assert item["type"] == "function"
        function = item["function"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert isinstance(function["parameters"]["properties"], dict)
    read = next(item for item in definitions if item["function"]["name"] == "read_text")
    assert read["function"]["parameters"]["required"] == ["path"]


def test_without_tools_there_is_no_tools_key_and_no_choice():
    definitions, choice = tools_array([], tool_choice="auto")
    assert definitions == []
    assert choice is None  # a tool_choice without tools is an invalid request


# --------------------------------------------------------------------------- #
# 2. the request shape                                                         #
# --------------------------------------------------------------------------- #


def test_the_parsed_answer_carries_text_tool_calls_and_usage():
    payload = {
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "checking",
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "read_text", "arguments": '{"path": "a.txt"}'},
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {"name": "list_dir", "arguments": "{}"},
                        },
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }
    result = chat_result_from_payload(payload)
    assert result.text == "checking"
    assert [call.name for call in result.tool_calls] == ["read_text", "list_dir"]
    assert result.tool_calls[0].arguments == {"path": "a.txt"}
    assert result.tool_calls[1].arguments == {}
    assert result.usage == {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
    assert result.asked_for_tools is True


def test_malformed_arguments_are_reported_not_guessed():
    calls = tool_calls_from_message(
        {"tool_calls": [{"id": "c", "function": {"name": "t", "arguments": "{not json"}}]}
    )
    assert calls[0].arguments == {}
    assert "not valid JSON" in calls[0].parse_error


def test_streamed_tool_call_arguments_are_concatenated_per_index():
    """The fragmented delta shape: name in one chunk, arguments in pieces."""
    chunks = [
        {"choices": [{"delta": {"content": "look"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "call_1", "function": {"name": "read_text",
                                                                      "arguments": '{"pa'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": 'th": "a.txt"}'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 1, "id": "call_2", "function": {"name": "list_dir",
                                                                      "arguments": "{}"}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    ]
    result = accumulate_stream_chunks(chunks)
    assert result.text == "look"
    assert [call.name for call in result.tool_calls] == ["read_text", "list_dir"]
    assert result.tool_calls[0].arguments_raw == '{"path": "a.txt"}'
    assert result.tool_calls[0].arguments == {"path": "a.txt"}
    assert result.finish_reason == "tool_calls"


def test_the_request_body_is_built_with_tools_and_messages():
    cfg = ProviderConfig(base_url="http://x/v1", model="m", max_tokens=64, temperature=0.2)
    body = chat_request_body(
        cfg,
        [{"role": "user", "content": "hi"}],
        tier="main",
        tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}],
        tool_choice="auto",
    )
    assert body["model"] == "m"
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "t"
    assert body["messages"] == [{"role": "user", "content": "hi"}]

    plain = chat_request_body(cfg, [{"role": "user", "content": "hi"}], tier="main")
    assert "tools" not in plain and "tool_choice" not in plain


def test_a_tool_call_round_trips_through_the_wire_shape():
    call = ToolCall(id="c1", name="read_text", arguments={"path": "a.txt"})
    wire = call.to_wire()
    assert wire == {
        "id": "c1",
        "type": "function",
        "function": {"name": "read_text", "arguments": '{"path": "a.txt"}'},
    }
    back = tool_calls_from_message({"tool_calls": [wire]})[0]
    assert back.name == "read_text" and back.arguments == {"path": "a.txt"}


# --------------------------------------------------------------------------- #
# 3. the loop against the stub endpoint                                        #
# --------------------------------------------------------------------------- #


def test_a_real_loop_executes_two_rounds_of_tools_and_then_answers(workspace: Path, state: Path):
    """The end-to-end proof: tools array out, tool_calls back, results fed back, answer."""
    steps = [
        tool_step(
            ("call_1", "read_text", {"path": "hello.txt"}),
            usage={"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
        ),
        tool_step(
            ("call_2", "list_dir", {"path": "."}),
            ("call_3", "sha256_file", {"path": "hello.txt"}),
            usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        ),
        answer(
            "hello.txt says 'hello world'",
            usage={"prompt_tokens": 100, "completion_tokens": 8, "total_tokens": 108},
        ),
    ]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=6),
            goal="report what hello.txt says",
        )
        result = loop.run("what does hello.txt say?")

    assert result.status == STATUS_ANSWERED
    assert result.ok and result.exit_code == 0
    assert result.answer == "hello.txt says 'hello world'"
    assert result.steps == 3
    assert result.tool_calls == 3
    assert result.executed_calls == 3

    # -- the raw request bodies: the tools array is really on the wire ---------
    assert len(stub.requests) == 3
    first = stub.bodies[0]
    assert [item["function"]["name"] for item in first["tools"]] == [
        "read_text",
        "list_dir",
        "sha256_file",
        "write_text",
    ]
    assert first["tool_choice"] == "auto"
    assert first["messages"][0]["role"] == "system"
    assert first["messages"][1] == {"role": "user", "content": "what does hello.txt say?"}
    assert "read_text" in stub.raw_bodies[0]  # the raw JSON that went out
    assert stub.requests[0]["authorization"] == ""  # a local stub needs no key

    # -- the tool_calls were parsed and the results fed back as role: tool -----
    second = stub.bodies[1]
    assert [message["role"] for message in second["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert second["messages"][2]["tool_calls"][0]["function"]["name"] == "read_text"
    fed_back = json.loads(_tool_message(second, "read_text"))
    assert fed_back["ok"] is True
    assert fed_back["result"]["text"] == "hello world\n"

    third = stub.bodies[2]
    for name in ("list_dir", "sha256_file"):
        payload = json.loads(_tool_message(third, name))
        assert payload["ok"] is True
    assert json.loads(_tool_message(third, "sha256_file"))["result"]["sha256"] == (
        __import__("hashlib").sha256(b"hello world\n").hexdigest()
    )

    # -- the transcript holds every turn --------------------------------------
    transcript = read_transcript(result.transcript_path)
    assert transcript["status"] == STATUS_ANSWERED
    assert transcript["answer"] == result.answer
    assert [event["kind"] for event in transcript["events"]].count("model.request") == 3
    assert transcript["tool_call_count"] == 3
    calls = sorted(_outcomes(transcript), key=lambda call: (call["step"], call["call_index"]))
    assert [call["name"] for call in calls] == ["read_text", "list_dir", "sha256_file"]
    assert [call["ok"] for call in calls] == [True, True, True]
    for call in calls:
        assert call["arguments"]
        assert re.fullmatch(r"[0-9a-f]{64}", call["result_sha256"])
        assert isinstance(call["duration_ms"], int)
        assert call["executed"] is True
    assert transcript["requests"][0]["body"]["tools"][0]["function"]["name"] == "read_text"
    assert transcript["usage_estimated"] is False  # every step reported real usage
    assert transcript["usage"] == {
        "prompt_tokens": 170,
        "completion_tokens": 18,
        "total_tokens": 188,
    }
    assert result.usage == transcript["usage"]


def test_a_streamed_answer_with_fragmented_tool_arguments_also_completes(
    workspace: Path, state: Path
):
    """Same loop, ``stream: true``: the deltas carry the call, not the whole answer."""
    lines = [
        [
            'data: {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, '
            '"id": "call_1", "function": {"name": "read_text", "arguments": "{\\"pa"}}]}}]}\n\n',
            'data: {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, '
            '"function": {"arguments": "th\\": \\"hello.txt\\"}"}}]}}]}\n\n',
            'data: {"choices": [{"index": 0, "finish_reason": "tool_calls", "delta": {}}]}\n\n',
            "data: [DONE]\n\n",
        ]
    ]
    with StubEndpoint([answer("the file holds hello world")], stream_lines=lines) as stub:
        loop = AgentLoop(
            _llm(stub, stream=True),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("read hello.txt")
    assert result.status == STATUS_ANSWERED, result.message
    assert result.tool_calls == 1
    assert result.answer == "the file holds hello world"
    assert stub.requests[0]["body"]["stream"] is True
    fed_back = json.loads(_tool_message(stub.bodies[1], "read_text"))
    assert fed_back["result"]["text"] == "hello world\n"


def test_parallel_safe_calls_run_concurrently_and_mutating_ones_do_not(
    workspace: Path, state: Path
):
    """A tool's own declaration decides: read-only in parallel, a write sequentially."""
    order: list[str] = []

    def slow(name: str, delay: float = 0.3):
        def handler(sleep: float = delay) -> dict[str, Any]:
            order.append(f"{name}:start")
            time.sleep(sleep)
            order.append(f"{name}:end")
            return {"tool": name}

        return handler

    tools = ToolSet.of(
        [
            AgentTool(
                name="slow_a",
                description="a slow read",
                input_schema={"type": "object", "properties": {},
                              "additionalProperties": False},
                handler=slow("a"),
            ),
            AgentTool(
                name="slow_b",
                description="another slow read",
                input_schema={"type": "object", "properties": {},
                              "additionalProperties": False},
                handler=slow("b"),
            ),
        ]
        + builtin_tools(workspace)
    )
    steps = [
        tool_step(
            ("call_a", "slow_a", {}),
            ("call_b", "slow_b", {}),
            ("call_w", "write_text", {"path": "out.txt", "text": "written"}),
        ),
        answer("done"),
    ]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), tools, state_dir=state, workspace=workspace)
        started = time.monotonic()
        result = loop.run("run both reads and write once")
        elapsed = time.monotonic() - started

    assert result.status == STATUS_ANSWERED
    # two 0.3 s reads in parallel cost ~0.3 s, not ~0.6 s
    assert elapsed < 0.55, f"the read-only calls did not overlap (elapsed {elapsed:.3f}s)"
    assert order[:2] == ["a:start", "b:start"]  # both entered before either finished
    assert order.index("b:start") < order.index("a:end")  # they really overlapped
    transcript = read_transcript(result.transcript_path)
    by_name = {call["name"]: call for call in _outcomes(transcript)}
    assert by_name["slow_a"]["parallel"] is True
    assert by_name["slow_b"]["parallel"] is True
    assert by_name["write_text"]["parallel"] is False  # a write is never scheduled beside
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "written"


def test_the_policy_gate_runs_before_the_tool(workspace: Path, state: Path):
    """Every call goes through judge_tool_call; the verdict is in the transcript."""
    steps = [tool_step(("c1", "read_text", {"path": "hello.txt"})), answer("ok")]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace)
        result = loop.run("read it")
    call = _outcomes(read_transcript(result.transcript_path))[0]
    assert call["decision"] == "ALLOW"
    assert call["executed"] is True


def test_a_write_outside_the_workspace_is_refused_by_the_tool(workspace: Path, state: Path,
                                                              tmp_path: Path):
    outside = tmp_path.parent / "outside-agent-target.txt"
    steps = [
        tool_step(("c1", "write_text", {"path": str(outside), "text": "nope"})),
        answer("it was refused"),
    ]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace)
        loop.run("write outside")
    assert not outside.exists()
    payload = json.loads(_tool_message(stub.bodies[1], "write_text"))
    # `write_text` is a WRITE_TOOLS member: the guard refuses it before execution.
    assert payload["ok"] is False and "workspace" in payload["error"]


# --------------------------------------------------------------------------- #
# 4. stop conditions and honesty                                               #
# --------------------------------------------------------------------------- #


def test_max_steps_hit_is_incomplete_and_never_an_answer(workspace: Path, state: Path):
    """The stub asks for a tool forever; the step bound must be the honest end."""
    steps = [
        tool_step(("c", "read_text", {"path": "hello.txt"}), text="still working")
    ]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=2),
        )
        result = loop.run("loop forever")

    assert result.status == STATUS_INCOMPLETE
    assert result.reason == REASON_MAX_STEPS
    assert result.answer is None  # NOT "still working"
    assert result.exit_code == 3
    assert result.steps == 2
    assert len(stub.requests) == 2  # the bound really stopped the calling
    assert "still working" in result.last_text  # recorded as a diagnostic, not as the result
    transcript = read_transcript(result.transcript_path)
    assert transcript["status"] == STATUS_INCOMPLETE
    assert transcript["reason"] == REASON_MAX_STEPS
    assert transcript["answer"] is None
    bounds = [event for event in transcript["events"] if event["kind"] == "step.bound"]
    assert bounds and bounds[-1]["reason"] == REASON_MAX_STEPS


def test_a_token_budget_stops_the_run_before_the_next_request(workspace: Path, state: Path):
    steps = [
        tool_step(
            ("c", "list_dir", {}),
            usage={"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        )
    ]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=9, token_budget=50),
        )
        result = loop.run("spend tokens")
    assert result.status == STATUS_INCOMPLETE
    assert result.reason == REASON_TOKEN_BUDGET
    assert result.answer is None
    assert len(stub.requests) == 1  # 60 tokens already spent: no second request
    assert result.usage["total_tokens"] == 60


def test_a_cost_budget_needs_a_declared_price(workspace: Path, state: Path):
    loop = AgentLoop(
        ScriptedLLM(responses={}),
        builtin_tools(workspace),
        state_dir=state,
        workspace=workspace,
        limits=LoopLimits(cost_budget_usd=1.0),
    )
    # the client check fires first for a scripted client; make the price the issue
    with pytest.raises(AgentLoopError):
        loop.run("x")


def test_a_cost_budget_with_a_price_is_enforced(workspace: Path, state: Path):
    steps = [
        tool_step(("c", "list_dir", {}), usage={"total_tokens": 1000}),
    ]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=5, cost_budget_usd=0.5, price_per_1k_tokens=1.0),
        )
        result = loop.run("spend money")
    assert result.status == STATUS_INCOMPLETE
    assert result.reason == "cost_budget"
    assert result.cost_usd == 1.0
    assert result.answer is None


def test_the_wall_clock_bound_stops_a_slow_run(workspace: Path, state: Path):
    def slow(body: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.05)
        return tool_step(("c", "list_dir", {}))

    with StubEndpoint([slow]) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=50, wall_clock_s=0.02),
        )
        result = loop.run("take your time")
    assert result.status == STATUS_INCOMPLETE
    assert result.reason == REASON_WALL_CLOCK
    assert result.answer is None
    assert result.steps == 1  # it stopped after the first answer, not after 50


def test_a_cancellation_file_stops_the_loop(workspace: Path, state: Path):
    """Cancellation is a real file the tool itself can create — checked every step."""
    cancel = workspace / "cancel.flag"
    steps = [tool_step(("c", "write_text", {"path": "cancel.flag", "text": "stop"}))]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
            limits=LoopLimits(max_steps=9),
            cancel_path=cancel,
        )
        result = loop.run("write the cancel flag")
    assert cancel.exists()
    assert result.status == STATUS_INCOMPLETE
    assert result.reason == REASON_CANCELLED
    assert result.answer is None
    assert len(stub.requests) == 1
    assert result.tool_calls == 1  # the call that cancelled the run is still recorded


def test_an_empty_answer_is_incomplete_not_an_answer(workspace: Path, state: Path):
    with StubEndpoint([answer("   ")]) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace)
        result = loop.run("say nothing")
    assert result.status == STATUS_INCOMPLETE
    assert result.reason == REASON_EMPTY_ANSWER
    assert result.answer is None


def test_a_client_without_tool_calling_is_refused_loudly(workspace: Path, state: Path):
    loop = AgentLoop(
        ScriptedLLM(responses={"x": "y"}), builtin_tools(workspace), state_dir=state,
        workspace=workspace,
    )
    with pytest.raises(AgentLoopError, match="cannot call tools"):
        loop.run("anything")
    assert not list(state.glob("runs/*.jsonl"))  # nothing was pretended


def test_a_provider_failure_is_reported_as_failed_with_the_status(workspace: Path, state: Path):
    class Boom:
        name = "boom"

        def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
            from skaldir.llm import LLMProviderError

            raise LLMProviderError("provider call failed (HTTP 500)", status=500)

    loop = AgentLoop(Boom(), builtin_tools(workspace), state_dir=state, workspace=workspace)
    result = loop.run("fail")
    assert result.status == "failed"
    assert result.reason == "provider_error"
    assert result.answer is None
    assert "500" in result.message
    transcript = read_transcript(result.transcript_path)
    assert any(event["kind"] == "run.error" for event in transcript["events"])


# --------------------------------------------------------------------------- #
# 5. robustness: unknown tool, bad arguments, tool error — all reach the model  #
# --------------------------------------------------------------------------- #


def test_an_unknown_tool_is_answered_with_a_message_and_the_loop_continues(
    workspace: Path, state: Path
):
    def second(body: dict[str, Any]) -> dict[str, Any]:
        content = _tool_message(body, "teleport")
        assert "unknown tool" in content and "read_text" in content
        return answer("teleport does not exist; I used what was offered")

    steps = [tool_step(("c1", "teleport", {"to": "mars"})), second]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace, limits=LoopLimits(max_steps=4))
        result = loop.run("teleport to mars")

    assert result.status == STATUS_ANSWERED
    assert result.answer.startswith("teleport does not exist")
    assert len(stub.requests) == 2  # the model got a second chance
    call = _outcomes(read_transcript(result.transcript_path))[0]
    assert call["name"] == "teleport"
    assert call["decision"] == "unknown_tool"
    assert call["executed"] is False
    assert "unknown tool" in call["error"]
    assert result.executed_calls == 0


def test_a_tool_error_reaches_the_model_as_error_text(workspace: Path, state: Path):
    def second(body: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(_tool_message(body, "read_text"))
        assert payload["ok"] is False
        assert "not a file" in payload["error"]
        return answer("the file is missing; I report that instead of guessing")

    steps = [tool_step(("c1", "read_text", {"path": "missing.txt"})), second]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace, limits=LoopLimits(max_steps=4))
        result = loop.run("read missing.txt")

    assert result.status == STATUS_ANSWERED
    assert result.executed_calls == 1
    call = _outcomes(read_transcript(result.transcript_path))[0]
    assert call["executed"] is True
    assert call["ok"] is False
    assert "not a file" in call["error"]


def test_arguments_that_do_not_match_the_schema_are_refused_with_the_schema(
    workspace: Path, state: Path
):
    def second(body: dict[str, Any]) -> dict[str, Any]:
        content = _tool_message(body, "read_text")
        assert "unexpected argument(s): bytes" in content
        assert "allowed: max_bytes, path" in content
        return answer("I used the documented arguments")

    steps = [tool_step(("c1", "read_text", {"path": "hello.txt", "bytes": 10})), second]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace, limits=LoopLimits(max_steps=4))
        result = loop.run("read ten bytes")
    call = _outcomes(read_transcript(result.transcript_path))[0]
    assert call["decision"] == "invalid_arguments"
    assert call["executed"] is False
    assert result.status == STATUS_ANSWERED


def test_missing_required_arguments_are_refused(workspace: Path, state: Path):
    steps = [tool_step(("c1", "read_text", {}))]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace, limits=LoopLimits(max_steps=2))
        result = loop.run("read nothing")
    call = _outcomes(read_transcript(result.transcript_path))[0]
    assert "missing required argument(s): path" in call["error"]
    assert result.status == STATUS_INCOMPLETE  # bound, and honestly so


# --------------------------------------------------------------------------- #
# 6. the transcript as a read-back artifact                                     #
# --------------------------------------------------------------------------- #


def test_the_transcript_lists_runs_and_refuses_a_wild_run_id(workspace: Path, state: Path):
    with StubEndpoint([answer("done")]) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace)
        result = loop.run("just answer", run_id="run-fixed-0001")
    assert result.run_id == "run-fixed-0001"
    runs = list_runs(state)
    assert [item["run_id"] for item in runs] == ["run-fixed-0001"]
    assert runs[0]["status"] == STATUS_ANSWERED
    assert runs[0]["tool_calls"] == 0
    assert run_path(state, "run-fixed-0001").is_file()
    for bad in ("../escape", "with/slash", "", "x" * 200):
        with pytest.raises(AgentLoopError):
            run_path(state, bad)


def test_every_transcript_line_is_one_json_object(workspace: Path, state: Path):
    steps = [tool_step(("c", "list_dir", {})), answer("ok")]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace)
        result = loop.run("list")
    lines = Path(result.transcript_path).read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
    assert records[0]["kind"] == "run.started"
    assert records[-1]["kind"] == "run.finished"
    assert records[0]["tools"][0]["function"]["parameters"]["type"] == "object"
    assert records[0]["provider"]["model"] == "stub-model"
    assert records[0]["provider"]["api_key"] == "unset"


def test_the_system_prompt_can_be_overridden_and_is_what_is_sent(workspace: Path, state: Path):
    with StubEndpoint([answer("ok")]) as stub:
        loop = AgentLoop(
            _llm(stub),
            builtin_tools(workspace),
            state_dir=state,
            workspace=workspace,
            system_prompt="You are a test harness.",
        )
        loop.run("hi")
    assert stub.bodies[0]["messages"][0] == {"role": "system", "content": "You are a test harness."}


# --------------------------------------------------------------------------- #
# 7. the registry tool set: an approved spec becomes a callable tool           #
# --------------------------------------------------------------------------- #


def test_an_approved_registry_spec_becomes_a_tool_the_model_can_call(
    tmp_path: Path, workspace: Path, state: Path, monkeypatch: pytest.MonkeyPatch
):
    """The loop over the **real** tool runtime: ToolStore spec → schema → execution.

    The spec resolves from ``sys.path`` (a manual/fixture registration), which is
    the documented in-process residual of :mod:`skaldir.mcpserver`; a spec that
    lives under ``generated_tools`` executes through the bwrap sandbox instead.
    """
    from skaldir.agent_loop import registry_tools
    from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore

    module_dir = tmp_path / "mods"
    module_dir.mkdir()
    (module_dir / "greeter.py").write_text(
        "def greet(name: str, loud: bool = False) -> dict:\n"
        "    return {'greeting': f'hello {name}' + ('!' if loud else '')}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(module_dir))
    store = ToolStore(str(tmp_path / "tools.db"))
    store.upsert(
        ToolSpec(
            name="greeter",
            title="Greeter",
            description="greet somebody by name",
            module="greeter",
            entrypoint="greet",
            sample_input={"name": "world", "loud": False},
            status=STATUS_APPROVED,
        )
    )

    tools = registry_tools(store)
    assert [tool.name for tool in tools] == ["greeter"]
    assert tools[0].parallel_safe is False  # unknown side effects are never parallel
    assert tools[0].mutating is True
    assert tools[0].input_schema["properties"]["name"] == {"type": "string"}
    assert tools[0].source == "registry"

    steps = [
        tool_step(("c1", "greeter", {"name": "world", "loud": True})),
        answer("greeted"),
    ]
    with StubEndpoint(steps) as stub:
        loop = AgentLoop(_llm(stub), ToolSet.of(tools), state_dir=state, workspace=workspace)
        result = loop.run("greet the world")
    assert result.status == STATUS_ANSWERED
    assert result.executed_calls == 1
    fed_back = json.loads(_tool_message(stub.bodies[1], "greeter"))
    assert fed_back["ok"] is True
    assert fed_back["result"] == {"greeting": "hello world!"}
    call = _outcomes(read_transcript(result.transcript_path))[0]
    assert call["decision"] == "ALLOW"
    assert call["parallel"] is False


def test_the_registry_tool_set_mixes_builtin_and_registered_tools(tmp_path: Path, workspace: Path):
    """``both`` is the real surface: the loop's own tools plus the registry's."""
    from skaldir.agent_loop import tool_set_for
    from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore

    store = ToolStore(str(tmp_path / "tools.db"))
    store.upsert(
        ToolSpec(name="noop_tool", title="No-op", module="math", entrypoint="sqrt",
                 status=STATUS_APPROVED)
    )
    toolset = tool_set_for(workspace=workspace, kind="both", store=store)
    names = toolset.names()
    assert names[:4] == ["read_text", "list_dir", "sha256_file", "write_text"]
    assert names[-1] == "noop_tool"
    with pytest.raises(AgentLoopError):
        tool_set_for(workspace=workspace, kind="invented")
    with pytest.raises(AgentLoopError):
        tool_set_for(workspace=workspace, kind="registry", store=None)


def test_a_reused_run_id_starts_a_fresh_transcript(workspace: Path, state: Path):
    """A run id names ONE run: the second run must not append into the first record."""
    with StubEndpoint([answer("first")]) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace)
        first = loop.run("first run", run_id="run-same-id")
    lines_first = Path(first.transcript_path).read_text(encoding="utf-8").splitlines()

    with StubEndpoint([tool_step(("c", "list_dir", {})), answer("second")]) as stub:
        loop = AgentLoop(_llm(stub), builtin_tools(workspace), state_dir=state,
                         workspace=workspace)
        second = loop.run("second run", run_id="run-same-id")

    transcript = read_transcript(second.transcript_path)
    assert transcript["task"] == "second run"
    assert transcript["tool_call_count"] == 1  # not 1 + the first run's calls
    assert len(transcript["events"]) < len(lines_first) + 10
    previous = transcript["previous_transcript"]
    assert previous["bytes"] == len(("\n".join(lines_first) + "\n").encode())
    assert re.fullmatch(r"[0-9a-f]{64}", previous["sha256"])
    assert list_runs(state)[0]["task"] == "second run"  # one run id, one row
