"""The schema-constrained call: proven against a real socket, never a transport mock.

The endpoint in this file is a **real** ``ThreadingHTTPServer`` on ``127.0.0.1``
(not an ``httpx`` mock), so the request that leaves the machine is the request
:class:`skaldir.llm_provider.OpenAICompatibleLLM` really sends — the tests read
it back off the wire and assert on the body, including ``response_format``.

What is proven, in the order the module promises it:

* ``test_the_request_carries_the_schema…`` — a native call puts
  ``response_format={"type": "json_schema", "json_schema": {name, strict, schema}}``
  on the wire, verbatim;
* ``test_a_valid_answer…`` — a conforming answer becomes a typed value, and the
  report names the validation backend that judged it;
* ``test_an_invalid_answer_is_reported…`` — a non-conforming answer is **not**
  coerced: ``ok`` is ``False``, ``value`` is ``None``, and the raw text plus the
  validation error are both in the outcome;
* ``test_the_repair_attempt…`` — the repair turn carries the model's own raw
  answer and the validation error, and the corrected answer is validated from
  scratch;
* ``test_a_refused_native_…`` / ``test_everything_refused_…`` — the fallback path
  is measured: json_object, then a prompted answer validated locally, with the
  skip reason for each mode;
* ``test_a_boolean_is_not_an_integer`` / ``test_a_string_is_not_a_number`` —
  strict types, the place ``jsonschema`` alone would be too generous;
* pydantic: a model class gives a typed instance, and a model that refuses a
  schema-valid answer is reported (never coerced).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from skaldir import llm_provider as lp
from skaldir import structured as st

# --------------------------------------------------------------------------- #
# a real endpoint on 127.0.0.1                                                 #
# --------------------------------------------------------------------------- #

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["pong"]},
        "probe": {"type": "integer"},
    },
    "required": ["answer", "probe"],
    "additionalProperties": False,
}

VALID_TEXT = '{"answer": "pong", "probe": 1}'
INVALID_TYPE_TEXT = '{"answer": "pong", "probe": "1"}'
MISSING_KEY_TEXT = '{"answer": "pong"}'

REFUSAL_MESSAGE = "response_format is not supported for this model"
REFUSAL_BODY = {"error": {"message": REFUSAL_MESSAGE, "type": "invalid_request_error"}}


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: ARG002
        return


class StubEndpoint:
    """An OpenAI-compatible endpoint with a scripted answer per request.

    ``answers`` is a list of either a string (the assistant text) or
    ``(status, body)`` for an error answer; the last entry repeats.
    """

    def __init__(self, answers: list, *, model: str = "stub-model") -> None:
        self.answers = list(answers)
        self.model = model
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        self._server = _QuietServer(("127.0.0.1", 0), self._handler())

    # -- lifecycle --------------------------------------------------------- #

    def _handler(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

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
                    index = len(stub.requests)
                    stub.requests.append(
                        {
                            "path": self.path,
                            "body": body,
                            "headers": {k.lower(): v for k, v in self.headers.items()},
                        }
                    )
                answer = stub.answers[min(index, len(stub.answers) - 1)]
                if isinstance(answer, tuple):
                    status, payload = answer
                    encoded = json.dumps(payload).encode("utf-8")
                else:
                    status = 200
                    payload = {
                        "id": f"chatcmpl-{index}",
                        "model": stub.model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": answer},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                    }
                    encoded = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def request_count(self) -> int:
        return len(self.requests)

    def bodies(self) -> list[dict]:
        return [entry["body"] for entry in self.requests]

    def messages_at(self, index: int) -> list[dict]:
        return list(self.requests[index]["body"].get("messages") or [])

    def response_format_at(self, index: int):
        return self.requests[index]["body"].get("response_format")

    def __enter__(self) -> StubEndpoint:
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        self._thread = thread
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()
        time.sleep(0.02)


def client_for(endpoint: StubEndpoint, **overrides) -> lp.OpenAICompatibleLLM:
    """The real client, pointed at the stub (no injected transport, no mock)."""
    config = {
        "provider": "openai-compatible",
        "base_url": endpoint.base_url,
        "model": "stub-model",
        "api_key": "not-a-real-key",
        "max_retries": 0,
        "timeout_s": 5.0,
    }
    config.update(overrides)
    cfg = lp.resolve_provider_config(config, env={}, config_path=Path("/nonexistent/none.yaml"))
    assert cfg.ready(), cfg.problems
    return lp.OpenAICompatibleLLM(cfg)


# --------------------------------------------------------------------------- #
# 1. the request that leaves the machine                                       #
# --------------------------------------------------------------------------- #


def test_the_request_carries_the_schema_and_the_mode(tmp_path):
    with StubEndpoint([VALID_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub),
            SCHEMA,
            prompt="answer the probe",
            tier="small",
            name="skaldir_probe",
        )

        assert stub.request_count == 1
        sent = stub.response_format_at(0)
        assert sent == {
            "type": "json_schema",
            "json_schema": {"name": "skaldir_probe", "strict": True, "schema": SCHEMA},
        }
        body = stub.bodies()[0]
        assert body["model"] == "stub-model"
        assert body["messages"] == [{"role": "user", "content": "answer the probe"}]
        # the credential travels in the header, never in the body
        assert "not-a-real-key" not in json.dumps(body)
        assert stub.requests[0]["headers"]["authorization"].startswith("Bearer ")

    assert outcome.ok is True
    assert outcome.value == {"answer": "pong", "probe": 1}
    assert outcome.mode == st.MODE_NATIVE
    assert outcome.attempts == 1
    assert outcome.validator.startswith("builtin") or "builtin" in outcome.validator
    assert outcome.usage["total_tokens"] == 18


def test_the_strict_flag_is_the_callers_choice():
    with StubEndpoint([VALID_TEXT]) as stub:
        st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", strict=False
        )
        assert stub.response_format_at(0)["json_schema"]["strict"] is False


# --------------------------------------------------------------------------- #
# 2. an invalid answer is reported, never coerced                              #
# --------------------------------------------------------------------------- #


def test_an_invalid_answer_is_reported_with_its_raw_text():
    with StubEndpoint([INVALID_TYPE_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", repair_attempts=0
        )

    assert outcome.ok is False
    assert outcome.value is None and outcome.model is None
    assert outcome.raw == INVALID_TYPE_TEXT  # exactly what the provider returned
    assert "expected integer" in outcome.validation_error
    assert outcome.error
    assert outcome.steps[0].raw == INVALID_TYPE_TEXT
    assert outcome.steps[0].validation_error
    # three modes were tried (native, json_object, prompted) and none coerced
    assert stub.request_count == 3


def test_a_missing_required_property_is_not_invented():
    with StubEndpoint([MISSING_KEY_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", max_attempts=1
        )
    assert outcome.ok is False
    assert "missing required property 'probe'" in outcome.validation_error
    assert outcome.value is None


def test_an_answer_that_is_not_json_is_reported_as_such():
    with StubEndpoint(["pong"]) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", max_attempts=1
        )
    assert outcome.ok is False
    assert "not JSON" in outcome.validation_error
    assert outcome.raw == "pong"


def test_a_boolean_is_not_an_integer():
    """``jsonschema`` alone accepts ``True`` for an integer in some drafts — we do not."""
    errors = st.builtin_validate({"type": "integer"}, True)
    assert errors and "expected integer" in errors[0]
    assert st.builtin_validate({"type": "integer"}, 1) == []
    assert st.builtin_validate({"type": "boolean"}, True) == []


def test_a_string_is_not_a_number():
    assert st.builtin_validate({"type": "number"}, "1") != []
    assert st.builtin_validate({"type": "number"}, 1.5) == []
    # a float with no fractional part is a JSON integer (spec, not coercion)
    assert st.builtin_validate({"type": "integer"}, 2.0) == []


def test_both_validators_run_and_the_report_names_them():
    errors, backend, notes = st.validate_payload(SCHEMA, {"answer": "pong", "probe": 1})
    assert errors == []
    assert backend in ("builtin", "jsonschema+builtin")
    if st.jsonschema_backend():
        assert backend == "jsonschema+builtin"
    assert notes == []
    errors, backend, _ = st.validate_payload(SCHEMA, {"answer": "pong", "probe": "1"})
    assert errors and backend == "jsonschema+builtin"
    # two independent implementations of the same rule agree
    assert sum("integer" in item for item in errors) >= 2


def test_the_builtin_backend_never_claims_jsonschema():
    errors, backend, _ = st.validate_payload(
        SCHEMA, {"answer": "pong", "probe": 1}, backend="builtin"
    )
    assert backend == "builtin"
    assert errors == []


# --------------------------------------------------------------------------- #
# 3. the repair path                                                          #
# --------------------------------------------------------------------------- #


def test_the_repair_attempt_carries_the_raw_answer_and_the_error():
    with StubEndpoint([MISSING_KEY_TEXT, VALID_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", repair_attempts=1
        )

    assert outcome.ok is True
    assert outcome.value == {"answer": "pong", "probe": 1}
    assert outcome.repaired is True
    assert outcome.attempts == 2
    assert len(outcome.steps) == 2
    assert outcome.steps[0].validation_error  # the first answer's failure is kept
    assert outcome.steps[1].ok is True

    repair_messages = stub.messages_at(1)
    assert [item["role"] for item in repair_messages] == ["user", "assistant", "user"]
    assert repair_messages[1]["content"] == MISSING_KEY_TEXT  # the model's own answer
    assert "did not satisfy the schema" in repair_messages[2]["content"]
    assert "missing required property 'probe'" in repair_messages[2]["content"]


def test_a_failed_repair_keeps_every_raw_answer():
    with StubEndpoint([MISSING_KEY_TEXT, INVALID_TYPE_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe",
            max_attempts=2, repair_attempts=1,
        )
    assert outcome.ok is False
    assert [step.raw for step in outcome.steps] == [MISSING_KEY_TEXT, INVALID_TYPE_TEXT]
    assert outcome.raw == INVALID_TYPE_TEXT
    assert outcome.value is None


def test_no_repair_means_no_second_request():
    with StubEndpoint([MISSING_KEY_TEXT]) as stub:
        st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe",
            mode=st.MODE_NATIVE, repair_attempts=0,
        )
    assert stub.request_count == 1


# --------------------------------------------------------------------------- #
# 4. the validated fallback path                                               #
# --------------------------------------------------------------------------- #


def test_a_refused_native_field_falls_back_to_json_object():
    with StubEndpoint([(400, REFUSAL_BODY), VALID_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", max_attempts=3
        )

    assert outcome.ok is True
    assert outcome.mode == st.MODE_JSON_OBJECT
    assert stub.response_format_at(0)["type"] == "json_schema"
    assert stub.response_format_at(1) == {"type": "json_object"}
    trail = {item["mode"]: item for item in outcome.mode_trail}
    assert trail[st.MODE_NATIVE]["status"] == "skipped"
    assert trail[st.MODE_NATIVE]["reason"] == st.REASON_UNSUPPORTED
    assert "response_format is not supported" in trail[st.MODE_NATIVE]["detail"]
    assert any("refused response_format" in note for note in outcome.notes)


def test_everything_refused_falls_back_to_a_validated_prompted_answer():
    with StubEndpoint(
        [(400, REFUSAL_BODY), (400, REFUSAL_BODY), VALID_TEXT]
    ) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", max_attempts=3
        )

    assert outcome.ok is True
    assert outcome.mode == st.MODE_PROMPTED
    assert stub.request_count == 3
    assert stub.response_format_at(0)["type"] == "json_schema"
    assert stub.response_format_at(1) == {"type": "json_object"}
    assert stub.response_format_at(2) is None  # nothing constrained it: we validated it
    prompted = stub.messages_at(2)[-1]["content"]
    assert "JSON Schema" in prompted and '"probe"' in prompted
    reasons = [item["reason"] for item in outcome.mode_trail[:2]]
    assert reasons == [st.REASON_UNSUPPORTED, st.REASON_UNSUPPORTED]
    assert outcome.mode_trail[-1]["mode"] == st.MODE_PROMPTED
    assert outcome.mode_trail[-1]["status"] == "answered"  # the only mode that answered


def test_a_refusal_is_never_read_from_a_5xx():
    """A server error is a transport problem — it must not downgrade the mode."""
    assert lp.structured_refusal_reason(REFUSAL_MESSAGE, status=400)
    assert lp.structured_refusal_reason(REFUSAL_MESSAGE, status=500) == ""
    assert lp.structured_refusal_reason("something else entirely", status=400) == ""


def test_a_rate_limit_is_not_a_validation_failure():
    with StubEndpoint([(429, {"error": {"message": "rate limited"}})]) as stub:
        with pytest.raises(lp.ProviderRateLimitError):
            st.structured_call(
                client_for(stub), SCHEMA, prompt="go", name="probe", max_attempts=3
            )


# --------------------------------------------------------------------------- #
# 5. reading the answer: extraction, never invention                           #
# --------------------------------------------------------------------------- #


def test_a_fenced_answer_is_unwrapped_and_the_note_says_so():
    with StubEndpoint(["```json\n" + VALID_TEXT + "\n```"]) as stub:
        outcome = st.structured_call(client_for(stub), SCHEMA, prompt="go", name="probe")
    assert outcome.ok is True
    assert outcome.value == {"answer": "pong", "probe": 1}
    assert "code fence" in outcome.steps[0].extraction
    assert any("code fence" in note for note in outcome.notes)
    assert outcome.raw.startswith("```json")  # the untouched raw text is still there


def test_prose_around_the_json_is_reported_as_an_extraction():
    text = f"Sure, here you go: {VALID_TEXT} — anything else?"
    with StubEndpoint([text]) as stub:
        outcome = st.structured_call(client_for(stub), SCHEMA, prompt="go", name="probe")
    assert outcome.ok is True
    assert "surrounding prose" in outcome.steps[0].extraction
    assert outcome.raw == text


def test_extract_json_returns_the_error_for_a_non_json_answer():
    value, note, error = st.extract_json("I cannot answer that")
    assert value is None and note == ""
    assert "not JSON" in error
    value, note, error = st.extract_json('{"a": 1}')
    assert value == {"a": 1} and note == "" and error == ""


# --------------------------------------------------------------------------- #
# 6. pydantic — typed answers, and the refusal is reported                      #
# --------------------------------------------------------------------------- #


class ProbeModel:
    """Unused placeholder kept out of the way — the real model comes from a fixture."""


@pytest.fixture
def probe_model():
    pydantic = pytest.importorskip("pydantic")

    class Probe(pydantic.BaseModel):
        answer: str
        probe: int

    return Probe


def test_a_pydantic_model_returns_a_typed_instance(probe_model):
    with StubEndpoint([VALID_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub), probe_model, prompt="go", response_model=probe_model
        )
    assert outcome.ok is True
    assert isinstance(outcome.model, probe_model)
    assert outcome.model.probe == 1
    assert outcome.schema_name == "Probe"
    assert outcome.value == {"answer": "pong", "probe": 1}


def test_the_model_schema_is_what_goes_on_the_wire(probe_model):
    with StubEndpoint([VALID_TEXT]) as stub:
        st.structured_call(client_for(stub), probe_model, prompt="go")
    sent = stub.response_format_at(0)["json_schema"]
    assert sent["name"] == "Probe"
    assert sent["schema"]["properties"]["probe"]["type"] == "integer"
    assert set(sent["schema"]["required"]) == {"answer", "probe"}


def test_a_model_that_refuses_a_schema_valid_answer_is_reported():
    """The JSON Schema is looser than the Python annotation (``format``) — no coercion."""
    pydantic = pytest.importorskip("pydantic")
    from datetime import date

    class Strict(pydantic.BaseModel):
        when: date

    with StubEndpoint(['{"when": "not-a-date"}']) as stub:
        outcome = st.structured_call(
            client_for(stub), Strict, prompt="go", max_attempts=3
        )
    assert outcome.ok is False
    assert outcome.model is None and outcome.value is None
    assert outcome.raw == '{"when": "not-a-date"}'
    assert "refused it" in outcome.error and "valid date" in outcome.error
    assert stub.request_count == 1  # a refusal that repeating cannot fix is not retried


# --------------------------------------------------------------------------- #
# 7. require() / the probe                                                     #
# --------------------------------------------------------------------------- #


def test_require_raises_with_the_evidence_attached():
    with StubEndpoint([INVALID_TYPE_TEXT]) as stub:
        outcome = st.structured_call(
            client_for(stub), SCHEMA, prompt="go", name="probe", max_attempts=1
        )
    with pytest.raises(st.StructuredValidationError) as caught:
        st.require(outcome)
    error = caught.value
    assert error.raw == INVALID_TYPE_TEXT
    assert "expected integer" in error.validation_error
    assert error.steps


def test_require_returns_the_payload_when_valid():
    with StubEndpoint([VALID_TEXT]) as stub:
        outcome = st.structured_call(client_for(stub), SCHEMA, prompt="go", name="probe")
    assert st.require(outcome) == {"answer": "pong", "probe": 1}


def test_structured_or_raise_is_the_exception_flavoured_helper():
    with StubEndpoint([VALID_TEXT]) as stub:
        value = st.structured_or_raise(
            client_for(stub), SCHEMA, prompt="go", name="probe"
        )
    assert value == {"answer": "pong", "probe": 1}


def test_the_configuration_is_refused_loudly():
    with pytest.raises(st.StructuredConfigError):
        st.structured_call(object(), SCHEMA, prompt="")
    with pytest.raises(st.StructuredConfigError):
        st.structured_call(object(), {}, prompt="go")
    with pytest.raises(st.StructuredConfigError):
        st.structured_call(object(), SCHEMA, prompt="go", mode="invented")
    with pytest.raises(st.StructuredConfigError):
        st.structured_call(object(), "not-a-schema", prompt="go")
    with pytest.raises(st.StructuredConfigError):
        st.validate_payload(SCHEMA, {}, backend="invented")


def test_strict_compatible_schema_reports_every_change():
    loose = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a"],
    }
    strict, notes = st.strict_compatible_schema(loose)
    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == {"a", "b"}
    assert notes  # every relaxation is named
    assert any("b was optional" in note for note in notes)


def test_schema_name_is_sanitised():
    assert lp.schema_name_for("my answer / v2") == "my_answer_v2"
    assert lp.schema_name_for("") == "response"
    with pytest.raises(lp.ProviderConfigError):
        lp.json_schema_response_format("x", "not-a-mapping")


# --------------------------------------------------------------------------- #
# 8. the CLI surface: ``provider test --structured``                           #
# --------------------------------------------------------------------------- #


def test_the_structured_flag_reaches_the_handler_through_the_group_parser():
    from skaldir import provider_cli as pcli

    args = pcli.build_parser().parse_args(["test", "--structured", "--mode", "native"])
    assert args.func is pcli.cmd_test
    assert args.structured is True
    assert args.mode == "native"
    assert args.schema_file is None
    # without the flag the verb behaves exactly as before
    plain = pcli.build_parser().parse_args(["test", "local"])
    assert plain.func is pcli.cmd_test
    assert getattr(plain, "structured", False) is False


def test_the_group_registers_on_the_real_product_parser():
    """The hand-over snippet, exercised on the CLI's own parser (never edited here)."""
    import argparse

    from skaldir import cli
    from skaldir import provider_cli as pcli

    parser = cli.build_parser()
    sub = next(
        action
        for action in parser._actions  # noqa: SLF001 — argparse has no public API
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )
    if "provider" not in sub.choices:  # the CLI owner wires it; do not double-register
        pcli.register_cli(sub)
    args = parser.parse_args(["provider", "test", "--structured", "--json"])
    assert args.func is pcli.cmd_test
    assert args.structured is True
    assert args.json is True


