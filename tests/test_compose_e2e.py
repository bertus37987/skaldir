"""COMPOSE end to end: a real composite through the product entry point.

What these tests prove (not "the code exists"):

* ``skaldir compose create/validate/run/show`` work through ``cli.main`` — the
  product entry point — with exactly one JSON envelope on stdout.
* A composite with **two cheap local steps** really executes: both steps are
  generated python tools in the registry, run through the shared bwrap sandbox
  (``mcpserver._wrapper_for`` -> ``gates._run``), no network, no container.
* A **docker step** really goes through ``DockerRuntimeManager.run``: the recorded
  argv carries ``--rm``, ``--network none``, the owner label and the pinned digest
  (a fake ``docker`` binary — no daemon, no image, nothing foreign touched).
* The kinds that **cannot** execute today (``api``, ``gui``) make ``compose run``
  exit 3 and name the step and the kind — never a pretend success.
* The composite is a registry citizen (``kind="composition"``) but is **not**
  exported to MCP, which registers ``kind="python"`` only.
"""

from __future__ import annotations

import argparse
import json
import stat
import sys

import pytest

from skaldir import cli, cli_contract
from skaldir.compose_cli import register_cli
from skaldir.gates import _sandbox_available
from skaldir.mcpserver import approved_python_specs
from skaldir.tools import KIND_PYTHON, STATUS_APPROVED, ToolSpec, ToolStore

DIGEST = "sha256:" + "b" * 64
SANDBOX = pytest.mark.skipif(
    not _sandbox_available(),
    reason="generated tools only execute through the shared bwrap sandbox (gates._run)",
)

#: A fake `docker` CLI: answers the probe + one-shot run argv, records every call.
_FAKE_DOCKER = r'''#!{python}
"""Fake docker CLI for the compose tests — records argv, never touches a daemon."""
import json
import os
import sys

argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\n")

if argv[:1] == ["version"]:
    print(json.dumps({{"Server": {{"Version": "29.6.1"}}}}))
    raise SystemExit(0)
if argv[:1] == ["info"]:
    print(json.dumps({{"ServerVersion": "29.6.1", "Architecture": "x86_64"}}))
    raise SystemExit(0)
if argv[:1] == ["run"]:
    print("container: " + " ".join(argv[-2:]))
    raise SystemExit(0)
print("fake docker: unexpected argv " + json.dumps(argv), file=sys.stderr)
raise SystemExit(2)
'''.format(python=sys.executable)  # noqa: UP032 - .format keeps the {{ }} JSON braces

#: The same binary, but the daemon never answers (the honest "blocked" path).
_FAKE_DOCKER_NO_DAEMON = r'''#!{python}
"""Fake docker CLI whose daemon is down."""
import sys

argv = sys.argv[1:]
if argv[:1] == ["version"]:
    print("Client: Docker Engine 29.6.1")
    raise SystemExit(0)
print("Cannot connect to the Docker daemon at unix:///var/run/docker.sock.", file=sys.stderr)
raise SystemExit(1)
'''.format(python=sys.executable)  # noqa: UP032 - same pattern


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #


def _runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    runtime = home / "godsv"
    runtime.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(runtime))
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return runtime


def _register_tool(runtime, name, body):
    module = f"{name}_mod"
    directory = runtime / "generated_tools" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{module}.py").write_text(body, encoding="utf-8")
    store = ToolStore(runtime / "tools.db")
    store.upsert(
        ToolSpec(name=name, title=name, description=f"step tool {name}", kind=KIND_PYTHON,
                 module=module, entrypoint="run", status=STATUS_APPROVED, tags=["text"],
                 sample_input={"text": "sample"})
    )
    store.close()


@pytest.fixture()
def product_entry(monkeypatch):
    """``cli.main`` with the ``compose`` group wired the way ``cli.py`` will wire it.

    The registration sits inside ``build_parser()`` and runs **before** the final
    ``cli_contract.annotate()`` pass — that pass gives every leaf its dotted
    ``command_path``, which is what makes the envelope say ``compose.run``.
    """
    real_build = cli.build_parser

    def build_parser():
        parser = real_build()
        sub = next(
            action for action in parser._actions  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
        )
        if "compose" not in sub.choices:
            register_cli(sub)
            cli_contract.annotate(parser)
        return parser

    monkeypatch.setattr(cli, "build_parser", build_parser)
    return cli.main


def _cli(capsys, argv):
    """Run one command through ``cli.main``; return (exit_code, envelope)."""
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out.strip())


