"""Step runners: the ``api`` and ``gui`` kinds COMPOSE refuses, plus the kind registry.

Evidence classes, stated per test class because they are not the same:

* :class:`TestApiStepRunnerAgainstARealServer` talks to a **real** HTTP server this file
  starts on ``127.0.0.1:0``: a real socket, a real response, and the declared assertions
  evaluated against that response. The stub records every request it received, so a claim
  like "the bound body went out" is checked on the server side.
* :class:`TestApiStepRunnerWire` uses ``httpx.MockTransport`` for the cases a stub server
  cannot produce deterministically (a timeout, a refused declaration that must not send
  anything).
* :class:`TestGuiStepRunner` drives the ``gui`` runner with the ACT layer's own in-process
  fixture runner and with a synthetic no-bridge host; the REAL container-display GUI step
  lives in ``tests/test_step_runners_gui.py``.
* :class:`TestRegistry` proves the seam the COMPOSE layer imports: exactly the four step
  kinds, ``api``/``gui`` executable, and the registry table buildable into the COMPOSE
  layer's own ``KindExecutor`` rows.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from skaldir import act_service as A
from skaldir import step_runners as R
from skaldir.compose_service import STEP_KINDS, KindExecutor
from skaldir.computer_use import ActionResult, DryRunRunner

# --------------------------------------------------------------------------- #
# a real stub server on loopback                                                #
# --------------------------------------------------------------------------- #


class _Handler(BaseHTTPRequestHandler):
    """A tiny JSON service: /hello, /gated, /flaky (503 twice, then 200), /echo."""

    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, method: str, body: bytes = b"") -> None:
        self.server.calls.append(  # type: ignore[attr-defined]
            f"{method} {self.path}" + (f" body={body.decode('utf-8')}" if body else "")
        )

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's interface
        self._record("GET")
        if self.path.startswith("/flaky"):
            seen = len(
                [call for call in self.server.calls if call.startswith("GET /flaky")]  # type: ignore[attr-defined]
            )
            if seen < 3:
                return self._send(503, {"retry": seen})
            return self._send(200, {"ok": True, "attempt": seen})
        if self.path.startswith("/always503"):
            return self._send(503, {"error": "still not ready"})
        if self.path.startswith("/gated"):
            return self._send(403, {"error": "no token"})
        return self._send(
            200, {"message": "hello skaldir", "items": [1, 2, 3], "tail": "TAIL-TOKEN-9"}
        )

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self._record("POST", raw)
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"unparsed": raw.decode("utf-8", "replace")}
        if self.path.startswith("/echo"):
            return self._send(201, {"echoed": payload, "token": "T-1"})
        return self._send(200, {"seen": payload})

    def log_message(self, *args, **kwargs) -> None:  # keep the pytest output clean
        pass


@pytest.fixture(scope="module")
def server():
    """A real HTTP server for the whole module; ``server["calls"]`` is what it saw."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.calls = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"base": f"http://127.0.0.1:{httpd.server_address[1]}", "calls": httpd.calls}
    finally:
        httpd.shutdown()
        httpd.server_close()


def _api_step(url: str, *, method: str = "GET", step_id: str = "api_step", timeout: float = 10.0):
    """A step declaration shaped like ``compose_service.StepSpec`` (same field names)."""
    return SimpleNamespace(
        id=step_id, kind="api", url=url, method=method, timeout_s=timeout, target="", outputs=[]
    )


def _no_request_transport():
    """A transport that fails the test if a request is ever sent through it."""

    def handler(request):  # pragma: no cover — reaching it is the failure
        raise AssertionError(
            f"a refused step must send nothing, sent {request.method} {request.url}"
        )

    return httpx.MockTransport(handler)


def _capturing_transport(seen: list):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"captured": str(request.url)})

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- #
# api — against a real server                                                    #
# --------------------------------------------------------------------------- #


