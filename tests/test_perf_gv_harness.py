"""GV-Perf-Harness-Kopie (tools/perf-gv): Smoke-Tests ohne App-Start.

Der Harness ist Node-Code; geprüft werden genau die Pfade, die **keinen** Build und
**keinen** Prozess-Spawn auslösen: `--help` (Exit 0), ein unbekanntes Szenario
(Exit 2, vor jedem Netz-/Prozesszugriff) und die per direktem Import aufgerufenen
P1-Helfer (Binary-Override + Env-Hygiene). Kein echter Build, kein App-Start/Kill.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "tools" / "perf-gv"
RUNNER = HARNESS / "run.mjs"
LAUNCH = HARNESS / "lib" / "launch.mjs"
DEFAULT_BASELINE = HARNESS / "baseline.json"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _node(*args: str, env: dict | None = None, timeout: int = 120):
    assert NODE is not None
    return subprocess.run(  # nosec B603 - fixed argv, local script, no shell
        [NODE, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(REPO),
    )


def _env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERF_")}
    env.update(extra)
    return env


def test_help_exits_zero_and_documents_the_gv_extensions():
    proc = _node(str(RUNNER), "--help", env=_env())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    # P1 + P2 + P3 are documented, and the P3 default baseline is the repo copy
    # (not the app-src mirror that every build-app rsync overwrites).
    assert "PERF_APP_BINARY" in out
    assert "--match" in out
    assert "--baseline" in out
    assert str(DEFAULT_BASELINE) in out


def test_unknown_scenario_exits_two_before_any_spawn():
    proc = _node(str(RUNNER), "no-such-scenario", env=_env())
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unknown scenario" in proc.stdout + proc.stderr
    assert "known:" in proc.stdout + proc.stderr


def test_p1_override_and_child_env_hygiene(tmp_path):
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        """
const m = await import(process.env.PERF_TEST_LAUNCH)
const env = m.childEnv({
  home: '/tmp/perf-gv-home',
  port: 9400,
  devUrl: null,
  base: {
    PATH: '/usr/bin',
    HERMES_DESKTOP_USER_DATA_DIR: '/home/someone/.hermes/godsv/desktop-userdata',
    HERMES_DESKTOP_BOOT_FAKE: '1',
    HERMES_DESKTOP_DEV_SERVER: 'http://127.0.0.1:5174'
  }
})
console.log(JSON.stringify({ override: m.appOverride(), env }))
""",
        encoding="utf-8",
    )
    fake_binary = str(tmp_path / "linux-unpacked" / "gods-voice")
    proc = _node(
        str(probe),
        env=_env(PERF_TEST_LAUNCH=LAUNCH.as_uri(), PERF_APP_BINARY=fake_binary),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["override"] == fake_binary
    env = payload["env"]
    assert env["HERMES_HOME"] == "/tmp/perf-gv-home"
    assert env["HERMES_DESKTOP_CDP_PORT"] == "9400"
    # the two ambient knobs must never reach the isolated child
    assert "HERMES_DESKTOP_USER_DATA_DIR" not in env
    assert "HERMES_DESKTOP_BOOT_FAKE" not in env
    assert "HERMES_DESKTOP_DEV_SERVER" not in env


def test_copy_is_a_complete_harness():
    for rel in (
        "run.mjs",
        "serve.mjs",
        "baseline.json",
        "README.md",
        "lib/cdp.mjs",
        "lib/stats.mjs",
        "lib/baseline.mjs",
        "lib/launch.mjs",
        "lib/paths.mjs",
        "scenarios/index.mjs",
        "scenarios/cold-start.mjs",
    ):
        assert (HARNESS / rel).is_file(), f"missing harness file: {rel}"