def test_provider_test_structured_reports_the_native_mode_and_the_chain(
    tmp_path, capsys, monkeypatch
):
    from skaldir import provider_cli as pcli

    monkeypatch.delenv("GODSV_TEST_STUB_KEY", raising=False)  # hermetic: state is deterministic
    config = tmp_path / "config.yaml"
    with StubEndpoint([VALID_TEXT]) as stub:
        assert (
            pcli.main(
                [
                    "add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                    "--api-key-env", "GODSV_TEST_STUB_KEY", "--config", str(config),
                ]
            )
            == 0
        )
        capsys.readouterr()
        assert stub.request_count == 0  # configuring a provider is offline
        code = pcli.main(["test", "--structured", "--json", "--config", str(config)])
        out = capsys.readouterr().out
        payload = json.loads(out)

    assert code == pcli.EXIT_OK
    assert payload["ok"] is True
    assert payload["mode_used"] == st.MODE_NATIVE
    assert payload["native_json_schema"] is True
    assert payload["request"]["response_format"]["type"] == "json_schema"
    assert payload["request"]["response_format"]["json_schema"]["strict"] is True
    assert payload["answer"]["value"] == {"answer": "pong", "probe": 1}
    assert payload["answer"]["raw"] == VALID_TEXT
    assert payload["validation"]["ok"] is True
    assert payload["validation"]["backend"]
    assert payload["credential"] in ("unset", "not-required")  # the state, never the value
    assert "GODSV_TEST_STUB_KEY" not in out
    assert payload["fallback_chain"]["order"][0].startswith("config:")
    assert any(step["status"] == "skipped" for step in payload["fallback_chain"]["steps"])


