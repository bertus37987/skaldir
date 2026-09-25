"""Exercise the installer without downloads or touching the user's home."""
import os
import shutil
import subprocess
from pathlib import Path


def test_installer_is_cli_only_and_links_skaldir(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    shutil.copyfile(Path(__file__).parents[1] / "install.sh", root / "install.sh")
    bindir = root / ".venv" / "bin"
    bindir.mkdir(parents=True)
    calls = tmp_path / "calls"
    for name in ("python", "skaldir", "godsvoice"):
        script = bindir / name
        script.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
        script.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
    uv.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), CALLS=str(calls),
               PATH=str(tools) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(["bash", str(root / "install.sh")], env=env,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (home / ".local/bin/skaldir").resolve() == bindir / "skaldir"
    assert "desktop" not in calls.read_text()
    assert "skaldir setup" in result.stdout
    assert "Fertig" not in result.stdout
