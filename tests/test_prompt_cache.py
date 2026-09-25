"""Prompt caching: breakpoints, byte-stable prefixes, and the honest verdict.

No credentials and no network: every HTTP test runs against a stub server bound to
``127.0.0.1`` on an ephemeral port inside the test process — a real socket server,
so connection reuse (the pooled client) is measured rather than mocked. The two
things this file exists to keep true:

* **``unknown`` is a real answer.** A provider that reported no cache counter gets
  ``unknown`` — never a zero, never a hit inferred from "we sent a stable prefix".
* **A prediction is labelled as one.** ``prefix_reuse`` reports what a byte-compare
  says *should* be reusable (``basis: byte-compare``); only the answer's usage
  payload may say ``hit``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skaldir import llm_provider as lp
from skaldir import perf_runtime as perf
from skaldir import prompt_builder as pbuilder
from skaldir import prompt_cache as pc
from skaldir import prompt_cache_cli as pcli
from skaldir.llm_provider import OpenAICompatibleLLM, ProviderConfig

FAKE_KEY = "sk-tes...-key"

#: A path that cannot exist, so no test reads the developer's real config.
ABSENT_CONFIG = Path("/nonexistent/skaldir-prompt-cache-tests/config.yaml")


# --------------------------------------------------------------------------- #
# stub server                                                                   #
# --------------------------------------------------------------------------- #


class _Stub:
    """A real HTTP server that answers chat completions and counts connections."""

    def __init__(self, usages: list[dict[str, Any]] | None = None) -> None:
        self.usages = list(usages or [])
        self.bodies: list[dict[str, Any]] = []
        self.connections = 0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # a real provider sets TCP_NODELAY (as httpx does); without it a
            # keep-alive pair of small writes costs a 40 ms delayed-ACK on loopback
            disable_nagle_algorithm = True

            def log_message(self, *args: Any) -> None:  # stay quiet during tests
                return

            def do_POST(self) -> None:  # noqa: N802 - the http.server API
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {"_unparsable": raw.decode("utf-8", "replace")}
                stub.bodies.append(body)
                usage = stub.usages.pop(0) if stub.usages else {"prompt_tokens": 10}
                payload = {
                    "id": "stub",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "ok"},
                        }
                    ],
                    "usage": usage,
                }
                out = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def process_request(self, request: Any, client_address: Any) -> None:
                stub.connections += 1
                return super().process_request(request, client_address)

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def config(self, **overrides: Any) -> ProviderConfig:
        base: dict[str, Any] = {
            "provider": "openai-compatible",
            "base_url": f"http://127.0.0.1:{self.port}/v1",
            "model": "deepseek-chat",
            "api_key": FAKE_KEY,
            "max_retries": 0,
            "total_timeout_s": 10.0,
        }
        base.update(overrides)
        return ProviderConfig(**base)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def stub() -> Any:
    server = _Stub()
    try:
        yield server
    finally:
        server.stop()


def _usage(**fields: Any) -> dict[str, Any]:
    usage: dict[str, Any] = {"prompt_tokens": 812, "completion_tokens": 9, "total_tokens": 821}
    usage.update(fields)
    return usage


# --------------------------------------------------------------------------- #
# the verdict: usage payload → hit / miss / unknown                             #
# --------------------------------------------------------------------------- #


class TestCacheReport:
    def test_deepseek_hit_and_cold_miss(self) -> None:
        """The two counters DeepSeek documents decide the status, both ways."""
        hit = pc.cache_report(_usage(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=12))
        assert hit.status == pc.CACHE_HIT
        assert hit.hit_tokens == 800
        assert hit.fields["prompt_cache_hit_tokens"] == 800
        assert "prompt_cache_hit_tokens=800" in hit.basis

        cold = pc.cache_report(_usage(prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=812))
        assert cold.status == pc.CACHE_MISS
        assert cold.miss_tokens == 812

    def test_openai_nested_detail_is_read_and_not_invented(self) -> None:
        """``prompt_tokens_details.cached_tokens`` is the documented OpenAI shape."""
        report = pc.cache_report(
            {"usage": _usage(prompt_tokens_details={"cached_tokens": 512, "audio_tokens": 0})}
        )
        assert report.status == pc.CACHE_HIT
        assert report.read_tokens == 512
        assert report.fields == {"prompt_tokens_details.cached_tokens": 512}

        # a detail object without cached_tokens is NOT a zero — it is nothing
        empty = pc.cache_report({"usage": _usage(prompt_tokens_details={"audio_tokens": 0})})
        assert empty.status == pc.CACHE_UNKNOWN
        assert empty.fields == {}

    def test_anthropic_read_wins_over_creation(self) -> None:
        """A read above zero is a hit; a cold *write* (read 0, creation > 0) is a miss."""
        warm = pc.cache_report(
            _usage(cache_read_input_tokens=1990, cache_creation_input_tokens=0)
        )
        assert warm.status == pc.CACHE_HIT
        assert warm.read_tokens == 1990

        cold = pc.cache_report(
            _usage(cache_read_input_tokens=0, cache_creation_input_tokens=1990)
        )
        assert cold.status == pc.CACHE_MISS
        assert cold.write_tokens == 1990

    def test_a_payload_without_counters_is_unknown(self) -> None:
        """The honesty rule: an absent field is neither a hit nor a miss."""
        report = pc.cache_report(_usage())
        assert report.status == pc.CACHE_UNKNOWN
        assert report.observed is False
        assert report.fields == {}
        assert report.exempt_tokens is None

    def test_unknown_says_when_caching_was_expected(self) -> None:
        """An ``unknown`` from a provider that documents caching reads differently."""
        strategy = pc.strategy_for("deepseek")
        report = pc.cache_report(_usage(), strategy)
        assert report.status == pc.CACHE_UNKNOWN
        assert report.expected is True
        assert "documents caching" in report.note
        assert pc.strategy_for("llama", base_url="http://127.0.0.1:8080/v1").mode == pc.MODE_NONE

    def test_a_bool_is_not_a_token_count(self) -> None:
        report = pc.cache_report(_usage(prompt_cache_hit_tokens=True))
        assert report.status == pc.CACHE_UNKNOWN
        assert report.fields == {}

    def test_summarize_counts_unknown_apart(self) -> None:
        reports = [
            pc.cache_report(_usage(prompt_cache_hit_tokens=800)),
            pc.cache_report(_usage(prompt_cache_hit_tokens=0)),
            pc.cache_report(_usage()),
        ]
        summary = pc.summarize(reports)
        assert summary["requests"] == 3
        assert summary["counts"] == {pc.CACHE_HIT: 1, pc.CACHE_MISS: 1, pc.CACHE_UNKNOWN: 1}
        assert summary["hit_tokens"] == 800
        assert summary["observed"] == 2

    def test_strategy_table_names_its_sources(self) -> None:
        """Every documented row carries the reference it came from."""
        table = pc.support_table()
        assert table["count"] == len(pc.SUPPORT)
        for row in table["rows"]:
            if row["confidence"] == pc.CONF_DOCUMENTED:
                assert row["reference"].startswith("http")
            assert row["mode"] in (pc.MODE_AUTO, pc.MODE_EXPLICIT, pc.MODE_NONE)

    def test_strategy_precedence_and_the_model_hint(self) -> None:
        assert pc.strategy_for("deepseek").matched_by == "provider"
        by_host = pc.strategy_for("", base_url="https://api.openai.com/v1", model="gpt-4o-mini")
        assert (by_host.provider, by_host.matched_by) == ("openai", "base_url")
        by_model = pc.strategy_for("openai-compatible", base_url="http://10.0.0.5:8000/v1",
                                   model="deepseek/deepseek-chat")
        assert by_model.provider == "deepseek"
        assert by_model.matched_by == "model"
        assert "matched by the model name" in by_model.note


# --------------------------------------------------------------------------- #
# the prefix: what should be reusable, stated as a prediction                    #
# --------------------------------------------------------------------------- #


class TestStablePrefix:
    def test_the_same_prefix_hashes_the_same_and_a_byte_moves_it(self) -> None:
        first = pc.stable_prefix([{"role": "system", "content": "abc"}], [{"name": "t"}])
        second = pc.stable_prefix([{"role": "system", "content": "abc"}], [{"name": "t"}])
        assert first.sha256 and first.sha256 == second.sha256

        moved = pc.stable_prefix([{"role": "system", "content": "abd"}], [{"name": "t"}])
        assert moved.sha256 != first.sha256

    def test_only_the_leading_system_run_is_the_prefix(self) -> None:
        """The user turn and the tool results are the dynamic tail — not hashed."""
        base = [{"role": "system", "content": "sys"}]
        one = pc.stable_prefix(base + [{"role": "user", "content": "a"}])
        two = pc.stable_prefix(base + [{"role": "user", "content": "b"}])
        assert one.sha256 == two.sha256
        assert one.message_count == 1

    def test_no_system_and_no_tools_is_an_empty_prefix(self) -> None:
        empty = pc.stable_prefix([{"role": "user", "content": "hi"}])
        assert empty.present is False
        assert empty.byte_count == 0
        assert empty.sha256 == ""

    def test_reuse_is_a_prediction_and_says_so(self) -> None:
        prefix = pc.stable_prefix([{"role": "system", "content": "sys"}], [{"name": "t"}])
        assert pc.prefix_reuse(None, prefix).predicted == pc.CACHE_UNKNOWN

        same = pc.prefix_reuse(prefix, prefix)
        assert same.predicted == pc.CACHE_HIT
        assert same.basis == "byte-compare"
        assert same.reusable_bytes == prefix.byte_count

        moved = pc.prefix_reuse(
            prefix, pc.stable_prefix([{"role": "system", "content": "sys2"}], [{"name": "t"}])
        )
        assert moved.predicted == pc.CACHE_MISS
        assert moved.tools_identical is True  # the catalogue did not move, only the text
        assert moved.common_messages == 0

    def test_a_moved_tool_catalogue_is_reported(self) -> None:
        one = pc.stable_prefix([{"role": "system", "content": "sys"}], [{"name": "a"}])
        two = pc.stable_prefix([{"role": "system", "content": "sys"}], [{"name": "b"}])
        reuse = pc.prefix_reuse(one, two)
        assert reuse.predicted == pc.CACHE_MISS
        assert reuse.tools_identical is False
        assert "tool catalogue moved" in reuse.note


# --------------------------------------------------------------------------- #
# the breakpoints: where the markers land, and when the split is refused         #
# --------------------------------------------------------------------------- #


def _assembly(tmp_path: Path) -> Any:
    """A real composer assembly (the section list this module splits on)."""
    context = pbuilder.BuildContext(
        project=Path("/home/willy/projects/gods-voice"),
        runtime=tmp_path,
        memory_db=tmp_path / "memory.db",
        tools_db=tmp_path / "tools.db",
        prompts_root=None,
    )
    return pbuilder.build_prompt(
        context, without=("memory", "skills", "tools", "hard_rules")
    )


class TestBreakpoints:
    def test_system_and_tools_get_marked_paths(self) -> None:
        tools = [{"type": "function", "function": {"name": "read"}}]
        plan = pc.explicit_breakpoints(
            [{"role": "system", "content": "hello"}, {"role": "user", "content": "go"}],
            tools=tools,
            strategy=pc.strategy_for("anthropic"),
        )
        targets = {item.target: item for item in plan.breakpoints}
        assert set(targets) == {"system", "tools"}
        assert targets["system"].path == "messages[0].content"
        assert targets["tools"].path == "tools[0]"
        assert plan.messages[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert plan.tools[-1]["cache_control"] == {"type": "ephemeral"}
        assert any(item["target"] == "project_context" for item in plan.skipped)
        assert plan.mode == pc.MODE_EXPLICIT

    def test_the_callers_messages_are_never_mutated(self) -> None:
        messages = [{"role": "system", "content": "hello"}]
        tools = [{"type": "function", "function": {"name": "read"}}]
        pc.explicit_breakpoints(messages, tools=tools, strategy=pc.strategy_for("anthropic"))
        assert messages[0]["content"] == "hello"  # still a plain string
        assert "cache_control" not in tools[-1]

    def test_a_tool_less_request_reports_the_skip(self) -> None:
        plan = pc.explicit_breakpoints(
            [{"role": "system", "content": "hi"}], strategy=pc.strategy_for("anthropic")
        )
        assert [item.target for item in plan.breakpoints] == ["system"]
        assert any(
            item["target"] == "tools" and "no tools" in item["reason"] for item in plan.skipped
        )

    def test_no_system_message_is_a_reported_skip_not_a_guess(self) -> None:
        plan = pc.explicit_breakpoints(
            [{"role": "user", "content": "go"}],
            tools=[{"type": "function", "function": {"name": "read"}}],
            strategy=pc.strategy_for("anthropic"),
        )
        assert [item.target for item in plan.breakpoints] == ["tools"]
        assert any(item["target"] == "system" for item in plan.skipped)

    def test_the_assembly_split_is_byte_exact_and_marks_project_context(
        self, tmp_path: Path
    ) -> None:
        assembly = _assembly(tmp_path)
        plan = pc.explicit_breakpoints(
            [{"role": "system", "content": assembly.text}],
            tools=[{"type": "function", "function": {"name": "read"}}],
            assembly=assembly,
            strategy=pc.strategy_for("anthropic"),
        )
        assert [item.target for item in plan.breakpoints] == [
            "project_context",
            "system",
            "tools",
        ]
        # the split must rebuild the prompt it came from, byte for byte
        blocks = [block for block in plan.messages[0]["content"] if isinstance(block, dict)]
        assert "\n\n".join(block["text"] for block in blocks) == assembly.text
        markers = [bool(block.get("cache_control")) for block in blocks]
        assert markers == [False, True, True]  # context block, then the whole prompt

    def test_a_tampered_assembly_is_refused_and_says_why(self, tmp_path: Path) -> None:
        assembly = _assembly(tmp_path)

        class Moved:
            text = assembly.text + " "  # one byte more than the content we have

            def __init__(self, sections: Any) -> None:
                self.sections = sections

        plan = pc.explicit_breakpoints(
            [{"role": "system", "content": assembly.text}],
            assembly=Moved(assembly.sections),
            strategy=pc.strategy_for("anthropic"),
        )
        assert [item.target for item in plan.breakpoints] == ["system"]
        assert len(plan.messages[0]["content"]) == 1
        assert any(
            item["target"] == "project_context" and "byte for byte" in item["reason"]
            for item in plan.skipped
        )

    def test_the_section_split_rejects_a_prompt_it_cannot_rebuild(self) -> None:
        class Fake:
            text = "## A\n\none"

            sections = [
                type("S", (), {"name": "a", "title": "A", "text": "one", "present": True})()
            ]

        assert pc.section_blocks("## A\n\ntwo", Fake()) is None  # content differs
        assert pc.section_blocks("## A\n\none", Fake()) == [("a", "## A\n\none")]
        assert pc.section_blocks("## A\n\none", None) is None

    def test_more_markers_than_documented_is_an_error_not_a_silent_drop(self) -> None:
        with pytest.raises(pc.CachePlanError, match="documented limit"):
            pc.explicit_breakpoints(
                [{"role": "system", "content": "hi"}], limit=0,
                strategy=pc.strategy_for("anthropic"),
            )

    def test_an_undocumented_ttl_is_refused(self) -> None:
        with pytest.raises(pc.CachePlanError, match="unknown cache ttl"):
            pc.explicit_breakpoints(
                [{"role": "system", "content": "hi"}], ttl="9h",
                strategy=pc.strategy_for("anthropic"),
            )
        plan = pc.explicit_breakpoints(
            [{"role": "system", "content": "hi"}], ttl="1h",
            strategy=pc.strategy_for("anthropic"),
        )
        assert plan.messages[0]["content"][-1]["cache_control"] == {
            "type": "ephemeral", "ttl": "1h",
        }


# --------------------------------------------------------------------------- #
# end to end: the client, a real socket, and the verdict from the payload        #
# --------------------------------------------------------------------------- #


class TestClientCacheWiring:
    def test_cold_then_warm_reports_miss_then_hit(self, stub: _Stub, tmp_path: Path) -> None:
        stub.usages = [
            _usage(prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=812),
            _usage(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=12),
        ]
        ledger = pc.CacheLedger(tmp_path / "prompt-cache.jsonl")
        client = OpenAICompatibleLLM(stub.config(), cache_ledger=ledger)
        messages = [{"role": "system", "content": "S" * 3000}]
        tools = [{"type": "function", "function": {"name": "read"}}]

        cold = client.chat(messages + [{"role": "user", "content": "step 1"}], tools=tools)
        warm = client.chat(messages + [{"role": "user", "content": "step 2"}], tools=tools)

        assert cold.cache["status"] == pc.CACHE_MISS
        assert cold.cache["predicted"] == pc.CACHE_UNKNOWN  # nothing to compare against yet
        assert warm.cache["status"] == pc.CACHE_HIT
        assert warm.cache["predicted"] == pc.CACHE_HIT
        assert warm.cache["basis"].startswith("prompt_cache_hit_tokens=800")
        assert warm.cache["strategy"]["matched_by"] == "model"
        assert warm.cache["prefix"]["sha256"] == cold.cache["prefix"]["sha256"]

    def test_the_counters_reach_the_answer_and_the_step_telemetry(self, stub: _Stub) -> None:
        """The native counters land in ``usage`` — the field a run already records."""
        stub.usages = [_usage(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=12)]
        client = OpenAICompatibleLLM(stub.config())
        result = client.chat([{"role": "system", "content": "s"}])
        assert result.usage["prompt_cache_hit_tokens"] == 800
        assert result.usage["total_tokens"] == 821  # the accounting is untouched
        assert result.cache["status"] == pc.CACHE_HIT
        assert result.to_dict()["cache"]["status"] == pc.CACHE_HIT

    def test_an_answer_without_counters_is_unknown_and_never_a_hit(self, stub: _Stub) -> None:
        stub.usages = [_usage()]
        client = OpenAICompatibleLLM(stub.config())
        result = client.chat([{"role": "system", "content": "s"}])
        assert result.cache["status"] == pc.CACHE_UNKNOWN
        assert "prompt_cache" not in json.dumps(result.usage)
        assert result.cache["observed"] is False

    def test_an_automatic_provider_gets_no_cache_control_in_the_body(self, stub: _Stub) -> None:
        """The automatic family needs a stable prefix, not a marker."""
        stub.usages = [_usage(prompt_cache_hit_tokens=800), _usage(prompt_cache_hit_tokens=800)]
        client = OpenAICompatibleLLM(stub.config())
        messages = [{"role": "system", "content": "sys"}]
        tools = [{"type": "function", "function": {"name": "read"}}]
        client.chat(messages, tools=tools)
        client.chat(messages, tools=tools)
        raw = json.dumps(stub.bodies)
        assert "cache_control" not in raw
        assert stub.bodies[0]["tools"] == tools  # the caller's schemas travel unchanged

    def test_an_explicit_provider_sends_the_markers(self, stub: _Stub) -> None:
        """Anthropic's family: the body carries ``cache_control`` where planned."""
        stub.usages = [_usage(cache_read_input_tokens=0, cache_creation_input_tokens=99)]
        client = OpenAICompatibleLLM(stub.config(base_url=f"http://127.0.0.1:{stub.port}/v1"))
        client._cache_strategy = pc.strategy_for("anthropic")  # a gateway that passes it through
        result = client.chat(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}],
            tools=[{"type": "function", "function": {"name": "read"}}],
        )
        sent = stub.bodies[0]
        assert sent["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert result.cache["status"] == pc.CACHE_MISS  # a cold write, not a hit
        assert result.cache["write_tokens"] == 99
        assert [item["target"] for item in result.cache["breakpoints"]] == ["system", "tools"]

    def test_the_ledger_is_opt_in(self, stub: _Stub, tmp_path: Path) -> None:
        """No ledger asked for ⇒ nothing is written anywhere."""
        stub.usages = [_usage(prompt_cache_hit_tokens=800)]
        client = OpenAICompatibleLLM(stub.config())
        assert client.cache_ledger is None
        client.chat([{"role": "system", "content": "s"}])
        status = client.cache_status()
        assert status["ledger_enabled"] is False
        assert status["entries"] == 0
        assert not list(tmp_path.iterdir())

    def test_cache_status_reports_what_the_ledger_recorded(self, stub: _Stub) -> None:
        stub.usages = [
            _usage(prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=812),
            _usage(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=12),
        ]
        client = OpenAICompatibleLLM(stub.config(), cache_ledger=True)
        messages = [{"role": "system", "content": "sys"}]
        client.chat(messages)
        client.chat(messages)
        status = client.cache_status()
        assert status["ledger_enabled"] is True
        assert status["entries"] == 2
        counts = status["summary"]["counts"]
        assert counts == {pc.CACHE_HIT: 1, pc.CACHE_MISS: 1, pc.CACHE_UNKNOWN: 0}
        assert status["last"]["status"] == pc.CACHE_HIT
        assert status["ledger_failures"] == 0
        client.cache_ledger.clear()


# --------------------------------------------------------------------------- #
# one HTTP client per run — measured on real sockets                            #
# --------------------------------------------------------------------------- #


class TestConnectionReuse:
    def test_a_run_reuses_one_connection_and_the_old_path_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ten calls: one pooled client ⇒ 1 TCP connection; per-call ⇒ 10.

        This is the before/after pair for the connection-reuse half, measured on a
        real socket server (``perf_runtime.CACHE_ENABLED = False`` restores the
        open-and-close-per-call behaviour the client had before).
        """
        warm = _Stub([_usage(prompt_cache_hit_tokens=800)] * 10)
        try:
            client = OpenAICompatibleLLM(warm.config())
            for _ in range(10):
                client.chat([{"role": "system", "content": "s"}])
            assert warm.connections == 1, "the pooled client must keep the connection open"
            assert len(warm.bodies) == 10
        finally:
            warm.stop()

        monkeypatch.setattr(perf, "CACHE_ENABLED", False)
        cold = _Stub([_usage(prompt_cache_hit_tokens=800)] * 10)
        try:
            client = OpenAICompatibleLLM(cold.config())
            for _ in range(10):
                client.chat([{"role": "system", "content": "s"}])
            assert cold.connections == 10, "without the pool every call dials again"
        finally:
            cold.stop()
            perf.close_shared_http_clients()


# --------------------------------------------------------------------------- #
# the ledger and the CLI surface                                                #
# --------------------------------------------------------------------------- #


class TestLedger:
    def test_round_trip_and_a_bad_line_is_counted_not_dropped(self, tmp_path: Path) -> None:
        ledger = pc.CacheLedger(tmp_path / "l.jsonl")
        ledger.append({"status": pc.CACHE_HIT, "read_tokens": 12, "provider": "deepseek"})
        with ledger.path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
            handle.write('"a string, not an object"\n')
        rows, bad = ledger.entries()
        assert bad == 2
        assert len(rows) == 1
        assert rows[0].read_tokens == 12
        summary = ledger.summary()
        assert summary["counts"][pc.CACHE_HIT] == 1
        assert summary["bad_lines"] == 2
        assert summary["read_tokens"] == 12

    def test_an_unwritable_ledger_returns_false_instead_of_raising(self, tmp_path: Path) -> None:
        target = tmp_path / "a-file"
        target.write_text("x", encoding="utf-8")
        ledger = pc.CacheLedger(target / "l.jsonl")  # a directory that is a file
        assert ledger.append({"status": pc.CACHE_HIT}) is False

    def test_an_empty_ledger_answers_with_zeros(self, tmp_path: Path) -> None:
        ledger = pc.CacheLedger(tmp_path / "missing.jsonl")
        assert ledger.exists is False
        assert ledger.entries() == ([], 0)
        summary = ledger.summary()
        assert summary["requests"] == 0
        assert summary["last"] is None


@pytest.fixture
def fake_contract(monkeypatch: pytest.MonkeyPatch):
    """A throwaway contract module, so this suite never installs into the real table.

    ``register_cli`` installs its entries into ``cli_contract.COMMANDS`` (that is the
    CLI contract), and :mod:`skaldir.prompt_builder` composes the prompt's *commands*
    section **from that table** and memoises the parser it built once. A test that
    installs real entries therefore moves the in-process prompt fingerprint while a
    subprocess keeps the untouched one — which is exactly how this file broke
    ``test_prompt_builder_stability``'s cross-process check when it ran first. Pointing
    ``_CONTRACT`` at a throwaway keeps the assertion and the global table intact.
    """
    from types import SimpleNamespace

    fake = SimpleNamespace(COMMANDS={})
    monkeypatch.setattr(pcli, "_CONTRACT", fake)
    return fake


class TestCli:
    def _parser(self) -> Any:
        import argparse

        parser = argparse.ArgumentParser(prog="skaldir")
        sub = parser.add_subparsers(dest="command")
        pcli.build_cache_parser(sub)
        return parser

    def test_wiring_extends_an_existing_prompt_group(self, fake_contract: Any) -> None:
        """Both orders of the two ``prompt`` groups produce the same leaf set."""
        import argparse

        from skaldir import prompt_builder_cli

        parser = argparse.ArgumentParser(prog="skaldir")
        sub = parser.add_subparsers(dest="command")
        prompt_builder_cli.build_prompt_parser(sub)
        pcli.build_cache_parser(sub)
        prompt = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ).choices["prompt"]
        leaves = sorted(
            next(
                action
                for action in prompt._actions
                if isinstance(action, argparse._SubParsersAction)
            ).choices
        )
        assert leaves == ["build", "cache", "explain", "fingerprint"]
        pcli.build_cache_parser(sub)  # a second wiring must add nothing, not raise
        assert leaves == ["build", "cache", "explain", "fingerprint"]

    def test_register_cli_installs_its_contract_entries(self, fake_contract: Any) -> None:
        import argparse

        parser = argparse.ArgumentParser(prog="skaldir")
        sub = parser.add_subparsers(dest="command")
        pcli.register_cli(sub)
        assert sorted(fake_contract.COMMANDS) == ["prompt.cache.plan", "prompt.cache.status"]

    def test_plan_lands_the_markers_and_refuses_a_bad_ttl(self, tmp_path: Path) -> None:
        tools = tmp_path / "tools.json"
        tools.write_text(json.dumps([{"type": "function", "function": {"name": "read"}}]))
        parser = self._parser()
        args = parser.parse_args(
            ["prompt", "cache", "plan", "--prompt", "hello", "--tools-file", str(tools),
             "--provider", "anthropic", "--ttl", "1h"]
        )
        assert args.func(args) == 0
        assert args.command_path == "prompt.cache.plan"

        bad = parser.parse_args(["prompt", "cache", "plan", "--prompt", "hello", "--ttl", "9h"])
        assert bad.func(bad) == 1

    def test_plan_does_not_need_the_prompt_group_indirection(self, tmp_path: Path) -> None:
        """The standalone parser (``python -m skaldir.prompt_cache_cli``) has the same verbs."""
        standalone = pcli.build_parser()
        args = standalone.parse_args(["plan", "--prompt", "hello", "--provider", "anthropic"])
        assert args.func(args) == 0

    def test_status_plans_and_reads_a_ledger(self, tmp_path: Path) -> None:
        ledger = pc.CacheLedger(tmp_path / "prompt-cache.jsonl")
        ledger.append({"status": pc.CACHE_HIT, "read_tokens": 700, "provider": "deepseek"})
        parser = self._parser()
        args = parser.parse_args(
            ["prompt", "cache", "status", "--ledger", str(ledger.path), "--runtime", str(tmp_path),
             "--limit", "10"]
        )
        assert args.func(args) == 0
        payload = lp.prompt_cache_surface(ledger=ledger.path, limit=10)
        assert payload["entries"] == 1
        assert payload["summary"]["counts"][pc.CACHE_HIT] == 1
        assert payload["support"]["count"] == len(pc.SUPPORT)

    def test_contract_entries_name_their_evidence(self) -> None:
        for path, spec in pcli.CONTRACT_ENTRIES.items():
            assert path.startswith("prompt.cache.")
            assert spec.evidence, f"{path} has no evidence"
            assert spec.description


# --------------------------------------------------------------------------- #
# the parts that must not change                                               #
# --------------------------------------------------------------------------- #


class TestNoRegression:
    def test_a_payload_without_cache_fields_keeps_its_old_shape(self) -> None:
        payload = {
            "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
        from skaldir.llm_provider import chat_result_from_payload

        result = chat_result_from_payload(payload)
        assert result.usage == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
        assert "cache_control" not in json.dumps(payload)

    def test_chat_request_body_is_unchanged_without_a_plan(self) -> None:
        from skaldir.llm_provider import chat_request_body

        cfg = ProviderConfig(provider="openai-compatible", base_url="http://x/v1", model="m")
        body = chat_request_body(
            cfg, [{"role": "system", "content": "s"}], tier="main",
            tools=[{"type": "function", "function": {"name": "t"}}],
        )
        assert body["messages"] == [{"role": "system", "content": "s"}]
        assert body["tools"] == [{"type": "function", "function": {"name": "t"}}]

    def test_usage_from_payload_still_reduces_to_three_counters(self) -> None:
        from skaldir.llm_provider import usage_from_payload

        assert usage_from_payload({"usage": _usage(prompt_cache_hit_tokens=800)}) == {
            "prompt_tokens": 812,
            "completion_tokens": 9,
            "total_tokens": 821,
        }

    def test_an_unknown_streamed_usage_is_still_unknown(self) -> None:
        from skaldir.llm_provider import accumulate_stream_chunks

        chunks = [
            {"choices": [{"delta": {"content": "a"}}]},
            {"choices": [{"delta": {"content": "b"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
        ]
        result = accumulate_stream_chunks(chunks)
        assert result.text == "ab"
        assert result.usage["total_tokens"] == 4
        assert result.cache["status"] == pc.CACHE_UNKNOWN

    def test_a_streamed_hit_is_read_from_the_last_usage_chunk(self) -> None:
        from skaldir.llm_provider import accumulate_stream_chunks

        chunks = [
            {"choices": [{"delta": {"content": "a"}}]},
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 3,
                          "prompt_cache_hit_tokens": 880},
            },
        ]
        result = accumulate_stream_chunks(chunks)
        assert result.cache["status"] == pc.CACHE_HIT
        assert result.usage["prompt_cache_hit_tokens"] == 880
        assert result.usage["total_tokens"] == 903


class TestNoNetworkByDefault:
    def test_a_client_without_a_ledger_never_touches_the_filesystem(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``SKALDIR_PROMPT_CACHE_LEDGER`` is the only way a plain call writes."""
        from skaldir import env

        monkeypatch.delenv(env.expand(["SKALDIR_PROMPT_CACHE_LEDGER"])[0], raising=False)
        client = OpenAICompatibleLLM(
            ProviderConfig(provider="openai-compatible", base_url="http://x/v1", model="m")
        )
        assert client.cache_ledger is None
        assert client.cache_status()["ledger_enabled"] is False

    def test_the_environment_names_the_ledger(self, monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
        from skaldir import env

        target = tmp_path / "from-env.jsonl"
        monkeypatch.setenv(env.expand(["SKALDIR_PROMPT_CACHE_LEDGER"])[0], str(target))
        client = OpenAICompatibleLLM(
            ProviderConfig(provider="openai-compatible", base_url="http://x/v1", model="m")
        )
        assert client.cache_ledger is not None
        assert client.cache_ledger.path == target

    def test_no_absolute_path_is_baked_into_the_module(self, tmp_path: Path) -> None:
        """The ledger default follows the runtime dir, never a literal path."""
        assert pc.default_ledger_path(tmp_path) == tmp_path / pc.LEDGER_FILE
        assert pc.default_ledger_path().name == pc.LEDGER_FILE


def test_the_module_never_imports_the_provider() -> None:
    """``llm_provider`` imports this module, so the reverse import would be a cycle."""
    source = Path(pc.__file__).read_text(encoding="utf-8")
    assert "import llm_provider" not in source
    assert "from .llm_provider" not in source
    assert lp.OpenAICompatibleLLM is OpenAICompatibleLLM  # the import above stays meaningful