def test_provider_test_structured_exits_2_on_an_answer_that_breaks_the_schema(
    tmp_path, capsys, monkeypatch
):
    from skaldir import provider_cli as pcli

    config = tmp_path / "config.yaml"
    monkeypatch.setenv("GODSV_TEST_STUB_KEY", "stub-only-credential")
    with StubEndpoint([INVALID_TYPE_TEXT]) as stub:
        assert (
            pcli.main(
                [
                    "add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                    "--api-key-env", "GODSV_TEST_STUB_KEY", "--config", str(config),
                ]
            )
            == 0
        )
        capsys.readouterr()
        code = pcli.main(["test", "--structured", "--json", "--config", str(config)])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

    assert code == pcli.EXIT_VERIFICATION_FAILED  # 2 — a real answer that does not fit
    assert payload["ok"] is False
    assert payload["answer"]["raw"] == INVALID_TYPE_TEXT  # the raw answer travels
    assert payload["validation"]["errors"]
    assert "expected integer" in payload["validation"]["errors"][0]
    assert "value that satisfies" in captured.err  # the verdict explains itself
    assert stub.request_count == 3  # native, json_object, prompted — all reported


def test_a_mode_that_the_endpoint_refuses_is_a_warning_not_a_failure(tmp_path, capsys):
    from skaldir import provider_cli as pcli

    config = tmp_path / "config.yaml"
    with StubEndpoint([(400, REFUSAL_BODY), VALID_TEXT]) as stub:
        assert (
            pcli.main(
                [
                    "add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                    "--api-key-env", "GODSV_TEST_STUB_KEY", "--config", str(config),
                ]
            )
            == 0
        )
        capsys.readouterr()
        code = pcli.main(["test", "--structured", "--json", "--config", str(config)])
        captured = capsys.readouterr()

    assert code == pcli.EXIT_OK  # the validated fallback answered
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["mode_used"] == st.MODE_JSON_OBJECT
    assert payload["native_json_schema"] is False
    assert "response_format is not supported" in payload["native_reason"]
    # the refusal is named on stderr — a warning, not a silent downgrade
    assert "json_object" not in captured.err  # no human line pretends it was native
    assert "mode native is not supported" in captured.err
    assert "response_format is not supported" in captured.err


def test_a_schema_file_is_read_and_refused_loudly_when_broken(tmp_path, capsys):
    from skaldir import provider_cli as pcli

    broken = tmp_path / "schema.json"
    broken.write_text("[]", encoding="utf-8")
    code = pcli.main(["test", "--structured", "--schema-file", str(broken)])
    captured = capsys.readouterr()
    assert code == pcli.EXIT_USAGE
    assert "not usable" in captured.err

    good = tmp_path / "good.json"
    good.write_text(json.dumps(SCHEMA), encoding="utf-8")
    with StubEndpoint([VALID_TEXT]) as stub:
        config = tmp_path / "config.yaml"
        pcli.main(
            [
                "add", "local", "--base-url", stub.base_url, "--model", "stub-model",
                "--config", str(config),
            ]
        )
        capsys.readouterr()
        assert (
            pcli.main(
                [
                    "test", "--structured", "--json", "--schema-file", str(good),
                    "--config", str(config),
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
    assert payload["schema"]["required"] == ["answer", "probe"]
    assert payload["request"]["response_format"]["json_schema"]["schema"] == SCHEMA
