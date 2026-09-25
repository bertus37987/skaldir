"""The ``skaldir provider …`` surface — including the two live proofs.

No external network and no credentials: the one endpoint used here is a real
socket server the test starts on ``127.0.0.1`` (a stub, not a transport mock, so
the real ``OpenAICompatibleLLM`` really speaks HTTP to it). The file also
proves the wiring contract *without* editing ``src/godsv/cli.py`` (another agent
owns that file): it takes the real product parser, retrieves its ``subparsers``
action and calls ``register_cli`` on it — the exact snippet the CLI owner adds.

Two proofs, named in the test names:

* ``test_proof1_…`` — ``provider test`` succeeds through the real client against
  the local stub: latency, model echo, answer and the request that left the
  machine (path, model, messages, Authorization);
* ``test_proof2_…`` — ``provider test`` on a remote provider whose
  ``api_key_env`` is unset exits **4** and attempts **no** request.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from skaldir import llm_provider as lp
from skaldir import provider_cli as pcli
from skaldir import provider_config as pc

#: a fake that LOOKS like a key — must never reach a file, a message or stdout
FAKE_KEY = "sk-" + "test" * 8
REMOTE_URL = "https://api.example.com/v1"

ENV_NAMES = (
    "GODSV_LLM_CONFIG",
    "GODSV_LLM_PROVIDER",
    "GODSV_LLM_BASE_URL",
    "GODSV_LLM_API_BASE",
    "GODSV_LLM_API_KEY",
    "GODSV_LLM_KEY",
    "GODSV_LLM_MODEL",
    "GODSV_LLM_TIER_MODELS",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Neither the developer's shell exports nor the real ~/.hermes may leak in."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GODSV_HOME", "/nonexistent/godsv-provider-cli-tests")
    monkeypatch.delenv("HERMES_HOME", raising=False)


def _forbid_http(monkeypatch) -> None:
    """Any real request becomes a test failure (proves a path attempts none)."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("no HTTP request may be attempted on this path")

    monkeypatch.setattr("httpx.Client.post", _boom)
    monkeypatch.setattr("httpx.Client.stream", _boom)


# --------------------------------------------------------------------------- #
# the stub endpoint (a real socket server on 127.0.0.1)                        #
# --------------------------------------------------------------------------- #


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: ARG002
        return


class StubEndpoint:
    """A minimal OpenAI-compatible endpoint with a scripted answer."""

    def __init__(
        self, *, status: int = 200, content: str = "pong", model: str = "stub-model",
        delay: float = 0.0,
    ) -> None:
        self.status = status
        self.content = content
        self.model = model
        self.delay = delay
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {"_unparsed": raw.decode("utf-8", "replace")}
                with stub._lock:
                    stub.requests.append(
                        {
                            "path": self.path,
                            "body": body,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                        }
                    )
                if stub.delay:
                    time.sleep(stub.delay)
                if stub.status >= 400:
                    payload: dict = {"error": {"message": "stub refusal", "type": "stub"}}
                else:
                    payload = {
                        "model": stub.model,
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": stub.content},
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                    }
                data = json.dumps(payload).encode()
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), daemon=True
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def __enter__(self) -> StubEndpoint:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


def _json_out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# the wiring contract                                                          #
# --------------------------------------------------------------------------- #


def _subparsers(parser: argparse.ArgumentParser):
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]


def _wired_product_parser() -> argparse.ArgumentParser:
    """The real ``skaldir`` parser with the provider group added, as the patch does.

    ``register_cli`` may not run twice (argparse: "conflicting subparser"), so this
    wires only when the group is missing — the same helper ``test_jev_facade_cli.py``
    uses, and the reason the group is safe to add to a build that already has it.
    """
    from skaldir.cli import build_parser

    parser = build_parser()
    sub = _subparsers(parser)
    if "provider" not in sub.choices:
        pcli.register_cli(sub)
    return parser


