"""JEV-19 (§19/§20) — the three calibration dataset shapes, the metric set, and
the discipline: **no raw data means no numbers**.

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -c "import godsv.system_one_calibration"
    ModuleNotFoundError: No module named 'godsv.system_one_calibration'

    $ .venv/bin/python -m pytest tests/test_jev_provider_calibration.py -p no:randomly
    ERROR tests/test_jev_provider_calibration.py - ModuleNotFoundError:
        No module named 'godsv.system_one_calibration'
    !!! Interrupted: 1 error during collection !!!

What this file pins (behaviour, never source text):

* the three dataset shapes (Voice / Completion / Computer Use) are declared and
  validated: an unknown field, scenario or kind is refused, never absorbed;
* every metric is computed **only** from the provided raw records. With no raw
  data nothing is reported as ``0``: the report is ``not_run`` with a reason and
  the CLI exits non-zero (``tools/eval_benchmark.py``'s discipline, §105);
* precision/recall/false-intervention/miss rate need a denominator; a metric
  without one is ``not_run``, never an interpolation;
* the completion extras exist as first-class metrics: false completion
  acceptance, false completion rejection, semantic evidence mismatch caught,
  stale evidence caught, mock-as-live caught;
* a threshold profile is frozen **from measured data** before holdout — freezing
  without the core metrics is refused, a frozen profile refuses edits, and a
  changed threshold must be a *new* profile id, never a silent edit (§89).

Evidence class: contract + mock (synthetic fixtures, labelled as such — these are
NOT product measurements). No development dataset exists in this repo, so the
real calibration numbers stay ``not_run`` and nothing here may be quoted as a
result.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from skaldir.system_one_calibration import (
    COMPLETION_DATASET_FIELDS,
    COMPLETION_EXTRA_METRICS,
    COMPUTER_USE_DATASET_FIELDS,
    DATASET_KINDS,
    METRIC_KEYS,
    VOICE_DATASET_FIELDS,
    CalibrationDatasetError,
    FrozenThresholdProfile,
    NoCalibrationDataError,
    assert_profile_unchanged,
    compute_metrics,
    dataset_shape,
    freeze_threshold_profile,
    load_dataset,
    validate_record,
)

# --------------------------------------------------------------------------- #
# fixtures — synthetic, and labelled as synthetic everywhere                    #
# --------------------------------------------------------------------------- #

#: 2 true interventions, 1 missed, 1 unnecessary → precision/recall 2/3,
#: false-intervention rate 1/2, miss rate 1/2.
VOICE_RECORDS = [
    {
        "case_id": "v-1",
        "scenario": "problematic_loop",
        "expected": {"intervention_expected": True},
        "observed": {
            "intervention": True,
            "escalated": True,
            "system_2_calls": 1,
            "baseline_system_2_calls": 4,
            "latency_ms": 120.0,
            "input_tokens": 400,
            "cost_usd": 0.002,
            "task_succeeded": True,
        },
    },
    {
        "case_id": "v-2",
        "scenario": "goal_drift",
        "expected": {"intervention_expected": True},
        "observed": {
            "intervention": True,
            "escalated": True,
            "system_2_calls": 1,
            "baseline_system_2_calls": 3,
            "latency_ms": 200.0,
            "input_tokens": 500,
            "cost_usd": 0.003,
            "task_succeeded": True,
        },
    },
    {
        "case_id": "v-3",
        "scenario": "unsupported_completion",
        "expected": {"intervention_expected": True},
        "observed": {
            "intervention": False,
            "escalated": False,
            "system_2_calls": 0,
            "baseline_system_2_calls": 2,
            "task_succeeded": False,
        },
    },
    {
        "case_id": "v-4",
        "scenario": "healthy_debugging",
        "expected": {"intervention_expected": False},
        "observed": {
            "intervention": True,
            "escalated": True,
            "system_2_calls": 2,
            "baseline_system_2_calls": 1,
            "task_succeeded": True,
        },
    },
]

COMPLETION_RECORDS = [
    {
        "case_id": "c-1",
        "scenario": "mock_only_evidence",
        "expected": {"should_accept": False, "expected_flags": ["mock_as_live"]},
        "observed": {"accepted": False, "flagged": True, "flags": ["mock_as_live"]},
    },
    {
        "case_id": "c-2",
        "scenario": "stale_evidence",
        "expected": {"should_accept": False, "expected_flags": ["stale_evidence"]},
        "observed": {"accepted": False, "flagged": True, "flags": ["stale_evidence"]},
    },
    {
        "case_id": "c-3",
        "scenario": "self_certification",
        "expected": {
            "should_accept": False,
            "expected_flags": ["semantic_evidence_mismatch"],
        },
        "observed": {"accepted": True, "flagged": False, "flags": []},
    },
    {
        "case_id": "c-4",
        "scenario": "fully_verified",
        "expected": {"should_accept": True, "expected_flags": []},
        "observed": {"accepted": True, "flagged": False, "flags": []},
    },
]

CU_RECORDS = [
    {
        "case_id": "cu-1",
        "scenario": "correct_window",
        "expected": {"intervention_expected": False, "action_allowed": True},
        "observed": {
            "intervention": False,
            "escalated": False,
            "blind_click": False,
            "reobserves": 1,
            "system_2_calls": 0,
            "baseline_system_2_calls": 3,
            "task_succeeded": True,
        },
    },
    {
        "case_id": "cu-2",
        "scenario": "anchor_invalid",
        "expected": {"intervention_expected": True, "action_allowed": False},
        "observed": {
            "intervention": True,
            "escalated": True,
            "blind_click": False,
            "reobserves": 2,
            "system_2_calls": 1,
            "baseline_system_2_calls": 3,
            "task_succeeded": True,
        },
    },
    {
        "case_id": "cu-3",
        "scenario": "user_takeover",
        "expected": {"intervention_expected": True, "action_allowed": False},
        "observed": {
            "intervention": False,
            "escalated": False,
            "blind_click": True,
            "reobserves": 0,
            "system_2_calls": 0,
            "baseline_system_2_calls": 2,
            "task_succeeded": False,
        },
    },
]

CLI = [sys.executable, "-m", "godsv.system_one_calibration"]


def _run_cli(*args, cwd=None):
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [*CLI, *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(cwd) if cwd else None,
    )


def _write(tmp_path, name: str, records: list[dict]) -> str:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# the three dataset shapes (§19)                                               #
# --------------------------------------------------------------------------- #


def test_the_three_dataset_shapes_exist_and_differ():
    assert DATASET_KINDS == ("voice", "completion", "computer_use")
    assert set(dataset_shape("voice")) == set(VOICE_DATASET_FIELDS)
    assert set(dataset_shape("completion")) == set(COMPLETION_DATASET_FIELDS)
    assert set(dataset_shape("computer_use")) == set(COMPUTER_USE_DATASET_FIELDS)
    assert "completion_flags" not in dataset_shape("voice")
    with pytest.raises(CalibrationDatasetError):
        dataset_shape("vibes")


def test_a_record_is_validated_against_its_shape():
    assert validate_record(VOICE_RECORDS[0], kind="voice") == "v-1"
    broken = dict(VOICE_RECORDS[0])
    del broken["observed"]
    with pytest.raises(CalibrationDatasetError):
        validate_record(broken, kind="voice")
    unknown_field = {**VOICE_RECORDS[0], "invented_metric": 1}
    with pytest.raises(CalibrationDatasetError):
        validate_record(unknown_field, kind="voice")
    unknown_scenario = {**VOICE_RECORDS[0], "scenario": "made_up_scenario"}
    with pytest.raises(CalibrationDatasetError):
        validate_record(unknown_scenario, kind="voice")


def test_loading_accepts_jsonl_and_json_and_refuses_unknown_files(tmp_path):
    jsonl = _write(tmp_path, "voice.jsonl", VOICE_RECORDS)
    assert len(load_dataset([jsonl], kind="voice")) == len(VOICE_RECORDS)
    json_path = tmp_path / "voice.json"
    json_path.write_text(json.dumps(VOICE_RECORDS), encoding="utf-8")
    assert len(load_dataset([str(json_path)], kind="voice")) == len(VOICE_RECORDS)
    with pytest.raises(FileNotFoundError):
        load_dataset([str(tmp_path / "nope.jsonl")], kind="voice")


# --------------------------------------------------------------------------- #
# no raw data → no numbers (§105)                                              #
# --------------------------------------------------------------------------- #


def test_no_raw_data_raises_instead_of_reporting_zeros():
    with pytest.raises(NoCalibrationDataError):
        compute_metrics([], kind="voice")


def test_the_cli_exits_non_zero_without_raw_data(tmp_path):
    no_args = _run_cli("--kind", "voice")
    assert no_args.returncode != 0
    assert "no raw data" in no_args.stderr.lower()
    assert "0.0" not in no_args.stdout

    empty = _write(tmp_path, "empty.jsonl", [])
    empty_run = _run_cli("--kind", "voice", "--dataset", empty)
    assert empty_run.returncode != 0
    assert "no raw data" in empty_run.stderr.lower()
    assert "0.0" not in empty_run.stdout

    missing = _run_cli("--kind", "voice", "--dataset", str(tmp_path / "missing.jsonl"))
    assert missing.returncode != 0


def test_every_metric_key_appears_even_when_unmeasurable():
    """Shape stability: the report always names the full metric set."""
    report = compute_metrics(VOICE_RECORDS, kind="voice")
    for key in METRIC_KEYS:
        assert key in report.metrics, key
        node = report.metrics[key]
        assert set(node) >= {"value", "status", "reason"}
        assert node["status"] in ("measured", "not_run")


# --------------------------------------------------------------------------- #
# the metric maths, from the raw records only                                  #
# --------------------------------------------------------------------------- #


def test_voice_rates_are_computed_from_the_labels():
    report = compute_metrics(VOICE_RECORDS, kind="voice")
    assert report.metrics["precision"]["value"] == pytest.approx(2 / 3)
    assert report.metrics["recall"]["value"] == pytest.approx(2 / 3)
    # only one case was labelled "no intervention expected" and it got one → 1/1
    assert report.metrics["false_intervention_rate"]["value"] == pytest.approx(1.0)
    # 1 missed of the 3 cases where an intervention was expected → 1/3
    assert report.metrics["miss_rate"]["value"] == pytest.approx(1 / 3)
    assert report.metrics["escalation_rate"]["value"] == pytest.approx(3 / 4)
    assert report.metrics["system2_calls_avoided"]["value"] == pytest.approx(
        (4 - 1) + (3 - 1) + (2 - 0) + (1 - 2)
    )
    assert report.metrics["latency"]["value"] == pytest.approx((120.0 + 200.0) / 2)
    assert report.metrics["input_tokens"]["value"] == 900
    assert report.metrics["cost"]["value"] == pytest.approx(0.005)
    # 3 of 4 records carry an objective outcome (v-3 is the failure)
    assert report.metrics["task_success_impact"]["value"] == pytest.approx(3 / 4)


def test_an_absent_label_makes_a_metric_not_run_rather_than_zero():
    unlabelled = [
        {
            "case_id": "v-1",
            "scenario": "healthy_debugging",
            "expected": {},
            "observed": {"intervention": False},
        }
    ]
    report = compute_metrics(unlabelled, kind="voice")
    for key in ("precision", "recall", "false_intervention_rate", "miss_rate"):
        node = report.metrics[key]
        assert node["value"] is None
        assert node["status"] == "not_run"
        assert node["reason"]
    for key in ("latency", "input_tokens", "cost", "system2_calls_avoided"):
        assert report.metrics[key]["status"] == "not_run"
    assert report.metrics["escalation_rate"]["status"] == "measured", (
        "counting is measurable without a ground-truth label"
    )


def test_confidence_bins_are_only_reported_when_confidences_exist():
    without = compute_metrics(VOICE_RECORDS, kind="voice")
    assert without.metrics["confidence_bins"]["status"] == "not_run"
    with_confidence = [
        {**VOICE_RECORDS[0], "observed": {**VOICE_RECORDS[0]["observed"], "confidence": 0.9}},
        {**VOICE_RECORDS[1], "observed": {**VOICE_RECORDS[1]["observed"], "confidence": 0.3}},
    ]
    report = compute_metrics(with_confidence, kind="voice")
    bins = report.metrics["confidence_bins"]["value"]
    assert bins is not None
    assert sum(bins.values()) == 2


# --------------------------------------------------------------------------- #
# completion extras (§20)                                                      #
# --------------------------------------------------------------------------- #


def test_the_completion_extras_are_first_class_metrics():
    assert COMPLETION_EXTRA_METRICS == (
        "false_completion_acceptance",
        "false_completion_rejection",
        "semantic_evidence_mismatch_caught",
        "stale_evidence_caught",
        "mock_as_live_caught",
    )
    report = compute_metrics(COMPLETION_RECORDS, kind="completion")
    for key in COMPLETION_EXTRA_METRICS:
        assert key in report.metrics
    # c-3 is accepted although it should not be → one false acceptance
    # 1 false acceptance out of the 4 judged cases
    assert report.metrics["false_completion_acceptance"]["value"] == pytest.approx(1 / 4)
    assert report.metrics["false_completion_rejection"]["value"] == pytest.approx(0.0)
    assert report.metrics["stale_evidence_caught"]["value"] == pytest.approx(1.0)
    assert report.metrics["mock_as_live_caught"]["value"] == pytest.approx(1.0)
    assert report.metrics["semantic_evidence_mismatch_caught"]["value"] == pytest.approx(0.0)


def test_a_false_acceptance_is_never_reported_as_a_success():
    """§20: a wrong completion pass is worse than extra verification."""
    report = compute_metrics(COMPLETION_RECORDS, kind="completion")
    assert report.metrics["false_completion_acceptance"]["numerator"] == 1


def test_computer_use_metrics_include_the_gui_specific_counters():
    report = compute_metrics(CU_RECORDS, kind="computer_use")
    # cu-2 escalated correctly (TP), cu-3 missed the takeover (FN), no FP at all
    assert report.metrics["precision"]["value"] == pytest.approx(1.0)
    assert report.metrics["recall"]["value"] == pytest.approx(1 / 2)
    assert report.metrics["false_intervention_rate"]["value"] == pytest.approx(0.0)
    assert report.metrics["miss_rate"]["value"] == pytest.approx(1 / 2)
    assert report.metrics["wrong_gui_actions"]["value"] == 1, "the blind click is counted"
    assert report.metrics["reobserves"]["value"] == 3
    assert report.metrics["system2_calls_avoided"]["value"] == pytest.approx(
        3 + 2 + 2
    )


# --------------------------------------------------------------------------- #
# the CLI reports measurements, never invented numbers                          #
# --------------------------------------------------------------------------- #


def test_the_cli_reports_the_measured_rates_and_exits_zero(tmp_path):
    voice = _write(tmp_path, "voice.jsonl", VOICE_RECORDS)
    run = _run_cli("--kind", "voice", "--dataset", voice, "--json")
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["kind"] == "voice"
    assert report["records_read"] == len(VOICE_RECORDS)
    assert report["metrics"]["precision"]["value"] == pytest.approx(2 / 3)
    assert report["synthetic"] is False


# --------------------------------------------------------------------------- #
# frozen threshold profiles (§89/§20)                                          #
# --------------------------------------------------------------------------- #


def test_freezing_without_measured_core_metrics_is_refused():
    with pytest.raises(CalibrationDatasetError):
        freeze_threshold_profile(
            profile_id="profile-x",
            question_pack_version="computer_use_v2",
            dataset_version="none",
            report=compute_metrics(
                [
                    {
                        "case_id": "v-1",
                        "scenario": "healthy_debugging",
                        "expected": {},
                        "observed": {"intervention": False},
                    }
                ],
                kind="voice",
            ),
            confidence_floor=0.7,
            noul_band=(0.4, 0.6),
        )
    with pytest.raises(NoCalibrationDataError):
        freeze_threshold_profile(
            profile_id="profile-x",
            question_pack_version="computer_use_v2",
            dataset_version="none",
            report=None,
            confidence_floor=0.7,
            noul_band=(0.4, 0.6),
        )


def test_a_frozen_profile_carries_the_measured_numbers_and_the_provenance():
    report = compute_metrics(VOICE_RECORDS, kind="voice")
    profile = freeze_threshold_profile(
        profile_id="profile-2026-09-16",
        question_pack_version="computer_use_v2",
        dataset_version="voice-dev-1",
        report=report,
        confidence_floor=0.7,
        noul_band=(0.4, 0.6),
    )
    assert isinstance(profile, FrozenThresholdProfile)
    assert profile.profile_id == "profile-2026-09-16"
    assert profile.frozen is True
    assert profile.question_pack_version == "computer_use_v2"
    assert profile.dataset_version == "voice-dev-1"
    assert profile.measured["precision"] == pytest.approx(2 / 3)
    assert profile.measured["recall"] == pytest.approx(2 / 3)
    assert profile.measured["false_intervention_rate"] == pytest.approx(1.0)
    assert profile.measured["miss_rate"] == pytest.approx(1 / 3)
    assert profile.measured_from_records == len(VOICE_RECORDS)
    # the §12 escalation reading reads these two attributes
    assert profile.choice_threshold == 0.7
    assert tuple(profile.noul_band) == (0.4, 0.6)


def test_a_frozen_profile_refuses_a_silent_threshold_change():
    report = compute_metrics(VOICE_RECORDS, kind="voice")
    profile = freeze_threshold_profile(
        profile_id="profile-1",
        question_pack_version="computer_use_v2",
        dataset_version="voice-dev-1",
        report=report,
        confidence_floor=0.7,
        noul_band=(0.4, 0.6),
    )
    assert_profile_unchanged(profile, confidence_floor=0.7, noul_band=(0.4, 0.6))
    with pytest.raises(CalibrationDatasetError):
        assert_profile_unchanged(profile, confidence_floor=0.6)
    with pytest.raises(CalibrationDatasetError):
        assert_profile_unchanged(profile, noul_band=(0.3, 0.7))


def test_a_holdout_run_does_not_touch_the_frozen_profile():
    """§20: freeze before holdout — the holdout measures, it does not retune."""
    dev_report = compute_metrics(VOICE_RECORDS, kind="voice")
    profile = freeze_threshold_profile(
        profile_id="profile-1",
        question_pack_version="computer_use_v2",
        dataset_version="voice-dev-1",
        report=dev_report,
        confidence_floor=0.7,
        noul_band=(0.4, 0.6),
    )
    before = profile.as_dict()
    holdout_report = compute_metrics(CU_RECORDS, kind="computer_use")
    assert holdout_report.kind == "computer_use"
    assert profile.as_dict() == before
    assert_profile_unchanged(profile, confidence_floor=0.7, noul_band=(0.4, 0.6))


def test_the_frozen_profile_drives_the_escalation_decision():
    """Integration: calibration output → §12 escalation band."""
    from skaldir.computer_use import EscalationSignals, system_two_escalation_decision

    report = compute_metrics(VOICE_RECORDS, kind="voice")
    profile = freeze_threshold_profile(
        profile_id="profile-1",
        question_pack_version="computer_use_v2",
        dataset_version="voice-dev-1",
        report=report,
        confidence_floor=0.7,
        noul_band=(0.4, 0.6),
    )
    inside = system_two_escalation_decision(
        profile=profile,
        evaluation=None,
        signals=EscalationSignals(noul_value=0.5),
        allowed_candidates=(),
    )
    assert "noul_inside_calibrated_uncertainty_band" in inside.triggers
    assert inside.profile_id == "profile-1"