class TestApiStepRunnerAgainstARealServer:
    def test_a_real_get_step_verifies_its_declared_status_and_body(self, server):
        step = _api_step(f"{server['base']}/hello")
        outcome = R.run_api_step(
            step, {"expect_status": "2xx", "expect_body_contains": ["hello skaldir", '"nope"']}
        )
        assert outcome.ok is False, "one of the two body assertions is false — no fake pass"
        outcome = R.run_api_step(
            step,
            {
                "expect_status": "2xx",
                "expect_body_contains": "hello skaldir",
                "expect_body_absent": "no token",
                "expect_body_regex": r'"items":\s*\[1, 2, 3\]',
            },
        )
        assert outcome.status == "ok" and outcome.ok is True
        assert outcome.exit_code == R.EXIT_OK
        payload = outcome.outputs
        assert payload["status_code"] == 200
        assert payload["attempts"] == 1
        assert payload["json"]["message"] == "hello skaldir"
        assert payload["text"].startswith('{"message"')
        assert payload["content_type"] == "application/json"
        assert [item["ok"] for item in payload["assertions"]] == [True, True, True, True]
        assert payload["elapsed_ms"] >= 0
        # the server really answered this request (not a mocked transport)
        assert "GET /hello" in server["calls"]

    def test_the_bound_json_body_really_goes_out_and_the_answer_is_asserted(self, server):
        step = _api_step(f"{server['base']}/echo", method="POST", step_id="post_echo")
        outcome = R.run_api_step(
            step,
            {
                "json": {
                    "name": "$input.name",
                    "n": 7,
                    "nested": {"from_step": "$steps.fetch.tag"},
                },
                "expect_status": [201],
                "expect_body_contains": ["T-1", '"name": "skaldir"'],
                "headers": {"Content-Type": "application/json"},
            },
            context={
                "input": {"name": "skaldir"},
                "steps": {"fetch": {"tag": "FROM-STEP-1"}},
                "exit_ok": True,
            },
        )
        assert outcome.ok is True, outcome.detail
        sent = [call for call in server["calls"] if call.startswith("POST /echo")]
        assert sent, "the POST never reached the server"
        assert '"name":"skaldir"' in sent[-1] and '"from_step":"FROM-STEP-1"' in sent[-1]
        assert outcome.outputs["status_code"] == 201
        assert outcome.outputs["json"]["echoed"]["nested"]["from_step"] == "FROM-STEP-1"

    def test_a_status_that_does_not_match_is_a_failure_with_the_real_answer_kept(self, server):
        outcome = R.run_api_step(_api_step(f"{server['base']}/gated"), {"expect_status": 200})
        assert outcome.status == "failed" and outcome.ok is False
        assert outcome.failure_class == R.FAILURE_STATUS
        assert outcome.exit_code == R.EXIT_VERIFICATION_FAILED
        assert outcome.outputs["status_code"] == 403, "the honest answer stays in the payload"
        assert "403" in outcome.detail

    def test_a_body_assertion_that_fails_is_a_body_mismatch(self, server):
        outcome = R.run_api_step(
            _api_step(f"{server['base']}/hello"), {"expect_body_contains": "ABSENT-TOKEN"}
        )
        assert outcome.ok is False
        assert outcome.failure_class == R.FAILURE_BODY
        assert outcome.outputs["status_code"] == 200

    def test_a_declared_retry_policy_really_retries_and_is_counted(self, server):
        before = len([call for call in server["calls"] if call.startswith("GET /flaky")])
        outcome = R.run_api_step(
            _api_step(f"{server['base']}/flaky"),
            {"expect_status": 200, "expect_body_contains": "attempt", "retry": {"attempts": 4}},
            sleep=lambda _seconds: None,
        )
        assert outcome.ok is True, outcome.detail
        assert outcome.outputs["attempts"] == 3
        assert outcome.outputs["status_code"] == 200
        after = len([call for call in server["calls"] if call.startswith("GET /flaky")])
        assert after - before == 3, "three requests really went out"

    def test_without_a_declared_retry_the_step_tries_once(self, server):
        before = len([call for call in server["calls"] if call.startswith("GET /always503")])
        outcome = R.run_api_step(
            _api_step(f"{server['base']}/always503"), {"expect_status": 200}
        )
        after = len([call for call in server["calls"] if call.startswith("GET /always503")])
        assert outcome.ok is False
        assert outcome.outputs["attempts"] == 1
        assert outcome.outputs["status_code"] == 503
        assert outcome.failure_class == R.FAILURE_STATUS
        assert after - before == 1, "one request, no retry"

    def test_an_unreachable_server_is_a_network_error_not_a_pass(self, tmp_path):
        outcome = R.run_api_step(
            _api_step("http://127.0.0.1:1/dead", timeout=2.0),
            {"expect_status": 200, "retry": {"attempts": 2, "backoff_s": 0.0}},
        )
        assert outcome.ok is False
        assert outcome.failure_class == R.FAILURE_NETWORK
        assert outcome.outputs["attempts"] == 2
        assert outcome.outputs["status_code"] == 0
        assert "did not reach a server" in outcome.detail

    def test_the_excerpt_is_clipped_while_the_assertions_read_the_whole_body(self, server):
        outcome = R.run_api_step(
            _api_step(f"{server['base']}/hello", step_id="clip"),
            {
                "max_excerpt": 20,
                "expect_body_contains": "TAIL-TOKEN-9",  # far beyond the excerpt
            },
        )
        assert outcome.ok is True, outcome.detail
        assert outcome.outputs["truncated"] is True
        assert len(outcome.outputs["text"]) == 20
        assert outcome.outputs["text_len"] > 20
        assert "TAIL-TOKEN-9" not in outcome.outputs["text"]

    def test_a_two_step_chain_threads_the_first_answer_into_the_second_request(self, server):
        """The point of the runners: a combined tool can chain HTTP steps for real."""
        context: dict = {"input": {"who": "chain"}, "steps": {}, "exit_ok": True}
        first = R.run_step(
            "api",
            _api_step(f"{server['base']}/hello", step_id="first"),
            {"expect_status": 200, "expect_body_contains": "hello skaldir"},
            context=context,
        )
        assert first.ok is True
        context["steps"]["first"] = dict(first.outputs)
        second = R.run_step(
            "api",
            _api_step(f"{server['base']}/echo", method="POST", step_id="second"),
            {
                "json": {
                    "message": "$steps.first.json.message",
                    "status": "$steps.first.status_code",
                    "who": "$input.who",
                },
                "expect_status": 201,
                "expect_body_contains": ["skaldir", '"status": 200'],
            },
            context=context,
        )
        assert second.ok is True, second.detail
        sent = [call for call in server["calls"] if call.startswith("POST /echo")]
        assert '"message":"hello skaldir"' in sent[-1]
        assert '"status":200' in sent[-1] and '"who":"chain"' in sent[-1]


