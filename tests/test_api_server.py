"""The API transport's generated pieces: routes, argv, envelope, status map, OpenAPI document.

No HTTP server and (with two exceptions) no command subprocess runs here — that is
``tests/test_api_server_http.py``. This file pins what the transport is *derived from*, so a drift
between the CLI contract and the HTTP surface fails here, cheaply and precisely:

* one route per CLI leaf, generated from ``build_parser()`` + ``cli_contract.COMMANDS``
* the exclusions (commands that cannot be a request/response route) are declared with a reason
* the exit-code → HTTP-status map is exactly the documented one and is total
* the OpenAPI document is derived from the same routes (bijection, per-command request schema,
  per-command ``data`` schema, resolvable refs)
* the guards: token file/env/generated, constant-time compare, loopback-only bind, body limit,
  refusable port, redacted logs
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

import pytest

from skaldir import api_server, api_server_cli, cli, cli_contract


def _free_port() -> int:
    """A port nothing is listening on right now (bound, then released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])

# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _register(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Do what the CLI owner's two-line paste does, on the parser we were handed.

    ``cli.build_parser()`` has already run ``cli_contract.annotate()`` by the time a test gets
    the parser, while the real paste sits *before* it. Re-annotating reproduces the real order's
    effect (each leaf gets its ``command_path``); ``annotate`` is idempotent by construction.
    """
    action = next(
        item for item in parser._actions  # noqa: SLF001
        if isinstance(item, argparse._SubParsersAction)  # noqa: SLF001
    )
    api_server_cli.register_cli(action)
    cli_contract.annotate(parser)
    return parser


@pytest.fixture(scope="module")
def wired_parser() -> argparse.ArgumentParser:
    """The real product parser with the API group registered (cli.py is not edited by us)."""
    return _register(cli.build_parser())


@pytest.fixture(scope="module")
def route_list(wired_parser: argparse.ArgumentParser) -> list[api_server.ApiRoute]:
    return api_server.routes(wired_parser)


@pytest.fixture(scope="module")
def document(route_list: list[api_server.ApiRoute]) -> dict:
    return api_server.openapi_document(route_list)


def _leaf_paths(parser: argparse.ArgumentParser) -> set[str]:
    return {path for path, _sub in cli_contract.leaf_commands(parser)}


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


def test_one_route_per_cli_leaf_minus_the_declared_exclusions(
    wired_parser: argparse.ArgumentParser, route_list: list[api_server.ApiRoute]
) -> None:
    leaves = _leaf_paths(wired_parser)
    exposed = {route.command for route in route_list}
    assert exposed == leaves - set(api_server.NON_HTTP)
    assert len(route_list) == len(exposed), "a command is exposed twice"
    assert len({route.path for route in route_list}) == len(route_list), "duplicate route path"
    assert api_server.routes(wired_parser) == route_list


def test_the_three_new_commands_are_in_the_route_table(
    wired_parser: argparse.ArgumentParser, route_list: list[api_server.ApiRoute]
) -> None:
    commands = {route.command for route in route_list}
    assert {"daemon.status"} <= commands, "the read-only probe must be reachable over HTTP"
    # serve/daemon.serve are the server itself and are excluded *with a reason*
    excluded = {item["command"] for item in api_server.exclusions(wired_parser)}
    assert {"serve", "daemon.serve"} <= excluded
    assert "daemon.status" not in excluded, "the probe is a normal read-only route"


def test_every_exclusion_states_a_reason_and_its_presence(
    wired_parser: argparse.ArgumentParser,
) -> None:
    leaves = _leaf_paths(wired_parser)
    entries = api_server.exclusions(wired_parser)
    assert entries, "an exclusion list that is empty hides the decision"
    for entry in entries:
        assert entry["reason"].strip(), f"{entry['command']} is excluded without a reason"
        assert entry["present_in_parser"] is (entry["command"] in leaves)
    assert {entry["command"] for entry in entries if entry["present_in_parser"]} == set(
        api_server.NON_HTTP
    ) & leaves


def test_route_metadata_comes_from_the_contract(route_list: list[api_server.ApiRoute]) -> None:
    for route in route_list:
        spec = cli_contract.spec_for(route.command)
        assert route.layer == spec.layer
        assert route.description == spec.description
        assert route.mutates == spec.mutates
        assert route.requires_credentials == spec.requires_credentials
        assert route.exit_codes == tuple(spec.exit_codes)
        assert route.data_schema == (spec.data_schema or cli_contract.NO_SCHEMA)


def test_the_path_is_the_command(route_list: list[api_server.ApiRoute]) -> None:
    for route in route_list:
        assert route.path == f"{api_server.API_PREFIX}/{route.command.replace('.', '/')}"
        # the inverse the error envelopes use must round-trip
        assert api_server._command_from_path(route.path) == route.command  # noqa: SLF001


def test_the_command_line_is_built_from_the_route_arguments(
    route_list: list[api_server.ApiRoute],
) -> None:
    by_command = {route.command: route for route in route_list}
    argv = api_server.build_argv(by_command["tools.show"], {"name": "nope"})
    assert argv == ["tools", "show", "nope", "--json", "--no-color"]

    argv = api_server.build_argv(
        by_command["memory-retrieval-check"],
        {"problem": "loops", "limit": 3, "min_confidence": 0.25, "verbose": True},
    )
    assert argv[:5] == ["memory-retrieval-check", "loops", "--limit", "3", "--min-confidence"]
    assert "0.25" in argv and "--verbose" in argv
    assert argv[-2:] == ["--json", "--no-color"]


def test_a_flag_sent_as_false_is_simply_not_passed(
    route_list: list[api_server.ApiRoute],
) -> None:
    for route in route_list:
        probe = next((spec for spec in route.args if spec["type"] == "flag"), None)
        if probe is None or any(spec["required"] for spec in route.args):
            continue
        assert probe["name"] not in api_server.build_argv(route, {probe["dest"]: False})
        assert probe["name"] in api_server.build_argv(route, {probe["dest"]: True})
        return
    pytest.skip("no argument-free route carries a boolean flag in this build")


def test_repeatable_arguments_repeat_the_flag(route_list: list[api_server.ApiRoute]) -> None:
    by_command = {route.command: route for route in route_list}
    selftest = by_command["selftest"]
    if selftest.arg("layer") is None:  # pragma: no cover — the CLI owns this flag
        pytest.skip("selftest lost its --layer flag")
    argv = api_server.build_argv(selftest, {"layer": ["ACT", "VERIFY"]})
    assert argv.count("--layer") == 2
    assert "ACT" in argv and "VERIFY" in argv


def test_unknown_and_missing_arguments_are_refused_before_anything_runs(
    route_list: list[api_server.ApiRoute],
) -> None:
    by_command = {route.command: route for route in route_list}
    with pytest.raises(api_server.ApiRequestError) as unknown:
        api_server.build_argv(by_command["tools.show"], {"nope": 1})
    assert "unknown argument" in str(unknown.value)

    with pytest.raises(api_server.ApiRequestError) as missing:
        api_server.build_argv(by_command["tools.show"], {})
    assert "requires" in str(missing.value)

    with pytest.raises(api_server.ApiRequestError):
        api_server.build_argv(by_command["memory-retrieval-check"], {"limit": "many"})

    with pytest.raises(api_server.ApiRequestError):
        api_server.build_argv(by_command["tools.list"], {"runtime": True})


def test_a_choice_outside_the_choices_is_refused(route_list: list[api_server.ApiRoute]) -> None:
    by_command = {route.command: route for route in route_list}
    route = by_command.get("registry.status")
    spec = route.arg("format") if route else None
    assert spec is None, "the server owns --format; it must not be a route argument"
    with pytest.raises(api_server.ApiRequestError):
        api_server.build_argv(by_command["tools.list"], {"format": "yaml"})


def test_mutually_exclusive_flags_are_refused(route_list: list[api_server.ApiRoute]) -> None:
    by_command = {route.command: route for route in route_list}
    route = by_command["registry.migrate"]
    if not route.mutually_exclusive:  # pragma: no cover — the CLI owns this group
        pytest.skip("registry.migrate has no mutually exclusive group")
    with pytest.raises(api_server.ApiRequestError) as exc:
        api_server.build_argv(route, {"dry_run": True, "apply": True})
    assert "only one of" in str(exc.value)


def test_the_request_body_accepts_the_mapping_or_its_args_wrapper() -> None:
    assert api_server.body_arguments({"name": "x"}) == {"name": "x"}
    assert api_server.body_arguments({"args": {"name": "x"}}) == {"name": "x"}
    assert api_server.body_arguments({"name": "x", "_stream": True}) == {"name": "x"}
    assert api_server.body_arguments(None) == {}
    with pytest.raises(api_server.ApiRequestError):
        api_server.body_arguments([1, 2, 3])


def test_the_stream_switch_is_request_metadata_not_an_argument() -> None:
    remaining, requested = api_server._consume_stream_flag(  # noqa: SLF001
        {"name": "x", "_stream": True}
    )
    assert (remaining, requested) == ({"name": "x"}, True)
    remaining, requested = api_server._consume_stream_flag(  # noqa: SLF001
        {"name": "x", "stream": "1"}
    )
    assert (remaining, requested) == ({"name": "x"}, True)
    remaining, requested = api_server._consume_stream_flag(  # noqa: SLF001
        {"name": "x", "_stream": False}
    )
    assert (remaining, requested) == ({"name": "x"}, False)
    remaining, requested = api_server._consume_stream_flag({"name": "x"})  # noqa: SLF001
    assert (remaining, requested) == ({"name": "x"}, False)
    assert api_server._consume_stream_flag(None) == (None, False)  # noqa: SLF001


# --------------------------------------------------------------------------- #
# the envelope and the status map
# --------------------------------------------------------------------------- #


def test_the_http_status_map_is_exactly_the_documented_one() -> None:
    assert api_server.HTTP_STATUS_BY_EXIT == {
        0: 200, 1: 400, 2: 422, 3: 409, 4: 401, 5: 404, 6: 409, 7: 500, 8: 504,
    }
    assert set(api_server.HTTP_STATUS_BY_EXIT) == set(cli_contract.EXIT_NAMES)
    for code in cli_contract.EXIT_NAMES:
        assert api_server.status_for_exit(code) == api_server.HTTP_STATUS_BY_EXIT[code]
    assert api_server.status_for_exit(99) == 500, "an unknown code must not look like success"


def test_a_server_envelope_is_the_contract_envelope() -> None:
    envelope = api_server.server_envelope(
        "tools.show", cli_contract.EXIT_NOT_FOUND, message="unknown tool: nope"
    )
    assert envelope["schema_version"] == cli_contract.SCHEMA_VERSION
    assert envelope["ok"] is False
    assert envelope["exit_code"] == 5
    assert envelope["error"]["code"] == "not_found"
    assert envelope["error"]["message"] == "unknown tool: nope"
    assert set(envelope) >= {
        "schema_version", "ok", "command", "godsvoice_version", "started_at", "duration_ms",
        "exit_code", "data", "error", "evidence", "warnings", "next_actions",
    }


def test_a_refusal_carries_no_secret_and_a_success_carries_evidence() -> None:
    ok = api_server.server_envelope(
        "api.health", cli_contract.EXIT_OK, data={"status": "ok"},
        evidence=[{"ref": "src/skaldir/api_server.py", "kind": "code", "note": "route table"}],
    )
    assert ok["ok"] is True and ok["evidence"]
    # the contract's own rule: a success without evidence is an internal error, not a success
    stripped = cli_contract.build_envelope(command="x", exit_code=0, data=None)
    assert stripped["ok"] is False and stripped["exit_code"] == cli_contract.EXIT_INTERNAL


def test_commands_run_without_the_transport_secrets() -> None:
    env = api_server.child_env({"GODSV_FORMAT": "json", api_server.TOKEN_ENV: "secret",
                                "PATH": "/usr/bin"})
    assert "GODSV_FORMAT" not in env, "the server owns the output format"
    assert api_server.TOKEN_ENV not in env, "a command has no business seeing the API token"
    assert env["PATH"] == "/usr/bin" and env["PYTHONUNBUFFERED"] == "1"


# --------------------------------------------------------------------------- #
# the OpenAPI document
# --------------------------------------------------------------------------- #


def test_the_document_is_the_routes(
    route_list: list[api_server.ApiRoute], document: dict
) -> None:
    assert document["openapi"] == "3.1.0"
    assert len(document["paths"]) == len(route_list)
    assert {path for path in document["paths"]} == {route.path for route in route_list}
    seen_ids: set[str] = set()
    mutating_with_security = 0
    for route in route_list:
        operations = document["paths"][route.path]
        commands = {operation.get("x-godsv-command") for operation in operations.values()}
        assert commands == {route.command}
        for operation in operations.values():
            assert operation["operationId"] not in seen_ids
            seen_ids.add(operation["operationId"])
            assert operation["responses"]
            assert operation["x-godsv-data-schema"] == route.data_schema
            assert operation["x-godsv-exit-codes"] == list(route.exit_codes)
            assert operation["x-godsv-http-status-by-exit"] == {
                str(key): value for key, value in sorted(api_server.HTTP_STATUS_BY_EXIT.items())
            }
            if route.mutates:
                assert operation.get("security") == [{"bearerAuth": []}], route.command
            else:
                assert operation.get("security") == [], route.command
        if route.mutates:
            mutating_with_security += 1
            assert list(operations) == ["post"], "a mutating command must be POST-only"
        else:
            assert sorted(operations) == ["get", "post"], (
                f"{route.command} is read-only and should also answer GET"
            )
    assert mutating_with_security == sum(1 for route in route_list if route.mutates)


def test_every_request_body_schema_matches_the_cli_arguments(
    document: dict, route_list: list[api_server.ApiRoute]
) -> None:
    by_command = {route.command: route for route in route_list}
    checked = 0
    for path, operations in document["paths"].items():
        schema = (operations["post"].get("requestBody") or {}).get("content", {}).get(
            "application/json", {}).get("schema")
        command = operations["post"]["x-godsv-command"]
        route = by_command[command]
        # One JSON object carries one key per ``dest``: a dest with two argparse actions
        # (``--headless``/``--interactive``, ``--digest``/``--image-digest``,
        # ``--progress``/``--no-progress``, a positional ``path`` next to ``--path``) is
        # ONE body property. Compare the dests as a set, so a *missing* argument still
        # fails while a repeated dest does not.
        expected = {item["dest"] for item in route.args}
        if schema is None:
            assert not expected, f"{path} has no body schema but {len(expected)} arguments"
            continue
        assert set(schema["properties"]) == expected, path
        assert sorted(schema.get("required", [])) == sorted(
            item["dest"] for item in route.args if item["required"]
        ), path
        assert schema["additionalProperties"] is False
        checked += 1
    with_args = [route for route in route_list if route.args]
    assert checked == len(with_args), "every route with arguments needs a body schema"


def test_every_declared_exit_code_has_its_status_in_the_responses(
    document: dict, route_list: list[api_server.ApiRoute]
) -> None:
    by_command = {route.command: route for route in route_list}
    for path, operations in document["paths"].items():
        operation = operations["post"]
        command = operation["x-godsv-command"]
        route = by_command[command]
        statuses = set(operation["responses"])
        for code in route.exit_codes:
            assert str(api_server.status_for_exit(code)) in statuses, (path, code)
        if route.mutates:
            assert "401" in statuses, f"{path} mutates but never declares the token refusal"
        assert "default" in statuses, (
            f"{path} must admit any exit code the command really returns"
        )
        assert set(statuses) - {"default"} <= {
            str(value) for value in api_server.HTTP_STATUS_BY_EXIT.values()
        }


def test_every_reference_resolves(document: dict) -> None:
    text = json.dumps(document)
    refs = {token for token in text.split('"$ref": "') if token.startswith("#/")}
    components = document["components"]["schemas"]
    for ref in refs:
        name = ref.split('"')[0].split("/")[-1]
        assert name in components, f"dangling $ref {ref}"


def test_the_document_excludes_the_commands_it_cannot_serve(document: dict) -> None:
    excluded = {item["command"]: item["reason"] for item in document["x-godsv-excluded"]}
    assert excluded
    for command, reason in excluded.items():
        assert reason.strip()
        assert all(command not in path for path in document["paths"])
    assert document["x-godsv-token-required"] is True


def test_the_document_validates_in_this_repo(
    document: dict, route_list: list[api_server.ApiRoute]
) -> None:
    assert api_server.validate_document(document, route_list) == []
    # and dropping one route from the table makes the same document invalid — proof the check
    # compares against the table instead of always agreeing
    reduced = [route for route in route_list if route.command != "daemon.status"]
    assert api_server.validate_document(document, reduced) != []


def test_the_document_loads_in_schemathesis(document: dict) -> None:
    """The tier MVP/08 §0 was blocked on: the product's own document feeds Schemathesis."""
    schemathesis = pytest.importorskip("schemathesis")
    schema = schemathesis.openapi.from_dict(document)
    operations = list(schema.get_all_operations())
    expected = sum(len(operations_) for operations_ in document["paths"].values())
    assert len(operations) == expected
    assert all(item.ok() for item in operations), "Schemathesis rejected part of the document"


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


