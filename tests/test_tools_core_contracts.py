"""The core toolset's *contracts*: schemas, permissions, timeouts, truncation.

These tests hold the shape every tool must have — the part that makes an agent
tool safe to hand to a model: a typed input schema (no extra keys), a typed
output schema, a declared permission per axis, a timeout, a truncation rule and a
smoke case — plus the sandbox policy those declarations are enforced by.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skaldir.tools_core import contract as C
from skaldir.tools_core import sandbox


@pytest.fixture()
def table():
    from skaldir.tools_core import registry

    return registry.contracts()


def test_every_core_tool_declares_the_full_contract(table):
    assert set(table) == {
        "terminal",
        "read_file",
        "write_file",
        "patch",
        "search_files",
        "list_dir",
        "web_fetch",
        "web_search",
        # the ninth: the built-in that brings the container runtime up (DOCKER-ENSURE)
        "docker.ensure",
    }
    for name, contract in table.items():
        assert contract.name == name
        assert contract.module.startswith("skaldir.tools_core.")
        assert contract.title and contract.description
        assert contract.timeout_s > 0
        assert contract.max_timeout_s >= contract.timeout_s
        assert contract.truncation.max_bytes > 0
        assert contract.truncation.spill_dir
        assert contract.smoke, f"{name}: no smoke case declared"
        assert contract.smoke.get("input") is not None
        assert contract.smoke.get("expect"), f"{name}: smoke case asserts nothing"


def test_every_input_and_output_schema_is_typed_and_closed(table):
    for name, contract in table.items():
        problems = C.validate_schema(contract.input_schema, where=f"{name}.input_schema")
        problems += C.validate_schema(contract.output_schema, where=f"{name}.output_schema")
        assert problems == [], problems


def test_the_open_key_maps_declare_their_value_type(table):
    """``terminal.env`` and ``web_fetch.headers`` are string MAPS, not free-form objects.

    Both take open keys (any variable / header name) whose values are strings — each
    implementation stringifies a non-string, and the description says so. Declaring a
    bare ``{"type": "object"}`` said nothing about the values, which is why the MCP
    mapper could not type them (``7/9`` instead of ``9/9``).
    """
    for tool, field in (("terminal", "env"), ("web_fetch", "headers")):
        spec = table[tool].input_schema["properties"][field]
        assert spec["type"] == "object", f"{tool}.{field} must be an object"
        assert spec["additionalProperties"] == {"type": "string"}, (
            f"{tool}.{field} must declare its value type (a string map), got "
            f"{spec.get('additionalProperties')!r}"
        )
        assert "stringif" in spec["description"], (
            f"{tool}.{field} must say that a non-string value is stringified"
        )


def test_input_schemas_are_the_real_contract(monkeypatch):
    """The schemas bite: missing required, unknown key, wrong type, out of range."""
    from skaldir.tools_core import registry

    terminal = registry.contracts()["terminal"].input_schema
    assert C.validate_input(terminal, {}) == ["missing required argument: command"]
    assert C.validate_input(terminal, {"command": "ls", "nope": 1}) == ["unknown argument: nope"]
    assert C.validate_input(terminal, {"command": 5}) == ["command: expected string, got int"]
    problems = C.validate_input(terminal, {"command": "ls", "timeout_s": 99999})
    assert problems == ["timeout_s: must be <= 600"]
    assert C.validate_input(terminal, {"command": "ls", "timeout_s": 5}) == []

    enum = registry.contracts()["write_file"].input_schema
    assert C.validate_input(enum, {"path": "a", "content": "b", "mode": "nope"}) == [
        "mode: must be one of ['overwrite', 'append', 'create']"
    ]


def test_permission_declarations_cover_the_three_axes(table):
    for contract in table.values():
        permission = contract.permission.to_dict()
        assert set(permission) == {"filesystem", "network", "subprocess"}
        fs, net, sub = permission["filesystem"], permission["network"], permission["subprocess"]
        assert fs["scope"] and fs["mode"]
        assert net["mode"] in {
            "none", "fetch", "search", "inherit",
            # the container runtime's own endpoint: one declared local socket (the
            # docker CLI's unix socket), never a general reach. Added here rather
            # than hidden behind "inherit", because "what does this tool dial?" is
            # exactly the question the declaration exists to answer.
            "local-socket",
        }
        assert isinstance(sub["allowed"], bool)
        # a tool that writes declares a scope and a mode; a reader never declares write
        if contract.mutates:
            assert fs["mode"] in {"write", "read-write"}
        if sub["allowed"]:
            assert sub["env_deny_pattern"] == sandbox.ENV_DENY_PATTERN
            assert sub["max_timeout_s"] >= sub["default_timeout_s"] > 0
            assert sub["kill"]


def test_only_terminal_may_start_processes(table):
    allowed = [name for name, c in table.items() if c.permission.subprocess.allowed]
    # docker.ensure starts the runtime CLI for a probe (`version`/`info` only — its
    # own guard refuses every other verb), so it is the second declared process user.
    assert allowed == ["terminal", "docker.ensure"]
    assert table["terminal"].permission.subprocess.shell is True
    for name, contract in table.items():
        if name not in ("terminal",):
            assert contract.permission.subprocess.shell is False


def test_only_the_web_tools_and_docker_ensure_reach_out(table):
    reaching = {name: c.permission.network.mode for name, c in table.items()}
    assert reaching == {
        "terminal": "inherit",
        "read_file": "none",
        "write_file": "none",
        "patch": "none",
        "search_files": "none",
        "list_dir": "none",
        "web_fetch": "fetch",
        "web_search": "search",
        # the one non-web reach: the container runtime's *local* socket. It is declared
        # rather than hidden, and it stays narrow — no key, no arbitrary host, and the
        # only endpoints are the ones $DOCKER_HOST (default: the local unix socket) names.
        "docker.ensure": "local-socket",
    }
    for name in ("web_fetch", "web_search"):
        assert table[name].permission.network.requires_key is False
        assert table[name].permission.network.key_env == ""
    docker = table["docker.ensure"].permission.network
    assert docker.requires_key is False and docker.key_env == ""
    assert "unix" in docker.schemes


def test_the_registry_tags_repeat_the_declared_limits(table):
    for contract in table.values():
        tags = contract.registry_tags()
        assert "core" in tags
        assert f"timeout:{contract.timeout_s}s" in tags
        assert f"truncate:{contract.truncation.max_bytes}b" in tags
        assert any(tag.startswith("fs:") for tag in tags)
        assert any(tag.startswith("net:") for tag in tags)
        assert any(tag.startswith("proc:") for tag in tags)


def test_contract_documents_are_json_serialisable(table):
    for contract in table.values():
        payload = contract.to_dict()
        assert payload["kind"] == "core"
        assert json.dumps(payload)  # must survive the CLI/envelope path
        assert payload["permissions"]["subprocess"]["allowed"] in (True, False)


# --------------------------------------------------------------------- limits


def test_truncation_is_head_plus_tail_with_a_marker_and_a_spill(tmp_path):
    rule = C.TruncationRule(max_bytes=1000, head_fraction=0.7, spill=True)
    assert rule.head_bytes == 700
    assert rule.tail_bytes == 300
    raw = bytes(range(256)) * 40  # 10240 bytes
    report = sandbox.truncate_bytes(raw, rule, label="stdout", runtime=tmp_path)
    assert report["truncated"] is True
    assert report["total_bytes"] == 10240
    assert report["returned_bytes"] == 1000
    assert report["removed_bytes"] == 9240
    assert "full output:" in report["marker"]
    assert report["spill_path"].startswith(str(tmp_path / "tool_output"))
    assert Path(report["spill_path"]).read_bytes() == raw
    head = raw[:700].decode("utf-8", "replace")
    tail = raw[-300:].decode("utf-8", "replace")
    assert report["text"].startswith(head)
    assert report["text"].endswith(tail)


def test_output_below_the_limit_is_returned_untouched(tmp_path):
    rule = C.TruncationRule(max_bytes=1000, spill=False)
    report = sandbox.truncate_bytes(b"short\n", rule, runtime=tmp_path)
    assert report["truncated"] is False
    assert report["text"] == "short\n"
    assert report["spill_path"] == ""
    assert report["marker"] == ""


def test_a_spilled_output_is_capped_too(tmp_path):
    rule = C.TruncationRule(max_bytes=100, spill=True, spill_max_bytes=1024)
    report = sandbox.truncate_bytes(b"x" * 5000, rule, label="big", runtime=tmp_path)
    assert report["spill_bytes"] == 1024
    assert report["spill_truncated"] is True
    assert Path(report["spill_path"]).stat().st_size == 1024


# -------------------------------------------------------------------- sandbox


def test_candidate_path_stays_inside_the_root(tmp_path):
    root = tmp_path / "ws"
    (root / "sub").mkdir(parents=True)
    inside, problem = sandbox.candidate_path(root, "sub/file.txt")
    assert problem == "" and inside == (root / "sub/file.txt").resolve()
    absolute, problem = sandbox.candidate_path(root, str(root / "a.txt"))
    assert problem == "" and absolute == (root / "a.txt").resolve()
    for bad in ("../escape.txt", "/etc/passwd", "~/secrets.txt", ""):
        path, problem = sandbox.candidate_path(root, bad)
        assert path is None and problem


def test_a_symlink_out_of_the_root_is_refused(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    path, problem = sandbox.candidate_path(root, "link/secret.txt")
    assert path is None
    assert "escapes the declared root" in problem


def test_write_deny_rules_name_the_reason(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    for relative, expected in (
        (".git/config", ".git/**"),
        (".env", ".env"),
        ("keys/id_rsa", "id_rsa*"),
        ("certs/server.pem", "*.pem"),
    ):
        assert sandbox.write_denied(root, root / relative) == expected, relative
    assert sandbox.write_denied(root, root / "src/main.py") == ""


def test_child_environment_drops_credentials_and_pythonpath(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-for-a-child")
    monkeypatch.setenv("SOME_TOKEN", "nope")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else/src")
    monkeypatch.setenv("EDITOR", "vim")
    env, report = sandbox.child_environment("/tmp")
    assert "DEEPSEEK_API_KEY" not in env and "SOME_TOKEN" not in env
    assert "PYTHONPATH" not in env
    assert "EDITOR" not in env
    dropped = set(report["dropped_credentials"])
    assert {"DEEPSEEK_API_KEY", "SOME_TOKEN"} <= dropped
    assert "PYTHONPATH" in report["dropped_by_design"]
    # the child learns where its sandbox is, in both spellings, and the values
    # of dropped variables never appear in the report
    assert env["SKALDIR_TOOL_WORKSPACE"] == "/tmp"
    assert env["GODSV_TOOL_WORKSPACE"] == "/tmp"
    assert "sk-not-for-a-child" not in json.dumps(report)


def test_binary_detection_is_byte_based():
    assert sandbox.is_probably_binary(b"text\x00more") is True
    assert sandbox.is_probably_binary("ünïcode".encode()) is False


def test_isolation_probe_reports_the_truth():
    report = sandbox.isolation_report(refresh=True)
    assert report["mode"] in {"process", "bubblewrap"}
    assert isinstance(report["available"], bool)
    if report["available"]:
        assert report["binary"]
    else:
        assert report["error"]


# ----------------------------------------------------------------- result type


def test_error_kinds_map_onto_the_cli_exit_codes():
    assert C.ERROR_EXIT[""] == 0
    assert C.ERROR_EXIT["usage"] == 1
    assert C.ERROR_EXIT["verification"] == 2
    assert C.ERROR_EXIT["blocked"] == 3
    assert C.ERROR_EXIT["not_found"] == 5
    assert C.ERROR_EXIT["conflict"] == 6
    assert C.ERROR_EXIT["internal"] == 7
    assert C.ERROR_EXIT["timeout"] == 8
    assert C.ERROR_EXIT["network"] == 8
    result = C.failure("terminal", "blocked", "nope", data={"cwd": "/etc"})
    assert result.exit_code == 3 and result.ok is False
    assert result.to_dict()["error_kind"] == "blocked"
    with pytest.raises(ValueError):
        C.failure("terminal", "made_up", "nope")


def test_a_tool_result_is_json_serialisable_and_carries_its_evidence():
    result = C.ToolResult(tool="terminal", ok=True, data={"exit_code": 1})
    result.evidence = [{"ref": "/bin/sh -c false", "kind": "run", "note": "cwd=/tmp"}]
    document = json.loads(json.dumps(result.to_dict()))
    assert document["exit_code"] == 0  # the *tool* call succeeded
    assert document["output"]["exit_code"] == 1  # the observed process status is data


def test_env_defaults_do_not_leak_the_developer_runtime(monkeypatch):
    """A core tool's workspace comes from the caller, never from the environment."""
    from skaldir.tools_core import registry

    monkeypatch.delenv("SKALDIR_TOOL_WORKSPACE", raising=False)
    monkeypatch.delenv("GODSV_TOOL_WORKSPACE", raising=False)
    monkeypatch.chdir("/tmp")
    assert registry.build_context().root == Path("/tmp").resolve()
    monkeypatch.setenv("SKALDIR_TOOL_WORKSPACE", "/tmp/declared")
    assert registry.build_context().root == Path("/tmp/declared").resolve()
    assert os.environ.get("SKALDIR_TOOL_WORKSPACE") == "/tmp/declared"
