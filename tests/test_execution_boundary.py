"""§1.1 — die gemeinsame Ausführungs-/Berechtigungsgrenze (`godsv.execution`).

Deckt den Auftrag in sechs Punkten: (a) Metazeichen bleiben argv, nie Shell;
(b) Pfadausbruch + verborgene Host-Pfade sind im Sandbox-Lauf unsichtbar;
(c) ``allow_network=False`` ⇒ kein Netz, und der Policy-Schalter wirkt;
(d) fail-closed ohne Sandbox ⇒ ``not-certifiable``, NIE ``ok`` (inkl. der
Container-Naht); (e) der Panel-Pfad ``action=test`` läuft über die Grenze und
fällt ohne Sandbox NICHT auf einen ungeschützten Lauf zurück; (f) der
Ergebnisvertrag der Testing-Tools bleibt unverändert.

Rot-first-Beleg für (e): gegen den alten ``plugin_api.py``-Stand
(direkter ``subprocess.run``, Zeile 1107) scheitern
``test_panel_test_without_sandbox_is_not_certifiable`` und
``test_panel_test_uses_the_shared_boundary`` — der Wrapper lief ungeschützt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sys
from pathlib import Path

import pytest

from skaldir import execution, gates
from skaldir.execution import RunPolicy, RunResult, ToolRun, get_container_executor, run_local

BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not BWRAP, reason="bwrap required")

ARGV_PROBE = "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"

NET_PROBE = (
    "import json, socket\n"
    "def net():\n"
    "    try:\n"
    "        socket.create_connection(('1.1.1.1', 80), timeout=3).close()\n"
    "        return 'open'\n"
    "    except Exception:\n"
    "        return 'blocked'\n"
    "print(json.dumps({'net': net(),\n"
    "                  'ifs': sorted(name for _i, name in socket.if_nameindex())}))\n"
)


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


class TestArgvIsNeverAShell:
    @needs_bwrap
    def test_metacharacters_are_passed_as_argv(self, tmp_path):
        _write(tmp_path / "probe.py", ARGV_PROBE)
        hostile = [
            "; touch pwned.txt",
            "$HOME",
            "`id`",
            "$(id)",
            "a b && echo x",
            "|",
            "> redirected.txt",
        ]
        result = run_local(
            ToolRun(argv=[sys.executable, "probe.py", *hostile], workdir=str(tmp_path))
        )
        assert result.status == "ok" and result.ok is True, result
        assert json.loads(result.stdout.strip()) == hostile
        # ein Shell-Parsing hätte diese Dateien angelegt
        assert not (tmp_path / "pwned.txt").exists()
        assert not (tmp_path / "redirected.txt").exists()


class TestHostPathsAreInvisible:
    @needs_bwrap
    def test_escape_and_hidden_host_paths_are_not_visible(self, tmp_path):
        workdir = tmp_path / "tool"
        workdir.mkdir()
        _write(tmp_path / "outside.txt", "must stay outside")
        _write(
            workdir / "probe.py",
            "import json, os, sys\n"
            "print(json.dumps({'visible': [p for p in sys.argv[1:] if os.path.exists(p)],\n"
            "                  'escape': os.path.exists('../outside.txt'),\n"
            "                  'home': os.path.expanduser('~'),\n"
            "                  'dotfiles': sorted(n for n in os.listdir(os.path.expanduser('~'))"
            " if n.startswith('.'))}))\n",
        )
        real_home = Path(os.path.expanduser("~"))
        hidden = [
            str(real_home / (".s" + "sh")),
            str(real_home / (".bash_" + "history")),
            str(tmp_path / "outside.txt"),
            "/var/" + "run/" + "docker.sock",
        ]
        result = run_local(
            ToolRun(argv=[sys.executable, "probe.py", *hidden], workdir=str(workdir))
        )
        assert result.status == "ok", result
        payload = json.loads(result.stdout.strip())
        assert payload["visible"] == [], payload
        assert payload["escape"] is False, payload
        # das Sandbox-Home ist das Arbeitsverzeichnis, keine Host-Dotfiles
        assert Path(payload["home"]).resolve() == workdir.resolve(), payload
        assert payload["dotfiles"] == [], payload


class TestNetworkPolicy:
    @needs_bwrap
    def test_default_policy_has_no_network(self, tmp_path):
        _write(tmp_path / "probe.py", NET_PROBE)
        result = run_local(ToolRun(argv=[sys.executable, "probe.py"], workdir=str(tmp_path)))
        assert result.status == "ok", result
        payload = json.loads(result.stdout.strip())
        assert payload["net"] == "blocked", payload
        assert payload["ifs"] == ["lo"], payload

    @needs_bwrap
    def test_allow_network_drops_the_namespace_isolation(self, tmp_path):
        """Der Policy-Schalter wirkt (ohne Internet-Zwang nachweisbar)."""
        host_ifaces = {name for _i, name in socket.if_nameindex()} - {"lo"}
        if not host_ifaces:
            pytest.skip("host has no interfaces besides lo")
        _write(tmp_path / "probe.py", NET_PROBE)
        result = run_local(
            ToolRun(argv=[sys.executable, "probe.py"], workdir=str(tmp_path)),
            policy=RunPolicy(allow_network=True),
        )
        assert result.status == "ok", result
        payload = json.loads(result.stdout.strip())
        assert set(payload["ifs"]) - {"lo"}, payload


class TestFailClosed:
    def test_missing_sandbox_is_not_certifiable_and_nothing_runs(self, tmp_path, monkeypatch):
        _write(tmp_path / "probe.py", "open('ran.txt', 'w').write('ran')\n")
        monkeypatch.setattr(gates, "_SANDBOX_CACHE", None)
        monkeypatch.setattr(gates.shutil, "which", lambda _name: None)
        result = run_local(
            ToolRun(argv=[sys.executable, "probe.py"], workdir=str(tmp_path))
        )
        assert result.status == "not-certifiable", result
        assert result.ok is False
        assert result.exit_code is None
        assert result.sandboxed is False
        assert "sandbox" in result.detail.lower()
        assert not (tmp_path / "ran.txt").exists()

    def test_runtime_sandbox_failure_is_not_certifiable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            gates,
            "_run",
            lambda *a, **k: (
                execution.NO_SANDBOX_EXIT,
                "sandbox unavailable — execution blocked (fail-closed)",
                False,
            ),
        )
        result = run_local(
            ToolRun(argv=[sys.executable, "-c", "print('ran')"], workdir=str(tmp_path))
        )
        assert result.status == "not-certifiable", result
        assert result.ok is False

    def test_explicit_opt_out_is_marked_never_silent(self, tmp_path, monkeypatch):
        """`require_sandbox=False` ist der einzige unsandboxed-Pfad — und er sagt es."""
        monkeypatch.setattr(gates, "_SANDBOX_CACHE", None)
        monkeypatch.setattr(gates.shutil, "which", lambda _name: None)
        result = run_local(
            ToolRun(argv=[sys.executable, "-c", "print('hi')"], workdir=str(tmp_path)),
            policy=RunPolicy(require_sandbox=False),
        )
        assert result.status == "ok", result
        assert result.sandboxed is False
        assert "unsandboxed" in result.detail

    def test_container_executor_fails_closed_without_docker(self):
        executor = get_container_executor()
        available, reason = executor.available()
        assert available is False
        assert "docker" in reason
        result = executor.run_job(object(), RunPolicy())
        assert result.status == "not-certifiable", result
        assert result.ok is False
        assert "docker" in result.detail


# --------------------------------------------------------------------------- #
# (e) Panel-`action=test` über die Grenze                                      #
# --------------------------------------------------------------------------- #

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)

TOOL_SRC = '''"""Tiny tool for the execution-boundary panel tests."""
def main(n: int = 2) -> dict:
    return {"doubled": n * 2}
'''


def _load_panel():
    spec = importlib.util.spec_from_file_location("godsv_panel_api_boundary", PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def panel():
    return _load_panel()


def _hermes_home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    (home / "godsv").mkdir(parents=True, exist_ok=True)
    return home


def _build_one_tool(home: Path) -> None:
    from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

    result = ToolBuilder(home / "godsv").build(
        ToolBuildRequest(
            capability="double a number for boundary tests",
            name="doubler",
            source_code=TOOL_SRC,
            module="doubler",
            entrypoint="main",
            sample_input={"n": 21},
        )
    )
    assert result["ok"] is True, result


class TestPanelTestRunsThroughTheBoundary:
    def test_panel_test_uses_the_shared_boundary(self, panel, tmp_path, monkeypatch):
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        _build_one_tool(home)
        seen: dict = {}

        def fake_run_local(run, *, policy=None):
            seen["run"] = run
            seen["policy"] = policy
            return RunResult(
                ok=True,
                status="ok",
                exit_code=0,
                stdout='{"doubled": 42}',
                sandboxed=True,
            )

        monkeypatch.setattr(execution, "run_local", fake_run_local)
        result = panel.tool_action_payload("doubler", "test")
        assert result["ok"] is True, result
        assert '"doubled": 42' in result["output"]
        assert "run" in seen, "panel action=test must call godsv.execution.run_local"
        wrapper = home / "godsv" / "generated_tools" / "doubler" / "bin" / "doubler"
        assert seen["run"].argv[0] == str(wrapper), seen["run"].argv
        assert seen["run"].argv[1] == json.dumps({"n": 21})
        assert seen["policy"].require_sandbox is True

    @needs_bwrap
    def test_panel_test_really_runs_inside_the_sandbox(self, panel, tmp_path, monkeypatch):
        """Echter Lauf (kein Mock): die Panel-Probe sieht die Sandbox-Fakten."""
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        probe_src = (
            "import os, socket\n"
            "def main() -> dict:\n"
            "    return {\n"
            "        'cwd': os.getcwd(),\n"
            "        'home': os.path.expanduser('~'),\n"
            "        'ifaces': sorted(name for _i, name in socket.if_nameindex()),\n"
            "    }\n"
        )
        from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

        build = ToolBuilder(home / "godsv").build(
            ToolBuildRequest(
                capability="probe the sandbox for the panel boundary test",
                name="sandbox_probe",
                source_code=probe_src,
                module="sandbox_probe",
                entrypoint="main",
                sample_input={},
            )
        )
        assert build["ok"] is True, build
        result = panel.tool_action_payload("sandbox_probe", "test")
        assert result["ok"] is True, result
        payload = json.loads(result["output"])
        tool_dir = (home / "godsv" / "generated_tools" / "sandbox_probe").resolve()
        assert Path(payload["cwd"]).resolve() == tool_dir, payload
        assert Path(payload["home"]).resolve() == tool_dir, payload
        assert payload["ifaces"] == ["lo"], payload

    def test_route_level_action_test_goes_through_the_boundary(self, panel, tmp_path, monkeypatch):
        """Route-Ebene über TestClient (Muster: tests/test_panel_trust_boundary.py)."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "tok-boundary")
        _build_one_tool(home)
        seen: dict = {}

        def fake_run_local(run, *, policy=None):
            seen["run"] = run
            return RunResult(
                ok=False,
                status="not-certifiable",
                sandboxed=False,
                detail="sandbox unavailable — execution blocked (fail-closed)",
            )

        monkeypatch.setattr(execution, "run_local", fake_run_local)
        app = FastAPI()
        app.include_router(panel.router)
        client = TestClient(app)
        response = client.post(
            "/tools/doubler/action",
            json={"action": "test"},
            headers={
                "origin": "app://hermes",
                "X-Hermes-Session-Token": "tok-boundary",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is False and body["not_certifiable"] is True, body
        assert seen.get("run") is not None, "the route must reach the shared boundary"

    def test_panel_test_without_sandbox_is_not_certifiable(self, panel, tmp_path, monkeypatch):
        """Ohne Sandbox: ehrlich nicht zertifizierbar — kein ungeschützter Rückfall."""
        home = _hermes_home(tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))
        _build_one_tool(home)
        seen: dict = {}

        def fake_run_local(run, *, policy=None):
            seen["run"] = run
            return RunResult(
                ok=False,
                status="not-certifiable",
                sandboxed=False,
                detail="sandbox unavailable — execution blocked (fail-closed)",
            )

        monkeypatch.setattr(execution, "run_local", fake_run_local)
        result = panel.tool_action_payload("doubler", "test")
        assert result["ok"] is False, result
        assert "sandbox" in result["output"].lower(), result
        assert result.get("not_certifiable") is True, result
        assert seen["run"].argv, "the panel must reach the boundary, not run the wrapper itself"
        # der Wrapper wurde wirklich nicht ausgeführt (kein Usage-Ledger-Eintrag)
        assert not (home / "godsv" / "tool_usage.jsonl").exists()


# --------------------------------------------------------------------------- #
# (f) Ergebnisvertrag der Testing-Tools bleibt unverändert                     #
# --------------------------------------------------------------------------- #


class TestTestingToolResultContract:
    def _runner(self, tmp_path, monkeypatch, simulated: RunResult):
        from skaldir import capability

        module = tmp_path / "checker.py"
        _write(module, "print('PASS: ok')\n")
        calls: dict = {}

        def fake_run_local(run, *, policy=None):
            calls["run"] = run
            return simulated

        monkeypatch.setattr(execution, "run_local", fake_run_local)
        return capability._testing_tool_runner(tmp_path, "checker", module), module, calls

    def test_pass_verdict_yields_runner_evidence(self, tmp_path, monkeypatch):
        simulated = RunResult(
            ok=True, status="ok", exit_code=0, stdout="PASS: doubled 21 to 42\n", sandboxed=True
        )
        runner, module, calls = self._runner(tmp_path, monkeypatch, simulated)
        target = str(tmp_path / "target")
        evidence = runner(target)
        assert evidence.source == "runner"
        assert evidence.check_id == "checker" and evidence.name == "checker"
        assert evidence.status == "passed" and evidence.ok is True
        assert evidence.cmd == f"python {module.name} {target}"
        assert calls["run"].extra_binds == [target]
        assert "PASS" in evidence.detail

    @pytest.mark.parametrize(
        "simulated, expected_detail",
        [
            (
                RunResult(ok=True, status="ok", exit_code=0, stdout="all fine\n"),
                "no explicit PASS",
            ),
            (
                RunResult(
                    ok=True,
                    status="ok",
                    exit_code=0,
                    stdout="FAIL: check did not PASS\n",
                ),
                "FAIL",
            ),
            (
                RunResult(ok=False, status="failed", exit_code=1, stdout="PASS\n"),
                "exit 1",
            ),
        ],
    )
    def test_contract_fails_closed(self, tmp_path, monkeypatch, simulated, expected_detail):
        runner, _module, _calls = self._runner(tmp_path, monkeypatch, simulated)
        evidence = runner(str(tmp_path / "target"))
        assert evidence.status == "failed" and evidence.ok is False
        assert expected_detail in evidence.detail, evidence.detail

    def test_sandbox_unavailable_is_a_failure_never_a_pass(self, tmp_path, monkeypatch):
        simulated = RunResult(
            ok=False,
            status="not-certifiable",
            sandboxed=False,
            detail="sandbox unavailable — execution blocked (fail-closed)",
        )
        runner, _module, _calls = self._runner(tmp_path, monkeypatch, simulated)
        evidence = runner(str(tmp_path / "target"))
        assert evidence.status == "failed" and evidence.ok is False
        assert "sandbox" in evidence.detail.lower(), evidence.detail