def test_the_token_comes_from_the_file_env_or_generation(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("f" * 40 + "\n", encoding="utf-8")
    token, source = api_server.resolve_token(token_file)
    assert (token, source) == ("f" * 40, "file")

    token, source = api_server.resolve_token(None, env={api_server.TOKEN_ENV: "e" * 20})
    assert (token, source) == ("e" * 20, "env")

    generated, source = api_server.resolve_token(None, env={})
    assert source == "generated" and len(generated) >= 32
    assert api_server.resolve_token(None, env={})[0] != generated, "two servers share no token"


def test_a_bad_token_file_is_refused_with_a_reason(tmp_path: Path) -> None:
    with pytest.raises(api_server.ApiServerUnavailable) as missing:
        api_server.resolve_token(tmp_path / "nope")
    assert "cannot read the token file" in str(missing.value)

    short = tmp_path / "short"
    short.write_text("abc\n", encoding="utf-8")
    with pytest.raises(api_server.ApiServerUnavailable) as exc:
        api_server.resolve_token(short)
    assert "too short" in str(exc.value)


def test_the_presented_token_is_read_from_headers_only() -> None:
    assert api_server.presented_token({"Authorization": "Bearer abc123"}) == "abc123"
    assert api_server.presented_token({"authorization": "bearer abc123"}) == "abc123"
    assert api_server.presented_token({"X-Godsv-Token": "abc123"}) == "abc123"
    assert api_server.presented_token({}) is None
    assert api_server.presented_token({"Authorization": "Basic abc123"}) == ""
    # a token in a query string is never even read (it would land in every access log)
    assert api_server.presented_token({"Host": "x", ":path": "/?token=abc"}) is None


def test_the_comparison_is_constant_time_and_fails_closed() -> None:
    assert api_server.token_matches("abc", "abc") is True
    assert api_server.token_matches("abc", "abd") is False
    assert api_server.token_matches("abc", "") is False
    assert api_server.token_matches("anything", "") is False, "no token must never authorize"
    assert api_server.token_matches("", "abc") is False


def test_only_mutating_commands_need_a_token(route_list: list[api_server.ApiRoute]) -> None:
    read_only = next(route for route in route_list if not route.mutates)
    mutating = next(route for route in route_list if route.mutates)
    token = "t" * 32

    assert api_server.authorization_failure(read_only, {}, token) is None
    assert api_server.authorization_failure(mutating, {}, token) is not None

    refusal = api_server.authorization_failure(mutating, {}, token)
    assert refusal is not None
    assert refusal["exit_code"] == 4
    assert refusal["error"]["code"] == "needs_credentials"
    assert refusal["error"]["details"]["reason"] == "missing_token"
    assert api_server.status_for_exit(refusal["exit_code"]) == 401

    wrong = api_server.authorization_failure(mutating, {"Authorization": "Bearer nope"}, token)
    assert wrong is not None
    assert wrong["error"]["details"]["reason"] == "invalid_token"
    assert api_server.authorization_failure(
        mutating, {"Authorization": f"Bearer {token}"}, token
    ) is None

    # a server started without a token refuses every mutation (fail closed, and it says why)
    fail_closed = api_server.authorization_failure(mutating, {"Authorization": "Bearer x"}, "")
    assert fail_closed is not None
    assert fail_closed["error"]["details"]["reason"] == "no_token_configured"
    assert fail_closed["exit_code"] == cli_contract.EXIT_NEEDS_CREDENTIALS


# --------------------------------------------------------------------------- #
# logs, loopback, port
# --------------------------------------------------------------------------- #


def test_the_token_never_reaches_a_log_line() -> None:
    token = "s3cr3t-token-value"
    record = "GET /cli/tools/show HTTP/1.1 Authorization: Bearer " + token
    assert token not in api_server.redact(record, [token])
    assert api_server.REDACTED in api_server.redact(record, [token])
    formatter = api_server.RedactingFormatter("%(message)s", None, [token])
    import logging

    entry = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                              "call with token=%s", (token,), None)
    assert token not in formatter.format(entry)
    assert token not in api_server.redact(f"token {token}")