def _two_step_doc(name="text_report"):
    return {
        "name": name,
        "title": "Text report",
        "description": "normalize then render",
        "tags": ["text"],
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": ["report"],
        "precondition": "$input.text present",
        "end_state": "$steps.render.report contains 'REPORT'",
        "steps": [
            {"id": "normalize", "kind": "tool", "target": "step_normalize",
             "inputs": {"text": "$input.text"}, "outputs": ["upper"],
             "precondition": "always",
             "end_state": "exit_ok and $steps.normalize.upper present"},
            {"id": "render", "kind": "tool", "target": "step_render",
             "inputs": {"upper": "$steps.normalize.upper"}, "outputs": ["report"],
             "precondition": "$steps.normalize.upper present",
             "end_state": "exit_ok and $steps.render.report contains 'REPORT'"},
        ],
    }


@pytest.fixture()
def local_workspace(tmp_path, monkeypatch):
    """Two approved generated tools + the composite document on disk."""
    runtime = _runtime(tmp_path, monkeypatch)
    _register_tool(runtime, "step_normalize",
                   "def run(text=''):\n    return {'upper': str(text).strip().upper()}\n")
    _register_tool(runtime, "step_render",
                   "def run(upper=''):\n    return {'report': 'REPORT:' + str(upper)}\n")
    spec = tmp_path / "text_report.json"
    spec.write_text(json.dumps(_two_step_doc()), encoding="utf-8")
    return runtime, spec


@pytest.fixture()
def fake_docker(tmp_path, monkeypatch):
    """A fake `docker` binary on disk + its argv log (no daemon, no container)."""
    script = tmp_path / "docker"
    script.write_text(_FAKE_DOCKER, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "docker_argv.jsonl"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    return script, log


def _argv_calls(log):
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# the real two-step composite through the product entry point                   #
# --------------------------------------------------------------------------- #


@SANDBOX
class TestRealCompositeThroughTheProductEntryPoint:
    def test_create_validate_run_show_through_cli_main(self, local_workspace, product_entry,
                                                       capsys):
        runtime, spec = local_workspace

        code, created = _cli(
            capsys, ["compose", "create", "--spec", str(spec), "--json"]
        )
        assert code == 0 and created["command"] == "compose.create"
        assert created["data"]["created"] is True
        assert created["data"]["validation"]["ok"] is True

        code, validated = _cli(
            capsys, ["compose", "validate", "--name", "text_report", "--approve", "--json"]
        )
        assert code == 0 and validated["command"] == "compose.validate"
        assert validated["data"]["status_before"] == "candidate"
        assert validated["data"]["status_after"] == STATUS_APPROVED

        code, run = _cli(
            capsys,
            ["compose", "run", "--name", "text_report", "--input", "text=  hello compose  ",
             "--json"],
        )
        assert code == 0 and run["command"] == "compose.run"
        assert run["ok"] is True and run["exit_code"] == 0
        data = run["data"]
        assert data["status"] == "verified" and data["verified"] is True
        assert data["outputs"] == {"report": "REPORT:HELLO COMPOSE"}
        assert [step["id"] for step in data["steps"]] == ["normalize", "render"]
        assert [step["status"] for step in data["steps"]] == ["ok", "ok"]
        assert data["steps"][0]["outputs"] == {"upper": "HELLO COMPOSE"}
        assert "end_state" in data["reason"]
        assert any(item["kind"] == "run" and "step:normalize" in item["ref"]
                   for item in run["evidence"])

        code, shown = _cli(capsys, ["compose", "show", "--name", "text_report", "--json"])
        assert code == 0 and shown["command"] == "compose.show"
        assert shown["data"]["registry"]["status"] == STATUS_APPROVED
        assert shown["data"]["validation"]["executable"] is True
        assert shown["data"]["mirror"]["drift_detected"] is False

    def test_a_run_without_the_input_cannot_start(self, local_workspace, product_entry, capsys):
        runtime, spec = local_workspace
        _cli(capsys, ["compose", "create", "--spec", str(spec), "--json"])
        _cli(capsys, ["compose", "validate", "--name", "text_report", "--approve", "--json"])
        code, run = _cli(capsys, ["compose", "run", "--name", "text_report", "--json"])
        assert code == 1 and run["error"]["code"] == "usage"
        assert run["data"]["steps"] == []

    def test_the_composite_is_a_registry_citizen_but_not_an_mcp_tool(self, local_workspace,
                                                                     product_entry, capsys):
        """The measured P0 note: MCP registers ``kind="python"`` only."""
        runtime, spec = local_workspace
        _cli(capsys, ["compose", "create", "--spec", str(spec), "--json"])
        _cli(capsys, ["compose", "validate", "--name", "text_report", "--approve", "--json"])
        store = ToolStore(runtime / "tools.db")
        try:
            composite = store.get("text_report")
            assert composite is not None
            assert composite.kind == "composition" and composite.status == STATUS_APPROVED
            # the MCP export surface itself (no scope filter): approved python only
            exported = [item.name for item in approved_python_specs(store)]
        finally:
            store.close()
        assert exported == ["step_normalize", "step_render"]
        assert "text_report" not in exported, (
            "a composite is not exported to MCP today — MCP registers kind='python' only; "
            "it is callable as one tool through `skaldir compose run`"
        )

    def test_a_step_failure_reports_exit_2_with_the_step(self, tmp_path, monkeypatch,
                                                         product_entry, capsys):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize",
                       "def run(text=''):\n    return {'upper': str(text).upper()}\n")
        _register_tool(runtime, "step_render",
                       "def run(upper=''):\n    raise RuntimeError('render exploded')\n")
        spec = tmp_path / "doc.json"
        spec.write_text(json.dumps(_two_step_doc()), encoding="utf-8")
        _cli(capsys, ["compose", "create", "--spec", str(spec), "--json"])
        _cli(capsys, ["compose", "validate", "--name", "text_report", "--approve", "--json"])
        code, run = _cli(
            capsys,
            ["compose", "run", "--name", "text_report", "--input", "text=x", "--json"],
        )
        assert code == 2 and run["error"]["code"] == "verification_failed"
        assert run["data"]["status"] == "failed"
        assert run["data"]["steps"][-1]["status"] == "failed"
        assert "render exploded" in run["data"]["reason"]


