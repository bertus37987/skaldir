"""Read-only verification must be enforced by the actual sandbox mount."""
import sys

import pytest

from skaldir import gates


def test_declared_check_cannot_write_readonly_workspace(tmp_path):
    if not gates._sandbox_available():
        pytest.skip("bubblewrap unavailable")
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    code, output, sandboxed = gates.run_declared_check(
        [sys.executable, "-c",
         "from pathlib import Path; Path('victim.txt').write_text('changed')"],
        workdir=tmp_path, timeout=10, read_only_workspace=True,
    )
    assert sandboxed
    assert code != 0
    assert "Read-only file system" in output
    assert victim.read_text() == "original"


def test_declared_read_check_runs_with_exact_argv(tmp_path):
    if not gates._sandbox_available():
        pytest.skip("bubblewrap unavailable")
    code, output, sandboxed = gates.run_declared_check(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", "a b; literal"],
        workdir=tmp_path, timeout=10, read_only_workspace=True,
    )
    assert (code, output, sandboxed) == (0, "a b; literal", True)