def test_only_loopback_is_bound_without_the_explicit_flag() -> None:
    for host in ("127.0.0.1", "::1", "localhost", "127.0.0.5"):
        api_server.check_bind_allowed(host, allow_remote=False)
    for host in ("0.0.0.0", "::", "192.168.1.10", "example.com"):
        with pytest.raises(api_server.ApiServerUnavailable) as exc:
            api_server.check_bind_allowed(host, allow_remote=False)
        assert "loopback" in str(exc.value)
        assert exc.value.exit_code == cli_contract.EXIT_USAGE
        api_server.check_bind_allowed(host, allow_remote=True)


def test_a_taken_port_is_refused_with_a_clear_message() -> None:
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    try:
        with pytest.raises(api_server.ApiServerUnavailable) as exc:
            api_server.bind_listener("127.0.0.1", port)
        message = str(exc.value)
        assert "already in use" in message and str(port) in message
        assert "--port" in exc.value.hint
        assert exc.value.details["errno"] in (98, 48)  # EADDRINUSE
    finally:
        held.close()
    # and once it is free again the same call succeeds
    probe = api_server.bind_listener("127.0.0.1", 0)
    try:
        assert probe.getsockname()[1] > 0
    finally:
        probe.close()


# --------------------------------------------------------------------------- #
# the app object (no server, no command subprocess)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def client(route_list: list[api_server.ApiRoute]) -> object:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    config = api_server.ServerConfig(
        token="t" * 32, token_source="file", route_map={route.command: route
                                                       for route in route_list},
    )
    return TestClient(api_server.build_app(config), raise_server_exceptions=False)


