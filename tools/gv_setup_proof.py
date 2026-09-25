#!/usr/bin/env python3
"""End-to-end proof of `skaldir setup` against two local stub endpoints.

Runs the REAL installed console script (``.venv/bin/skaldir``) against a real
``ThreadingHTTPServer`` on 127.0.0.1 for BOTH mandatory keys — no real credential
is needed or used. Every command's raw stdout and exit code are recorded.

    .venv/bin/python tools/gv_setup_proof.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from test_setup_flow import StubEndpoint  # noqa: E402

SKALDIR = REPO / ".venv" / "bin" / "skaldir"
FAKE_KEY = "sk-" + "proof" * 6


def run(argv: list[str], env: dict[str, str], *, label: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(SKALDIR), *argv],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = proc.stdout.strip()
    err = proc.stderr.strip()
    print(f"\n$ skaldir {' '.join(argv)}")
    print(f"[{label}] exit={proc.returncode}")
    if out:
        print(out)
    if err:
        print("stderr:", err)
    return proc.returncode, out


def base_env(runtime: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SKALDIR_", "GODSV_", "HERMES_", "OPENAI_"))
    }
    env["SKALDIR_HOME"] = str(runtime)
    env["PYTHONPATH"] = str(REPO / "src")
    env["TMPDIR"] = "/tmp"
    env["STUB_JEV_KEY"] = FAKE_KEY
    env["STUB_LLM_KEY"] = FAKE_KEY
    return env


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="gv-setup-proof-"))
    runtime = work / "runtime"
    config = runtime / "config.yaml"
    env = base_env(runtime)
    llm = StubEndpoint(model="proof-llm")
    jev = StubEndpoint(model="proof-jev")
    failures: list[str] = []

    def check(condition: bool, note: str) -> None:
        print(("  PASS  " if condition else "  FAIL  ") + note)
        if not condition:
            failures.append(note)

    try:
        print("=" * 78)
        print("STEP 0 — the CLI is usable WITHOUT setup, and says what is missing")
        print("=" * 78)
        code, out = run(["--version"], env, label="version")
        check(code == 0, "skaldir --version works with no config at all")
        code, out = run(["setup", "--state", "--json"], env, label="state before")
        check(code == 3, "setup --state exits 3 (a mandatory key is missing)")
        state = json.loads(out)
        check(state["data"]["configured"] is False, "state says: not configured")
        check(
            len(state["data"]["degraded"]["missing_keys"]) == 2,
            "state names BOTH missing keys",
        )
        code, out = run(["setup", "--check", "--json"], env, label="check before")
        check(code == 3, "setup --check exits 3 before setup (nothing configured)")

        print()
        print("=" * 78)
        print("STEP 1 — a literal key in an env var NAME is refused")
        print("=" * 78)
        code, out = run(
            [
                "setup",
                "--llm-base-url", llm.base_url,
                "--llm-model", "proof-llm",
                "--llm-api-key-env", FAKE_KEY,
                "--jev-base-url", jev.base_url,
                "--jev-model", "proof-jev",
                "--jev-api-key-env", "STUB_JEV_KEY",
                "--json",
            ],
            env,
            label="literal key",
        )
        check(code == 3, "a literal key is refused (exit 3)")
        check(FAKE_KEY not in out, "the refused literal key is NOT echoed to stdout")
        check(not config.exists(), "nothing was written")

        print()
        print("=" * 78)
        print("STEP 2 — `skaldir setup` with both keys (headless, no TTY)")
        print("=" * 78)
        code, out = run(
            [
                "setup",
                "--jev-base-url", jev.base_url,
                "--jev-model", "proof-jev",
                "--jev-api-key-env", "STUB_JEV_KEY",
                "--llm-base-url", llm.base_url,
                "--llm-model", "proof-llm",
                "--llm-api-key-env", "STUB_LLM_KEY",
                "--yes",
                "--json",
            ],
            env,
            label="setup",
        )
        check(code == 0, "setup exits 0")
        check(config.is_file(), f"the config file exists: {config}")
        payload = json.loads(out)["data"]
        for role in ("typesafe", "llm"):
            item = payload["roles"][role]
            check(item["ok"] is True, f"{role}: validation ok")
            measurement = item.get("measure") or item
            check(
                bool(measurement.get("model_echo")),
                f"{role}: reported a measured model echo ({measurement.get('model_echo')})",
            )
            check(
                measurement.get("streaming", {}).get("supported") is True,
                f"{role}: streaming measured as supported",
            )
            check(
                bool(measurement.get("structured", {}).get("mode_used")),
                f"{role}: structured mode measured "
                f"({measurement.get('structured', {}).get('mode_used')})",
            )
        check(
            payload["verify"]["llm_completion"]["ok"] is True,
            "verified: one real completion through the written config",
        )
        check(
            payload["verify"]["jev_decision"]["ok"] is True,
            "verified: one real Jev decision through the written config "
            f"(status={payload['verify']['jev_decision']['status']})",
        )
        check(payload["write"]["written"] is True, "the write is reported")
        check(FAKE_KEY not in out, "no credential value appears in the report")
        check(FAKE_KEY not in config.read_text(), "no credential value is in the config")

        print()
        print("=" * 78)
        print("STEP 3 — the config file shape (placeholders only)")
        print("=" * 78)
        raw = config.read_text()
        print(raw)
        for absent in ("api_key:", "apikey:", "secret:", "token:", "password:"):
            check(absent not in raw, f"the config carries no literal '{absent}'")
        check("api_key_env" in raw, "the config names credential VARIABLES")

        print()
        print("=" * 78)
        print("STEP 4 — `skaldir setup --check` re-validates; exit 0 only if both answer")
        print("=" * 78)
        code, out = run(["setup", "--check", "--json"], env, label="check after")
        check(code == 0, "setup --check exits 0 with a working config")
        checked = json.loads(out)["data"]
        check(checked["ok"] is True, "--check reports ok")
        backups = list(runtime.glob("*.bak"))
        check(not backups, "--check wrote nothing (no backup was taken)")

        print()
        print("=" * 78)
        print("STEP 5 — a second setup keeps a byte-for-byte backup")
        print("=" * 78)
        before = config.read_text()
        code, out = run(
            [
                "setup",
                "--jev-base-url", jev.base_url,
                "--jev-model", "proof-jev-2",
                "--jev-api-key-env", "STUB_JEV_KEY",
                "--llm-base-url", llm.base_url,
                "--llm-model", "proof-llm-2",
                "--llm-api-key-env", "STUB_LLM_KEY",
                "--yes",
                "--json",
            ],
            env,
            label="setup again",
        )
        check(code == 0, "the second setup exits 0")
        backups = list(runtime.glob("config.yaml.pre-provider-*.bak"))
        check(bool(backups), "a timestamped backup was written")
        if backups:
            check(
                backups[0].read_text() == before,
                "the backup is the byte-for-byte previous config",
            )

        print()
        print("=" * 78)
        print("STEP 6 — --check goes red when a provider stops working")
        print("=" * 78)
        llm.close()
        code, out = run(["setup", "--check", "--json"], env, label="check with a dead LLM")
        check(code != 0, "setup --check exits non-zero when a key no longer answers")
        dead = json.loads(out)["data"]
        check(dead["roles"]["llm"]["ok"] is False, "the dead LLM key is reported as failing")
        check(dead["roles"]["typesafe"]["ok"] is True, "the healthy Jev key still passes")

        print()
        print("=" * 78)
        print("STEP 7 — a remote endpoint with no credential exits 4, no request made")
        print("=" * 78)
        remote_runtime = work / "remote"
        remote_env = base_env(remote_runtime)
        code, out = run(
            [
                "setup",
                "--jev-base-url", "https://api.example.com/v1",
                "--jev-model", "jev-latest",
                "--jev-api-key-env", "ABSENT_JEV_KEY",
                "--llm-base-url", "https://api.example.com/v1",
                "--llm-model", "gpt-4o-mini",
                "--llm-api-key-env", "ABSENT_LLM_KEY",
                "--yes",
                "--json",
            ],
            remote_env,
            label="no credential",
        )
        check(code == 4, "exit 4: a remote endpoint with no credential")
        blob = json.loads(out)["data"]
        check(
            "ABSENT_LLM_KEY" in json.dumps(blob) or "ABSENT_JEV_KEY" in json.dumps(blob),
            "the missing variable NAME is named",
        )
        check(
            not (remote_runtime / "config.yaml").exists(),
            "nothing was written for an unusable key",
        )
    finally:
        llm.close()
        jev.close()

    print()
    print("=" * 78)
    print(f"RESULT: {'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILED'}")
    for note in failures:
        print("  - " + note)
    print(f"workdir: {work}")
    print("=" * 78)
    if not failures:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
