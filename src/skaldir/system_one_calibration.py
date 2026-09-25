"""Calibration and holdout for System One (§19/§20, ABNAHME §89/JEV-19).

Three dataset shapes, one metric set, and one rule that is the whole point:

> **No raw data means no numbers.** A metric without a denominator is
> ``not_run`` with a reason — never ``0``, never an interpolation, and the CLI
> exits non-zero instead of printing a zero table (the same discipline
> ``tools/eval_benchmark.py`` follows for the §97 runs).

    Voice            problematic loops, healthy debugging, goal drift, legitimate
                     supporting work, unsupported/correct completion, stale/current
                     evidence, research needed/not, injection vs. harmless quote
    Completion       direct live evidence, mock-only, stale, wrong revision/workspace,
                     partial/irrelevant/contradictory, self-certification,
                     independence present/missing, changed artifact, unknown side
                     effect, fully verified
    Computer Use     correct/wrong app, correct/wrong window, modal present/absent,
                     action success/no-op/wrong-target, layout/app-version change,
                     focus stolen/user takeover, anchor valid/invalid, retry
                     safe/unsafe, reobserve/System-2 needed

Metrics (§20): precision, recall, false intervention rate, miss rate, escalation
rate, confidence bins, latency, input tokens, cost, System-2 calls avoided, task
success impact — plus the completion extras (false completion acceptance, false
completion rejection, semantic evidence mismatch caught, stale evidence caught,
mock-as-live caught) and the Computer-Use counters (reobserves, screenshots,
wrong GUI actions).

Threshold profiles are **frozen before holdout** (:func:`freeze_threshold_profile`)
and a frozen profile refuses edits: changing a threshold needs a *new* profile id
(:func:`refreeze_with_new_id`), and :func:`assert_profile_unchanged` is how a
holdout run proves it did not retune anything.

Honest status: this repo has no development dataset of its own, so every number
here is ``not_run`` in the product; the fixtures in the tests are synthetic and
labelled as such. Nothing in this module is a measurement until raw data exists.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# the three dataset shapes (§19)                                               #
# --------------------------------------------------------------------------- #

DATASET_KINDS: tuple[str, ...] = ("voice", "completion", "computer_use")

#: Allowed top-level fields of one raw record, per kind. A record is validated
#: against its shape — an unknown field is refused, never absorbed (§19).
VOICE_DATASET_FIELDS: tuple[str, ...] = ("case_id", "scenario", "expected", "observed")
COMPLETION_DATASET_FIELDS: tuple[str, ...] = ("case_id", "scenario", "expected", "observed")
COMPUTER_USE_DATASET_FIELDS: tuple[str, ...] = (
    "case_id",
    "scenario",
    "expected",
    "observed",
    "gui_state_fingerprint",
)

#: The §19 scenarios, verbatim per dataset.
VOICE_SCENARIOS: tuple[str, ...] = (
    "problematic_loop",
    "healthy_debugging",
    "goal_drift",
    "legitimate_supporting_work",
    "unsupported_completion",
    "correct_completion",
    "stale_evidence",
    "current_evidence",
    "research_needed",
    "research_not_needed",
    "injection_attempt",
    "harmless_quote",
)

COMPLETION_SCENARIOS: tuple[str, ...] = (
    "direct_live_evidence",
    "mock_only_evidence",
    "stale_evidence",
    "wrong_revision",
    "wrong_workspace",
    "partial_evidence",
    "irrelevant_evidence",
    "contradictory_evidence",
    "self_certification",
    "independence_present",
    "independence_missing",
    "changed_artifact_after_test",
    "unknown_side_effect",
    "fully_verified",
)

COMPUTER_USE_SCENARIOS: tuple[str, ...] = (
    "correct_app",
    "wrong_app",
    "correct_window",
    "wrong_window",
    "modal_present",
    "modal_absent",
    "action_success",
    "action_noop",
    "wrong_target",
    "layout_changed",
    "app_version_changed",
    "focus_stolen",
    "user_takeover",
    "anchor_valid",
    "anchor_invalid",
    "retry_safe",
    "retry_unsafe",
    "reobserve_needed",
    "system2_needed",
)

_EXPECTED_FIELDS: dict[str, tuple[str, ...]] = {
    "voice": ("intervention_expected", "completion_supported"),
    "completion": ("should_accept", "expected_flags"),
    "computer_use": ("intervention_expected", "action_allowed"),
}

_OBSERVED_FIELDS: dict[str, tuple[str, ...]] = {
    "voice": (
        "intervention",
        "escalated",
        "system_2_calls",
        "baseline_system_2_calls",
        "latency_ms",
        "input_tokens",
        "cost_usd",
        "task_succeeded",
        "baseline_task_succeeded",
        "confidence",
        "noul_value",
    ),
    "completion": (
        "accepted",
        "flagged",
        "flags",
        "latency_ms",
        "input_tokens",
        "cost_usd",
        "system_2_calls",
        "baseline_system_2_calls",
    ),
    "computer_use": (
        "intervention",
        "escalated",
        "blind_click",
        "wrong_gui_action",
        "reobserves",
        "screenshots",
        "system_2_calls",
        "baseline_system_2_calls",
        "latency_ms",
        "input_tokens",
        "cost_usd",
        "task_succeeded",
        "baseline_task_succeeded",
        "confidence",
        "noul_value",
    ),
}

_SCENARIOS: dict[str, tuple[str, ...]] = {
    "voice": VOICE_SCENARIOS,
    "completion": COMPLETION_SCENARIOS,
    "computer_use": COMPUTER_USE_SCENARIOS,
}

#: The §20 metric set, then the completion extras, then the Computer-Use counters.
CORE_METRIC_KEYS: tuple[str, ...] = (
    "precision",
    "recall",
    "false_intervention_rate",
    "miss_rate",
    "escalation_rate",
    "confidence_bins",
    "latency",
    "input_tokens",
    "cost",
    "system2_calls_avoided",
    "task_success_impact",
)

COMPLETION_EXTRA_METRICS: tuple[str, ...] = (
    "false_completion_acceptance",
    "false_completion_rejection",
    "semantic_evidence_mismatch_caught",
    "stale_evidence_caught",
    "mock_as_live_caught",
)

COMPUTER_USE_EXTRA_METRICS: tuple[str, ...] = ("reobserves", "screenshots", "wrong_gui_actions")

METRIC_KEYS: tuple[str, ...] = (
    CORE_METRIC_KEYS + COMPLETION_EXTRA_METRICS + COMPUTER_USE_EXTRA_METRICS
)

CONFIDENCE_BIN_EDGES: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


class CalibrationDatasetError(RuntimeError):
    """A raw record, a dataset shape or a threshold profile violates §19/§89."""


class NoCalibrationDataError(RuntimeError):
    """There is nothing to measure — the honest answer is 'no raw data'."""


# --------------------------------------------------------------------------- #
# loading and validation                                                       #
# --------------------------------------------------------------------------- #


def dataset_shape(kind: str) -> tuple[str, ...]:
    """The allowed top-level fields of one raw record of this kind."""
    shapes = {
        "voice": VOICE_DATASET_FIELDS,
        "completion": COMPLETION_DATASET_FIELDS,
        "computer_use": COMPUTER_USE_DATASET_FIELDS,
    }
    if kind not in shapes:
        raise CalibrationDatasetError(
            f"unknown dataset kind: {kind!r} (known: {DATASET_KINDS})"
        )
    return shapes[kind]


def validate_record(record: Any, *, kind: str) -> str:
    """Validate one raw record against its shape; return its ``case_id``."""
    shape = dataset_shape(kind)
    if not isinstance(record, dict):
        raise CalibrationDatasetError(f"a raw {kind} record must be an object")
    unknown = sorted(set(record) - set(shape))
    if unknown:
        raise CalibrationDatasetError(
            f"{kind}: unknown field(s) {unknown} — a metric nobody defined is never absorbed"
        )
    required = ("case_id", "scenario", "expected", "observed")
    missing = [name for name in required if name not in record]
    if missing:
        raise CalibrationDatasetError(f"{kind}: record is missing {missing}")
    case_id = str(record["case_id"] or "")
    if not case_id:
        raise CalibrationDatasetError(f"{kind}: a record needs a case_id")
    scenario = str(record["scenario"] or "")
    if scenario not in _SCENARIOS[kind]:
        raise CalibrationDatasetError(
            f"{kind}: unknown scenario {scenario!r} (known: {len(_SCENARIOS[kind])} §19 scenarios)"
        )
    for block_name in ("expected", "observed"):
        block = record[block_name]
        if not isinstance(block, dict):
            raise CalibrationDatasetError(f"{kind}/{case_id}: {block_name} must be an object")
        allowed = _EXPECTED_FIELDS[kind] if block_name == "expected" else _OBSERVED_FIELDS[kind]
        unexpected = sorted(set(block) - set(allowed))
        if unexpected:
            raise CalibrationDatasetError(
                f"{kind}/{case_id}: unknown {block_name} field(s) {unexpected} "
                f"(known: {list(allowed)})"
            )
    expected_flags = record["expected"].get("expected_flags")
    if expected_flags is not None and not isinstance(expected_flags, list):
        raise CalibrationDatasetError(f"{kind}/{case_id}: expected_flags must be a list")
    return case_id


def load_dataset(paths: list[str], *, kind: str) -> list[dict]:
    """Load raw records from JSONL/JSON files and validate every one of them."""
    records: list[dict] = []
    for pattern in paths:
        for name in sorted(glob.glob(pattern)) or [pattern]:
            path = Path(name)
            if not path.is_file():
                raise FileNotFoundError(f"raw dataset file not found: {path}")
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                payload = json.loads(text)
                candidates = payload if isinstance(payload, list) else [payload]
            else:
                candidates = [json.loads(line) for line in text.splitlines() if line.strip()]
            for candidate in candidates:
                validate_record(candidate, kind=kind)
                records.append(candidate)
    return records


# --------------------------------------------------------------------------- #
# metric nodes                                                                 #
# --------------------------------------------------------------------------- #


def _measured(value: Any, **extra: Any) -> dict:
    node: dict[str, Any] = {"value": value, "status": "measured", "reason": ""}
    node.update(extra)
    return node


def _not_run(reason: str) -> dict:
    return {"value": None, "status": "not_run", "reason": reason, "n": 0}


def _rate(numerator: int, denominator: int, why: str) -> dict:
    """A rate that refuses to exist without a denominator."""
    if denominator <= 0:
        return _not_run(why)
    return _measured(
        round(numerator / denominator, 6), numerator=numerator, denominator=denominator
    )


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _confusion(records: list[dict], *, expected_key: str, observed_key: str) -> dict:
    """TP/FP/FN/TN over records carrying both a label and an observation."""
    tp = fp = fn = tn = 0
    judged = 0
    for record in records:
        expected = _flag(record["expected"].get(expected_key))
        observed = _flag(record["observed"].get(observed_key))
        if expected is None or observed is None:
            continue
        judged += 1
        if expected and observed:
            tp += 1
        elif expected and not observed:
            fn += 1
        elif not expected and observed:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "judged": judged}


def _detection_metrics(confusion: dict) -> dict[str, dict]:
    tp, fp, fn, tn = (confusion[key] for key in ("tp", "fp", "fn", "tn"))
    why = (
        "no raw case carries both the expected label and the observed outcome — "
        "precision/recall have no denominator (§105)"
    )
    return {
        "precision": _rate(tp, tp + fp, why),
        "recall": _rate(tp, tp + fn, why),
        "false_intervention_rate": _rate(fp, fp + tn, why),
        "miss_rate": _rate(fn, tp + fn, why),
    }


def _escalation_rate(records: list[dict]) -> dict:
    escalated = sum(1 for record in records if record["observed"].get("escalated") is True)
    return _rate(escalated, len(records), "the dataset is empty")


def _confidence_bins(records: list[dict]) -> dict:
    values = [
        value
        for value in (_num(record["observed"].get("confidence")) for record in records)
        if value is not None
    ]
    if not values:
        return _not_run("no raw case records an answer confidence — bins would be invented")
    bins: dict[str, int] = {}
    for low, high in zip(CONFIDENCE_BIN_EDGES, CONFIDENCE_BIN_EDGES[1:], strict=False):
        label = f"{low:.1f}-{high:.1f}"
        in_bin = [
            value for value in values if low <= value < high or (high == 1.0 and value == 1.0)
        ]
        bins[label] = len(in_bin)
    node = _measured(bins, n=len(values))
    return node


def _latency(records: list[dict]) -> dict:
    values = [
        value
        for value in (_num(record["observed"].get("latency_ms")) for record in records)
        if value is not None
    ]
    if not values:
        return _not_run("no raw case records latency_ms")
    return _measured(round(statistics.fmean(values), 4), n=len(values), **{
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "median": round(statistics.median(values), 4),
    })


def _sum_field(records: list[dict], field_name: str, why: str) -> dict:
    values = [
        value
        for value in (_num(record["observed"].get(field_name)) for record in records)
        if value is not None
    ]
    if not values:
        return _not_run(why)
    return _measured(round(sum(values), 6), n=len(values))


def _count_field(records: list[dict], field_name: str, why: str) -> dict:
    values = [
        value
        for value in (_num(record["observed"].get(field_name)) for record in records)
        if value is not None
    ]
    if not values:
        return _not_run(why)
    return _measured(int(sum(values)), n=len(values))


def _system2_calls_avoided(records: list[dict]) -> dict:
    pairs = []
    for record in records:
        baseline = _num(record["observed"].get("baseline_system_2_calls"))
        observed = _num(record["observed"].get("system_2_calls"))
        if baseline is None or observed is None:
            continue
        pairs.append(baseline - observed)
    if not pairs:
        return _not_run(
            "no raw case records both system_2_calls and baseline_system_2_calls — "
            "an 'avoided' number needs a baseline"
        )
    return _measured(round(sum(pairs), 6), n=len(pairs))


def _task_success_impact(records: list[dict]) -> dict:
    """The objective outcome: a *delta* where a baseline exists, else the rate."""
    deltas = []
    for record in records:
        baseline = _flag(record["observed"].get("baseline_task_succeeded"))
        observed = _flag(record["observed"].get("task_succeeded"))
        if baseline is None or observed is None:
            continue
        deltas.append(float(observed) - float(baseline))
    if deltas:
        return _measured(round(statistics.fmean(deltas), 6), n=len(deltas), kind="delta")
    outcomes = [
        value
        for value in (_flag(record["observed"].get("task_succeeded")) for record in records)
        if value is not None
    ]
    if not outcomes:
        return _not_run(
            "no raw case records an objective task_succeeded — an end state is a claim, "
            "not a measurement (§0/§105)"
        )
    node = _rate(sum(1 for value in outcomes if value), len(outcomes), "no outcome recorded")
    node["kind"] = "rate"
    return node


def _completion_metrics(records: list[dict]) -> dict:
    judged_accept = [
        record
        for record in records
        if _flag(record["expected"].get("should_accept")) is not None
        and _flag(record["observed"].get("accepted")) is not None
    ]
    false_accepts = [
        record
        for record in judged_accept
        if record["expected"]["should_accept"] is False and record["observed"]["accepted"] is True
    ]
    false_rejects = [
        record
        for record in judged_accept
        if record["expected"]["should_accept"] is True and record["observed"]["accepted"] is False
    ]
    why = (
        "no raw case carries both expected.should_accept and observed.accepted — "
        "false completion acceptance has no denominator (§20)"
    )
    metrics = {
        "false_completion_acceptance": _rate(len(false_accepts), len(judged_accept), why),
        "false_completion_rejection": _rate(len(false_rejects), len(judged_accept), why),
    }
    accepted_ids = [record["case_id"] for record in false_accepts]
    metrics["false_completion_acceptance"]["case_ids"] = accepted_ids
    for metric, flag_name in (
        ("semantic_evidence_mismatch_caught", "semantic_evidence_mismatch"),
        ("stale_evidence_caught", "stale_evidence"),
        ("mock_as_live_caught", "mock_as_live"),
    ):
        expected_cases = [
            record
            for record in records
            if flag_name in (record["expected"].get("expected_flags") or [])
        ]
        caught = [
            record
            for record in expected_cases
            if flag_name in (record["observed"].get("flags") or [])
        ]
        metrics[metric] = _rate(
            len(caught),
            len(expected_cases),
            f"no raw case expects the flag {flag_name!r} — nothing to have caught",
        )
        metrics[metric]["case_ids_caught"] = [record["case_id"] for record in caught]
        metrics[metric]["case_ids_missed"] = [
            record["case_id"] for record in expected_cases if record not in caught
        ]
    return metrics


def _computer_use_metrics(records: list[dict]) -> dict:
    blind = [record for record in records if record["observed"].get("blind_click") is True]
    wrong = [record for record in records if record["observed"].get("wrong_gui_action") is True]
    return {
        "reobserves": _count_field(records, "reobserves", "no raw case records reobserves"),
        "screenshots": _count_field(records, "screenshots", "no raw case records screenshots"),
        "wrong_gui_actions": _measured(
            len(blind) + len(wrong),
            n=len(records),
            case_ids=sorted({record["case_id"] for record in blind + wrong}),
        ),
    }


@dataclass
class CalibrationReport:
    """The measured report of one dataset — every key, measured or ``not_run``."""

    kind: str
    records_read: int
    metrics: dict[str, dict] = field(default_factory=dict)
    dataset_version: str = ""
    scenarios: dict[str, int] = field(default_factory=dict)
    synthetic: bool = False

    def unmeasurable(self) -> tuple[str, ...]:
        return tuple(key for key, node in self.metrics.items() if node["status"] == "not_run")

    def measured(self) -> tuple[str, ...]:
        return tuple(key for key, node in self.metrics.items() if node["status"] == "measured")

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "records_read": self.records_read,
            "dataset_version": self.dataset_version,
            "synthetic": self.synthetic,
            "scenarios": dict(self.scenarios),
            "metrics": {key: dict(node) for key, node in self.metrics.items()},
            "metrics_measured": list(self.measured()),
            "metrics_not_run": list(self.unmeasurable()),
        }


def compute_metrics(
    records: list[dict],
    *,
    kind: str,
    dataset_version: str = "",
    synthetic: bool = False,
) -> CalibrationReport:
    """Compute the §20 metric set — from the provided raw records only.

    Raises :class:`NoCalibrationDataError` when there is nothing to measure; a
    metric that cannot be measured from these records is ``not_run`` with a
    reason, never a zero.
    """
    dataset_shape(kind)
    if not records:
        raise NoCalibrationDataError(
            "no raw data for kind "
            f"{kind!r} — nothing is measured and nothing will be printed as a zero"
        )
    for record in records:
        validate_record(record, kind=kind)

    metrics: dict[str, dict] = {}
    if kind == "completion":
        metrics.update(_detection_metrics(_confusion(
            records, expected_key="should_accept", observed_key="accepted"
        )))
    else:
        metrics.update(_detection_metrics(_confusion(
            records, expected_key="intervention_expected", observed_key="intervention"
        )))
    metrics["escalation_rate"] = _escalation_rate(records)
    metrics["confidence_bins"] = _confidence_bins(records)
    metrics["latency"] = _latency(records)
    metrics["input_tokens"] = _count_field(
        records, "input_tokens", "no raw case records input_tokens"
    )
    metrics["cost"] = _sum_field(records, "cost_usd", "no raw case records cost_usd")
    metrics["system2_calls_avoided"] = _system2_calls_avoided(records)
    metrics["task_success_impact"] = _task_success_impact(records)

    if kind == "completion":
        metrics.update(_completion_metrics(records))
    else:
        for key in COMPLETION_EXTRA_METRICS:
            metrics[key] = _not_run(f"the {key} metric is defined for the completion dataset")
    if kind == "computer_use":
        metrics.update(_computer_use_metrics(records))
    else:
        for key in COMPUTER_USE_EXTRA_METRICS:
            metrics[key] = _not_run(f"the {key} counter is defined for the computer_use dataset")

    ordered = {key: metrics[key] for key in METRIC_KEYS if key in metrics}
    scenarios: dict[str, int] = {}
    for record in records:
        scenarios[str(record["scenario"])] = scenarios.get(str(record["scenario"]), 0) + 1
    return CalibrationReport(
        kind=kind,
        records_read=len(records),
        metrics=ordered,
        dataset_version=dataset_version,
        scenarios=scenarios,
        synthetic=bool(synthetic),
    )


def not_run_report(kind: str, reason: str) -> CalibrationReport:
    """The all-``not_run`` report — what 'no raw data' looks like, explicitly."""
    dataset_shape(kind)
    return CalibrationReport(
        kind=kind,
        records_read=0,
        metrics={key: _not_run(reason) for key in METRIC_KEYS},
    )


# --------------------------------------------------------------------------- #
# thresholds, frozen before holdout (§89/§20)                                  #
# --------------------------------------------------------------------------- #

#: The core metrics a threshold profile must have measured before it is frozen.
_FREEZE_REQUIRED_METRICS: tuple[str, ...] = (
    "precision",
    "recall",
    "false_intervention_rate",
    "miss_rate",
)


@dataclass(frozen=True)
class FrozenThresholdProfile:
    """Thresholds frozen before holdout, with the measurements they came from.

    A frozen profile refuses edits; a change needs a new profile id
    (:func:`refreeze_with_new_id`). ``choice_threshold``/``noul_band`` are the
    attributes the §12 escalation reading consumes.
    """

    profile_id: str
    question_pack_version: str
    dataset_version: str
    confidence_floor: float
    noul_band: tuple[float, float]
    measured: dict = field(default_factory=dict)
    measured_from_records: int = 0
    frozen: bool = True
    frozen_at: float = 0.0
    provenance: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise CalibrationDatasetError("a threshold profile needs a profile_id")
        if not self.question_pack_version or not self.dataset_version:
            raise CalibrationDatasetError(
                "a threshold profile names its question_pack_version and dataset_version (§89)"
            )
        low, high = (float(self.noul_band[0]), float(self.noul_band[1]))
        if not 0.0 <= low <= high <= 1.0:
            raise CalibrationDatasetError(
                f"noul_band must be 0 <= low <= high <= 1: {self.noul_band}"
            )
        object.__setattr__(self, "noul_band", (low, high))
        if not 0.0 <= float(self.confidence_floor) <= 1.0:
            raise CalibrationDatasetError(
                f"confidence_floor must be within 0..1: {self.confidence_floor}"
            )
        if not self.frozen_at:
            object.__setattr__(self, "frozen_at", time.time())

    # -- accessors the rest of the runtime uses ---------------------------

    @property
    def choice_threshold(self) -> float:
        """§12 reads a Choice/Score confidence floor under this name."""
        return float(self.confidence_floor)

    @property
    def score_threshold(self) -> float:
        return float(self.confidence_floor)

    @property
    def threshold_profile_id(self) -> str:
        return self.profile_id

    def as_dict(self) -> dict:
        return {
            "threshold_profile_id": self.profile_id,
            "profile_id": self.profile_id,
            "question_pack_version": self.question_pack_version,
            "dataset_version": self.dataset_version,
            "confidence_floor": float(self.confidence_floor),
            "noul_band": [float(self.noul_band[0]), float(self.noul_band[1])],
            "measured": dict(self.measured),
            "measured_from_records": int(self.measured_from_records),
            "frozen": bool(self.frozen),
            "frozen_at": float(self.frozen_at),
            "provenance": dict(self.provenance),
        }


def freeze_threshold_profile(
    *,
    profile_id: str,
    question_pack_version: str,
    dataset_version: str,
    report: CalibrationReport | None,
    confidence_floor: float,
    noul_band: tuple[float, float],
) -> FrozenThresholdProfile:
    """Freeze a profile from a **measured** report — never from invention.

    Refuses when there is no report (:class:`NoCalibrationDataError`) or when a
    core metric is ``not_run``: a profile must carry its measured precision,
    recall, false-intervention rate and miss rate, otherwise "frozen before
    holdout" would freeze numbers nobody measured.
    """
    if report is None:
        raise NoCalibrationDataError(
            "no calibration report — a threshold profile is never frozen without measured data"
        )
    missing = [
        key
        for key in _FREEZE_REQUIRED_METRICS
        if report.metrics.get(key, {}).get("status") != "measured"
    ]
    if missing:
        raise CalibrationDatasetError(
            f"cannot freeze {profile_id!r}: {missing} are not measured — freezing an "
            "unmeasured threshold would invent a number (§89)"
        )
    measured = {
        "precision": report.metrics["precision"]["value"],
        "recall": report.metrics["recall"]["value"],
        "false_intervention_rate": report.metrics["false_intervention_rate"]["value"],
        "miss_rate": report.metrics["miss_rate"]["value"],
        "escalation_rate": report.metrics["escalation_rate"]["value"],
        "records": report.records_read,
    }
    return FrozenThresholdProfile(
        profile_id=profile_id,
        question_pack_version=question_pack_version,
        dataset_version=dataset_version,
        confidence_floor=float(confidence_floor),
        noul_band=(float(noul_band[0]), float(noul_band[1])),
        measured=measured,
        measured_from_records=report.records_read,
        provenance={"kind": report.kind, "dataset_version": dataset_version},
    )


def assert_profile_unchanged(
    profile: FrozenThresholdProfile,
    *,
    confidence_floor: float | None = None,
    noul_band: tuple[float, float] | None = None,
) -> None:
    """Prove a holdout run did not move a frozen threshold (§89/§20)."""
    if confidence_floor is not None and float(confidence_floor) != float(profile.confidence_floor):
        raise CalibrationDatasetError(
            f"profile {profile.profile_id!r} is frozen at confidence_floor "
            f"{profile.confidence_floor}; a change needs a NEW profile id, never a silent edit"
        )
    if noul_band is not None:
        wanted = (float(noul_band[0]), float(noul_band[1]))
        current = (float(profile.noul_band[0]), float(profile.noul_band[1]))
        if wanted != current:
            raise CalibrationDatasetError(
                f"profile {profile.profile_id!r} is frozen at noul_band {current}; "
                f"{wanted} needs a NEW profile id, never a silent edit"
            )


def refreeze_with_new_id(
    profile: FrozenThresholdProfile,
    *,
    profile_id: str,
    dataset_version: str,
    confidence_floor: float | None = None,
    noul_band: tuple[float, float] | None = None,
) -> FrozenThresholdProfile:
    """The only legitimate way to change thresholds: a new id and dataset version."""
    if profile_id == profile.profile_id:
        raise CalibrationDatasetError(
            f"{profile_id!r} is already frozen — a changed threshold needs a new profile id"
        )
    if dataset_version == profile.dataset_version:
        raise CalibrationDatasetError(
            "a re-frozen profile names the dataset version it was measured on — "
            f"{dataset_version!r} is the old one"
        )
    return FrozenThresholdProfile(
        profile_id=profile_id,
        question_pack_version=profile.question_pack_version,
        dataset_version=dataset_version,
        confidence_floor=(
            float(profile.confidence_floor) if confidence_floor is None else float(confidence_floor)
        ),
        noul_band=(
            (float(profile.noul_band[0]), float(profile.noul_band[1]))
            if noul_band is None
            else (float(noul_band[0]), float(noul_band[1]))
        ),
        measured=dict(profile.measured),
        measured_from_records=profile.measured_from_records,
        provenance={**dict(profile.provenance), "refrozen_from": profile.profile_id},
    )


# --------------------------------------------------------------------------- #
# CLI — measurements or a non-zero exit, never a zero table                     #
# --------------------------------------------------------------------------- #


def _print_human(report: CalibrationReport) -> None:
    print(
        f"calibration — kind={report.kind} records={report.records_read} "
        f"dataset_version={report.dataset_version or '(unset)'}"
    )
    if report.synthetic:
        print("!! SYNTHETIC FIXTURE — metric math only, NOT a measurement")
    for key in METRIC_KEYS:
        node = report.metrics.get(key)
        if node is None:
            continue
        if node["status"] == "not_run":
            print(f"  {key}: NOT RUN — {node['reason']}")
        else:
            print(f"  {key}: {node['value']}  (n={node.get('n', '-')})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibration/holdout metrics for System One (§19/§20)."
    )
    parser.add_argument("--kind", required=True, choices=list(DATASET_KINDS))
    parser.add_argument(
        "--dataset", nargs="*", default=[], help="raw JSONL/JSON files (globs allowed)"
    )
    parser.add_argument("--dataset-version", default="", help="the frozen dataset version label")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--freeze", action="store_true", help="freeze a threshold profile from this run"
    )
    parser.add_argument("--profile-id", default="", help="profile id for --freeze")
    parser.add_argument("--confidence-floor", type=float, default=0.0)
    parser.add_argument("--noul-band", default="", help="'low,high' for --freeze")
    args = parser.parse_args(argv)

    if not args.dataset:
        print(
            "no raw data — no dataset was given. Nothing is measured and nothing is "
            "printed as zero. Pass the raw records explicitly.",
            file=__import__("sys").stderr,
        )
        return 2
    try:
        records = load_dataset(args.dataset, kind=args.kind)
    except FileNotFoundError as exc:
        print(f"no raw data — {exc}", file=__import__("sys").stderr)
        return 2
    except (CalibrationDatasetError, ValueError, json.JSONDecodeError) as exc:
        print(f"raw dataset refused: {exc}", file=__import__("sys").stderr)
        return 3
    if not records:
        print(
            "no raw data — the given files parsed to zero records. No metric can be "
            "reported from an empty dataset.",
            file=__import__("sys").stderr,
        )
        return 2

    report = compute_metrics(
        records, kind=args.kind, dataset_version=args.dataset_version
    )
    if args.freeze:
        band = (0.0, 1.0)
        if args.noul_band:
            try:
                low, high = (float(part) for part in args.noul_band.split(",", 1))
                band = (low, high)
            except (TypeError, ValueError):
                print("--noul-band must look like '0.4,0.6'", file=__import__("sys").stderr)
                return 3
        if not args.profile_id:
            print("--freeze needs --profile-id", file=__import__("sys").stderr)
            return 3
        try:
            profile = freeze_threshold_profile(
                profile_id=args.profile_id,
                question_pack_version=f"{args.kind}_v1",
                dataset_version=args.dataset_version or "unlabelled",
                report=report,
                confidence_floor=args.confidence_floor,
                noul_band=band,
            )
        except (CalibrationDatasetError, NoCalibrationDataError) as exc:
            print(f"refused to freeze a threshold profile: {exc}", file=__import__("sys").stderr)
            return 5
        print(json.dumps(profile.as_dict(), indent=2, sort_keys=True))
        return 0

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        _print_human(report)
        print(
            f"\n{len(report.unmeasurable())} of {len(report.metrics)} metric rows are "
            "NOT RUN (no denominator) and are reported as such instead of as a zero."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry point
    raise SystemExit(main())


__all__ = [
    "COMPLETION_DATASET_FIELDS",
    "COMPLETION_EXTRA_METRICS",
    "COMPLETION_SCENARIOS",
    "COMPUTER_USE_DATASET_FIELDS",
    "COMPUTER_USE_EXTRA_METRICS",
    "COMPUTER_USE_SCENARIOS",
    "CONFIDENCE_BIN_EDGES",
    "CORE_METRIC_KEYS",
    "DATASET_KINDS",
    "METRIC_KEYS",
    "VOICE_DATASET_FIELDS",
    "VOICE_SCENARIOS",
    "CalibrationDatasetError",
    "CalibrationReport",
    "FrozenThresholdProfile",
    "NoCalibrationDataError",
    "assert_profile_unchanged",
    "compute_metrics",
    "dataset_shape",
    "freeze_threshold_profile",
    "load_dataset",
    "main",
    "not_run_report",
    "refreeze_with_new_id",
    "validate_record",
]