def test_health_is_a_real_envelope(
    client: object, route_list: list[api_server.ApiRoute]
) -> None:
    response = client.get(api_server.HEALTH_PATH)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["command"] == "api.health"
    assert body["evidence"], "a success without evidence is a contract violation"
    assert body["data"]["routes"] == len(route_list)
    assert body["data"]["token_required_for_mutations"] is True
    assert response.headers["X-Godsv-Exit-Code"] == "0"
    assert response.headers["Cache-Control"] == "no-store"


def test_the_served_openapi_document_is_the_generated_one(client: object, document: dict) -> None:
    response = client.get(api_server.OPENAPI_PATH)
    assert response.status_code == 200
    assert response.json() == document


def test_the_root_page_lists_the_routes(client: object) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "/cli/tools/show" in response.text


def test_an_unknown_route_answers_with_the_envelope(client: object) -> None:
    response = client.get("/cli/nope/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["exit_code"] == 5 and body["error"]["code"] == "not_found"
    assert body["command"] == "nope.nope"


def test_a_wrong_method_answers_with_the_envelope(client: object) -> None:
    response = client.delete("/cli/tools/show")
    assert response.status_code == 400
    body = response.json()
    assert body["exit_code"] == 1 and body["error"]["code"] == "usage"
    assert "not allowed" in body["error"]["message"]


def test_a_mutating_call_without_a_token_is_401_and_runs_nothing(client: object) -> None:
    response = client.post("/cli/completion/begin", json={"goal": "x"})
    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False and body["exit_code"] == 4
    assert body["error"]["code"] == "needs_credentials"
    assert body["error"]["details"]["reason"] == "missing_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_mutating_call_with_a_wrong_token_is_401(client: object) -> None:
    response = client.post("/cli/completion/begin", json={"goal": "x"},
                           headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert response.json()["error"]["details"]["reason"] == "invalid_token"


def test_a_bad_json_body_is_400_and_no_command_runs(client: object) -> None:
    response = client.post("/cli/completion/begin", content=b"{not json",
                           headers={"Authorization": "Bearer " + "t" * 32,
                                    "Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "usage"


def test_an_unknown_argument_is_400_and_no_command_runs(client: object) -> None:
    response = client.post("/cli/completion/begin", json={"nope": 1},
                           headers={"Authorization": "Bearer " + "t" * 32})
    assert response.status_code == 400
    assert "unknown argument" in response.json()["error"]["message"]


def test_an_oversized_body_is_refused_by_the_limit(client: object) -> None:
    payload = {"goal": "x" * (api_server.DEFAULT_MAX_BODY_BYTES + 1)}
    response = client.post("/cli/completion/begin", json=payload,
                           headers={"Authorization": "Bearer " + "t" * 32})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["details"]["reason"] == "payload_too_large"
    assert body["error"]["details"]["limit_bytes"] == api_server.DEFAULT_MAX_BODY_BYTES


# --------------------------------------------------------------------------- #
# the hand-over contract with the CLI owner
# --------------------------------------------------------------------------- #


def test_the_contract_entries_are_complete_enough_for_the_dump_invariant() -> None:
    for command in api_server_cli.CONTRACT_ENTRIES:
        record = cli_contract.spec_for(command).as_dict()
        assert record["layer"] in set(cli_contract.LAYERS) | {"kernel"}
        assert record["exit_codes"] and all(code in range(9) for code in record["exit_codes"])
        assert record["data_schema"]["type"], command
        if 0 in record["exit_codes"]:
            assert record["default_evidence"], f"{command} may exit 0 with no evidence source"
    assert sorted(api_server_cli.CONTRACT_ENTRIES) == ["daemon.serve", "daemon.status", "serve"]


def test_the_dump_invariant_holds_once_the_group_is_registered(
    wired_parser: argparse.ArgumentParser,
) -> None:
    """What ``tests/test_cli_contract_dump.py`` asserts after the paste: nothing unregistered."""
    inventory = cli_contract.dump(wired_parser)
    names = {item["command"] for item in inventory["commands"]}
    assert {"serve", "daemon.serve", "daemon.status"} <= names
    assert inventory["unregistered"] == [], (
        "a registered leaf without a contract entry: " + ", ".join(inventory["unregistered"])
    )
    assert inventory["leaf_commands"] == len(inventory["commands"])
    for name in ("serve", "daemon.serve", "daemon.status"):
        record = next(item for item in inventory["commands"] if item["command"] == name)
        assert record["registered"] is True
        assert record["layer"] == "kernel"
        assert record["args"] and record["has_json_flag"] is True
        assert record["default_evidence"], f"{name} may exit 0 without an evidence source"
        assert record["data_schema"]["type"]


def test_install_contract_entries_is_additive_and_idempotent() -> None:
    before = dict(cli_contract.COMMANDS)
    try:
        cli_contract.COMMANDS.pop("daemon.status", None)
        assert api_server_cli.install_contract_entries() == ["daemon.status"]
        assert api_server_cli.install_contract_entries() == []
        assert "daemon.status" in cli_contract.COMMANDS
    finally:
        cli_contract.COMMANDS.clear()
        cli_contract.COMMANDS.update(before)


def test_the_hand_over_snippet_names_the_three_commands() -> None:
    snippet = api_server_cli.contract_snippet()
    for command in api_server_cli.CONTRACT_ENTRIES:
        assert f'"{command}"' in snippet
    assert "_C(" in snippet and "_ev(" in snippet


def test_the_hand_over_snippet_pastes_clean_and_builds_the_same_entries() -> None:
    """The paste is proven, not claimed: it is executed and compared to the runtime entries."""
    snippet = api_server_cli.contract_snippet()
    long_lines = [line for line in snippet.splitlines() if len(line) > 100]
    assert not long_lines, f"pasted lines over 100 columns: {long_lines}"

    namespace: dict = {"_C": cli_contract.CommandSpec, "_ev": api_server_cli._ev}  # noqa: SLF001
    exec(compile("COMMANDS = {\n" + snippet + "}\n", "<hand-over>", "exec"), namespace)  # noqa: S102
    pasted = namespace["COMMANDS"]
    assert pasted.keys() == api_server_cli.CONTRACT_ENTRIES.keys()
    for command, spec in pasted.items():
        expected = api_server_cli.CONTRACT_ENTRIES[command]
        assert spec == expected, f"the paste and the runtime entry for {command} differ"
        assert spec.evidence and spec.notes
    # and every pasted entry satisfies the CLI contract's own rules
    for command, spec in pasted.items():
        assert spec.layer == "kernel"
        assert spec.exit_codes and all(code in range(9) for code in spec.exit_codes)
        if spec.data_schema is not None:
            assert spec.data_schema["type"], command
        else:
            # `serve` produces no payload: the dump reports NO_SCHEMA for it
            assert cli_contract.spec_for(command).as_dict()["data_schema"]["type"], command


def test_register_cli_wires_both_spellings_and_refuses_to_double_wire() -> None:
    parser = _register(cli.build_parser())  # the real paste, plus annotate (real order)
    action = next(item for item in parser._actions  # noqa: SLF001
                  if isinstance(item, argparse._SubParsersAction))  # noqa: SLF001
    api_server_cli.register_cli(action)  # idempotent: argparse would raise on a double add
    assert {"serve", "daemon"} <= set(action.choices)
    daemon = action.choices["daemon"]
    daemon_sub = next(item for item in daemon._actions  # noqa: SLF001
                      if isinstance(item, argparse._SubParsersAction))  # noqa: SLF001
    assert {"serve", "status"} <= set(daemon_sub.choices)
    parsed = parser.parse_args(["serve", "--http", "--port", "9999"])
    assert parsed.command_path == "serve" and parsed.port == 9999
    parsed = parser.parse_args(["daemon", "status", "--port", "8765", "--json"])
    assert parsed.command_path == "daemon.status" and parsed.json is True


def test_registering_before_annotate_is_what_gives_the_leaves_their_command_path() -> None:
    """The paste order matters: ``register_cli(sub)`` runs *before* ``cli_contract.annotate()``."""
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    api_server_cli.register_cli(sub)
    cli_contract.annotate(parser)
    for argv, expected in ((["serve", "--http"], "serve"),
                           (["daemon", "serve", "--http"], "daemon.serve"),
                           (["daemon", "status", "--json"], "daemon.status")):
        assert parser.parse_args(argv).command_path == expected


def test_register_cli_accepts_a_parser_as_well_as_the_subparsers_action() -> None:
    parser = cli.build_parser()
    api_server_cli.register_cli(parser)  # accepts either, for a forgiving hand-over
    assert "serve" in {
        name for item in parser._actions  # noqa: SLF001
        if isinstance(item, argparse._SubParsersAction)  # noqa: SLF001
        for name in item.choices
    }


def test_a_port_outside_0_to_65535_is_a_usage_line_not_a_traceback(capsys) -> None:
    """R-1: `serve --port 70000` exits 1 with a usage envelope, never a traceback."""
    parser = _register(cli.build_parser())
    args = parser.parse_args(["serve", "--http", "--port", "70000", "--json"])
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    assert code == cli_contract.EXIT_USAGE
    assert "Traceback" not in captured.err
    document_ = json.loads(captured.out)
    assert document_["error"]["code"] == "usage"
    assert "0-65535" in document_["error"]["message"]


def test_serve_without_the_http_flag_reports_a_usage_error(capsys) -> None:
    parser = _register(cli.build_parser())
    args = parser.parse_args(["serve", "--json"])
    code = cli_contract.run_command(args, args.func)
    assert code == cli_contract.EXIT_USAGE
    document_ = json.loads(capsys.readouterr().out)
    assert document_["command"] == "serve"
    assert document_["error"]["code"] == "usage"
    assert "--http" in (document_["error"]["hint"] or "")


def test_daemon_status_finds_no_server_and_says_how_to_start_one(capsys) -> None:
    parser = _register(cli.build_parser())
    port = _free_port()
    args = parser.parse_args(["daemon", "status", "--port", str(port), "--json"])
    code = cli_contract.run_command(args, args.func)
    assert code == cli_contract.EXIT_NOT_FOUND
    document_ = json.loads(capsys.readouterr().out)
    assert document_["data"]["running"] is False
    assert document_["data"]["port"] == port
    assert document_["error"]["code"] == "not_found"
    assert "serve --http" in (document_["error"]["hint"] or "")


def test_a_missing_dependency_is_a_blocked_exit_not_a_traceback(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("fastapi", "uvicorn"):
            raise ImportError(f"no module named {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(api_server.ApiServerUnavailable) as exc:
        api_server.fastapi_modules()
    assert "fastapi" in str(exc.value) and "pip install" in exc.value.hint


# --------------------------------------------------------------------------- #
# the two real command calls that need no HTTP transport
# --------------------------------------------------------------------------- #


def test_run_cli_returns_the_cli_envelope_and_its_exit_code() -> None:
    route = next(item for item in api_server.routes() if item.command == "memory-stats")
    argv = api_server.build_argv(route, {})
    result = api_server.run_cli(argv, command="memory-stats", route=route, timeout_s=120)
    assert result.exit_code in (0, 5, 1), result.envelope
    assert result.envelope["command"] == "memory-stats"
    assert result.envelope["schema_version"] == cli_contract.SCHEMA_VERSION
    assert result.status == api_server.status_for_exit(result.envelope["exit_code"])
    assert result.duration_ms >= 0 and result.argv == tuple(argv)


def test_run_cli_maps_a_command_failure_to_its_status() -> None:
    route = next(item for item in api_server.routes() if item.command == "tools.show")
    argv = api_server.build_argv(route, {"name": "definitely-not-a-tool"})
    result = api_server.run_cli(argv, command="tools.show", route=route, timeout_s=120)
    assert result.exit_code == cli_contract.EXIT_NOT_FOUND, result.envelope
    assert result.envelope["error"]["code"] == "not_found"
    assert result.status == 404
    assert result.stdout.strip().startswith("{")


def test_a_command_that_overruns_is_killed_and_reported_as_exit_8_and_504(
    tmp_path: Path,
) -> None:
    """The real timeout path: a child that never answers is killed, not waited for."""
    slow = tmp_path / "slow-python"
    slow.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    slow.chmod(0o755)
    route = next(item for item in api_server.routes() if item.command == "doctor")
    started = time.time()
    result = api_server.run_cli(
        ["doctor", "--json"], command="doctor", route=route, timeout_s=1, python=str(slow)
    )
    assert time.time() - started < 20, "the deadline did not cut the command short"
    assert result.timed_out is True
    assert result.exit_code == cli_contract.EXIT_UNKNOWN
    assert result.status == 504
    assert result.envelope["error"]["code"] == "unknown"
    assert "did not finish" in result.envelope["error"]["message"]


def test_a_command_that_prints_no_envelope_is_never_reported_as_success(
    tmp_path: Path,
) -> None:
    """A command that ignores ``--json`` yields exit 7 with what it really printed."""
    noisy = tmp_path / "noisy-python"
    noisy.write_text("#!/bin/sh\necho 'not an envelope'\n", encoding="utf-8")
    noisy.chmod(0o755)
    route = next(item for item in api_server.routes() if item.command == "doctor")
    result = api_server.run_cli(
        ["doctor", "--json"], command="doctor", route=route, timeout_s=30, python=str(noisy)
    )
    assert result.exit_code == cli_contract.EXIT_INTERNAL
    assert result.status == 500
    assert result.envelope["ok"] is False
    assert "no JSON envelope" in result.envelope["error"]["message"]


def test_streaming_forwards_one_envelope_per_line() -> None:
    route = next(item for item in api_server.routes()
                 if item.command == "contract.list" and not item.mutates)
    argv = api_server.build_argv(route, {})
    lines = list(api_server.iter_envelopes(argv, command=route.command, route=route,
                                           timeout_s=120))
    assert len(lines) >= 1
    for envelope in lines:
        assert isinstance(envelope, dict)
        assert "exit_code" in envelope and "schema_version" in envelope
    assert lines[-1]["exit_code"] == 0


def test_the_stream_declaration_is_explicit(route_list: list[api_server.ApiRoute]) -> None:
    """A route is marked streaming exactly when the contract declares it (and it exists)."""
    assert "run.watch" in api_server.STREAM_COMMANDS, (
        "the long-run watch command is the CLI's real stream; the declaration must name it"
    )
    document_ = api_server.openapi_document(route_list)
    declared = {path for path, operations in document_["paths"].items()
                if operations["post"].get("x-godsv-streams")}
    expected = {route.path for route in route_list if route.command in api_server.STREAM_COMMANDS}
    assert declared == expected
    for route in route_list:
        assert route.streams is (route.command in api_server.STREAM_COMMANDS)