# --------------------------------------------------------------------------- #
# api — wire details a stub server cannot produce deterministically               #
# --------------------------------------------------------------------------- #


class TestApiStepRunnerWire:
    def test_a_timeout_is_reported_as_a_timeout_and_may_be_retried(self):
        attempts = {"count": 0}

        def handler(request):
            attempts["count"] += 1
            raise httpx.ReadTimeout("too slow", request=request)

        outcome = R.run_api_step(
            _api_step("http://example.invalid/slow", timeout=0.5),
            {"expect_status": 200, "retry": {"attempts": 3, "backoff_s": 0.0, "on_error": True}},
            transport=httpx.MockTransport(handler),
            sleep=lambda _seconds: None,
        )
        assert outcome.ok is False
        assert outcome.failure_class == R.FAILURE_TIMEOUT
        assert outcome.outputs["attempts"] == 3
        assert attempts["count"] == 3
        assert "timed out" in outcome.detail

    def test_a_retrying_run_that_ends_in_an_error_does_not_report_a_stale_answer(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, json={"error": "busy"})
            raise httpx.ReadTimeout("too slow", request=request)

        outcome = R.run_api_step(
            _api_step("http://example.invalid/flaky"),
            {"expect_status": 200, "retry": {"attempts": 2, "backoff_s": 0.0}},
            transport=httpx.MockTransport(handler),
            sleep=lambda _seconds: None,
        )
        assert outcome.ok is False
        assert outcome.failure_class == R.FAILURE_TIMEOUT
        assert outcome.outputs["attempts"] == 2
        assert outcome.outputs["status_code"] == 0, "the 503 was not the last word"
        assert outcome.outputs["text"] == "" and outcome.outputs["json"] is None
        assert "timed out" in outcome.outputs["error"]

    def test_a_retry_policy_that_does_not_retry_errors_stops_at_the_first_one(self):
        attempts = {"count": 0}

        def handler(request):
            attempts["count"] += 1
            raise httpx.ConnectError("refused", request=request)

        outcome = R.run_api_step(
            _api_step("http://example.invalid/dead"),
            {"retry": {"attempts": 3, "backoff_s": 0.0, "on_error": False}},
            transport=httpx.MockTransport(handler),
            sleep=lambda _seconds: None,
        )
        assert outcome.failure_class == R.FAILURE_NETWORK
        assert outcome.outputs["attempts"] == 1 and attempts["count"] == 1

    def test_an_inline_reference_in_the_url_is_substituted(self):
        seen: list = []
        outcome = R.run_api_step(
            _api_step("http://stub.local/items/$steps.list.id?x=$input.n"),
            {},
            context={"input": {"n": "9"}, "steps": {"list": {"id": "42"}}, "exit_ok": True},
            transport=_capturing_transport(seen),
        )
        assert outcome.ok is True
        assert str(seen[0].url) == "http://stub.local/items/42?x=9"

    def test_an_unresolved_reference_is_refused_and_nothing_is_sent(self):
        outcome = R.run_api_step(
            _api_step("http://stub.local/$steps.missing.key"),
            {},
            context={"input": {}, "steps": {}, "exit_ok": True},
            transport=_no_request_transport(),
        )
        assert outcome.ok is False and outcome.blocked is False
        assert outcome.failure_class == R.FAILURE_USAGE
        assert outcome.exit_code == R.EXIT_USAGE
        assert "missing" in outcome.detail

    def test_a_reference_without_a_context_is_refused_rather_than_sent_literally(self):
        outcome = R.run_api_step(
            _api_step("http://stub.local/plain"),
            {"headers": {"X-Token": "$steps.auth.token"}},
            transport=_no_request_transport(),
        )
        assert outcome.failure_class == R.FAILURE_USAGE
        assert "no composite context" in outcome.detail

    @pytest.mark.parametrize(
        "arguments,step_url,expected",
        [
            ({}, "ftp://files.local/x", "not an http(s) URL"),
            ({"expct_status": 200}, "http://stub.local/x", "unsupported api field(s)"),
            ({"json": {"a": 1}, "body": "raw"}, "http://stub.local/x", "both `json` and `body`"),
            ({"retry": {"attempts": 99}}, "http://stub.local/x", "outside 1..10"),
            ({"retry": {"attempts": 2, "unknown": 1}}, "http://stub.local/x", "unknown field(s)"),
            ({"expect_body_regex": "([unclosed"}, "http://stub.local/x", "does not compile"),
            ({"expect_status": "2xx5"}, "http://stub.local/x", "neither a status code"),
            ({"timeout_s": -1}, "http://stub.local/x", "timeout_s must be positive"),
        ],
    )
    def test_a_declaration_the_runner_cannot_honour_is_refused_before_any_request(
        self, arguments, step_url, expected
    ):
        outcome = R.run_api_step(
            _api_step(step_url), arguments, transport=_no_request_transport()
        )
        assert outcome.ok is False
        assert outcome.failure_class == R.FAILURE_USAGE
        assert outcome.exit_code == R.EXIT_USAGE
        assert expected in outcome.detail, outcome.detail

    def test_a_typo_in_the_method_is_refused(self):
        outcome = R.run_api_step(
            _api_step("http://stub.local/x", method="FETCH"),
            {},
            transport=_no_request_transport(),
        )
        assert outcome.failure_class == R.FAILURE_USAGE and "not one of" in outcome.detail

    def test_a_missing_url_is_refused_and_names_the_step(self):
        outcome = R.run_api_step(
            SimpleNamespace(id="nameless", kind="api", url="", method="GET", timeout_s=5.0), {}
        )
        assert outcome.failure_class == R.FAILURE_USAGE
        assert "nameless" in outcome.detail and "needs a url" in outcome.detail

    def test_every_declared_output_key_is_always_present(self, tmp_path):
        outcome = R.run_api_step(
            _api_step("http://127.0.0.1:1/dead", timeout=1.0), {}, transport=None
        )
        assert set(outcome.outputs) == set(R.API_OUTPUT_KEYS)

    def test_without_httpx_the_runner_blocks_with_that_capability_named(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "httpx", None)  # `import httpx` then raises
        outcome = R.run_api_step(_api_step("http://stub.local/x"), {"expect_status": 200})
        assert outcome.blocked is True
        assert outcome.missing_kind == R.KIND_API
        assert outcome.exit_code == R.EXIT_BLOCKED
        assert "httpx" in outcome.detail


