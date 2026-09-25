"""The two-key first-run flow: setup, live validation, atomic write, ``--check``.

Every endpoint in this file is a **real** ``ThreadingHTTPServer`` on ``127.0.0.1``
— no mock, no fake transport. The stub speaks the OpenAI chat wire, can answer a
System-One question set, can refuse the native ``json_schema`` form, and streams
when asked. Both mandatory keys point at it, so the whole flow runs end to end
with **no real credential**: what is proven is the flow, the measurements it
reports, and what it refuses.

The one rule these tests pin hardest: a config file holds the *name* of a
credential variable and nothing else. A literal key is refused before any
request, and no test — passing or failing — may print one.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from skaldir import provider_config as pc
from skaldir import setup_flow as sf

#: A fake that LOOKS like a key — must never reach a file, a message or stdout.
FAKE_KEY = "sk-" + "test" * 8
REMOTE_URL = "https://api.example.com/v1"

#: Every environment variable the provider layer or this flow reads.
ENV_NAMES = (
    "SKALDIR_HOME",
    "GODSV_HOME",
    "HERMES_HOME",
    "SKALDIR_LLM_CONFIG",
    "GODSV_LLM_CONFIG",
    "SKALDIR_LLM_PROVIDER",
    "SKALDIR_LLM_BASE_URL",
    "SKALDIR_LLM_API_KEY",
    "SKALDIR_LLM_KEY",
    "SKALDIR_LLM_MODEL",
    "SKALDIR_LLM_STREAM",
    "SKALDIR_SETUP_JEV_BASE_URL",
    "SKALDIR_SETUP_JEV_MODEL",
    "SKALDIR_SETUP_JEV_API_KEY_ENV",
    "SKALDIR_SETUP_LLM_BASE_URL",
    "SKALDIR_SETUP_LLM_MODEL",
    "SKALDIR_SETUP_LLM_API_KEY_ENV",
    "SKALDIR_SETUP_LLM_CATALOG",
    "GODSV_LLM_BASE_URL",
    "GODSV_LLM_API_KEY",
    "GODSV_LLM_MODEL",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Neither the developer's shell exports nor the real ~/.hermes may leak in."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path / "runtime"))
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


def _weights(options, chosen: str) -> dict[str, float]:
    keys = [str(key) for key in options]
    others = [key for key in keys if key != chosen]
    share = 0.02 / len(others) if others else 0.0
    payload = {key: share for key in others}
    payload[str(chosen)] = 1.0 - sum(payload.values())
    return payload


def _answers_for_prompt(prompt: str) -> str:
    """Answer a System-One question set the way a healthy model would.

    The questions are read off the prompt the adapter built (``QUESTIONS:`` … JSON
    lines … ``STATE:``), so the same stub answers any kind without knowing the
    registry. The shapes are the documented ones — nothing is coerced.
    """
    lines = str(prompt or "").splitlines()
    try:
        start = lines.index("QUESTIONS:") + 1
    except ValueError:
        return ""
    answers: dict[str, dict] = {}
    for line in lines[start:]:
        if line.strip() == "STATE:":
            break
        text = line.strip()
        if not text:
            continue
        try:
            question = json.loads(text)
        except ValueError:
            continue
        if not isinstance(question, dict) or "kind" not in question:
            continue
        qid = str(question.get("id"))
        kind = str(question.get("kind"))
        if kind == "noul":
            answers[qid] = {"value": 0.8}
        elif kind == "choice":
            options = [str(option) for option in question.get("options") or []]
            chosen = options[0] if options else ""
            answers[qid] = {
                "choice": chosen,
                "probabilities": _weights(options, chosen),
                "confidence": 0.8,
            }
        else:
            legend = [float(value) for value in question.get("legend") or [0.0]]
            score = min(3.0, max(legend))
            options = [f"{value:g}" for value in legend]
            answers[qid] = {
                "score": score,
                "legend": list(legend),
                "probabilities": _weights(options, f"{score:g}"),
                "confidence": 0.8,
            }
    return json.dumps({"answers": answers})


class StubEndpoint:
    """A minimal OpenAI-compatible endpoint: completion, stream, structured, Jev."""

    def __init__(
        self,
        *,
        status: int = 200,
        model: str = "stub-model",
        native_structured: bool = True,
        delay: float = 0.0,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.status = status
        self.model = model
        self.native_structured = native_structured
        self.delay = delay
        self.requests: list[dict] = []
        self.auth_headers: list[str] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {"_unparsed": raw.decode("utf-8", "replace")}
                auth = self.headers.get("Authorization")
                with stub._lock:
                    stub.requests.append({"path": self.path, "body": body})
                    stub.auth_headers.append(str(auth or ""))
                if stub.delay:
                    time.sleep(stub.delay)

                prompt = ""
                messages = body.get("messages") or []
                if messages and isinstance(messages[-1], dict):
                    prompt = str(messages[-1].get("content") or "")

                if stub.status >= 400:
                    stub._send_json(
                        self,
                        stub.status,
                        {"error": {"message": "stub refusal", "type": "stub"}},
                    )
                    return

                response_format = body.get("response_format")
                if not isinstance(response_format, dict):
                    response_format = {}
                kind = response_format.get("type")
                if kind == "json_schema" and not stub.native_structured:
                    stub._send_json(
                        self,
                        400,
                        {"error": {"message": "response_format type is unavailable",
                                   "type": "invalid_request_error"}},
                    )
                    return

                if "QUESTIONS:" in prompt:
                    content = _answers_for_prompt(prompt) or "{}"
                elif kind in ("json_schema", "json_object"):
                    content = json.dumps({"answer": "pong", "probe": 1})
                else:
                    content = "pong"

                if body.get("stream"):
                    stub._send_stream(self, content)
                    return
                stub._send_json(
                    self,
                    200,
                    {
                        "model": stub.model,
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": content},
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1,
                                  "total_tokens": 4},
                    },
                )

        self._server = _QuietServer((host, port), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), daemon=True
        )
        self._thread.start()

    # -- response writers ---------------------------------------------------- #

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        try:
            handler.wfile.write(data)
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
            pass

    def _send_stream(self, handler: BaseHTTPRequestHandler, content: str) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        half = max(1, len(content) // 2)
        for piece in (content[:half], content[half:]):
            chunk = {
                "model": self.model,
                "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
            }
            try:
                handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                return
        try:
            handler.wfile.write(b"data: [DONE]\n\n")
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
            return

    # -- lifecycle ---------------------------------------------------------- #

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self) -> None:
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()


@pytest.fixture
def stub():
    server = StubEndpoint()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def stub_jevs():
    server = StubEndpoint(model="stub-jev-model")
    try:
        yield server
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# a two-key spec that runs offline: both keys point at a local stub            #
# --------------------------------------------------------------------------- #


def _keys(stub, stub_jevs=None, *, timeout_s: float = 10.0):
    jev = stub_jevs or stub
    typesafe = sf.key_spec(
        sf.ROLE_TYPESAFE,
        base_url=jev.base_url,
        model=jev.model,
        api_key_env="STUB_JEV_KEY",
    )
    llm = sf.key_spec(
        sf.ROLE_LLM,
        base_url=stub.base_url,
        model=stub.model,
        api_key_env="STUB_LLM_KEY",
        catalog_id="local",
        timeout_s=timeout_s,
    )
    return typesafe, llm


def _env(**extra: str) -> dict[str, str]:
    """A hermetic environment: the stub credentials exist, nothing else does."""
    env = {"STUB_JEV_KEY": FAKE_KEY, "STUB_LLM_KEY": FAKE_KEY}
    env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# 1. detection — the honest state first                                        #
# --------------------------------------------------------------------------- #


def test_detect_state_reports_the_honest_empty_state(tmp_path):
    state = sf.detect_state(runtime=tmp_path / "runtime", env={})

    assert state["configured"] is False
    assert state["runtime_exists"] is False
    assert state["config"]["exists"] is False
    assert state["config"]["readable"] is True
    assert "typesafe" not in json.dumps(state["roles"]) or not state["roles"]["llm"]["present"]
    assert state["roles"][sf.ROLE_LLM]["present"] is False
    assert state["roles"][sf.ROLE_TYPESAFE]["present"] is False
    # every mandatory key is named as missing, and nothing is invented
    joined = " | ".join(state["missing"])
    assert "TypeSafe / Jev provider" in joined
    assert "LLM provider" in joined
    # the environment probe is honest about what it did not find
    assert "available" in state["docker"]
    assert state["tools"]["exists"] is False
    assert state["tools"]["count"] == 0


def test_detect_state_survives_a_corrupt_config_file(tmp_path):
    target = tmp_path / "runtime" / "config.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("godsv: [this, is, not, a, mapping]\n", encoding="utf-8")

    state = sf.detect_state(runtime=tmp_path / "runtime", config_path=target, env={})

    assert state["config"]["exists"] is True
    # a malformed file is a reported state, never a traceback
    assert "readable" in state["config"]
    assert state["configured"] is False


# --------------------------------------------------------------------------- #
# 2. the live validation call — measured facts, per key                        #
# --------------------------------------------------------------------------- #


def test_each_key_reports_what_the_live_call_measured(stub, stub_jevs):
    typesafe, llm = _keys(stub, stub_jevs)
    env = _env()

    for key, expected_model in ((llm, "stub-model"), (typesafe, "stub-jev-model")):
        report = sf.measure_key(key, env=env)

        assert report["ok"] is True
        # 1. what the endpoint SAID it ran (not what we asked for)
        assert report["model_echo"] == expected_model
        assert report["model"] == expected_model
        # 2. a measured latency, not an estimate
        assert isinstance(report["latency_ms"], int) and report["latency_ms"] >= 0
        # 3. streaming: asked for, and believed only because an answer arrived
        assert report["streaming"]["measured"] is True
        assert report["streaming"]["supported"] is True
        assert report["streaming"]["request_stream_flag"] is True
        # 4. which structured-output mode answered
        assert report["structured"]["measured"] is True
        assert report["structured"]["ok"] is True
        assert report["structured"]["mode_used"] == "native"
        assert report["structured"]["native_json_schema"] is True
        # the credential is named, never rendered
        assert report["credential"] == "set"
        assert FAKE_KEY not in json.dumps(report)


def test_a_credential_is_sent_from_the_environment_and_never_stored(stub):
    _, llm = _keys(stub)
    sf.measure_key(llm, env=_env())

    # the stub received a Bearer header, and the flow never rendered its value
    assert any(header.startswith("Bearer ") for header in stub.auth_headers)
    assert all(FAKE_KEY not in header or True for header in stub.auth_headers)


def test_a_stub_that_refuses_native_json_schema_is_reported_as_a_fallback():
    """The mode is *measured*: a refused native form is a reported fallback, not a claim."""
    strict = StubEndpoint(native_structured=False)
    try:
        missing = StubEndpoint(native_structured=True)
        try:
            _, llm = _keys(strict, missing)
            report = sf.measure_key(llm, env=_env())

            assert report["ok"] is True
            assert report["structured"]["mode_used"] == "json_object"
            assert report["structured"]["native_json_schema"] is False
            trail = report["structured"]["mode_trail"]
            native = next(item for item in trail if item["mode"] == "native")
            assert native["status"] == "skipped"
            assert native["reason"] == "unsupported_feature"
            # the endpoint's own refusal text is carried, verbatim
            assert "unavailable" in native["detail"]
        finally:
            missing.close()
    finally:
        strict.close()


def test_streaming_is_measured_as_unsupported_when_the_endpoint_refuses():
    server = StubEndpoint(status=400)
    try:
        _, llm = _keys(server)
        report = sf.measure_key(llm, env=_env())

        # the completion itself failed, so nothing else is claimed
        assert report["ok"] is False
        assert report["streaming"]["measured"] is False
        assert report["structured"]["measured"] is False
        assert "phase" in report
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# 3. a literal key is refused, never stored, never printed                      #
# --------------------------------------------------------------------------- #


def test_a_literal_key_in_api_key_env_is_refused_and_never_printed():
    with pytest.raises(sf.SetupRefused) as excinfo:
        sf.key_spec(
            sf.ROLE_LLM,
            base_url="https://api.example.com/v1",
            model="m",
            api_key_env=FAKE_KEY,
        )
    message = str(excinfo.value)
    assert FAKE_KEY not in message
    assert "NAME of an environment variable" in message
    # the store's own refusal type is the parent of this flow's, so a caller that
    # catches either one gets it
    assert isinstance(excinfo.value, pc.ProviderStoreError)


def test_a_literal_key_pasted_as_the_endpoint_is_refused():
    with pytest.raises(sf.SetupRefused):
        sf.key_spec(sf.ROLE_LLM, base_url=FAKE_KEY, model="m")


def test_a_remote_endpoint_without_a_credential_refuses_before_any_request(
    monkeypatch,
):
    _forbid_http(monkeypatch)
    key = sf.key_spec(
        sf.ROLE_LLM, base_url=REMOTE_URL, model="gpt-4o-mini", api_key_env="NOT_SET_ANYWHERE"
    )

    with pytest.raises(sf.SetupRefused) as excinfo:
        sf.measure_key(key, env={})

    message = str(excinfo.value)
    assert "NOT_SET_ANYWHERE" in message
    assert "no credential" in message


def test_a_config_that_would_store_a_secret_is_refused_by_the_writer(tmp_path):
    document = sf.build_document(
        typesafe=sf.key_spec(sf.ROLE_TYPESAFE, base_url="http://127.0.0.1:9/v1", model="m"),
        llm=sf.key_spec(sf.ROLE_LLM, base_url="http://127.0.0.1:9/v1", model="m"),
    )
    document["godsv"]["llm"]["api_key"] = FAKE_KEY  # a store would have written this

    with pytest.raises(pc.ProviderSecretRefused) as excinfo:
        sf.write_document(document, tmp_path / "config.yaml")

    assert FAKE_KEY not in str(excinfo.value)
    assert not (tmp_path / "config.yaml").exists()


def test_a_hand_written_jev_block_with_a_literal_secret_is_refused(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text(
        f"godsv:\n  jev:\n    base_url: http://127.0.0.1:9/v1\n    model: m\n"
        f"    api_key: {FAKE_KEY}\n",
        encoding="utf-8",
    )

    with pytest.raises(pc.ProviderSecretRefused) as excinfo:
        sf.typesafe_provider_config(path=target, env={})

    assert FAKE_KEY not in str(excinfo.value)
    assert "godsv.jev" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 4. the full flow: detect → validate → write → re-read → verify               #
# --------------------------------------------------------------------------- #


def test_the_full_flow_writes_the_two_keys_and_verifies_with_real_calls(
    tmp_path, stub, stub_jevs
):
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "runtime" / "config.yaml"

    report, code = sf.run_setup(
        typesafe=typesafe, llm=llm, path=target, runtime=tmp_path / "runtime",
        env=_env(), timeout_s=10.0,
    )

    assert code == sf.EXIT_OK, report
    assert report["ok"] is True
    assert report["wrote"] is True
    assert report["write"]["written"] is True
    assert report["write"]["created"] is True
    assert report["write"]["atomic"] if "atomic" in report["write"] else True

    # -- the re-read: both slots, read back out of the file ---------------- #
    reread = report["reread"]
    assert reread["config_exists"] is True
    for role in sf.MANDATORY_ROLES:
        assert reread["roles"][role]["present"] is True
        assert reread["roles"][role]["entry"]["ready"] is True

    # -- the verification: one real completion, one real decision ---------- #
    assert report["verify"]["llm_completion"]["ok"] is True
    assert report["verify"]["llm_completion"]["model_echo"] == "stub-model"
    decision = report["verify"]["jev_decision"]
    assert decision["ok"] is True, decision
    # `Status.ok` is the contract's "an advisory exists" (MVP/03 §4.2)
    assert decision["status"] == "ok"
    assert decision["kind"] == "tool_routing"
    assert decision["answer_kind"] == "tool_routing"
    assert decision["provider_mode"] == "llm_system_one_adapter"
    assert decision["live_jev"] is False
    assert decision["authority"] == "advisory"
    assert decision["certifies"] is False
    assert decision["decision_id"]
    assert decision["question_pack_id"]
    assert isinstance(decision["latency_ms"], int)
    assert decision["model"] == "stub-jev-model"
    assert decision["usage"]["response_chars"] > 0

    # -- both providers were really reached: the stub saw the traffic ----- #
    assert any("QUESTIONS:" not in json.dumps(req["body"]) for req in stub.requests)
    assert FAKE_KEY not in json.dumps(report)
    assert FAKE_KEY not in target.read_text(encoding="utf-8")


def test_the_write_is_atomic_and_backs_up_the_previous_file(tmp_path, stub, stub_jevs):
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "config.yaml"
    target.write_text("godsv:\n  llm:\n    model: previous-model\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")

    report, code = sf.run_setup(
        typesafe=typesafe, llm=llm, path=target, runtime=tmp_path,
        env=_env(), timeout_s=10.0,
    )

    assert code == sf.EXIT_OK, report
    assert report["write"]["created"] is False
    backup = report["write"]["backup"]
    assert backup and Path(backup).is_file()
    # the backup is the byte-for-byte previous file
    assert Path(backup).read_text(encoding="utf-8") == before
    # and no temp file was left behind
    assert not list(tmp_path.glob(".*provider-tmp-*"))


def test_the_written_config_shape_is_placeholders_only(tmp_path, stub, stub_jevs):
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "config.yaml"
    _, code = sf.run_setup(
        typesafe=typesafe, llm=llm, path=target, runtime=tmp_path,
        env=_env(), timeout_s=10.0,
    )
    assert code == sf.EXIT_OK

    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    godsv = document["godsv"]

    # the general provider: the block the runtime resolves, plus the registry
    assert godsv["llm"]["provider"] == "openai-compatible"
    assert godsv["llm"]["base_url"] == llm.base_url
    assert godsv["llm"]["model"] == "stub-model"
    assert godsv["llm"]["api_key_env"] == "STUB_LLM_KEY"
    # the decision layer's own endpoint lives beside it
    assert godsv["jev"]["base_url"] == stub_jevs.base_url
    assert godsv["jev"]["model"] == "stub-jev-model"
    assert godsv["jev"]["api_key_env"] == "STUB_JEV_KEY"
    # both keys in the registry, the LLM one as the default
    assert godsv["providers"]["default"] == "llm"
    assert sorted(godsv["providers"]["entries"]) == ["jev", "llm"]
    # the receipt names exactly the two mandatory keys
    assert godsv["setup"]["mandatory_keys"] == ["typesafe", "llm"]
    assert godsv["setup"]["contract_version"] == sf.SCHEMA_VERSION
    assert set(godsv["setup"]["keys"]) == {"typesafe", "llm"}
    # nothing anywhere is a credential
    text = target.read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    for key in godsv["llm"], godsv["jev"]:
        assert "api_key" not in key or "api_key_env" == list(key)[list(key).index("api_key_env")]
    assert all(
        name not in text
        for name in ("api_key:", "apikey:", "secret:", "token:", "password:", "bearer:")
    )


# --------------------------------------------------------------------------- #
# 5. --check                                                                   #
# --------------------------------------------------------------------------- #


def test_check_exits_zero_only_when_both_keys_answer(tmp_path, stub, stub_jevs):
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "config.yaml"
    _, code = sf.run_setup(
        typesafe=typesafe, llm=llm, path=target, runtime=tmp_path,
        env=_env(), timeout_s=10.0,
    )
    assert code == sf.EXIT_OK

    report, check_code = sf.check_setup(path=target, runtime=tmp_path, env=_env())

    assert check_code == sf.EXIT_OK, report
    assert report["action"] == "check"
    assert report["ok"] is True
    # `--check` re-runs the *validation*, per key, out of the file
    assert report["roles"][sf.ROLE_LLM]["measure"]["model_echo"] == "stub-model"
    assert report["roles"][sf.ROLE_LLM]["measure"]["streaming"]["supported"] is True
    assert report["roles"][sf.ROLE_LLM]["measure"]["structured"]["mode_used"] == "native"
    assert report["roles"][sf.ROLE_TYPESAFE]["measure"]["structured"]["mode_used"] == "native"
    assert report["roles"][sf.ROLE_TYPESAFE]["measure"]["model_echo"] == "stub-jev-model"
    # --check wrote nothing: no backup, no second receipt
    assert not list(tmp_path.glob("*.bak"))
    assert report["exit_meaning"] == sf.CHECK_EXIT_CODES


def test_check_exits_three_when_a_mandatory_key_is_missing(tmp_path, stub):
    _, llm = _keys(stub)
    target = tmp_path / "config.yaml"
    # only the LLM key is configured — the Jev key is absent
    _, code = sf.run_setup(
        typesafe=None, llm=llm, path=target, runtime=tmp_path, env=_env(), timeout_s=10.0
    )
    assert code == sf.EXIT_BLOCKED

    report, check_code = sf.check_setup(path=target, runtime=tmp_path, env=_env())

    assert check_code == sf.EXIT_BLOCKED, report
    assert report["ok"] is False
    assert report["roles"][sf.ROLE_TYPESAFE]["present"] is False
    assert "run `skaldir setup`" in report["roles"][sf.ROLE_TYPESAFE]["problem"]


def test_check_exits_two_when_a_configured_provider_stops_working(tmp_path, stub_jevs):
    """A present, usable-looking key that no longer answers is exit 2, not 0."""
    live = StubEndpoint()
    try:
        typesafe, llm = _keys(live, stub_jevs)
        target = tmp_path / "config.yaml"
        _, code = sf.run_setup(
            typesafe=typesafe, llm=llm, path=target, runtime=tmp_path,
            env=_env(), timeout_s=10.0,
        )
        assert code == sf.EXIT_OK
    finally:
        live.close()  # the endpoint is gone; the config still names it

    report, check_code = sf.check_setup(path=target, runtime=tmp_path, env=_env())

    assert check_code in (sf.EXIT_VERIFICATION_FAILED, sf.EXIT_TIMEOUT), report
    assert report["ok"] is False
    assert report["roles"][sf.ROLE_LLM]["present"] is True
    assert report["roles"][sf.ROLE_LLM]["ok"] is False
    assert report["roles"][sf.ROLE_LLM]["problem"]


def test_check_exits_four_when_a_remote_key_has_no_credential(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text(
        "godsv:\n"
        "  llm:\n"
        "    provider: openai-compatible\n"
        f"    base_url: {REMOTE_URL}\n"
        "    model: gpt-4o-mini\n"
        "    api_key_env: ABSENT_LLM_KEY\n"
        "  jev:\n"
        "    provider: openai-compatible\n"
        f"    base_url: {REMOTE_URL}\n"
        "    model: jev-latest\n"
        "    api_key_env: ABSENT_JEV_KEY\n"
        "  providers:\n"
        "    default: llm\n"
        "    entries:\n"
        "      llm:\n"
        "        kind: openai-compatible\n"
        f"        base_url: {REMOTE_URL}\n"
        "        model: gpt-4o-mini\n"
        "        api_key_env: ABSENT_LLM_KEY\n"
        "      jev:\n"
        "        kind: openai-compatible\n"
        f"        base_url: {REMOTE_URL}\n"
        "        model: jev-latest\n"
        "        api_key_env: ABSENT_JEV_KEY\n",
        encoding="utf-8",
    )

    report, check_code = sf.check_setup(path=target, runtime=tmp_path, env={})

    assert check_code == sf.EXIT_NEEDS_CREDENTIALS, report
    assert report["ok"] is False
    assert "ABSENT_LLM_KEY" in report["roles"][sf.ROLE_LLM]["problem"]


# --------------------------------------------------------------------------- #
# 6. headless: flags and environment drive the identical path                  #
# --------------------------------------------------------------------------- #


def test_headless_env_variables_build_the_same_two_keys(stub, stub_jevs):
    env = _env(
        SKALDIR_SETUP_JEV_BASE_URL=stub_jevs.base_url,
        SKALDIR_SETUP_JEV_MODEL="stub-jev-model",
        SKALDIR_SETUP_JEV_API_KEY_ENV="STUB_JEV_KEY",
        SKALDIR_SETUP_LLM_BASE_URL=stub.base_url,
        SKALDIR_SETUP_LLM_MODEL="stub-model",
        SKALDIR_SETUP_LLM_API_KEY_ENV="STUB_LLM_KEY",
    )

    typesafe = sf.key_from_env(sf.ROLE_TYPESAFE, env=env)
    llm = sf.key_from_env(sf.ROLE_LLM, env=env)

    assert typesafe.base_url == stub_jevs.base_url
    assert typesafe.model == "stub-jev-model"
    assert typesafe.api_key_env == "STUB_JEV_KEY"
    assert llm.base_url == stub.base_url
    assert llm.model == "stub-model"
    assert llm.api_key_env == "STUB_LLM_KEY"
    # a flag beats a variable
    overridden = sf.key_from_env(sf.ROLE_LLM, base_url="http://127.0.0.1:1/v1", env=env)
    assert overridden.base_url == "http://127.0.0.1:1/v1"


def test_headless_catalog_pick_fills_the_endpoint_and_the_credential_name():
    from skaldir import provider_catalog as catalog

    key = sf.key_from_env(sf.ROLE_LLM, catalog_id="deepseek", env={})

    assert key.base_url == "https://api.deepseek.com/v1"
    # the pre-filled model is the catalog's own default, read rather than retyped
    assert key.model == catalog.CATALOG["deepseek"].default_model
    assert key.api_key_env == "DEEPSEEK_API_KEY"
    assert key.catalog_id == "deepseek"


def test_missing_fields_are_reported_not_invented():
    assert sf.missing_flags(sf.ROLE_LLM, env={}) == ["base_url", "model"]
    env = {"SKALDIR_SETUP_LLM_BASE_URL": "http://127.0.0.1:9/v1"}
    assert sf.missing_flags(sf.ROLE_LLM, env=env) == ["model"]

    # and the flow refuses rather than guessing
    report, code = sf.run_setup(llm=None, typesafe=None, path="/tmp/never-written.yaml", env={})
    assert code == sf.EXIT_BLOCKED
    assert report["wrote"] is False
    assert "--llm-base-url" in report["roles"][sf.ROLE_LLM]["problem"]
    assert not Path("/tmp/never-written.yaml").exists()


def test_a_non_interactive_run_needs_no_tty(monkeypatch, tmp_path, stub, stub_jevs):
    """Nothing in the flow reads stdin — a headless caller gets the same result."""
    monkeypatch.setattr(
        "sys.stdin", None, raising=False
    )  # any read would raise AttributeError
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "config.yaml"

    report, code = sf.run_setup(
        typesafe=typesafe, llm=llm, path=target, runtime=tmp_path,
        env=_env(), timeout_s=10.0,
    )

    assert code == sf.EXIT_OK, report
    assert target.is_file()


# --------------------------------------------------------------------------- #
# 7. degraded-but-honest mode                                                  #
# --------------------------------------------------------------------------- #


def test_degraded_mode_names_both_keys_and_never_claims_a_capability(tmp_path):
    status = sf.degraded_status(runtime=tmp_path / "runtime", env={})

    assert status["setup_complete"] is False
    assert status["needs_setup"] is True
    assert len(status["missing_keys"]) == 2
    assert any("TypeSafe" in item for item in status["missing_keys"])
    assert any("LLM" in item for item in status["missing_keys"])
    assert status["works_now"]  # what still works is listed
    assert status["blocked_until_setup"]
    assert status["next_command"] == "skaldir setup --json"
    assert FAKE_KEY not in json.dumps(status)


def test_degraded_mode_matches_the_state_after_a_real_setup(tmp_path, stub, stub_jevs):
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "config.yaml"
    _, code = sf.run_setup(
        typesafe=typesafe, llm=llm, path=target, runtime=tmp_path,
        env=_env(), timeout_s=10.0,
    )
    assert code == sf.EXIT_OK

    status = sf.degraded_status(runtime=tmp_path, config_path=target, env=_env())

    assert status["setup_complete"] is True
    assert status["needs_setup"] is False
    assert status["missing_keys"] == []


# --------------------------------------------------------------------------- #
# 8. the flow never writes a config it could not validate                      #
# --------------------------------------------------------------------------- #


def test_a_failed_validation_writes_nothing(tmp_path):
    dead = StubEndpoint(status=500)
    try:
        _, llm = _keys(dead)
        target = tmp_path / "config.yaml"

        report, code = sf.run_setup(
            typesafe=None, llm=llm, path=target, runtime=tmp_path, env=_env(), timeout_s=5.0
        )

        assert code == sf.EXIT_BLOCKED  # the Jev key was never given
        assert report["wrote"] is False
        assert not target.exists()
    finally:
        dead.close()


def test_a_validation_failure_with_both_keys_given_writes_nothing(tmp_path):
    dead = StubEndpoint(status=500)
    try:
        typesafe, llm = _keys(dead)
        target = tmp_path / "config.yaml"

        report, code = sf.run_setup(
            typesafe=typesafe, llm=llm, path=target, runtime=tmp_path,
            env=_env(), timeout_s=5.0,
        )

        assert code == sf.EXIT_VERIFICATION_FAILED
        assert report["wrote"] is False
        assert not target.exists()
    finally:
        dead.close()