def test_register_cli_adds_the_group_to_a_subparsers_handle():
    parser = argparse.ArgumentParser(prog="x")
    sub = parser.add_subparsers(dest="command")
    returned = pcli.register_cli(sub)
    assert returned is not None
    argv = {
        "list": ["list"],
        "add": ["add", "local", "--base-url", "http://127.0.0.1:1/v1", "--model", "m"],
        "test": ["test"],
        "remove": ["remove", "local"],
        "default": ["default", "local"],
        "tiers": ["tiers"],
        "doctor": ["doctor"],
    }
    for verb, args in argv.items():
        assert parser.parse_args(["provider", *args]).func is not None, verb
    assert parser.parse_args(["provider", "list"]).func is pcli.cmd_list
    assert parser.parse_args(["provider", "test"]).func is pcli.cmd_test
    assert parser.parse_args(["provider", "doctor"]).func is pcli.cmd_doctor


def test_the_snippet_works_against_the_real_product_parser():
    parser = _wired_product_parser()
    args = parser.parse_args(["provider", "list", "--json"])
    assert args.func is pcli.cmd_list
    assert args.json is True
    args = parser.parse_args(
        ["provider", "add", "local", "--base-url", "http://127.0.0.1:1/v1", "--model", "m"]
    )
    assert args.func is pcli.cmd_add
    assert args.base_url == "http://127.0.0.1:1/v1"
    assert args.kind == pc.DEFAULT_KIND


def test_the_group_is_additive_and_the_config_flag_survives_the_verb():
    parser = _wired_product_parser()
    assert parser.parse_args(["metrics"]).func is not None
    before = parser.parse_args(["provider", "--config", "/tmp/x.yaml", "list"])
    after = parser.parse_args(["provider", "list", "--config", "/tmp/x.yaml"])
    assert before.config == after.config == "/tmp/x.yaml"


def test_every_verb_runs_through_argparse_without_a_verb_or_with_help(capsys):
    assert pcli.main([]) == pcli.EXIT_USAGE
    assert "provider" in capsys.readouterr().out
    assert pcli.main(["--help"]) == pcli.EXIT_OK


def test_argparse_usage_error_returns_the_usage_code(capsys):
    assert pcli.main(["add", "local", "--model", "m"]) == pcli.EXIT_USAGE
    assert "base-url" in capsys.readouterr().err


def test_the_group_declares_eight_contract_paths():
    assert len(pcli.CONTRACT_PATHS) == 8
    assert set(pcli.CONTRACT_PATHS) == {
        "provider.add",
        "provider.catalog",
        "provider.default",
        "provider.doctor",
        "provider.list",
        "provider.remove",
        "provider.test",
        "provider.tiers",
    }


def test_the_contract_entries_are_declared_or_say_what_is_missing():
    """The CLI's own invariant: every parser leaf needs a ``cli_contract.COMMANDS`` entry.

    ``tests/test_cli_contract_dump.py`` fails when a leaf is missing from the table.
    The table lives in the CLI owner's ``cli_contract.py``, so this reports the
    dependency instead of editing a file this package does not own — and passes as
    soon as the snippet from ``provider_cli.CONTRACTS_SNIPPET`` is pasted in.
    """
    if pcli._CONTRACT is None:
        pytest.skip("this build has no cli_contract module: the group stands alone")
    missing = [path for path in pc_paths() if path not in pcli._CONTRACT.COMMANDS]
    if missing:
        pytest.skip("awaiting the CLI owner's cli_contract.COMMANDS entries: " + ", ".join(missing))
    for path in pc_paths():
        spec = pcli._CONTRACT.COMMANDS[path]
        assert spec.evidence, f"{path} must declare default evidence"
        assert spec.data_schema, f"{path} must declare a data schema"


def pc_paths() -> tuple[str, ...]:
    return pcli.CONTRACT_PATHS


# --------------------------------------------------------------------------- #
# list / add / remove / default / tiers                                        #
# --------------------------------------------------------------------------- #


