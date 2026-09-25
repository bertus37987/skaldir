"""§27 — the benchmark arms as a runnable scaffold (`tools/jev_benchmark_arms.py`).

ROT-FIRST evidence, captured before the tool existed:

    $ .venv/bin/python tools/jev_benchmark_arms.py --list
    .venv/bin/python: can't open file
        '/home/willy/projects/gods-voice/tools/jev_benchmark_arms.py':
        [Errno 2] No such file or directory

    $ cd $HOME/jev-red-head   # a detached worktree of HEAD with only this test copied in
    $ PYTHONPATH=src .venv/bin/python -m pytest tests/test_jev_provider_arms.py -p no:randomly
    FAILED tests/test_jev_provider_arms.py::test_the_tool_exists_and_lists_every_goal_arm
    FAILED tests/test_jev_provider_arms.py::test_without_raw_data_the_tool_fails_loudly
    FAILED tests/test_jev_provider_arms.py::test_a_missing_arm_is_named_and_never_scored
    FAILED (...)::test_an_unmeasurable_metric_is_reported_as_not_measurable
    FAILED (...)::test_an_unknown_arm_is_reported_instead_of_silently_counted
    FAILED tests/test_jev_provider_arms.py::test_vendor_claims_are_excluded_and_listed
    FAILED tests/test_jev_provider_arms.py::test_only_vendor_material_is_refused_outright
    7 failed in 0.12s
    (the tool and the tests are uncommitted, so at HEAD the tool does not exist)

What this file pins (behaviour, never source text):

* the arms of §27 exist as a catalogue (Voice A–D, Completion A–C, CU A–B) and
  ``--list`` prints them without ever implying a measurement;
* **no raw data, no numbers**: without run files the tool exits non-zero and
  prints no metric; a missing arm is *named* as missing, never scored ``0``;
* **vendor numbers never become ours**: a record marked as a vendor claim is
  excluded from every metric and listed as such, and when only vendor material
  exists the tool refuses (exit 5) instead of adopting the numbers;
* the metric maths are not re-implemented here — they stay in
  ``tools/eval_benchmark.py`` (§97), and an unmeasurable metric is reported as
  NOT MEASURABLE rather than as a zero.

Evidence class: contract + mock (a synthetic raw-run file, labelled synthetic).
The raw runs in this file are fabricated to exercise the *plumbing*; they are not
a benchmark result and must never be quoted as one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "jev_benchmark_arms.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO),
    )


def _arm_record(arm: str, *, run_id: str) -> dict:
    """A synthetically shaped raw run for the arm plumbing."""
    return {
        "run_id": run_id,
        "arm": arm,
        "ground_truth": {"intervention_expected": True},
        "observed": {
            "task_succeeded": True,
            "completion_claimed": False,
            "interventions": 1,
            "system_2_calls": 1,
            "recovery_ok": True,
        },
        "latency": {"total_s": 4.0},
        "usage": {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.001},
    }


def _write(tmp_path, name: str, records: list[dict]) -> str:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# the catalogue                                                                #
# --------------------------------------------------------------------------- #


def test_the_tool_exists_and_lists_every_goal_arm():
    listed = _run("--list")
    assert listed.returncode == 0
    for arm in (
        "A_existing_system2_triage",
        "B_llm_system_one_adapter",
        "C_jev_shadow",
        "D_jev_active_system2_escalation",
        "A_current_verifier_path",
        "B_llm_system_one_preflight",
        "C_jev_preflight_targeted_verifier",
        "A_system2_every_decision",
        "B_jev_reactive_controller",
    ):
        assert arm in listed.stdout, arm
    assert "nothing here is a measurement" in listed.stdout


# --------------------------------------------------------------------------- #
# no raw data → no numbers                                                     #
# --------------------------------------------------------------------------- #


def test_without_raw_data_the_tool_fails_loudly(tmp_path):
    no_args = _run()
    assert no_args.returncode != 0
    assert "no raw data" in no_args.stderr.lower()
    assert "task_success" not in no_args.stdout

    empty = _write(tmp_path, "empty.jsonl", [])
    empty_run = _run(empty)
    assert empty_run.returncode != 0
    assert "no raw data" in empty_run.stderr.lower()

    missing = _run(str(tmp_path / "missing.jsonl"))
    assert missing.returncode != 0
    unreadable = _write(tmp_path, "broken.jsonl", [])
    Path(unreadable).write_text("{ not json", encoding="utf-8")
    assert _run(unreadable).returncode == 3


def test_a_missing_arm_is_named_and_never_scored(tmp_path):
    runs = _write(tmp_path, "runs.jsonl", [_arm_record("C_jev_shadow", run_id="r-1")])
    result = _run(runs)
    assert result.returncode == 0, result.stderr
    assert "missing: A_existing_system2_triage" in result.stdout
    assert "B_llm_system_one_adapter" in result.stdout


def test_an_unmeasurable_metric_is_reported_as_not_measurable(tmp_path):
    bare = {"run_id": "r-1", "arm": "B_jev_reactive_controller", "observed": {"interventions": 1}}
    runs = _write(tmp_path, "bare.jsonl", [bare])
    result = _run(runs)
    assert result.returncode == 0, result.stderr
    assert "NOT MEASURABLE" in result.stdout
    assert "task_success: 0" not in result.stdout


def test_an_unknown_arm_is_reported_instead_of_silently_counted(tmp_path):
    runs = _write(tmp_path, "unknown.jsonl", [_arm_record("Z_made_up_arm", run_id="r-1")])
    result = _run(runs)
    assert result.returncode == 4
    assert "no §27 arm recognised" in result.stderr


# --------------------------------------------------------------------------- #
# vendor numbers are never our measurement                                     #
# --------------------------------------------------------------------------- #


def test_vendor_claims_are_excluded_and_listed(tmp_path):
    records = [
        _arm_record("A_existing_system2_triage", run_id="ours-1"),
        {**_arm_record("D_jev_active_system2_escalation", run_id="vendor-1"), "vendor_claim": True},
        {
            **_arm_record("B_llm_system_one_adapter", run_id="vendor-2"),
            "source": "vendor_benchmark",
        },
    ]
    runs = _write(tmp_path, "mixed.jsonl", records)
    result = _run(runs, "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["our_runs"] == 1
    assert report["runs_read"] == 3
    assert len(report["vendor_claims_excluded"]) == 2
    # the vendor arm is reported as missing, not as one of our configurations
    assert "D_jev_active_system2_escalation" in report["arms_missing"]["voice_triage"]
    assert "D_jev_active_system2_escalation" not in report["metrics"]["configurations"]


def test_only_vendor_material_is_refused_outright(tmp_path):
    records = [
        {**_arm_record("A_existing_system2_triage", run_id="vendor-1"), "vendor_claim": True},
        {**_arm_record("D_jev_active_system2_escalation", run_id="vendor-2"), "source": "typesafe"},
    ]
    runs = _write(tmp_path, "vendor.jsonl", records)
    result = _run(runs)
    assert result.returncode == 5
    assert "vendor" in result.stderr.lower()
    assert "task_success" not in result.stdout
    assert "0.0" not in result.stdout
