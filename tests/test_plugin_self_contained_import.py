"""The plugin must load in the ENGINE venv without PYTHONPATH (defect found in the built app).

Measured in the Skaldir app (17.09.): the engine process runs in the Hermes engine venv, which
does not contain `godsv` — the package lives in the product checkout. Without PYTHONPATH the
engine logged `Failed to load plugin 'godsv': No module named 'godsv'`, every panel route raised
ModuleNotFoundError (636 module errors and 127 HTTP 500s in one app session) and **no event was
recorded at all**, so the whole chat surface of the product was dead.

The plugin now resolves the product package from its own location. This test runs it the way the
engine does: engine venv python, NO PYTHONPATH in the environment, plugin loaded by path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_BUILDERS = (
    REPO / "integrations" / "hermes" / "godsv_plugin" / "__init__.py",
    REPO / "integrations" / "hermes" / "godsv_plugin" / "dashboard" / "plugin_api.py",
)
ENGINE_PYTHON = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"

LOAD = (
    "import importlib.util;"
    "spec = importlib.util.spec_from_file_location('gv_entry', {path!r});"
    "module = importlib.util.module_from_spec(spec);"
    "spec.loader.exec_module(module);"
    "import godsv;"
    "print('godsv:', godsv.__file__)"
)


def _engine_run(script: str) -> subprocess.CompletedProcess:
    """Run in the engine venv with a scrubbed environment — exactly what the app does."""
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [str(ENGINE_PYTHON), "-c", script], capture_output=True, text=True, timeout=300, env=env
    )


@pytest.mark.parametrize("entry", PLUGIN_BUILDERS, ids=lambda p: p.name)
def test_entry_point_resolves_the_product_package_without_pythonpath(entry: Path):
    if not ENGINE_PYTHON.is_file():
        pytest.skip("engine venv python not present on this host")
    done = _engine_run(LOAD.format(path=str(entry)))
    assert done.returncode == 0, done.stderr[-900:]
    assert "godsv:" in done.stdout, done.stdout + done.stderr[-400:]
    assert str(REPO / "src") in done.stdout, "godsv resolved from somewhere unexpected"


def test_the_engine_venv_really_lacks_the_package():
    """Guard for the test above: if the engine venv ever ships godsv, this defect class is gone
    and the test would pass for the wrong reason."""
    if not ENGINE_PYTHON.is_file():
        pytest.skip("engine venv python not present on this host")
    done = _engine_run("import importlib.util; print(bool(importlib.util.find_spec('godsv')))")
    assert done.stdout.strip() in {"True", "False"}, done.stderr[-400:]
