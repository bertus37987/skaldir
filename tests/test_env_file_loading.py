"""``skaldir setup`` writes the keys to ``.env``; the next command must read them.

The measured gap (UX hand-off): ``setup`` validates the two keys from ``.env`` (0600,
beside ``config.yaml``), while every command read only the process environment — so a user
who had just run setup was told "no credentials" by the very next command.

Two properties, both taken through the **real CLI entry point** (a subprocess, so the whole
path is under test — ``cli.main`` → ``env.load_env_file_into`` → the provider):

1. a key that exists **only** in ``.env`` reaches a run;
2. a real environment variable still **wins** over the same name in the file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from test_agent_loop import StubEndpoint, answer

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

BASE_URL = "http://127.0.0.1:9/v1"  # a dead port: if this were used, the run would fail
MODEL = "stub-env-file"
STUB_KEY = "stub-env-key"


def _env_file(tmp_path: Path, *, base_url: str = BASE_URL) -> Path:
    """Write the credentials file where ``setup`` puts it: beside the config."""
    home = tmp_path / "godsv"
    home.mkdir(parents=True, exist_ok=True)
    path = home / ".env"
    path.write_text(
        "\n".join(
            [
                "# written by `skaldir setup`",
                f"SKALDIR_LLM_BASE_URL={base_url}",
                f"SKALDIR_LLM_MODEL={MODEL}",
                f"SKALDIR_LLM_API_KEY={STUB_KEY}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _child_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A clean environment: no provider key of ours is exported, only the file carries one."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(SRC),
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "GODSV_HOME": str(tmp_path / "godsv"),
        "HERMES_HOME": str(tmp_path / "hermes"),
    }
    for name in ("SKALDIR_LLM_BASE_URL", "SKALDIR_LLM_MODEL", "SKALDIR_LLM_API_KEY",
                 "OPENAI_BASE_URL", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        env.pop(name, None)
    env.update(extra)
    return env


def _run_cli(tmp_path: Path, argv: list[str], **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "skaldir", *argv],
        cwd=str(REPO_ROOT),
        env=_child_env(tmp_path, **extra),
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_a_key_that_exists_only_in_the_env_file_reaches_a_real_run(tmp_path: Path):
    """``setup`` then the next command: the run must reach the provider the file names."""
    _env_file(tmp_path)
    with StubEndpoint([answer("hello from the stub")]) as stub:
        _env_file(tmp_path, base_url=stub.base_url)
        done = _run_cli(tmp_path, ["agent", "run", "say hello", "--json", "--max-steps", "2"])

    assert done.returncode == 0, done.stderr[-1500:]
    payload = json.loads(done.stdout)
    assert payload["command"] == "agent.run"
    assert payload["data"]["status"] == "answered"
    assert payload["data"]["answer"] == "hello from the stub"
    assert stub.requests, "the .env-only provider was never called — the run used nothing"


def test_a_real_environment_variable_beats_the_file(tmp_path: Path):
    """The file is a default, never an override: an exported variable wins outright."""
    _env_file(tmp_path)  # a dead port
    with StubEndpoint([answer("answered through the exported base url")]) as stub:
        done = _run_cli(
            tmp_path,
            ["agent", "run", "say hello", "--json", "--max-steps", "2"],
            SKALDIR_LLM_BASE_URL=stub.base_url,
            SKALDIR_LLM_MODEL=MODEL,
            SKALDIR_LLM_API_KEY=STUB_KEY,
        )

    assert done.returncode == 0, done.stderr[-1500:]
    payload = json.loads(done.stdout)
    assert payload["data"]["answer"] == "answered through the exported base url"
    assert stub.requests, "the exported base URL was ignored in favour of the file"
    assert str(payload["data"]["provider"]["base_url"]) == stub.base_url


def test_the_loader_reports_what_it_loaded_and_what_it_kept(tmp_path: Path, monkeypatch):
    """The helper itself: names only, one report, an already-set name never overwritten."""
    from skaldir import env as env_module

    home = tmp_path / "godsv"
    home.mkdir()
    path = home / ".env"
    path.write_text(
        "SKALDIR_LLM_BASE_URL=http://from-file/v1\nSKALDIR_LLM_MODEL=from-file\n",
        encoding="utf-8",
    )
    target = {"SKALDIR_LLM_MODEL": "from-environment"}
    report = env_module.load_env_file_into(target, path=path)
    assert report["exists"] is True
    assert report["loaded"] == ["SKALDIR_LLM_BASE_URL"]
    assert report["kept"] == ["SKALDIR_LLM_MODEL"]
    assert target["SKALDIR_LLM_BASE_URL"] == "http://from-file/v1"
    assert target["SKALDIR_LLM_MODEL"] == "from-environment"

    # an override is explicit, and a missing file is a report, never an exception
    report = env_module.load_env_file_into(
        dict(target), path=path, override=True
    )
    assert report["loaded"] == ["SKALDIR_LLM_BASE_URL", "SKALDIR_LLM_MODEL"]
    missing = env_module.load_env_file_into({}, path=tmp_path / "nope" / ".env")
    assert missing["exists"] is False and missing["error"] == ""


def test_the_cli_entry_point_does_not_raise_when_there_is_no_env_file(tmp_path: Path):
    """A build with no credentials file must behave exactly as before."""
    done = _run_cli(tmp_path, ["--version"])
    assert done.returncode == 0
    assert "skaldir" in done.stdout.lower()
