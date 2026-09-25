"""The reasoning-budget guard: a budget artifact is retried once, then refused **by name**.

Measured twice on 2026-09-19 — a routing block and a compaction asker — and probed with a
control: the endpoint answers **HTTP 200** with ``finish_reason=length``, an empty ``content``
and the whole answer sitting in ``reasoning_content`` (``content_chars=0``,
``reasoning_tokens`` filled). At ``max_tokens=24`` that is what a reasoner returns; at 400 both
fill. So it is a *budget artifact*, not a broken endpoint and not "the model said nothing".

What this file holds down:

* the guard retries the shape **once** with a larger budget and returns that answer — with both
  attempts recorded (``budget``, ``finish_reason``, ``content_chars``, ``reasoning_tokens``);
* when the retry is empty too, the caller gets :class:`skaldir.llm_provider.ReasoningBudgetError`
  — a name, the counters and the budget it tried — never an empty content as an answer;
* the guard is **narrow**: ``length`` with text (a truncated answer), an empty answer with
  ``stop``, a tool call, and a budget already at the ceiling are all left exactly as they were.

Every test runs against a stub socket server in the same process: no network, no credential.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skaldir import llm_provider as lp
from skaldir.llm_provider import OpenAICompatibleLLM, ReasoningBudgetError, resolve_provider_config

ABSENT_CONFIG = Path("/nonexistent/skaldir-reasoning-budget-tests/config.yaml")
FAKE_KEY = "sk-test-key"


@dataclass
class Step:
    """One scripted answer: the shape a reasoning endpoint really returns."""

    content: str = ""
    finish_reason: str = "stop"
    reasoning: str = ""
    reasoning_tokens: int | None = None
    completion_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        usage: dict[str, Any] = {}
        if self.completion_tokens is not None:
            usage["completion_tokens"] = int(self.completion_tokens)
            usage["prompt_tokens"] = 8
            usage["total_tokens"] = int(self.completion_tokens) + 8
        if self.reasoning_tokens is not None:
            usage.setdefault("completion_tokens_details", {})["reasoning_tokens"] = int(
                self.reasoning_tokens
            )
        body = {
            "id": "stub-1",
            "object": "chat.completion",
            "model": "test-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": self.content,
                            "reasoning_content": self.reasoning},
                "finish_reason": self.finish_reason,
            }],
            "usage": usage,
        }
        body.update(self.extra)
        return body


class BudgetStub:
    """A real socket endpoint with a scripted step list; the last step repeats."""

    def __init__(self, steps: list[Step]) -> None:
        assert steps
        self._steps = list(steps)
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                with stub._lock:
                    stub.requests.append(body)
                    step = stub._steps[min(len(stub.requests) - 1, len(stub._steps) - 1)]
                payload = json.dumps(step.payload()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> BudgetStub:
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.stop()
        return False


def client_for(stub: BudgetStub, **overrides: Any) -> OpenAICompatibleLLM:
    settings: dict[str, Any] = {"base_url": stub.base_url, "model": "test-model",
                                "api_key": FAKE_KEY}
    settings.update(overrides)
    return OpenAICompatibleLLM(
        resolve_provider_config(settings, env={}, config_path=ABSENT_CONFIG)
    )


def ask(client: OpenAICompatibleLLM) -> Any:
    return client.chat([{"role": "user", "content": "route this"}], tier="main")


#: the trap, exactly as it was measured: 200, `length`, no content, reasoning filled
REASONING_ONLY = Step(content="", finish_reason="length", reasoning="thinking about it…",
                      reasoning_tokens=24, completion_tokens=24)


def test_the_guard_retries_once_with_a_bigger_budget_and_keeps_both_attempts():
    steps = [REASONING_ONLY, Step(content="the real answer", completion_tokens=12)]
    with BudgetStub(steps) as stub:
        client = client_for(stub, max_tokens=24)
        result = ask(client)

    assert result.text == "the real answer"
    assert result.budget["retried"] is True
    first, second = result.budget["attempts"]
    assert first == {"budget": 24, "finish_reason": "length", "content_chars": 0,
                     "reasoning_tokens": 24}
    assert second["budget"] == 48
    assert second["content_chars"] == len("the real answer")
    # the retry really asked the endpoint for the bigger budget — not just its own bookkeeping
    assert [body.get("max_tokens") for body in stub.requests] == [24, 48]
    assert len(stub.requests) == 2, "one bounded retry, never a loop"
    assert result.to_dict()["budget"]["attempts"][0]["reasoning_tokens"] == 24


def test_both_attempts_empty_is_a_named_refusal_never_an_empty_answer():
    with BudgetStub([REASONING_ONLY]) as stub:
        client = client_for(stub, max_tokens=24)
        with pytest.raises(ReasoningBudgetError) as excinfo:
            ask(client)

    message = str(excinfo.value)
    assert "reasoning budget exhausted" in message
    assert "finish_reason=length" in message
    assert "reasoning_tokens 24" in message
    assert "budget 48" in message, "the refusal names the budget the retry tried"
    assert ReasoningBudgetError.code == "reasoning_budget_exhausted"
    assert len(stub.requests) == 2, "the retry is bounded: two requests, then the refusal"
    # …and the client kept the evidence, because a refusal carries no answer object
    assert client.last_budget["attempts"][-1]["content_chars"] == 0


def test_a_budget_already_at_the_ceiling_is_refused_without_a_retry():
    with BudgetStub([REASONING_ONLY]) as stub:
        client = client_for(stub, max_tokens=lp.REASONING_BUDGET_CEILING)
        with pytest.raises(ReasoningBudgetError) as excinfo:
            ask(client)

    assert "ceiling" in str(excinfo.value)
    assert len(stub.requests) == 1, "no larger budget exists — so no request is wasted"


def test_a_truncated_answer_with_text_is_not_retried():
    """``length`` **with** content is a truncated answer; the caller reports it, we do not retry."""
    with BudgetStub([Step(content="half an ans", finish_reason="length")]) as stub:
        client = client_for(stub, max_tokens=24)
        result = ask(client)

    assert result.text == "half an ans"
    assert result.budget == {"attempts": [{"budget": 24, "finish_reason": "length",
                                           "content_chars": 11, "reasoning_tokens": 0}],
                             "retried": False}
    assert len(stub.requests) == 1


def test_an_empty_answer_that_is_not_the_artifact_is_returned_untouched():
    """``stop`` with no content is the loop's own ``empty_answer`` — not this guard's business."""
    with BudgetStub([Step(content="", finish_reason="stop")]) as stub:
        client = client_for(stub, max_tokens=24)
        result = ask(client)

    assert result.text == ""
    assert result.budget["retried"] is False
    assert len(stub.requests) == 1


def test_a_tool_call_with_no_text_is_an_answer_not_an_artifact():
    """The guard never eats a tool call: a call the loop can act on is not an empty answer."""
    call = lp.ToolCall(id="t1", name="read_text", arguments={"path": "a.py"})
    assert lp.reasoning_budget_artifact(
        lp.ChatResult(text="", tool_calls=[call], finish_reason="length")
    ) is False
    assert lp.reasoning_budget_artifact(
        lp.ChatResult(text="", finish_reason="length")
    ) is True
    assert lp.reasoning_budget_artifact(
        lp.ChatResult(text="something", finish_reason="length")
    ) is False


def test_the_text_path_recovers_and_reports_the_reasoning_tokens_separately():
    """``complete`` (the asker/route path) gets the same guard, with the counters on the client."""
    with BudgetStub([REASONING_ONLY, Step(content="recovered", completion_tokens=5)]) as stub:
        client = client_for(stub, max_tokens=24)
        text = client.complete(prompt_id="route", prompt="decide", tier="main")

    assert text == "recovered"
    attempts = client.last_budget["attempts"]
    assert attempts[0]["reasoning_tokens"] == 24 and attempts[0]["content_chars"] == 0
    assert attempts[1]["budget"] == 48
    assert [body.get("max_tokens") for body in stub.requests] == [24, 48]


def test_the_text_path_refuses_by_name_when_the_retry_is_empty_too():
    with BudgetStub([REASONING_ONLY]) as stub:
        client = client_for(stub, max_tokens=24)
        with pytest.raises(ReasoningBudgetError) as excinfo:
            client.complete(prompt_id="route", prompt="decide", tier="main")

    assert "reasoning budget exhausted" in str(excinfo.value)
    assert len(stub.requests) == 2


def test_the_text_path_still_reports_an_ordinary_empty_completion():
    """A non-artifact empty answer keeps the old refusal text (the guard is additive)."""
    with BudgetStub([Step(content="", finish_reason="stop")]) as stub:
        client = client_for(stub, max_tokens=24)
        with pytest.raises(lp.ProviderResponseError) as excinfo:
            client.complete(prompt_id="route", prompt="decide", tier="main")

    assert not isinstance(excinfo.value, ReasoningBudgetError)
    assert "empty completion" in str(excinfo.value)
    assert len(stub.requests) == 1


# --------------------------------------------------------------------------- #
# the cheap decisions: keep/shorten/drop asks the smallest configured tier
# --------------------------------------------------------------------------- #


def test_a_cheap_decision_asks_the_configured_small_tier():
    """Jev's keep/shorten/drop questions are cheap — they go to `small` when it is declared."""
    from skaldir import context_manager as cm

    with BudgetStub([Step(content="{}")]) as stub:
        tiers = {"small": "small-model", "main": "test-model"}
        client = client_for(stub, max_tokens=24, tiers=tiers)
        assert client.cheap_tier() == "small"
        asker = cm.build_provider_asker(client=client)
        assert asker.tier == "small", "the asker asks the cheap tier, not the main model"
        client.chat([{"role": "user", "content": "keep or drop?"}], tier=asker.tier)

    assert stub.requests[-1]["model"] == "small-model", "the request really named the small model"


def test_a_block_with_no_declared_tier_still_uses_the_main_model():
    """No invented tier: a single-model block behaves exactly as it did before."""
    from skaldir import context_manager as cm

    with BudgetStub([Step(content="{}")]) as stub:
        client = client_for(stub, max_tokens=24, tiers={"main": "test-model"})
        assert client.cheap_tier() == "main"
        assert cm.build_provider_asker(client=client).tier == "main"


def test_an_explicit_tier_still_wins_over_the_cheap_default():
    from skaldir import context_manager as cm

    with BudgetStub([Step(content="{}")]) as stub:
        client = client_for(stub, max_tokens=24, tiers={"small": "small-model"})
        assert cm.build_provider_asker(client=client, tier="main").tier == "main"


def test_a_mid_tier_is_used_when_there_is_no_small_one():
    with BudgetStub([Step(content="{}")]) as stub:
        tiers = {"mid": "mid-model", "main": "test-model"}
        client = client_for(stub, max_tokens=24, tiers=tiers)
        assert client.cheap_tier() == "mid"
        assert lp.cheap_decision_tier(client) == "mid"
        # …and a client that cannot answer is not a failure
        assert lp.cheap_decision_tier(object()) == "main"