# --------------------------------------------------------------------------- #
# gui — delegation, the fixture refusal, and the honest no-bridge exit 3         #
# --------------------------------------------------------------------------- #


def _action(description: str, *, expect: str = "ok", kind: str = "cli") -> dict:
    action: dict = {
        "kind": kind,
        "channel": "api",
        "description": description,
        "verify": {"expect_stdout_contains": expect},
    }
    if kind in ("cli", "api_call"):
        action["argv"] = ["echo", expect]
    return action


def _fixture_runner(actions, *observations: str) -> DryRunRunner:
    """The ACT layer's own fixture runner, scripted per declared expectation."""
    behaviors = {}
    for action in actions:
        text = str((action.get("verify") or {}).get("expect_stdout_contains", "") or "")
        behaviors[str(action.get("description", ""))] = ActionResult(ok=True, stdout=text)
    return DryRunRunner(observations=list(observations) or [""], behaviors=behaviors)


def _gui_step(target: str = "inline", *, step_id: str = "gui_step", timeout: float = 30.0):
    return SimpleNamespace(
        id=step_id, kind="gui", target=target, timeout_s=timeout, note="", outputs=[]
    )


def _inline_arguments(*, window: str = "AFTER-WINDOW", description: str = "first") -> dict:
    actions = [_action(description)]
    return {
        "actions": actions,
        "end_state": {"channel": "window_title", "expect_observe_contains": window},
        "goal": "a declared gui step",
    }