def test_add_then_list_reports_the_registry_redacted(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    assert pcli.main(["add", "deepseek", "--base-url", REMOTE_URL, "--model", "deepseek-chat",
                      "--api-key-env", "GODSV_TEST_PROVIDER_KEY", "--config", str(path)]) == 0
    human = capsys.readouterr().out
    assert "provider added: deepseek" in human  # the human view is one line set
    code = pcli.main(["list", "--json", "--config", str(path)])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == pcli.EXIT_OK
    assert payload["count"] == 1 and payload["default"] == "deepseek"
    row = payload["providers"][0]
    assert row["api_key_env"] == "GODSV_TEST_PROVIDER_KEY"
    assert row["credential"] == "unset"
    assert row["endpoint"] == REMOTE_URL + "/chat/completions"
    assert "api_key" not in row
    assert FAKE_KEY not in out


def test_add_projects_the_provider_into_the_block_the_runtime_reads(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    pcli.main(["add", "local", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m1",
               "--tier-small", "s1", "--config", str(path)])
    capsys.readouterr()
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["godsv"]["llm"]["model"] == "m1"
    assert document["godsv"]["llm"]["tiers"] == {"small": "s1"}
    cfg = lp.resolve_provider_config(env={}, config_path=path)
    assert cfg.ready() is True


def test_add_refuses_a_literal_key_with_the_usage_code_and_never_prints_it(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    code = pcli.main(["add", "x", "--base-url", REMOTE_URL, "--model", "m",
                      "--api-key-env", FAKE_KEY, "--config", str(path)])
    captured = capsys.readouterr()
    assert code == pcli.EXIT_USAGE
    assert FAKE_KEY not in captured.err and FAKE_KEY not in captured.out
    assert "NAME of an environment variable" in captured.err
    assert not path.exists()


def test_a_refused_write_exits_three_and_names_no_value(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    code = pcli.main(["add", "x", "--base-url", f"https://api.example.com/v1?api_key={FAKE_KEY}",
                      "--model", "m", "--config", str(path)])
    captured = capsys.readouterr()
    assert code == pcli.EXIT_BLOCKED
    assert FAKE_KEY not in captured.err
    assert not path.exists()


def test_a_literal_secret_in_the_file_blocks_every_verb(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "godsv": {
                    "llm": {
                        "provider": "openai-compatible",
                        "base_url": REMOTE_URL,
                        "model": "m",
                        "api_key": FAKE_KEY,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()
    for argv in (
        ["list"],
        ["doctor"],
        ["test"],
        ["add", "x", "--base-url", REMOTE_URL, "--model", "m"],
    ):
        code = pcli.main([*argv, "--config", str(path)])
        captured = capsys.readouterr()
        assert code == pcli.EXIT_BLOCKED, argv
        assert "api_key" in captured.err
        assert FAKE_KEY not in captured.err
    assert path.read_bytes() == before


def test_duplicate_add_is_a_conflict_and_force_replaces_it(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    pcli.main(["add", "a", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m1",
               "--config", str(path)])
    capsys.readouterr()
    assert pcli.main(["add", "a", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m2",
                      "--config", str(path)]) == pcli.EXIT_CONFLICT
    capsys.readouterr()
    assert pcli.main(["add", "a", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m2",
                      "--force", "--config", str(path)]) == pcli.EXIT_OK
    capsys.readouterr()
    payload = pcli.main(["list", "--json", "--config", str(path)])
    assert payload == pcli.EXIT_OK


def test_remove_and_default_report_their_codes(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    pcli.main(["add", "a", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m1",
               "--config", str(path)])
    pcli.main(["add", "b", "--base-url", "http://127.0.0.1:8081/v1", "--model", "m2",
               "--config", str(path)])
    capsys.readouterr()

    assert pcli.main(["default", "b", "--config", str(path)]) == pcli.EXIT_OK
    payload = _json_out_from(pcli, capsys, ["list", "--json", "--config", str(path)])
    assert payload["default"] == "b"

    assert pcli.main(["remove", "nope", "--config", str(path)]) == pcli.EXIT_NOT_FOUND
    capsys.readouterr()
    assert pcli.main(["remove", "b", "--config", str(path)]) == pcli.EXIT_OK
    payload = _json_out_from(pcli, capsys, ["list", "--json", "--config", str(path)])
    assert payload["count"] == 1 and payload["default"] == ""


def _json_out_from(module, capsys, argv) -> dict:
    capsys.readouterr()  # drop whatever the previous verb printed
    code = module.main(argv)
    out = capsys.readouterr().out
    assert code == pcli.EXIT_OK
    return json.loads(out)


def test_tiers_shows_and_sets_the_mapping(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    pcli.main(["add", "a", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m1",
               "--config", str(path)])
    capsys.readouterr()
    assert pcli.main(["tiers", "a", "--config", str(path)]) == pcli.EXIT_OK
    shown = capsys.readouterr().out
    assert "small:" in shown and "main:" in shown

    payload = _json_out_from(
        pcli,
        capsys,
        ["tiers", "a", "--small", "s1", "--main", "m9", "--json", "--config", str(path)],
    )
    assert payload["tiers"] == {"small": "s1", "main": "m9"}
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["godsv"]["llm"]["tiers"] == {"small": "s1", "main": "m9"}

    assert pcli.main(["tiers", "nope", "--config", str(path)]) == pcli.EXIT_NOT_FOUND
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# PROOF 1 — a real call through the real client against the local stub          #
# --------------------------------------------------------------------------- #


def test_proof1_provider_test_succeeds_through_the_real_client(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    with StubEndpoint() as stub:
        monkeypatch.setenv("GODSV_TEST_STUB_KEY", "stub-only-credential")
        assert pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                          "--api-key-env", "GODSV_TEST_STUB_KEY", "--config", str(config)]) == 0
        capsys.readouterr()
        assert stub.request_count == 0  # adding a provider is offline

        code = pcli.main(["test", "local", "--json", "--config", str(config)])
        out = capsys.readouterr().out
        payload = json.loads(out)

    assert code == pcli.EXIT_OK
    assert payload["ok"] is True
    assert payload["name"] == "local"
    assert payload["endpoint"] == stub.base_url + "/chat/completions"
    assert payload["model"] == "stub-model"
    assert payload["model_echo"] == "stub-model"  # what the endpoint said, not what we asked
    assert payload["finish_reason"] == "stop"
    assert payload["answer_preview"] == "pong"
    assert payload["answer_chars"] == 4
    assert payload["latency_ms"] >= 0
    assert payload["tier"] == "small"
    assert payload["client"] == "OpenAICompatibleLLM"
    # The rename wave is the whole point of this assertion: `resolved_by` names the real
    # module (the import package is `skaldir` now; `godsv` is only an alias finder).
    assert payload["resolved_by"] == "skaldir.llm_provider.provider_for"
    assert payload["credential"] == "set"
    assert payload["usage"] == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    assert payload["redacted"] is False

    # …and what actually left the machine
    assert stub.request_count == 1
    request = stub.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "stub-model"
    assert request["body"]["messages"] == [{"role": "user", "content": pc.PROBE_PROMPT}]
    assert request["headers"]["authorization"].startswith("Bearer ")


def test_proof1_the_probe_runs_the_class_the_factory_resolves(tmp_path, capsys, monkeypatch):
    """The reported ``client`` is the one ``provider_for`` builds for this config."""
    config = tmp_path / "config.yaml"
    with StubEndpoint() as stub:
        pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                   "--config", str(config)])
        capsys.readouterr()
        cfg = lp.resolve_provider_config(
            {"provider": "openai-compatible", "base_url": stub.base_url, "model": "stub-model"},
            env={}, config_path=config,
        )
        assert type(lp.provider_for(cfg)).__name__ == "OpenAICompatibleLLM"
        assert pcli.main(["test", "local", "--json", "--config", str(config)]) == pcli.EXIT_OK
        payload = _json_out(capsys)
    assert payload["client"] == type(lp.provider_for(cfg)).__name__


def test_proof1_a_local_stub_needs_no_credential(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    with StubEndpoint(content="all good") as stub:
        pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                   "--config", str(config)])
        capsys.readouterr()
        code = pcli.main(["test", "--json", "--config", str(config)])
        payload = _json_out(capsys)
    assert code == pcli.EXIT_OK  # no name given: the default provider is probed
    assert payload["credential"] == "not-required"
    assert payload["answer_preview"] == "all good"


def test_probe_preview_redacts_what_an_endpoint_echoes_back(tmp_path, capsys):
    """An answer that contains something key-shaped is redacted before reporting."""
    config = tmp_path / "config.yaml"
    with StubEndpoint(content=f"here is your key {FAKE_KEY}") as stub:
        pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                   "--config", str(config)])
        capsys.readouterr()
        code = pcli.main(["test", "--json", "--config", str(config)])
        out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == pcli.EXIT_OK
    assert payload["redacted"] is True
    assert FAKE_KEY not in out
    assert "REDACTED" in payload["answer_preview"]


def test_proof1_a_hand_written_block_is_still_probeable(tmp_path, capsys):
    """A config the owner wrote by hand (no registry) is testable too."""
    config = tmp_path / "config.yaml"
    with StubEndpoint(content="hand written") as stub:
        config.write_text(
            yaml.safe_dump(
                {
                    "godsv": {
                        "llm": {
                            "provider": "openai-compatible",
                            "base_url": stub.base_url,
                            "model": "stub-model",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        code = pcli.main(["test", "--json", "--config", str(config)])
        payload = _json_out(capsys)
    assert code == pcli.EXIT_OK
    assert payload["name"] == "<active>"
    assert payload["model_echo"] == "stub-model"
    assert payload["answer_preview"] == "hand written"


# --------------------------------------------------------------------------- #
# PROOF 2 — no credential means exit 4 and no request                          #
# --------------------------------------------------------------------------- #


def test_proof2_provider_test_without_a_credential_exits_four(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    monkeypatch.delenv("GODSV_TEST_UNSET_KEY", raising=False)
    assert pcli.main(["add", "remote", "--base-url", REMOTE_URL, "--model", "m",
                      "--api-key-env", "GODSV_TEST_UNSET_KEY", "--config", str(config)]) == 0
    capsys.readouterr()
    _forbid_http(monkeypatch)  # a request here would fail the test, not the provider

    code = pcli.main(["test", "remote", "--json", "--config", str(config)])
    captured = capsys.readouterr()
    assert code == pcli.EXIT_NEEDS_CREDENTIALS
    assert code == 4
    assert captured.out == ""  # no payload pretending to be a result
    assert "export GODSV_TEST_UNSET_KEY" in captured.err
    assert "no credential" in captured.err


def test_proof2_the_default_provider_without_a_credential_exits_four(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    monkeypatch.delenv("GODSV_TEST_UNSET_KEY", raising=False)
    pcli.main(["add", "remote", "--base-url", REMOTE_URL, "--model", "m",
               "--api-key-env", "GODSV_TEST_UNSET_KEY", "--config", str(config)])
    capsys.readouterr()
    _forbid_http(monkeypatch)
    assert pcli.main(["test", "--config", str(config)]) == 4
    assert "export GODSV_TEST_UNSET_KEY" in capsys.readouterr().err


def test_a_real_401_from_the_endpoint_also_exits_four(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    with StubEndpoint(status=401) as stub:
        pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                   "--config", str(config)])
        capsys.readouterr()
        code = pcli.main(["test", "local", "--config", str(config)])
        err = capsys.readouterr().err
    assert code == pcli.EXIT_NEEDS_CREDENTIALS
    assert "401" in err


def test_a_provider_error_exits_blocked(tmp_path, capsys):
    """A 500 is not in the retry policy (§43: only 429/529/timeouts) — one attempt."""
    config = tmp_path / "config.yaml"
    with StubEndpoint(status=500) as stub:
        pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                   "--config", str(config)])
        capsys.readouterr()
        code = pcli.main(["test", "local", "--config", str(config)])
        capsys.readouterr()
        assert stub.request_count == 1
    assert code == pcli.EXIT_BLOCKED


def test_a_rate_limited_endpoint_is_retried_per_the_documented_policy(tmp_path, capsys):
    """429 ⇒ the shared policy (max_retries=2) really retries: 1 + 2 attempts."""
    config = tmp_path / "config.yaml"
    with StubEndpoint(status=429) as stub:
        pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                   "--config", str(config)])
        capsys.readouterr()
        code = pcli.main(["test", "local", "--config", str(config)])
        err = capsys.readouterr().err
        assert stub.request_count == 3
    assert code == pcli.EXIT_BLOCKED  # exhausted, and honest about it
    assert "429" in err


def test_provider_test_refuses_an_unknown_name(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    pcli.main(["add", "a", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m",
               "--config", str(config)])
    capsys.readouterr()
    assert pcli.main(["test", "nope", "--config", str(config)]) == pcli.EXIT_NOT_FOUND
    assert "no provider named 'nope'" in capsys.readouterr().err


def test_provider_test_on_an_empty_config_names_the_command(tmp_path, capsys):
    code = pcli.main(["test", "--config", str(tmp_path / "config.yaml")])
    captured = capsys.readouterr()
    assert code == pcli.EXIT_BLOCKED
    assert "skaldir provider add" in captured.err


# --------------------------------------------------------------------------- #
# the product envelope (what the CLI owner's wiring produces)                   #
# --------------------------------------------------------------------------- #


def _driven(args: argparse.Namespace) -> int:
    from skaldir import cli_contract

    return int(cli_contract.run_command(args, args.func))


def _wired_args(argv: list[str]) -> argparse.Namespace:
    """Args as the product CLI produces them (``annotate()`` sets ``command_path``)."""
    from skaldir import cli_contract

    parser = _wired_product_parser()
    cli_contract.annotate(parser)  # what cli.py's build_parser() does at the end
    return parser.parse_args(["provider", *argv])


def test_the_group_produces_one_valid_envelope_through_the_product_driver(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    pcli.main(["add", "local", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m",
               "--config", str(config)])
    capsys.readouterr()
    args = _wired_args(["list", "--json", "--config", str(config)])
    code = _driven(args)
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert code == 0 == envelope["exit_code"]
    assert envelope["ok"] is True
    assert envelope["command"] == "provider.list"
    assert envelope["data"]["count"] == 1
    assert envelope["evidence"]  # "no success without evidence" — the group's own default
    assert "provider list:" in captured.err  # the human view is diagnostics


def test_a_needs_credentials_verdict_becomes_an_exit_four_envelope(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    monkeypatch.delenv("GODSV_TEST_UNSET_KEY", raising=False)
    pcli.main(["add", "remote", "--base-url", REMOTE_URL, "--model", "m",
               "--api-key-env", "GODSV_TEST_UNSET_KEY", "--config", str(config)])
    capsys.readouterr()
    _forbid_http(monkeypatch)
    args = _wired_args(["test", "remote", "--json", "--config", str(config)])
    code = _driven(args)
    envelope = json.loads(capsys.readouterr().out)
    assert code == 4 == envelope["exit_code"]
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "needs_credentials"
    assert "export GODSV_TEST_UNSET_KEY" in envelope["error"]["message"]


def test_a_broken_provider_becomes_a_blocked_envelope_with_next_actions(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    pcli.main(["add", "remote", "--base-url", REMOTE_URL, "--model", "m", "--config", str(config)])
    capsys.readouterr()
    args = _wired_args(["doctor", "--json", "--config", str(config)])
    code = _driven(args)
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3 == envelope["exit_code"]
    assert envelope["error"]["code"] == "blocked"
    assert envelope["data"]["ok"] is False
    assert envelope["next_actions"]


# --------------------------------------------------------------------------- #
# doctor                                                                      #
# --------------------------------------------------------------------------- #


def test_doctor_exits_zero_when_every_provider_is_usable(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    pcli.main(["add", "local", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m",
               "--config", str(config)])
    capsys.readouterr()
    code = pcli.main(["doctor", "--json", "--config", str(config)])
    payload = _json_out(capsys)
    assert code == pcli.EXIT_OK
    assert payload["ok"] is True and payload["broken"] == []


def test_doctor_exits_three_with_what_is_missing(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    monkeypatch.delenv("GODSV_TEST_MISSING", raising=False)
    pcli.main(["add", "local", "--base-url", "http://127.0.0.1:8080/v1", "--model", "m",
               "--config", str(config)])
    pcli.main(["add", "remote", "--base-url", REMOTE_URL, "--model", "m",
               "--api-key-env", "GODSV_TEST_MISSING", "--config", str(config)])
    capsys.readouterr()
    code = pcli.main(["doctor", "--json", "--config", str(config)])
    payload = _json_out(capsys)
    assert code == pcli.EXIT_BLOCKED
    assert payload["ok"] is False
    assert payload["broken"] == ["remote"]
    assert payload["usable"] == ["local"]
    assert any("GODSV_TEST_MISSING" in action["reason"] for action in payload["next_actions"])


def test_doctor_makes_no_request(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    pcli.main(["add", "remote", "--base-url", REMOTE_URL, "--model", "m", "--config", str(config)])
    capsys.readouterr()
    _forbid_http(monkeypatch)
    assert pcli.main(["doctor", "--config", str(config)]) == pcli.EXIT_BLOCKED


def test_doctor_on_an_empty_config_is_zero_with_the_next_command(tmp_path, capsys):
    code = pcli.main(["doctor", "--json", "--config", str(tmp_path / "config.yaml")])
    payload = _json_out(capsys)
    assert code == pcli.EXIT_OK
    assert payload["ok"] is False and payload["count"] == 0
    assert payload["next_actions"][0]["command"].startswith("skaldir provider add")


# --------------------------------------------------------------------------- #
# the config flag placement and the standalone/ wired duality                  #
# --------------------------------------------------------------------------- #


def test_the_config_flag_works_before_or_after_the_verb(tmp_path, capsys):
    for argv in (["--config", str(tmp_path / "a.yaml"), "list"],
                 ["list", "--config", str(tmp_path / "a.yaml")]):
        capsys.readouterr()
        code = pcli.main([*argv, "--json"])
        payload = _json_out(capsys)
        assert code == pcli.EXIT_OK
        assert Path(payload["config_file"]).name == "a.yaml"


def test_secrets_never_reach_stdout_in_json_mode(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    monkeypatch.setenv("GODSV_TEST_STUB_KEY", "stub-only-credential")
    with StubEndpoint() as stub:
        pcli.main(["add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                   "--api-key-env", "GODSV_TEST_STUB_KEY", "--config", str(config)])
        capsys.readouterr()  # the add verb's human view
        pcli.main(["test", "--json", "--config", str(config)])
        test_out = capsys.readouterr().out
        pcli.main(["list", "--json", "--config", str(config)])
        list_out = capsys.readouterr().out
    for out in (test_out, list_out):
        payload = json.loads(out)  # exactly one JSON document on stdout
        assert "stub-only-credential" not in out
        assert payload
    assert "stub-only-credential" not in config.read_text(encoding="utf-8")
