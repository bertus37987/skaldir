"""Desktop-Panel load-smoke: runs tools/check_desktop_plugin.mjs with node (skips without node)."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "check_desktop_plugin.mjs"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_desktop_panel_contract_loads():
    assert NODE is not None
    proc = subprocess.run(  # nosec B603 - fixed argv, local script, no shell
        [NODE, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "contract OK" in proc.stdout