class TestGuiStepRunner:
    def test_the_in_process_fixture_is_refused_by_default(self, tmp_path):
        args = _inline_arguments()
        outcome = R.run_gui_step(_gui_step(), args, act_runner=_fixture_runner(args["actions"]))
        assert outcome.blocked is True
        assert outcome.missing_kind == R.KIND_GUI
        assert outcome.exit_code == R.EXIT_BLOCKED
        assert outcome.failure_class == R.FAILURE_POLICY
        assert "in-process fixture" in outcome.detail
        assert "dry-run" in outcome.outputs["reason"]
        assert outcome.outputs["executed"] == 0, "nothing ran"

    def test_with_the_explicit_opt_in_the_fixture_result_stays_labelled_a_fixture(self, tmp_path):
        args = {**_inline_arguments(), "state": tmp_path}
        outcome = R.run_gui_step(
            _gui_step(),
            args,
            runtime=tmp_path,
            act_runner=_fixture_runner(args["actions"], "AFTER-WINDOW"),
            allow_fixture=True,
        )
        assert outcome.ok is True, outcome.detail
        assert outcome.outputs["status"] == "verified"
        assert outcome.outputs["live"] is False
        assert outcome.outputs["scope"] == "in-process fixture"
        assert "in-process fixture" in outcome.detail

    def test_a_fixture_run_that_misses_the_end_state_is_a_failure(self, tmp_path):
        args = _inline_arguments(window="NOT-ON-THE-DISPLAY")
        outcome = R.run_gui_step(
            _gui_step(),
            {**args, "state": tmp_path},
            runtime=tmp_path,
            act_runner=_fixture_runner(args["actions"], "something else"),
            allow_fixture=True,
        )
        assert outcome.ok is False and outcome.blocked is False
        assert outcome.failure_class == R.FAILURE_VERIFICATION
        assert outcome.exit_code == R.EXIT_VERIFICATION_FAILED
        assert outcome.outputs["end_state"]["missing"] == ["NOT-ON-THE-DISPLAY"]

    def test_a_host_without_a_bridge_blocks_with_the_precise_missing_capability(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            A,
            "resolve_runner",
            lambda kind="auto", **kwargs: (
                None,
                {
                    "requested": kind,
                    "bridge": "",
                    "resolved": False,
                    "isolation": "",
                    "real": False,
                    "fixture": False,
                    "scope": "real",
                    "reason": "no real bridge is available on this host: podman is not installed",
                },
            ),
        )
        monkeypatch.setattr(
            A,
            "capability_report",
            lambda **kwargs: {
                "bridges": [
                    {"id": "container-gui", "available": False, "reason": "podman is missing"}
                ],
                "capabilities": [
                    {
                        "name": "type_text",
                        "available": False,
                        "reason": "unavailable: podman is not installed",
                        "missing_capability": "podman (a rootless container runtime)",
                    }
                ],
                "verdict": "blocked",
            },
        )
        args = {
            "actions": [
                {
                    "kind": "type_text",
                    "payload": {"text": "x"},
                    "verify": {"expect_observe_contains": "x"},
                }
            ],
            "end_state": {"expect_observe_contains": "x"},
        }
        outcome = R.run_gui_step(_gui_step(), args)
        assert outcome.blocked is True
        assert outcome.exit_code == R.EXIT_BLOCKED
        assert "podman (a rootless container runtime)" in outcome.outputs["missing_capability"]
        assert "podman (a rootless container runtime)" in outcome.detail
        assert outcome.outputs["executed"] == 0

    def test_an_unknown_runner_kind_is_refused_with_the_known_names(self):
        outcome = R.run_gui_step(
            _gui_step(), {**_inline_arguments(), "runner_kind": "teleport"}
        )
        assert outcome.failure_class == R.FAILURE_USAGE
        assert outcome.exit_code == R.EXIT_USAGE
        assert "teleport" in outcome.detail and "container-gui" in outcome.detail

    @pytest.mark.parametrize(
        "target,arguments,expected",
        [
            ("", {}, "needs either a stored sub-tool"),
            ("inline", {}, "needs either a stored sub-tool"),
            (
                "probe",
                {"actions": [_action("a")], "end_state": {"expect_observe_contains": "x"}},
                "declares both a sub-tool",
            ),
            ("inline", {"actions": [_action("a")]}, "needs an `end_state` object"),
            (
                "inline",
                {"actions": [_action("a")], "end_state": {"channel": "window_title"}},
                "declares no expectation",
            ),
            ("inline", {"unexpected": 1}, "unsupported gui field(s)"),
            (
                "inline",
                {
                    "actions": ["not-an-object"],
                    "end_state": {"expect_observe_contains": "x"},
                },
                "must be a list of declared action objects",
            ),
        ],
    )
    def test_a_gui_declaration_that_cannot_be_run_is_refused(self, target, arguments, expected):
        outcome = R.run_gui_step(_gui_step(target), arguments)
        assert outcome.failure_class == R.FAILURE_USAGE
        assert outcome.exit_code == R.EXIT_USAGE
        assert expected in outcome.detail, outcome.detail

    def test_a_real_bridge_that_vanishes_is_not_swallowed(self, monkeypatch):
        """A crashing bridge is a failed step, never an ok one."""

        class Exploding:
            isolation = "container"

            def observe(self):
                raise RuntimeError("the display died")

        monkeypatch.setattr(
            A,
            "run_action_list",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the display died")),
        )
        outcome = R.run_gui_step(_gui_step(), _inline_arguments(), act_runner=Exploding())
        assert outcome.ok is False and outcome.blocked is False
        assert outcome.failure_class == R.FAILURE_RUNNER
        assert "the display died" in outcome.detail

    def test_every_declared_output_key_is_always_present(self, tmp_path):
        args = _inline_arguments()
        outcome = R.run_gui_step(
            _gui_step(),
            {**args, "state": tmp_path},
            runtime=tmp_path,
            act_runner=_fixture_runner(args["actions"], "AFTER-WINDOW"),
            allow_fixture=True,
        )
        assert set(outcome.outputs) == set(R.GUI_OUTPUT_KEYS)

    def test_the_runner_closes_a_runner_it_resolved_itself(self, monkeypatch, tmp_path):
        closed: list[str] = []

        class Fake:
            isolation = "container"

            def close(self):
                closed.append("closed")

        monkeypatch.setattr(
            A,
            "resolve_runner",
            lambda kind="auto", **kwargs: (
                Fake(),
                {
                    "fixture": False,
                    "scope": "real",
                    "isolation": "container",
                    "bridge": "container-gui",
                    "real": True,
                    "reason": "",
                },
            ),
        )
        monkeypatch.setattr(
            A,
            "run_action_list",
            lambda *a, **k: {
                "status": "verified",
                "live": True,
                "executed": 1,
                "actions": [],
                "end_state": {"ok": True},
                "exit_code": 0,
                "reason": "verified",
                "isolation": "container",
                "scope": "real",
            },
        )
        outcome = R.run_gui_step(_gui_step(), _inline_arguments(), runtime=tmp_path)
        assert outcome.ok is True
        assert closed == ["closed"], "a container resolved per step must not leak"

    def test_a_reference_inside_a_declared_action_is_substituted_from_the_context(
        self, monkeypatch, tmp_path
    ):
        seen: dict = {}

        def fake_run(actions, **kwargs):
            seen["actions"] = [dict(item) for item in actions]
            seen["end_state"] = kwargs.get("end_state")
            return {
                "status": "verified",
                "live": True,
                "executed": 1,
                "actions": [],
                "end_state": {"ok": True},
                "exit_code": 0,
                "reason": "verified",
                "isolation": "container",
                "scope": "real",
            }

        class Real:
            isolation = "container"

        monkeypatch.setattr(A, "run_action_list", fake_run)
        args = {
            "actions": [
                {
                    "kind": "type_text",
                    "payload": {"text": "$steps.fetch.text"},
                    "verify": {"expect_observe_contains": "$steps.fetch.text"},
                }
            ],
            "end_state": {"expect_observe_contains": "$steps.fetch.text"},
        }
        outcome = R.run_gui_step(
            _gui_step(),
            args,
            context={"input": {}, "steps": {"fetch": {"text": "hello skaldir"}}},
            runtime=tmp_path,
            act_runner=Real(),
        )
        assert outcome.ok is True, outcome.detail
        assert seen["actions"][0]["payload"]["text"] == "hello skaldir"
        assert seen["actions"][0]["verify"]["expect_observe_contains"] == "hello skaldir"
        assert seen["end_state"]["expect_observe_contains"] == "hello skaldir"

    def test_a_reference_inside_a_declared_action_without_a_context_is_refused(self):
        args = {
            "actions": [
                {
                    "kind": "type_text",
                    "payload": {"text": "$steps.fetch.text"},
                    "verify": {"expect_observe_contains": "x"},
                }
            ],
            "end_state": {"expect_observe_contains": "x"},
        }
        outcome = R.run_gui_step(_gui_step(), args)
        assert outcome.failure_class == R.FAILURE_USAGE
        assert outcome.exit_code == R.EXIT_USAGE
        assert "$steps.fetch.text" in outcome.detail
        assert outcome.outputs["executed"] == 0

    def test_an_injected_runner_is_never_closed_by_the_step_runner(self, monkeypatch, tmp_path):
        closed: list[str] = []

        class Mine:
            isolation = "container"

            def close(self):
                closed.append("closed")

        monkeypatch.setattr(
            A,
            "run_action_list",
            lambda *a, **k: {
                "status": "verified",
                "live": True,
                "executed": 1,
                "actions": [],
                "end_state": {"ok": True},
                "exit_code": 0,
                "reason": "verified",
                "isolation": "container",
                "scope": "real",
            },
        )
        outcome = R.run_gui_step(
            _gui_step(), _inline_arguments(), runtime=tmp_path, act_runner=Mine()
        )
        assert outcome.ok is True
        assert closed == [], "the caller owns the runner it passed in"


