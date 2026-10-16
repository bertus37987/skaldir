"""Offline tests for the OpenAI-compatible provider.

No credentials and no network: every HTTP test runs against a stub server bound
to ``127.0.0.1`` on an ephemeral port inside the test process. The single live
test lives in ``tests/test_llm_provider_live.py`` and is skipped unless
``GODSV_LLM_LIVE=1``.

The stub is deliberately a real socket server (not a transport mock) so the
tests exercise real timeouts, real connection handling and real SSE chunking.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skaldir import env
from skaldir import llm_provider as lp
from skaldir.llm import (
    CallableLLM,
    LLMError,
    LLMProviderError,
    ScriptedLLM,
    provider_for,
)
from skaldir.llm_provider import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    OpenAICompatibleLLM,
    ProviderAuthError,
    ProviderConfigError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    endpoint_for,
    provider_status,
    resolve_provider_config,
)
from skaldir.system_one_policy import plan_retry

#: A path that cannot exist: keeps the tests independent of the developer's
#: real ~/.hermes/godsv/config.yaml.
ABSENT_CONFIG = Path("/nonexistent/godsv-llm-provider-tests/config.yaml")

FAKE_KEY = "sk-test-not-a-real-key"


# --------------------------------------------------------------------------- #
# stub server                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class Step:
    """One scripted answer of the stub endpoint."""

    status: int = 200
    body: Any = None
    raw: str | None = None
    sse: list[str] | None = None
    delay: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)


def ok(text: str, *, model: str = "test-model") -> Step:
    return Step(body={"choices": [{"message": {"role": "assistant", "content": text}}]})


def http_error(status: int, message: str) -> Step:
    return Step(status=status, body={"error": {"message": message, "type": "stub"}})


def sse(*pieces: str) -> Step:
    """Streamed answer: one SSE ``data:`` line per piece, then ``[DONE]``."""
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n"
        for piece in pieces
    ]
    lines.append("data: [DONE]\n\n")
    return Step(sse=lines, headers={"Content-Type": "text/event-stream"})


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: ARG002
        """A client that already timed out is expected — not a test failure."""
        return


class StubProvider:
    """A minimal OpenAI-compatible endpoint with a scripted answer sequence.

    The last step repeats forever, so "always 429" scripts do not need a count.
    Every request is recorded (path, headers, parsed JSON body).
    """

    def __init__(self, steps: list[Step]) -> None:
        assert steps, "the stub needs at least one step"
        self._steps = list(steps)
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                payload = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(payload) if payload else {}
                except ValueError:
                    body = {"_unparsed": payload.decode("utf-8", "replace")}
                with stub._lock:
                    stub.requests.append(
                        {
                            "path": self.path,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                            "body": body,
                        }
                    )
                    step = stub._steps[min(len(stub.requests) - 1, len(stub._steps) - 1)]
                if step.delay:
                    time.sleep(step.delay)
                try:
                    stub._respond(self, step)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client timed out and closed first — asserted by the test

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), daemon=True
        )

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, step: Step) -> None:
        handler.send_response(step.status)
        for name, value in step.headers.items():
            handler.send_header(name, value)
        if step.sse is not None:
            # genuinely chunked: the client has to stream, not slurp a buffer
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            for line in step.sse:
                data = line.encode()
                handler.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                handler.wfile.flush()
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()
            return
        payload = (
            step.raw.encode()
            if step.raw is not None
            else json.dumps(step.body if step.body is not None else {}).encode()
        )
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
        handler.wfile.flush()

    # -- lifecycle --------------------------------------------------------- #

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def start(self) -> StubProvider:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> StubProvider:
        return self.start()

    def __exit__(self, *exc: Any) -> bool:
        self.stop()
        return False


def client_for(stub: StubProvider, **overrides: Any) -> OpenAICompatibleLLM:
    """An isolated client for the stub endpoint (no env, no config file)."""
    settings: dict[str, Any] = {
        "base_url": stub.base_url,
        "model": "test-model",
        "api_key": FAKE_KEY,
    }
    settings.update(overrides)
    cfg = resolve_provider_config(settings, env={}, config_path=ABSENT_CONFIG)
    return OpenAICompatibleLLM(cfg)


# --------------------------------------------------------------------------- #
# configuration resolution                                                    #
# --------------------------------------------------------------------------- #


class TestResolution:
    def test_explicit_config_wins_over_env_and_file(self, tmp_path: Path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "godsv:\n  llm:\n    base_url: http://file.invalid/v1\n    model: file-model\n",
            encoding="utf-8",
        )
        monkeypatch.setenv(ENV_BASE_URL, "http://env.invalid/v1")
        monkeypatch.setenv(ENV_MODEL, "env-model")
        cfg = resolve_provider_config(
            {"base_url": "http://explicit.invalid/v1", "model": "explicit-model"}
        )
        assert cfg.base_url == "http://explicit.invalid/v1"
        assert cfg.model == "explicit-model"
        assert cfg.sources["base_url"] == "explicit"

        env_wins = resolve_provider_config(None, config_path=cfg_file)
        assert env_wins.base_url == "http://env.invalid/v1"
        assert env_wins.model == "env-model"
        assert env_wins.sources["base_url"] == f"env:{ENV_BASE_URL}"

        monkeypatch.delenv(ENV_BASE_URL)
        monkeypatch.delenv(ENV_MODEL)
        file_wins = resolve_provider_config(None, config_path=cfg_file)
        assert file_wins.base_url == "http://file.invalid/v1"
        assert file_wins.model == "file-model"
        assert file_wins.sources["base_url"] == f"config:{cfg_file}"

    def test_config_file_is_read_from_godsv_llm_block(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "godsv:\n"
            "  llm:\n"
            "    base_url: http://localhost:8080/v1\n"
            "    model: qwen2.5\n"
            "    timeout_s: 12.5\n"
            "    max_retries: 4\n"
            "    stream: true\n"
            "    headers:\n"
            "      HTTP-Referer: https://example.invalid\n"
            "    max_tokens_field: max_completion_tokens\n"
            "    tiers:\n"
            "      small: qwen2.5-3b\n",
            encoding="utf-8",
        )
        cfg = resolve_provider_config(None, env={}, config_path=cfg_file)
        assert cfg.base_url == "http://localhost:8080/v1"
        assert cfg.timeout_s == 12.5
        assert cfg.max_retries == 4
        assert cfg.stream is True
        assert cfg.headers == {"HTTP-Referer": "https://example.invalid"}
        assert cfg.max_tokens_field == "max_completion_tokens"
        assert cfg.tier_models == {"small": "qwen2.5-3b"}
        assert cfg.ready() is True
        assert cfg.config_file == str(cfg_file)

    def test_toplevel_llm_block_is_accepted(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n  base_url: http://localhost:9/v1\n  model: m\n", encoding="utf-8"
        )
        cfg = resolve_provider_config(None, env={}, config_path=cfg_file)
        assert (cfg.base_url, cfg.model) == ("http://localhost:9/v1", "m")

    def test_godsv_llm_config_env_selects_the_file(self, tmp_path: Path, monkeypatch):
        cfg_file = tmp_path / "custom.yaml"
        cfg_file.write_text("godsv:\n  llm:\n    model: from-env-path\n", encoding="utf-8")
        monkeypatch.setenv(lp.ENV_CONFIG, str(cfg_file))
        assert lp.default_config_file() == cfg_file
        assert resolve_provider_config(None, env={}).model == "from-env-path"

    def test_godsv_home_is_the_default_location(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("GODSV_HOME", str(tmp_path))
        # the runtime-dir precedence is SKALDIR_HOME → GODSV_HOME → HERMES_HOME: clear the
        # two that outrank the one under test (a leaked SKALDIR_HOME would point elsewhere)
        monkeypatch.delenv("SKALDIR_HOME", raising=False)
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.delenv(lp.ENV_CONFIG, raising=False)
        assert lp.default_config_file() == tmp_path / "config.yaml"
        (tmp_path / "config.yaml").write_text(
            "godsv:\n  llm:\n    model: home-model\n", encoding="utf-8"
        )
        assert resolve_provider_config(None, env={}).model == "home-model"

    def test_env_tier_models_string(self):
        cfg = resolve_provider_config(
            {"base_url": "http://localhost:1/v1", "model": "base"},
            env={lp.ENV_TIER_MODELS: "small=tiny,main=big"},
            config_path=ABSENT_CONFIG,
        )
        assert cfg.tier_models == {"small": "tiny", "main": "big"}
        assert cfg.model_for_tier("small") == "tiny"
        assert cfg.model_for_tier("mid") == "base"

    def test_unknown_tier_is_refused(self):
        with pytest.raises(ProviderConfigError) as exc:
            resolve_provider_config(
                {"base_url": "http://localhost:1/v1", "model": "m", "tiers": {"giant": "x"}},
                env={},
                config_path=ABSENT_CONFIG,
            )
        assert "unknown tiers" in str(exc.value)

    def test_env_fallbacks_for_openai_style_names(self):
        cfg = resolve_provider_config(
            None,
            env={
                "OPENAI_BASE_URL": "http://openai-style.invalid/v1",
                "OPENAI_API_KEY": "sk-from-fallback",
                "OPENAI_MODEL": "fallback-model",
            },
            config_path=ABSENT_CONFIG,
        )
        assert cfg.base_url == "http://openai-style.invalid/v1"
        assert cfg.api_key == "sk-from-fallback"
        assert cfg.sources["api_key"] == "env:OPENAI_API_KEY"
        assert cfg.model == "fallback-model"
        assert cfg.ready() is True

    def test_api_key_env_names_the_variable_to_read(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "godsv:\n"
            "  llm:\n"
            "    base_url: http://localhost:1/v1\n"
            "    model: m\n"
            "    api_key_env: MY_PRIVATE_LLM_KEY\n",
            encoding="utf-8",
        )
        cfg = resolve_provider_config(
            None, env={"MY_PRIVATE_LLM_KEY": "sk-looked-up"}, config_path=cfg_file
        )
        assert cfg.api_key == "sk-looked-up"
        assert cfg.api_key_env == "MY_PRIVATE_LLM_KEY"
        assert cfg.sources["api_key"] == "env:MY_PRIVATE_LLM_KEY"

    def test_api_key_env_named_but_unset_says_which_var(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "godsv:\n"
            "  llm:\n"
            "    base_url: http://localhost:1/v1\n"
            "    model: m\n"
            "    api_key_env: MY_MISSING_KEY\n",
            encoding="utf-8",
        )
        cfg = resolve_provider_config(None, env={}, config_path=cfg_file)
        assert cfg.api_key == ""
        assert "MY_MISSING_KEY" in lp.not_configured_message(cfg)
        # a keyless local endpoint stays usable — the key is optional for llama.cpp/vLLM
        assert cfg.ready() is True

    def test_the_401_verdict_names_the_plan_env_var_not_the_generic_default(self):
        """RESUME §3: the auth-error wording must name the plan's own key variable."""
        cfg = lp.ProviderConfig(
            base_url="https://api.xiaomimimo.com/v1",
            model="mimo-v2.5-pro",
            api_key_env="MIMO_API_KEY",
            api_key="x",
        )
        client = lp.OpenAICompatibleLLM(cfg)

        class _Resp:
            status_code = 401

            def json(self):
                return {"error": {"message": "invalid_key"}}

            text = "invalid_key"

        message = str(client._http_error(_Resp()))
        assert "MIMO_API_KEY" in message
        assert "SKALDIR_LLM_API_KEY" not in message

    def test_a_config_file_is_not_a_vault(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "godsv:\n"
            "  llm:\n"
            "    base_url: http://localhost:1/v1\n"
            "    model: m\n"
            "    api_key: sk-in-the-file\n",
            encoding="utf-8",
        )
        cfg = resolve_provider_config(None, env={}, config_path=cfg_file)
        assert cfg.api_key == ""  # never read from a file
        assert any("literal 'api_key:'" in problem for problem in cfg.problems)
        with pytest.raises(ProviderConfigError) as exc:
            provider_for(None, env={}, config_path=cfg_file)
        assert "secrets are never read from config files" in str(exc.value)
        assert "sk-in-the-file" not in str(exc.value)

    def test_every_documented_secret_key_is_refused(self, tmp_path: Path):
        for name in ("token", "secret", "password", "access_token", "bearer"):
            cfg_file = tmp_path / f"{name}.yaml"
            cfg_file.write_text(
                "godsv:\n"
                "  llm:\n"
                "    base_url: http://localhost:1/v1\n"
                f"    model: m\n    {name}: x\n",
                encoding="utf-8",
            )
            cfg = resolve_provider_config(None, env={}, config_path=cfg_file)
            assert any(f"literal '{name}:'" in problem for problem in cfg.problems), name

    def test_secret_in_the_file_is_refused_even_with_an_explicit_config(self, tmp_path: Path):
        """A file that stores a key is a problem in its own right — fix the file."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "godsv:\n"
            "  llm:\n"
            "    base_url: http://localhost:1/v1\n"
            "    model: m\n"
            "    api_key: sk-in-the-file\n",
            encoding="utf-8",
        )
        with pytest.raises(ProviderConfigError) as exc:
            provider_for(
                {"base_url": "http://explicit.invalid/v1", "model": "explicit"},
                env={},
                config_path=cfg_file,
            )
        assert "api_key:" in str(exc.value)
        assert "sk-in-the-file" not in str(exc.value)

    def test_the_key_never_renders(self, tmp_path: Path):
        cfg = resolve_provider_config(
            {"base_url": "http://localhost:1/v1", "model": "m", "api_key": FAKE_KEY},
            env={},
            config_path=ABSENT_CONFIG,
        )
        assert FAKE_KEY not in repr(cfg)
        assert "api_key=<set>" in repr(cfg)
        assert "api_key=<unset>" in repr(
            resolve_provider_config(
                {"base_url": "http://localhost:1/v1", "model": "m"},
                env={},
                config_path=ABSENT_CONFIG,
            )
        )
        status = provider_status(
            {"base_url": "http://localhost:1/v1", "model": "m", "api_key": FAKE_KEY},
            env={},
            config_path=ABSENT_CONFIG,
        )
        dumped = json.dumps(status)
        assert FAKE_KEY not in dumped
        assert status["api_key"] == "set"
        assert status["api_key_env"] == ""

    def test_status_is_json_safe_and_reports_sources(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("godsv:\n  llm:\n    model: file-model\n", encoding="utf-8")
        status = provider_status(
            {"base_url": "http://localhost:1/v1"}, env={}, config_path=cfg_file
        )
        assert status["ready"] is True
        assert status["model"] == "file-model"
        assert status["sources"]["model"] == f"config:{cfg_file}"
        assert status["sources"]["base_url"] == "explicit"
        assert status["api_key"] == "unset"
        assert status["endpoint"].endswith("/chat/completions")
        assert status["max_retries"] == lp.DEFAULT_MAX_RETRIES
        assert status["env_contract"]["base_url"][0] == ENV_BASE_URL
        json.dumps(status)  # must never raise (CLI --json surface)

    def test_status_warns_about_a_remote_endpoint_without_a_key(self):
        status = provider_status(
            {"base_url": "https://api.example.invalid/v1", "model": "m"},
            env={},
            config_path=ABSENT_CONFIG,
        )
        assert "401" in status["warning"] and ENV_API_KEY in status["warning"]

    def test_local_endpoint_without_key_has_no_warning(self):
        status = provider_status(
            {"base_url": "http://127.0.0.1:8080/v1", "model": "m"},
            env={},
            config_path=ABSENT_CONFIG,
        )
        assert "warning" not in status

    def test_numeric_and_boolean_syntax_errors_are_loud(self):
        with pytest.raises(ProviderConfigError) as exc:
            resolve_provider_config(
                {"base_url": "http://localhost:1/v1", "model": "m", "timeout_s": "abc"},
                env={},
                config_path=ABSENT_CONFIG,
            )
        assert "not a number" in str(exc.value)
        with pytest.raises(ProviderConfigError) as exc2:
            resolve_provider_config(
                {"base_url": "http://localhost:1/v1", "model": "m", "stream": "maybe"},
                env={},
                config_path=ABSENT_CONFIG,
            )
        assert "not a boolean" in str(exc2.value)

    def test_validation_problems(self):
        bad_url = resolve_provider_config(
            {"base_url": "localhost:8080/v1", "model": "m"}, env={}, config_path=ABSENT_CONFIG
        )
        assert any("http://" in problem for problem in bad_url.problems)
        assert bad_url.ready() is False
        bad_provider = resolve_provider_config(
            {"provider": "wat", "base_url": "http://localhost:1/v1", "model": "m"},
            env={},
            config_path=ABSENT_CONFIG,
        )
        assert any("unknown provider" in problem for problem in bad_provider.problems)

    def test_endpoint_for(self):
        assert endpoint_for("http://h/v1") == "http://h/v1/chat/completions"
        assert endpoint_for("http://h/v1/") == "http://h/v1/chat/completions"
        assert endpoint_for("http://h/v1/chat/completions") == "http://h/v1/chat/completions"
        assert endpoint_for("") == ""

    def test_resolution_never_writes_anywhere(self, tmp_path: Path):
        cfg_file = tmp_path / "config.yaml"
        original = "godsv:\n  llm:\n    base_url: http://localhost:1/v1\n    model: m\n"
        cfg_file.write_text(original, encoding="utf-8")
        before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
        provider_for(None, env={}, config_path=cfg_file)
        assert cfg_file.read_text(encoding="utf-8") == original
        assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"]

    def test_the_module_docstring_is_the_contract(self):
        """The documented env vars and config keys must stay in the docstring."""
        doc = lp.__doc__ or ""
        for name in (
            lp.ENV_PROVIDER,
            lp.ENV_BASE_URL,
            lp.ENV_API_KEY,
            lp.ENV_MODEL,
            lp.ENV_TIER_MODELS,
            lp.ENV_TIMEOUT,
            lp.ENV_TOTAL_TIMEOUT,
            lp.ENV_MAX_RETRIES,
            lp.ENV_BACKOFF,
            lp.ENV_MAX_TOKENS,
            lp.ENV_STREAM,
            lp.ENV_CONFIG,
        ):
            assert name in doc, f"{name} is not documented in godsv.llm_provider"
        for name in lp.ENV_BASE_URL_FALLBACKS + lp.ENV_API_KEY_FALLBACKS + lp.ENV_MODEL_FALLBACKS:
            assert name in doc, f"fallback {name} is not documented"
        for key in (
            "base_url",
            "api_key_env",
            "model",
            "tiers",
            "timeout_s",
            "total_timeout_s",
            "max_retries",
            "backoff_s",
            "max_tokens_field",
            "temperature",
            "headers",
            "extra_body",
            "responses",
        ):
            assert key in doc, f"config key {key} is not documented"
        assert "SKALDIR_LLM=1" in doc  # the plugin gate must not be confused with this one
        assert "GODSV_LLM=1" in doc, "the pre-rename spelling is still read and must be named"

    def test_env_lookup_order_is_explicit(self):
        assert lp.ENV_CONTRACT["base_url"] == (
            lp.ENV_BASE_URL,
            "SKALDIR_LLM_API_BASE",
            "OPENAI_BASE_URL",
        )
        assert lp.ENV_CONTRACT["api_key"] == (ENV_API_KEY, "OPENAI_API_KEY", "SKALDIR_LLM_KEY")
        # …and the pre-rename spelling of the same variable is its documented twin:
        # `env.getenv` reads SKALDIR_LLM_KEY first and GODSV_LLM_KEY second.
        assert env.legacy_name(lp.ENV_API_KEY) == "GODSV_LLM_API_KEY"
        assert env.legacy_name("SKALDIR_LLM_KEY") == "GODSV_LLM_KEY"
        assert lp.ENV_CONTRACT["model"] == (ENV_MODEL, "OPENAI_MODEL")

    def test_retry_policy_is_the_shared_one_not_a_second_contract(self):
        assert lp.plan_retry is plan_retry
        assert lp.DEFAULT_MAX_RETRIES + 1 == lp.MAX_ATTEMPTS == 3


# --------------------------------------------------------------------------- #
# factory                                                                     #
# --------------------------------------------------------------------------- #


class TestFactory:
    def test_returns_a_real_client_when_configured(self):
        client = provider_for(
            {"base_url": "http://127.0.0.1:8080/v1", "model": "m"},
            env={},
            config_path=ABSENT_CONFIG,
        )
        assert isinstance(client, OpenAICompatibleLLM)
        assert str(client.endpoint()).endswith("/v1/chat/completions")

    def test_unconfigured_run_states_the_exact_exports(self):
        with pytest.raises(ProviderConfigError) as exc:
            provider_for(None, env={}, config_path=ABSENT_CONFIG)
        message = str(exc.value)
        assert ENV_BASE_URL in message and ENV_MODEL in message and ENV_API_KEY in message

    def test_scripted_fallback_is_used_when_nothing_is_configured(self):
        fallback = ScriptedLLM(responses={"voice_triage": "{}"})
        client = provider_for(
            None, scripted=fallback, env={}, config_path=ABSENT_CONFIG
        )
        assert client is fallback

    def test_a_client_object_passes_through_unchanged(self):
        scripted = ScriptedLLM(responses={"x": "y"})
        assert provider_for(scripted, env={}) is scripted
        assert provider_for(CallableLLM(lambda **_: "ok"), env={}).complete(
            prompt_id="x", prompt="p", tier="small"
        ) == "ok"

    def test_provider_scripted_builds_a_scripted_client(self):
        client = provider_for(
            {"provider": "scripted", "responses": {"voice_triage": '{"stuck": false}'}},
            env={},
            config_path=ABSENT_CONFIG,
        )
        assert isinstance(client, ScriptedLLM)
        assert client.complete(prompt_id="voice_triage", prompt="p", tier="small") == (
            '{"stuck": false}'
        )

    def test_provider_scripted_without_responses_is_refused(self):
        with pytest.raises(ProviderConfigError) as exc:
            provider_for(
                {"provider": "scripted"}, env={}, config_path=ABSENT_CONFIG
            )
        assert "responses" in str(exc.value)

    def test_provider_none_is_refused_not_silently_ignored(self):
        with pytest.raises(ProviderConfigError) as exc:
            provider_for(
                {"provider": "none"}, env={}, config_path=ABSENT_CONFIG
            )
        assert "disabled" in str(exc.value)

    def test_garbage_config_type_is_refused(self):
        with pytest.raises(ProviderConfigError):
            provider_for(42, env={}, config_path=ABSENT_CONFIG)

    def test_summary_reports_set_unset_without_the_key(self):
        cfg = resolve_provider_config(
            {"base_url": "http://localhost:1/v1", "model": "m", "api_key": FAKE_KEY},
            env={},
            config_path=ABSENT_CONFIG,
        )
        summary = lp.provider_summary(cfg)
        assert "api_key=set" in summary and FAKE_KEY not in summary


# --------------------------------------------------------------------------- #
# the HTTP client (real sockets)                                              #
# --------------------------------------------------------------------------- #


class TestHttpSuccess:
    def test_non_streamed_completion(self):
        with StubProvider([ok("hello world")]) as stub:
            client = client_for(stub)
            out = client.complete(prompt_id="voice_triage", prompt="are you stuck?", tier="small")
            assert out == "hello world"
            assert stub.request_count == 1
            request = stub.requests[0]
            assert request["path"] == "/v1/chat/completions"
            assert request["headers"]["authorization"] == f"Bearer {FAKE_KEY}"
            assert request["body"]["model"] == "test-model"
            assert request["body"]["messages"] == [{"role": "user", "content": "are you stuck?"}]
            assert request["body"]["max_tokens"] == lp.DEFAULT_MAX_TOKENS
            assert "stream" not in request["body"]

    def test_streamed_completion_is_chunked_and_joined(self):
        with StubProvider([sse('{"stuck"', ": false}")]) as stub:
            client = client_for(stub, stream=True)
            assert client.complete(prompt_id="p", prompt="x", tier="small") == '{"stuck": false}'
            request = stub.requests[0]
            assert request["body"]["stream"] is True
            assert request["headers"]["accept"] == "text/event-stream"

    def test_content_as_parts_is_joined(self):
        parts = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        step = Step(body={"choices": [{"message": {"content": parts}}]})
        with StubProvider([step]) as stub:
            assert client_for(stub).complete(prompt_id="p", prompt="x", tier="small") == "ab"

    def test_tier_selects_the_model(self):
        with StubProvider([ok("x")]) as stub:
            client = client_for(stub, tiers={"small": "tiny-model"})
            client.complete(prompt_id="p", prompt="x", tier="small")
            client.complete(prompt_id="p", prompt="x", tier="main")
            assert [r["body"]["model"] for r in stub.requests] == ["tiny-model", "test-model"]

    def test_extra_body_headers_temperature_and_token_field(self):
        with StubProvider([ok("x")]) as stub:
            client = client_for(
                stub,
                extra_body={"reasoning_effort": "low"},
                headers={"HTTP-Referer": "https://example.invalid"},
                temperature=0.2,
                max_tokens=77,
                max_tokens_field="max_completion_tokens",
            )
            client.complete(prompt_id="p", prompt="x", tier="small")
            body = stub.requests[0]["body"]
            assert body["reasoning_effort"] == "low"
            assert body["temperature"] == 0.2
            assert body["max_completion_tokens"] == 77 and "max_tokens" not in body
            assert stub.requests[0]["headers"]["http-referer"] == "https://example.invalid"


class TestHttpErrors:
    def test_401_is_auth_error_not_retried_and_keeps_the_key_out(self):
        with StubProvider([http_error(401, "invalid api key")]) as stub:
            client = client_for(stub)
            with pytest.raises(ProviderAuthError) as exc:
                client.complete(prompt_id="p", prompt="x", tier="small")
            assert exc.value.status == 401
            assert stub.request_count == 1  # §43: 401 is never retried
            assert exc.value.sdk_retries == 0
            assert ENV_API_KEY in str(exc.value)
            assert "invalid api key" in str(exc.value)  # provider detail is surfaced
            assert FAKE_KEY not in str(exc.value)  # our credential never is

    def test_403_is_auth_error(self):
        with StubProvider([http_error(403, "forbidden")]) as stub:
            with pytest.raises(ProviderAuthError):
                client_for(stub).complete(prompt_id="p", prompt="x", tier="small")
            assert stub.request_count == 1

    def test_429_is_retried_with_backoff_then_succeeds(self):
        with StubProvider([http_error(429, "slow down"), ok("after retry")]) as stub:
            client = client_for(stub)  # default backoff base (0.25s, shared policy)
            started = time.monotonic()
            out = client.complete(prompt_id="p", prompt="x", tier="small")
            elapsed = time.monotonic() - started
            assert out == "after retry"
            assert stub.request_count == 2
            assert elapsed >= 0.25 - 0.01  # the §43 backoff really happened

    def test_429_exhausts_retries_and_reports_the_attempt_count(self):
        with StubProvider([http_error(429, "rate limited")]) as stub:
            client = client_for(stub, max_retries=1, backoff_s=0.01)
            with pytest.raises(ProviderRateLimitError) as exc:
                client.complete(prompt_id="p", prompt="x", tier="small")
            assert stub.request_count == 2  # initial + one retry
            assert exc.value.status == 429
            assert exc.value.sdk_retries == 1
            assert exc.value.sdk_retries + 1 == lp.MAX_ATTEMPTS - 1  # §43 accounting

    def test_529_overload_is_retried_per_policy(self):
        with StubProvider([http_error(529, "overloaded")]) as stub:
            client = client_for(stub, max_retries=1, backoff_s=0.01)
            with pytest.raises(LLMProviderError) as exc:
                client.complete(prompt_id="p", prompt="x", tier="small")
            assert stub.request_count == 2
            assert exc.value.status == 529

    def test_500_is_not_retried(self):
        with StubProvider([http_error(500, "boom")]) as stub:
            with pytest.raises(LLMProviderError) as exc:
                client_for(stub, max_retries=2, backoff_s=0.01).complete(
                    prompt_id="p", prompt="x", tier="small"
                )
            assert exc.value.status == 500
            assert stub.request_count == 1

    def test_422_contract_error_is_not_retried(self):
        with StubProvider([http_error(422, "unprocessable")]) as stub:
            with pytest.raises(LLMProviderError) as exc:
                client_for(stub, max_retries=2, backoff_s=0.01).complete(
                    prompt_id="p", prompt="x", tier="small"
                )
            assert exc.value.status == 422
            assert stub.request_count == 1

    def test_malformed_json_body(self):
        with StubProvider([Step(raw='{"choices": [')]) as stub:
            with pytest.raises(ProviderResponseError) as exc:
                client_for(stub).complete(prompt_id="p", prompt="x", tier="small")
            assert "not JSON" in str(exc.value)
            assert stub.request_count == 1  # a malformed answer is not retried

    def test_html_error_page(self):
        with StubProvider([Step(status=200, raw="<html>gateway</html>")]) as stub:
            with pytest.raises(ProviderResponseError):
                client_for(stub).complete(prompt_id="p", prompt="x", tier="small")

    def test_answer_without_choices(self):
        with StubProvider([Step(body={"id": "x", "object": "chat.completion"})]) as stub:
            with pytest.raises(ProviderResponseError) as exc:
                client_for(stub).complete(prompt_id="p", prompt="x", tier="small")
            assert "choices" in str(exc.value)

    def test_empty_completion(self):
        with StubProvider([ok("   ")]) as stub:
            with pytest.raises(ProviderResponseError) as exc:
                client_for(stub).complete(prompt_id="p", prompt="x", tier="small")
            assert "empty" in str(exc.value)

    def test_malformed_stream_chunk(self):
        with StubProvider([Step(sse=["data: {oops\n\n", "data: [DONE]\n\n"])]) as stub:
            with pytest.raises(ProviderResponseError) as exc:
                client_for(stub, stream=True).complete(prompt_id="p", prompt="x", tier="small")
            assert "chunk" in str(exc.value)

    def test_stream_without_content(self):
        with StubProvider([Step(sse=["data: [DONE]\n\n"])]) as stub:
            with pytest.raises(ProviderResponseError) as exc:
                client_for(stub, stream=True).complete(prompt_id="p", prompt="x", tier="small")
            assert "empty" in str(exc.value)

    def test_a_provider_that_ignores_stream_is_read_not_reported_as_empty(self):
        """A gateway that answers a ``stream`` request with one whole JSON body still answered.

        Measured: the perceived-speed audit's own stub did exactly this, and the run reported
        ``incomplete/empty_answer`` — a lie about an answer that was sitting in the body.
        """
        with StubProvider([ok("hello world")]) as stub:
            client = client_for(stub, stream=True)
            result = client.chat([{"role": "user", "content": "hi"}], tier="main")
            assert result.text == "hello world"
            assert client.stream_stats["stream_ignored"] is True

    def test_a_body_that_is_not_an_answer_is_still_not_an_answer(self):
        """The fallback is narrow on purpose: only a ``choices`` object counts as an answer."""
        with StubProvider([Step(raw='{"detail": "gateway says no"}')]) as stub:
            client = client_for(stub, stream=True)
            result = client.chat([{"role": "user", "content": "hi"}], tier="main")
            assert result.text == "" and result.tool_calls == []
            assert "stream_ignored" not in client.stream_stats


class TestTimeouts:
    def test_read_timeout_is_typed_and_not_retried_when_retries_are_off(self):
        with StubProvider([Step(body={"choices": []}, delay=0.6)]) as stub:
            client = client_for(stub, timeout_s=0.15, max_retries=0)
            started = time.monotonic()
            with pytest.raises(ProviderTimeoutError) as exc:
                client.complete(prompt_id="p", prompt="x", tier="small")
            assert time.monotonic() - started < 0.6  # gave up before the answer arrived
            assert exc.value.status is None
            assert "timeout after" in str(exc.value)
            assert stub.request_count == 1

    def test_retry_never_runs_outside_the_total_deadline(self):
        with StubProvider([Step(body={"choices": []}, delay=0.6)]) as stub:
            # first attempt burns 0.2s of a 0.4s budget; the 0.25s backoff no longer fits
            client = client_for(stub, timeout_s=0.2, total_timeout_s=0.4, max_retries=2)
            with pytest.raises(ProviderTimeoutError):
                client.complete(prompt_id="p", prompt="x", tier="small")
            assert stub.request_count == 1

    def test_connection_refused_is_a_timeout_error(self):
        cfg = resolve_provider_config(
            {"base_url": "http://127.0.0.1:9/v1", "model": "m", "timeout_s": 0.3},
            env={},
            config_path=ABSENT_CONFIG,
        )
        with pytest.raises(ProviderTimeoutError) as exc:
            OpenAICompatibleLLM(cfg).complete(prompt_id="p", prompt="x", tier="small")
        assert "ConnectError" in str(exc.value)


class TestIntegration:
    def test_provider_for_reaches_the_stub_end_to_end(self):
        with StubProvider([ok('{"stuck": false}')]) as stub:
            client = provider_for(
                {
                    "base_url": stub.base_url,
                    "model": "stub-model",
                    "api_key": FAKE_KEY,
                    "max_retries": 0,
                },
                env={},
                config_path=ABSENT_CONFIG,
            )
            assert client.complete(prompt_id="voice_triage", prompt="x", tier="small") == (
                '{"stuck": false}'
            )
            assert stub.requests[0]["body"]["model"] == "stub-model"

    def test_env_only_configuration_reaches_the_stub(self):
        with StubProvider([ok("env configured")]) as stub:
            env = {
                ENV_BASE_URL: stub.base_url,
                ENV_MODEL: "env-model",
                ENV_API_KEY: FAKE_KEY,
                lp.ENV_MAX_RETRIES: "0",
            }
            client = provider_for(None, env=env, config_path=ABSENT_CONFIG)
            assert client.complete(prompt_id="p", prompt="x", tier="small") == "env configured"
            assert stub.requests[0]["headers"]["authorization"] == f"Bearer {FAKE_KEY}"

    def test_injected_httpx_client_is_reused_and_not_closed(self):
        import httpx

        with StubProvider([ok("reused")]) as stub:
            cfg = resolve_provider_config(
                {"base_url": stub.base_url, "model": "m"}, env={}, config_path=ABSENT_CONFIG
            )
            with httpx.Client() as shared:
                client = OpenAICompatibleLLM(cfg, client=shared)
                assert client.complete(prompt_id="p", prompt="x", tier="small") == "reused"
                assert shared.is_closed is False

    def test_scripted_and_callable_clients_still_work(self):
        scripted = ScriptedLLM(responses={"x": "y"}, default="d")
        assert scripted.complete(prompt_id="x", prompt="p", tier="small") == "y"
        assert scripted.complete(prompt_id="other", prompt="p", tier="small") == "d"
        assert CallableLLM(lambda *, prompt_id, prompt, tier: tier).complete(
            prompt_id="x", prompt="p", tier="main"
        ) == "main"
        with pytest.raises(LLMError):
            ScriptedLLM(responses={}).complete(prompt_id="none", prompt="p", tier="small")

    def test_chat_request_body_reasoning_effort_for_luna_with_tools(self):
        cfg = resolve_provider_config(
            {"base_url": "https://api.openai.com/v1", "model": "gpt-5.6-luna"},
            env={},
            config_path=ABSENT_CONFIG,
        )
        tools = [{"type": "function", "function": {"name": "test_tool"}}]
        body_with_tools = lp.chat_request_body(
            cfg, [{"role": "user", "content": "hi"}], tier="main", tools=tools
        )
        assert body_with_tools.get("reasoning_effort") == "none"

        body_without_tools = lp.chat_request_body(
            cfg, [{"role": "user", "content": "hi"}], tier="main", tools=None
        )
        assert "reasoning_effort" not in body_without_tools

    def test_chat_auto_retries_with_reasoning_effort_none_on_400(self):
        err_400 = http_error(
            400,
            "Function tools with reasoning_effort are not supported for gpt-5.6-luna in "
            "/v1/chat/completions. To use function tools, use /v1/responses or set "
            "reasoning_effort to 'none'.",
        )
        ok_tool = Step(
            body={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "f", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        )
        with StubProvider([err_400, ok_tool]) as stub:
            cfg = resolve_provider_config(
                {"base_url": stub.base_url, "model": "custom-reasoning-model"},
                env={},
                config_path=ABSENT_CONFIG,
            )
            client = OpenAICompatibleLLM(cfg)
            tools = [{"type": "function", "function": {"name": "f"}}]
            res = client.chat([{"role": "user", "content": "run tool"}], tools=tools)
            assert len(stub.requests) == 2
            assert stub.requests[1]["body"].get("reasoning_effort") == "none"
            assert len(res.tool_calls) == 1

    def test_tool_schema_strips_forbidden_top_level_keywords(self):
        cfg = resolve_provider_config(
            {"base_url": "https://api.openai.com/v1", "model": "gpt-5.6-luna"},
            env={},
            config_path=ABSENT_CONFIG,
        )
        tool_with_anyof = {
            "type": "function",
            "function": {
                "name": "example_tool",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "anyOf": [{"required": ["a"]}],
                    "allOf": [{"required": ["a"]}],
                    "not": {"type": "null"},
                },
            },
        }
        body = lp.chat_request_body(
            cfg, [{"role": "user", "content": "hi"}], tier="main", tools=[tool_with_anyof]
        )
        params = body["tools"][0]["function"]["parameters"]
        assert "anyOf" not in params
        assert "allOf" not in params
        assert "not" not in params
        assert params.get("type") == "object"
        assert "a" in params.get("properties", {})

    def test_skill_act_tool_schema_is_openai_compliant(self):
        from skaldir.agent_toolset import skill_act_tool

        tool = skill_act_tool()
        schema = tool.input_schema
        assert schema.get("type") == "object"
        for forbidden in ("anyOf", "oneOf", "allOf", "enum", "const", "not"):
            assert forbidden not in schema, f"forbidden top-level {forbidden} found in skill_act"