# --------------------------------------------------------------------------- #
# the docker step: a real call through the docker runtime                       #
# --------------------------------------------------------------------------- #


class TestDockerStep:
    def _doc(self):
        return {
            "name": "docker_echo",
            "inputs": {"who": {"type": "string", "required": True}},
            "outputs": ["stdout"],
            "precondition": "always",
            "end_state": "$steps.echo.stdout present",
            "steps": [
                {"id": "echo", "kind": "docker", "image_digest": DIGEST,
                 "argv": ["sh", "-c", "echo $input.who"], "outputs": ["stdout", "exit_code"],
                 "precondition": "always",
                 "end_state": "exit_ok and $steps.echo.exit_code == 0"},
            ],
        }

    def _prepare(self, local_workspace, capsys, docker_bin):
        runtime, _spec = local_workspace
        spec = runtime.parent.parent / "docker_echo.json"
        spec.write_text(json.dumps(self._doc()), encoding="utf-8")
        _cli(capsys, ["compose", "create", "--spec", str(spec), "--json"])
        code, _ = _cli(
            capsys, ["compose", "validate", "--name", "docker_echo", "--approve", "--json"]
        )
        assert code == 0
        return runtime, docker_bin

    def test_a_docker_step_runs_through_the_manager_with_owner_labels(self, local_workspace,
                                                                     fake_docker, product_entry,
                                                                     capsys):
        script, log = fake_docker
        runtime, _ = self._prepare(local_workspace, capsys, script)
        code, run = _cli(
            capsys,
            ["compose", "run", "--name", "docker_echo", "--input", "who=docker",
             "--docker-bin", str(script), "--json"],
        )
        assert code == 0, run.get("error")
        assert run["data"]["status"] == "verified"
        assert run["data"]["outputs"] == {"stdout": "container: -c echo docker"}
        calls = _argv_calls(log)
        assert [call[:1] for call in calls] == [["version"], ["info"], ["run"]]
        run_argv = calls[-1]
        assert run_argv[:2] == ["run", "--rm"], "one-shot runs must clean themselves up"
        assert run_argv[run_argv.index("--network") + 1] == "none"
        assert "godsv.owner=skaldir" in run_argv, "a container we start must be ours by label"
        assert DIGEST in run_argv, "a docker step runs a pinned digest, never a mutable tag"
        assert run_argv[-3:] == ["sh", "-c", "echo docker"], "the argv refs are substituted"

    def test_a_docker_step_without_a_pinned_digest_is_refused_at_validation(self, local_workspace,
                                                                            product_entry, capsys):
        runtime, _spec = local_workspace
        payload = self._doc()
        payload["steps"][0].pop("image_digest")
        payload["steps"][0]["image"] = "alpine:3.20"
        spec = runtime.parent.parent / "unpinned.json"
        spec.write_text(json.dumps(payload), encoding="utf-8")
        code, created = _cli(capsys, ["compose", "create", "--spec", str(spec), "--json"])
        assert code == 2
        assert any("pin-required" in item
                   for item in created["data"]["validation"]["problems"])

    def test_an_unreachable_daemon_blocks_the_run_with_the_real_detail(self, tmp_path, monkeypatch,
                                                                      local_workspace,
                                                                      product_entry, capsys):
        script = tmp_path / "docker_no_daemon"
        script.write_text(_FAKE_DOCKER_NO_DAEMON, encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        runtime, _ = self._prepare(local_workspace, capsys, script)
        code, run = _cli(
            capsys,
            ["compose", "run", "--name", "docker_echo", "--input", "who=x",
             "--docker-bin", str(script), "--json"],
        )
        assert code == 3 and run["error"]["code"] == "blocked"
        assert run["data"]["status"] == "blocked"
        assert run["data"]["missing_kinds"] == ["docker"]
        assert "Cannot connect to the Docker daemon" in run["data"]["reason"]
        assert run["data"]["steps"][0]["missing_kind"] == "docker"


# --------------------------------------------------------------------------- #
# the kinds that used to be refused: now really run, or refused by name        #
# --------------------------------------------------------------------------- #


class TestKindsThatUsedToBeRefused:
    def _chain(self, step):
        return {
            "name": "not_yet",
            "inputs": {"text": {"type": "string", "required": True}},
            "outputs": ["out"],
            "precondition": "always",
            "end_state": "always",
            "steps": [
                {"id": "local", "kind": "tool", "target": "step_normalize",
                 "inputs": {"text": "$input.text"}, "outputs": ["upper"],
                 "precondition": "always", "end_state": "exit_ok"},
                step,
            ],
        }

    def test_an_api_step_runs_and_its_real_request_decides_the_verdict(self, local_workspace,
                                                                      product_entry, capsys):
        """No up-front refusal: the api step really goes out and its own result is the
        verdict (exit 2 for a refused connection), with the failure class in the record."""
        runtime, _spec = local_workspace
        payload = self._chain(
            {"id": "call", "kind": "api", "url": "http://127.0.0.1:9/v1", "method": "POST",
             "timeout_s": 5, "outputs": ["text"], "precondition": "always",
             "end_state": "exit_ok"}
        )
        payload["outputs"] = ["upper", "text"]
        spec = runtime.parent.parent / "api_chain.json"
        spec.write_text(json.dumps(payload), encoding="utf-8")
        code, created = _cli(capsys, ["compose", "create", "--spec", str(spec), "--json"])
        assert code == 0, created
        code, validated = _cli(
            capsys, ["compose", "validate", "--name", "not_yet", "--approve", "--json"]
        )
        assert code == 0 and validated["data"]["missing_kinds"] == []
        assert validated["data"]["executable"] is True
        code, run = _cli(
            capsys, ["compose", "run", "--name", "not_yet", "--input", "text=x", "--json"]
        )
        assert code == 2 and run["error"]["code"] == "verification_failed"
        assert run["data"]["verified"] is False
        assert run["data"]["missing_kinds"] == []
        assert [step["id"] for step in run["data"]["steps"]] == ["local", "call"]
        call = run["data"]["steps"][-1]
        assert call["status"] == "failed" and call["kind"] == "api"
        assert call["failure_class"] == "network_error"

    def test_a_gui_step_without_a_bridge_is_exit_3_and_names_the_capability(self, local_workspace,
                                                                           product_entry, capsys,
                                                                           monkeypatch):
        """A named refusal through the CLI: blocked, exit 3, the missing capability in the
        step record (the bridge itself is stubbed so the test never needs a display)."""
        import skaldir.act_service as act_service

        monkeypatch.setattr(
            act_service,
            "resolve_runner",
            lambda kind, **kw: (
                None,
                {"requested": kind, "resolved": False, "real": False, "fixture": False,
                 "reason": "no isolation bridge is available on this stubbed host"},
            ),
        )
        runtime, _spec = local_workspace
        payload = self._chain(
            {"id": "click", "kind": "gui", "target": "open_browser", "outputs": ["status"],
             "precondition": "always", "end_state": "exit_ok"}
        )
        payload["outputs"] = ["upper", "status"]
        spec = runtime.parent.parent / "gui_chain.json"
        spec.write_text(json.dumps(payload), encoding="utf-8")
        _cli(capsys, ["compose", "create", "--spec", str(spec), "--json"])
        _cli(capsys, ["compose", "validate", "--name", "not_yet", "--approve", "--json"])
        code, run = _cli(
            capsys, ["compose", "run", "--name", "not_yet", "--input", "text=x", "--json"]
        )
        assert code == 3 and run["error"]["code"] == "blocked"
        assert run["data"]["missing_kinds"] == ["gui"]
        click = run["data"]["steps"][-1]
        assert click["status"] == "blocked" and click["missing_kind"] == "gui"
        assert "stubbed host" in click["detail"]
        assert click["missing_capability"]

    def test_the_executor_table_names_a_runner_for_every_kind(self, product_entry, tmp_path,
                                                             monkeypatch, capsys):
        _runtime(tmp_path, monkeypatch)
        code, envelope = _cli(capsys, ["compose", "candidates", "--json"])
        assert code == 0
        table = envelope["data"]["executors"]
        for kind, row in table.items():
            assert row["executable"] is True, kind
            assert row["runner"], kind
            assert isinstance(row["available"], bool), kind
        assert "run_api_step" in table["api"]["runner"]
        assert "run_gui_step" in table["gui"]["runner"]