# --------------------------------------------------------------------------- #
# the registry the COMPOSE layer imports                                         #
# --------------------------------------------------------------------------- #


class TestRegistry:
    def test_the_registry_exposes_exactly_the_compose_step_kinds(self):
        registry = R.runner_registry()
        assert list(registry) == list(R.RUNNER_KINDS)
        assert set(R.runner_registry()) == set(STEP_KINDS) == {"tool", "docker", "api", "gui"}

    def test_api_and_gui_have_a_runner_now_and_the_other_two_keep_their_owner(self):
        report = R.registry_report()
        assert set(report) == set(STEP_KINDS)
        for kind, row in report.items():
            assert row["kind"] == kind
            assert row["executable"] is True, kind
            assert row["reason"] == "", kind
            assert row["runner"], kind
        assert report["api"]["owner"] == "skaldir.step_runners"
        assert report["gui"]["owner"] == "skaldir.step_runners"
        assert report["tool"]["owner"] == "skaldir.compose_service"
        assert report["docker"]["owner"] == "skaldir.compose_service"
        assert R.runner_for("api").implemented is True
        assert R.runner_for("gui").implemented is True

    def test_the_registry_table_builds_the_compose_layers_own_executor_rows(self):
        """The hand-over is 3 lines: this is the row the COMPOSE layer builds from it."""
        built = {
            kind: KindExecutor(row["kind"], row["runner"], row["reason"])
            for kind, row in R.registry_report().items()
        }
        assert all(item.implemented for item in built.values())
        assert [item.to_dict()["executable"] for item in built.values()] == [True] * 4
        assert all(item.to_dict()["reason"] == "" for item in built.values())

    def test_a_composite_with_api_and_gui_steps_validates_as_executable(self, tmp_path):
        """The COMPOSE layer's own validator, fed by this registry's executor table."""
        from skaldir import compose_service as C
        from skaldir.tools import ToolStore

        store = ToolStore(tmp_path / "tools.db")

        document = {
            "name": "chain_probe",
            "inputs": {},
            "outputs": ["status_code", "status"],
            "precondition": "always",
            "end_state": "$steps.inspect.status present",
            "steps": [
                {
                    "id": "fetch",
                    "kind": "api",
                    # a literal url: compose's own api validation requires http(s) here
                    "url": "http://127.0.0.1:9/x",
                    "method": "GET",
                    "outputs": ["status_code", "text"],
                    "precondition": "always",
                    "end_state": "$steps.fetch.status_code >= 100",
                },
                {
                    "id": "inspect",
                    "kind": "gui",
                    "target": "inline",
                    "outputs": ["status"],
                    # an inline flow is a declaration: the actions, and the end state that is
                    # read back from the real display
                    "inputs": {
                        "actions": [
                            {
                                "kind": "cli",
                                "channel": "api",
                                "argv": ["sh", "-c", "echo CW"],
                                "verify": {"expect_stdout_contains": "CW"},
                            }
                        ],
                        "end_state": {
                            "channel": "window_title",
                            "expect_observe_contains": "CW",
                        },
                    },
                    "precondition": "exit_ok",
                    "end_state": "$steps.inspect.status != ''",
                },
            ],
        }
        comp = C.parse_composite(document)
        try:
            after = C.validate_composite(store, comp)
            payload = after.to_dict()
        finally:
            store.close()
        # The COMPOSE table is the product's own now (no monkeypatching it into place): a
        # chain of api + gui steps validates as executable, and both tables name the same
        # runner functions for the two kinds this registry supplies.
        assert after.missing_kinds == []
        assert after.executable is True
        assert payload["executors"]["api"]["executable"] is True
        assert payload["executors"]["gui"]["executable"] is True
        assert payload["executors"]["api"]["reason"] == ""
        assert "run_api_step" in payload["executors"]["api"]["runner"]
        assert "act_service" in payload["executors"]["gui"]["runner"]
        registry = R.registry_report()
        assert registry["api"]["executable"] is True and registry["gui"]["executable"] is True
        for kind, name in (("api", "run_api_step"), ("gui", "run_gui_step")):
            assert name in registry[kind]["runner"]
            assert name in payload["executors"][kind]["runner"]

    def test_run_step_dispatches_by_kind_and_refuses_an_unknown_one(self):
        outcome = R.run_step("teleport", _api_step("http://stub.local/x"))
        assert outcome.blocked is True
        assert outcome.missing_kind == "teleport"
        assert "unknown step kind" in outcome.detail
        assert outcome.failure_class == R.FAILURE_USAGE

    def test_a_delegated_kind_keeps_its_own_binding_rule(self):
        step = SimpleNamespace(id="echo", kind="docker", argv=["echo", "hi"], image="alpine",
                               image_digest="sha256:" + "a" * 64, timeout_s=5.0)
        with pytest.raises(R.RunnerRefused) as excinfo:
            R.runner_for("docker").run(step, {"text": "not the docker way"})
        assert "binds through its declaration" in str(excinfo.value)
        with pytest.raises(R.RunnerRefused) as excinfo:
            R.runner_for("tool").run(
                SimpleNamespace(id="t", kind="tool", target="some_tool"), {"a": 1}
            )
        assert "binds through its declaration" in str(excinfo.value)

    def test_a_tool_step_without_its_registry_is_refused(self):
        step = SimpleNamespace(id="t", kind="tool", target="some_tool", output=[])
        with pytest.raises(R.RunnerRefused) as excinfo:
            R.runner_for("tool").run(step, {})
        assert "tool store" in str(excinfo.value)

    def test_the_output_key_vocabulary_is_published_per_kind(self):
        assert R.output_keys_for("api") == R.API_OUTPUT_KEYS
        assert R.output_keys_for("GUI") == R.GUI_OUTPUT_KEYS
        assert R.output_keys_for("docker") == ()

    def test_as_step_tuple_carries_the_compose_convention(self, server):
        outcome = R.run_api_step(_api_step(f"{server['base']}/hello"), {"expect_status": 200})
        ok, outputs, raw, detail, missing = R.as_step_tuple(outcome)
        assert ok is True
        assert outputs == outcome.outputs
        assert raw == outcome.raw
        assert missing == ""
        assert "HTTP 200 answered" in detail
        blocked = R.run_step("teleport", _api_step("http://stub.local/x"))
        ok, _outputs, _raw, _detail, missing = R.as_step_tuple(blocked)
        assert ok is False and missing == "teleport"

    def test_the_host_facts_report_what_each_kind_can_really_do(self):
        facts = R.environment_facts()
        assert set(facts["kinds"]) == set(R.RUNNER_KINDS)
        for kind, row in facts["kinds"].items():
            assert row["available"] is True, (kind, row["detail"])
            assert row["runner"]
        assert "bridges" in facts
