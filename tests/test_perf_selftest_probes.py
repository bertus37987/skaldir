"""Probe *cost* of `skaldir selftest --json`: no provider request, one command tree.

The regression this file pins (measured on this box, 2026-09-20): the settings named
``provider=llm_system_one_adapter`` and the runtime's ``.env`` held a credential, so the
ROUTE probe's ``advise()`` spent **one real LLM call of 2.1-2.3 s** — 70 % of the whole
command (3050 ms of 3537 ms), which pushed its median past the 3 s startup budget in
``tools/gv_cli_perf.py: BUDGETS["selftest_ms"]`` while every other check stayed green.
A *reachability* selftest cannot be gated on the network: the probe now answers offline
on the runtime's replay fixture and reports the mode the settings resolve to.

Asserted here:

1. the ROUTE probe answers with a live-looking provider configured and an endpoint that
   refuses connections — so a probe that still sends the request fails instead of passing;
2. the shipped command makes no provider request at all under a black-hole endpoint (a
   request there would only return after the HTTP timeout, so the subprocess bound is the
   measurement);
3. the three probes that read the command surface build ``build_parser()`` once per
   invocation, and never inherit the previous invocation's tree.

Deliberately NOT here: the wall-clock budgets themselves (median against budget, worst
against a ceiling) — those live in ``tests/test_perf_cli_budget.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skaldir import cli, cli_contract
from skaldir.jev import runtime as jev_runtime

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
CLI_CODE = "import sys;from skaldir.cli import main;sys.exit(main())"

#: Refuses connections immediately (nothing listens on port 1): a probe that really
#: sends the request gets no evaluation, quickly — the failure is visible, not a hang.
REFUSED_ENDPOINT = "http://127.0.0.1:1/v1"

#: Swallows every connection attempt: a request sent here can only come back after the
#: HTTP timeout, so "answered within the bound below" is proof no request was sent.
BLACKHOLE_ENDPOINT = "http://10.255.255.1:1234/v1"


def _live_settings_runtime(tmp_path: Path) -> Path:
    """A runtime whose settings name the live LLM adapter (no credential stored in it)."""
    base = tmp_path / "selftest-rt"
    base.mkdir(parents=True, exist_ok=True)
    jev_runtime.enable(base, provider="llm_system_one_adapter")
    return base


def _live_provider_env(*, endpoint: str) -> dict[str, str]:
    """The environment of a box with a credential and an endpoint to spend it on."""
    return {
        "SKALDIR_LLM_BASE_URL": endpoint,
        "SKALDIR_LLM_MODEL": "selftest-perf-probe",
        "SKALDIR_LLM_API_KEY": "not-a-real-credential",
    }


def test_the_route_probe_spends_no_provider_request(tmp_path, monkeypatch):
    """With a live adapter configured, ROUTE still answers offline — in milliseconds."""
    base = _live_settings_runtime(tmp_path)
    for name, value in _live_provider_env(endpoint=REFUSED_ENDPOINT).items():
        monkeypatch.setenv(name, value)

    started = time.perf_counter()
    report = cli_contract.run_selftest(base, layers=["ROUTE"])
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    layer = report["layers"][0]
    assert layer["ok"] is True, layer["blocked_reason"]
    probe = next(item for item in layer["probes"] if item["name"] == "advise")
    assert probe["ok"] is True, probe["detail"]
    assert "provider_mode=llm_system_one_adapter" in probe["detail"], (
        "the probe must report the mode the *settings* resolve to, not hide it"
    )
    assert "offline" in probe["detail"], probe["detail"]
    assert elapsed_ms < 1000.0, (
        f"ROUTE probed in {elapsed_ms:.0f} ms with a live adapter configured — the probe "
        "is spending a provider request again (measured: 2516 ms of 3050 ms before the fix)"
    )


def test_the_whole_selftest_command_makes_no_provider_request(tmp_path):
    """The shipped command, under a black-hole endpoint — bounded by the subprocess timeout."""
    base = _live_settings_runtime(tmp_path)
    env = {
        **os.environ,
        "SKALDIR_HOME": str(base),
        "PYTHONPATH": str(SRC),
        **_live_provider_env(endpoint=BLACKHOLE_ENDPOINT),
    }
    for name in ("HERMES_HOME", "GODSV_HOME"):
        env.pop(name, None)  # $SKALDIR_HOME wins, and the legacy spellings are not read

    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-c", CLI_CODE, "selftest", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "`skaldir selftest --json` did not answer in 8 s with a black-hole provider "
            "endpoint configured — a probe is waiting on the network again"
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert result.returncode in (0, 2), result.stdout + result.stderr
    document = json.loads(result.stdout)
    advise = next(
        probe
        for layer in document["data"]["layers"]
        for probe in layer["probes"]
        if probe["name"] == "advise"
    )
    assert advise["ok"] is True, advise["detail"]
    assert elapsed_ms < 4000.0, (
        f"`skaldir selftest --json` took {elapsed_ms:.0f} ms under a black-hole endpoint "
        "(measured on the fixed tree: 891-1060 ms)"
    )


def test_the_probes_build_the_command_tree_once_per_invocation(tmp_path, monkeypatch):
    """ACT/COMPOSE/VERIFY share one ``build_parser()``; the next run re-reads the tree."""
    base = _live_settings_runtime(tmp_path)
    builds: list[float] = []
    original = cli.build_parser

    def counted():
        builds.append(time.perf_counter())
        return original()

    monkeypatch.setattr(cli, "build_parser", counted)

    cli_contract.run_selftest(base, layers=["ACT", "COMPOSE", "VERIFY"])
    assert len(builds) == 1, (
        f"one invocation rebuilt the command tree {len(builds)}× — measured 47-90 ms per "
        "extra parse on top of the one `cli.main` already paid"
    )

    cli_contract.run_selftest(base, layers=["ACT", "COMPOSE", "VERIFY"])
    assert len(builds) == 2, (
        "a second invocation inherited the first one's command tree — a measurement must "
        "re-read the tree it is measuring"
    )
