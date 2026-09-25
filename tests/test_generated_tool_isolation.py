"""Block 1 / Defekt 5 — generierte Tools laufen isoliert, nicht in-process.

Befund vorher: ``mcpserver._wrapper_for`` importierte das generierte Modul in
den MCP-Serverprozess und rief es direkt auf (``function(**arguments)``). Ein
„approved“ Tool hatte damit vollen Host-Zugriff (FS/Netz/Env); nur die
Build-Gates waren sandboxed. ``allowed_domains`` im Manifest war rein
deklarativ.

Rot-first-Beleg (Wrapper auf den alten in-process-Pfad gesetzt, echter Lauf)::

    $ .venv/bin/python -m pytest tests/test_generated_tool_isolation.py -q
    FFFFF  (failed) — fremde /tmp-Datei und ~/.ssh sichtbar, Netz nicht gesperrt

Vertrag nach dem Fix: generierte Tools (``<base_dir>/generated_tools/<name>/``)
werden über ``gates._run`` (bwrap) ausgeführt — derselbe Sandboxpfad wie die
Gates: Netz-Namespace entzogen, Home/root/tmp leer, das Tool-Verzeichnis
beschreibbar. Fehlt die Sandbox, läuft NICHTS (fail-closed).

Restgrenze (bewusst, im Test festgehalten): die Domänenfreigabe ist weiterhin
deklarativ — es gibt keine per-Domain-Erlaubnis, das Netz ist global zu.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from skaldir import gates
from skaldir.mcpserver import _wrapper_for
from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore

pytest.importorskip("fastmcp", reason="MCP-Tests brauchen das Extra: godsv[mcp]")

BWRAP = shutil.which("bwrap") is not None
needs_bwrap = pytest.mark.skipif(not BWRAP, reason="bwrap required")

PROBE = (
    "import os\n"
    "import socket\n"
    "import pathlib\n"
    "\n"
    "def main() -> dict:\n"
    "    home = pathlib.Path.home()\n"
    "    visible = [p.name for p in (home.iterdir() if home.exists() else [])]\n"
    "    marker = os.environ.get('GODSV_ISO_PROBE', '')\n"
    "    try:\n"
    "        socket.create_connection(('1.1.1.1', 80), timeout=3).close()\n"
    "        net = 'open'\n"
    "    except Exception:\n"
    "        net = 'blocked'\n"
    "    return {\n"
    "        'cwd': os.getcwd(),\n"
    "        'home': str(home),\n"
    "        'home_entries': visible,\n"
    "        'foreign_tmp': bool(marker) and os.path.exists(marker),\n"
    "        'tmp_entries': sorted(os.listdir('/tmp'))[:20],\n"
    "        'network': net,\n"
    "        'env_keys': sorted(k for k in os.environ if k.startswith('GODSV_')),\n"
    "        'wrote': _write_probe(),\n"
    "    }\n"
    "\n"
    "def _write_probe() -> bool:\n"
    "    try:\n"
    "        pathlib.Path('tool_ran_here.txt').write_text('ok', encoding='utf-8')\n"
    "        return True\n"
    "    except Exception:\n"
    "        return False\n"
)


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "runtime"
    tool_dir = root / "generated_tools" / "probe_tool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "probe_mod.py").write_text(PROBE, encoding="utf-8")
    return root


def _spec(name: str = "probe_tool") -> ToolSpec:
    return ToolSpec(
        name=name,
        title="Probe",
        description="isolation probe",
        kind="python",
        module="probe_mod",
        entrypoint="main",
        status=STATUS_APPROVED,
    )


def _call(runtime, *, arguments=None):
    wrapper = _wrapper_for(_spec(), base_dir=runtime)
    return wrapper(arguments or {})


class TestGeneratedToolsRunSandboxed:
    @needs_bwrap
    def test_foreign_tmp_and_home_are_invisible(self, runtime, tmp_path):
        """Die Kernprobe: fremde /tmp-Datei und Home-Inhalt sind nicht sichtbar."""
        foreign_dir = Path(f"/tmp/gv-iso-{os.getpid()}")
        foreign_dir.mkdir(exist_ok=True)
        foreign = foreign_dir / "secret.txt"
        foreign.write_text("must not be visible", encoding="utf-8")
        try:
            result = _call(runtime)
            assert isinstance(result, dict), result
            assert "error" not in result, result
            assert result["foreign_tmp"] is False, result
            assert "gv-iso-" + str(os.getpid()) not in result["tmp_entries"], result
            # Home ist leer: keine .ssh/.config/.bash_history im Sandbox-Home
            for marker in (".ssh", ".config", ".bash_history", ".gitconfig"):
                assert marker not in result["home_entries"], result
            # Das echte Home ist gar nicht sichtbar (gleicher Name, leerer Inhalt)
            assert result["home"] == result["cwd"], result
        finally:
            shutil.rmtree(foreign_dir, ignore_errors=True)

    @needs_bwrap
    def test_network_namespace_is_unshared(self, runtime):
        """Netz ist zu — `allowed_domains` bleibt deklarativ (Restgrenze)."""
        result = _call(runtime)
        assert "error" not in result, result
        assert result["network"] == "blocked", result

    @needs_bwrap
    def test_the_tool_directory_stays_writable(self, runtime):
        """Legitime Arbeit bleibt möglich: das Tool-Verzeichnis ist beschreibbar."""
        result = _call(runtime)
        assert "error" not in result, result
        assert result["wrote"] is True, result
        tool_dir = runtime / "generated_tools" / "probe_tool"
        assert (tool_dir / "tool_ran_here.txt").read_text(encoding="utf-8") == "ok"

    @needs_bwrap
    def test_host_environment_is_not_inherited(self, runtime, monkeypatch):
        """Der Sandboxprozess bekommt einen Minimal-Env, nicht den Host-Env."""
        monkeypatch.setenv("GODSV_ISO_PROBE", "/tmp/whatever")
        result = _call(runtime)
        assert "error" not in result, result
        assert result["env_keys"] == [], result

    @needs_bwrap
    def test_cwd_is_the_tool_directory(self, runtime):
        result = _call(runtime)
        assert "error" not in result, result
        assert Path(result["cwd"]).resolve() == (
            runtime / "generated_tools" / "probe_tool"
        ).resolve()


class TestFailsClosedWithoutSandbox:
    def test_missing_bwrap_blocks_execution(self, runtime, monkeypatch):
        """Ohne Sandbox läuft NICHTS — kein stiller Rückfall auf in-process."""
        monkeypatch.setattr(gates, "_SANDBOX_CACHE", None)
        monkeypatch.setattr(gates.shutil, "which", lambda _name: None)
        result = _call(runtime)
        assert isinstance(result, dict), result
        assert "error" in result, result
        assert "sandbox" in str(result["error"]).lower(), result
        # Der Beweis, dass die Tool-Funktion wirklich NICHT lief:
        assert not (runtime / "generated_tools" / "probe_tool" / "tool_ran_here.txt").exists()

    def test_bwrap_runtime_failure_blocks_execution(self, runtime, monkeypatch):
        """bwrap scheitert zur Laufzeit (z. B. Namespace-Setup) ⇒ blockiert."""
        monkeypatch.setattr(
            gates,
            "_run",
            lambda *a, **k: (
                -127,
                "sandbox unavailable — execution blocked (fail-closed)",
                False,
            ),
        )
        result = _call(runtime)
        assert "error" in result, result
        assert "sandbox" in str(result["error"]).lower(), result


class TestResultContract:
    @needs_bwrap
    def test_structured_result_is_returned(self, runtime):
        result = _call(runtime)
        assert isinstance(result, dict), result
        assert result.get("cwd") and result.get("network"), result

    @needs_bwrap
    def test_tool_crash_is_a_structured_error_not_a_server_crash(self, runtime):
        (runtime / "generated_tools" / "probe_tool" / "probe_mod.py").write_text(
            "def main() -> dict:\n    raise RuntimeError('tool exploded')\n",
            encoding="utf-8",
        )
        result = _call(runtime)
        assert isinstance(result, dict), result
        assert "error" in result, result
        assert "RuntimeError" in str(result["error"]) or "exploded" in str(result["error"])

    def test_syspath_fixture_stays_in_process_documented_residual(self, tmp_path, monkeypatch):
        """Restgrenze: Specs ohne generierte Ablage (base_dir=None) laufen in-process.

        Solche Tools stammen aus der Registry/manuellen Tests, nicht aus dem
        Builder — für generierte Tools greift immer der Sandboxpfad.
        """
        module = tmp_path / "fixture_mod.py"
        module.write_text(
            "def main() -> dict:\n    return {'in_process': True}\n", encoding="utf-8"
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        spec = _spec(name="fixture_tool")
        spec.module = "fixture_mod"
        result = _wrapper_for(spec)()
        assert result == {"in_process": True}, result


class TestUsageLedgerStillRecords:
    @needs_bwrap
    def test_sandboxed_call_is_recorded_in_the_usage_ledger(self, runtime):
        _call(runtime)
        ledger = runtime / "tool_usage.jsonl"
        assert ledger.exists(), "sandboxed MCP call must still land in the usage ledger"
        entry = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert entry["name"] == "probe_tool"
        assert entry["ok"] is True


class TestRealBuilderOutputIsIsolated:
    @needs_bwrap
    def test_builder_generated_tool_runs_through_the_sandbox(self, tmp_path):
        """Echter Builder-Lauf → echter MCP-Aufruf über den Sandboxpfad."""
        from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

        runtime = tmp_path / "rt"
        build = ToolBuilder(runtime).build(
            ToolBuildRequest(
                capability="double a number, isolated",
                name="doubler",
                source_code=(
                    "import os\n"
                    "def main(n: int = 2) -> dict:\n"
                    "    return {'doubled': n * 2, 'here': os.getcwd()}\n"
                ),
                module="doubler",
                entrypoint="main",
                sample_input={"n": 21},
            )
        )
        assert build.get("ok"), build
        store = ToolStore(runtime / "tools.db")
        try:
            spec = store.get("doubler")
        finally:
            store.close()
        assert spec is not None
        result = _wrapper_for(spec, base_dir=runtime)({"n": 5})
        assert isinstance(result, dict), result
        assert result.get("error") is None or "error" not in result, result
        assert result["doubled"] == 10, result
        assert Path(result["here"]).resolve() == (
            runtime / "generated_tools" / "doubler"
        ).resolve()
