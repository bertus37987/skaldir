#!/usr/bin/env python3
"""Benchmark evaluation for the Skaldir/God's Voice comparison arms (§97 JEV-19, §114).

Reads RAW JSONL runs (the shape of ``docs/BENCHMARK-RUN-SCHEMA.json``
``run_record`` + the metric inputs the schema names) and computes the §97 metric
set per configuration:

    task success · false success · unnecessary interventions · missed errors
    · System-2 calls · latency · tokens/cost · recovery

Two rules are hard, and they are the whole point of this script:

1. **No raw data means NO numbers.** With no readable run files (or files with
   zero parsed runs) the script writes ``no runs`` to stderr and exits non-zero.
   It never prints a zero, a dash or an empty table as if a measurement existed.
2. **A metric with no denominator is ``null`` with a reason** — never ``0`` and
   never an interpolation. ``task_success`` needs a run whose ground truth is
   known; ``false_success`` needs runs that claimed completion; precision/recall
   need ground-truth labels. Anything else is reported as unmeasurable.

Usage::

    .venv/bin/python tools/eval_benchmark.py docs/audit/benchmark-runs/*.jsonl
    .venv/bin/python tools/eval_benchmark.py --json runs.jsonl
    .venv/bin/python tools/eval_benchmark.py --selftest      # synthetic fixture, labelled as such

Programmatic use::

    from tools.eval_benchmark import evaluate_runs, load_runs

Every parameter is explicit; unknown keys in a raw record are preserved under
``extra`` so nothing silently disappears.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from pathlib import Path

SCHEMA_REF = "docs/BENCHMARK-RUN-SCHEMA.json#1.0"

#: §97 arm names. Present/absent is reported per arm; a missing arm is not a zero.
VOICE_ARMS = ("A_system2_triage", "B_llm_system_one", "C_jev_shadow", "D_jev_active_system2")
COMPUTER_USE_ARMS = ("A_system2_every_step", "B_system_one_state_controller")

#: The metric rows this script can produce (§97).
METRIC_KEYS = (
    "task_success",
    "false_success",
    "unnecessary_intervention",
    "missed_error",
    "system_2_calls",
    "latency_ttft_s",
    "latency_total_s",
    "tokens_total",
    "cost_usd",
    "recovery_ok",
)


class NoRunsError(RuntimeError):
    """Raised when there is nothing to evaluate — the honest state is 'no runs'."""


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _flag(value):
    if isinstance(value, bool):
        return value
    return None


def load_runs(paths: list[str]) -> list[dict]:
    """Load raw runs from JSONL files (and/or one JSON array per file)."""
    runs: list[dict] = []
    for pattern in paths:
        for name in sorted(glob.glob(pattern)) or [pattern]:
            path = Path(name)
            if not path.is_file():
                raise FileNotFoundError(f"raw run file not found: {path}")
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                payload = json.loads(text)
                records = payload if isinstance(payload, list) else [payload]
            else:
                records = [json.loads(line) for line in text.splitlines() if line.strip()]
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError(
                        f"{path}: a raw run must be a JSON object,"
                        f" got {type(record).__name__}"
                    )
                record.setdefault("_source", str(path))
                runs.append(record)
    return runs


def _ground_truth(record: dict) -> dict:
    block = record.get("ground_truth")
    return block if isinstance(block, dict) else {}


def _observed(record: dict) -> dict:
    block = record.get("observed")
    return block if isinstance(block, dict) else {}


def _latency(record: dict) -> dict:
    block = record.get("latency")
    return block if isinstance(block, dict) else {}


def _usage(record: dict) -> dict:
    block = record.get("usage")
    return block if isinstance(block, dict) else {}


def _rate(numerator: int, denominator: int, why: str) -> dict:
    """A rate that refuses to exist without a denominator."""
    if denominator <= 0:
        return {"value": None, "numerator": numerator, "denominator": denominator, "reason": why}
    return {
        "value": round(numerator / denominator, 4),
        "numerator": numerator,
        "denominator": denominator,
    }


def _mean(values: list[float], why: str) -> dict:
    if not values:
        return {"value": None, "n": 0, "reason": why}
    return {
        "value": round(statistics.fmean(values), 4),
        "n": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "median": round(statistics.median(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else None,
    }


def _sum(values: list[float], why: str) -> dict:
    if not values:
        return {"value": None, "n": 0, "reason": why}
    return {"value": round(sum(values), 4), "n": len(values)}


def _task_success(runs: list[dict]) -> dict:
    """Success rate over runs whose ground truth states whether success was expected.

    A claimed completion is NOT a success (ABNAHME §0/§5.2): the run must carry an
    objective outcome flag. Records without it are excluded and counted.
    """
    judged = [r for r in runs if _flag(_observed(r).get("task_succeeded")) is not None]
    wins = sum(1 for r in judged if _observed(r)["task_succeeded"] is True)
    out = _rate(
        wins,
        len(judged),
        "no run carries observed.task_succeeded — an end state is a claim, not a measurement (§0)",
    )
    out["excluded_runs_without_objective_outcome"] = len(runs) - len(judged)
    return out


def _false_success(runs: list[dict]) -> dict:
    """Completion claimed while the objective evidence contradicts it (§105/§97)."""
    claimed = [r for r in runs if _flag(_observed(r).get("completion_claimed")) is True]
    bad = [r for r in claimed if _observed(r).get("evidence_satisfied") is False]
    out = _rate(
        len(bad),
        len(claimed),
        "no run both claimed completion and recorded evidence_satisfied — no denominator",
    )
    out["claimed_runs"] = len(claimed)
    out["contradicted_run_ids"] = [r.get("run_id") for r in bad]
    return out


def _intervention_quality(runs: list[dict]) -> dict:
    """§97: unnecessary interventions and missed errors, both against ground truth."""
    labelled = [r for r in runs if _flag(_ground_truth(r).get("intervention_expected")) is not None]
    unnecessary = [
        r
        for r in labelled
        if _ground_truth(r)["intervention_expected"] is False
        and (_observed(r).get("interventions") or 0) > 0
    ]
    missed = [
        r
        for r in labelled
        if _ground_truth(r)["intervention_expected"] is True
        and (_observed(r).get("interventions") or 0) == 0
    ]
    return {
        "unnecessary_intervention": _rate(
            len(unnecessary),
            len(labelled),
            "no run carries ground_truth.intervention_expected — precision/recall undefined (§105)",
        ),
        "missed_error": _rate(
            len(missed),
            len(labelled),
            "no run carries ground_truth.intervention_expected — precision/recall undefined (§105)",
        ),
        "labelled_runs": len(labelled),
        "unnecessary_run_ids": [r.get("run_id") for r in unnecessary],
        "missed_run_ids": [r.get("run_id") for r in missed],
    }


def _counts(runs: list[dict], field: str) -> dict:
    values = [_num(_observed(r).get(field)) for r in runs]
    present = [v for v in values if v is not None]
    if not present:
        return {"total": None, "n": 0, "reason": f"no run records observed.{field}"}
    return {"total": int(sum(present)), "n": len(present)}


def _recovery(runs: list[dict]) -> dict:
    judged = [r for r in runs if _flag(_observed(r).get("recovery_ok")) is not None]
    wins = sum(1 for r in judged if _observed(r)["recovery_ok"] is True)
    out = _rate(wins, len(judged), "no run records observed.recovery_ok")
    doubles = [r for r in runs if _observed(r).get("double_side_effect") is True]
    out["double_side_effect_runs"] = len(doubles)
    out["double_side_effect_run_ids"] = [r.get("run_id") for r in doubles]
    return out


def evaluate_runs(runs: list[dict]) -> dict:
    """Compute the §97 metric block. Raises NoRunsError when there is nothing to measure."""
    if not runs:
        raise NoRunsError("no runs")

    configurations: dict[str, list[dict]] = {}
    for record in runs:
        key = str(record.get("configuration") or record.get("arm") or "<unlabelled>")
        configurations.setdefault(key, []).append(record)

    report: dict = {
        "schema_ref": SCHEMA_REF,
        "runs_read": len(runs),
        "configurations": {},
        "arms_present": {
            "voice_triage": [a for a in VOICE_ARMS if a in configurations],
            "computer_use": [a for a in COMPUTER_USE_ARMS if a in configurations],
        },
        "arms_missing": {
            "voice_triage": [a for a in VOICE_ARMS if a not in configurations],
            "computer_use": [a for a in COMPUTER_USE_ARMS if a not in configurations],
        },
    }

    for name, group in sorted(configurations.items()):
        ttft = [_num(_latency(r).get("ttft_s")) for r in group]
        total = [_num(_latency(r).get("total_s")) for r in group]
        tokens = [_num(_usage(r).get("total_tokens")) for r in group]
        if all(v is None for v in tokens):
            tokens = [
                (_num(_usage(r).get("input_tokens")) or 0)
                + (_num(_usage(r).get("output_tokens")) or 0)
                if _usage(r).get("input_tokens") is not None
                or _usage(r).get("output_tokens") is not None
                else None
                for r in group
            ]
        cost = [_num(_usage(r).get("cost_usd")) for r in group]
        report["configurations"][name] = {
            "runs": len(group),
            "task_success": _task_success(group),
            "false_success": _false_success(group),
            "intervention": _intervention_quality(group),
            "system_2_calls": _counts(group, "system_2_calls"),
            "tool_calls": _counts(group, "tool_calls"),
            "screenshots": _counts(group, "screenshots"),
            "reobserves": _counts(group, "reobserves"),
            "latency_ttft_s": _mean(
                [v for v in ttft if v is not None], "no run records latency.ttft_s"
            ),
            "latency_total_s": _mean(
                [v for v in total if v is not None], "no run records latency.total_s"
            ),
            "tokens_total": _sum(
                [v for v in tokens if v is not None], "no run records usage tokens"
            ),
            "cost_usd": _sum([v for v in cost if v is not None], "no run records usage.cost_usd"),
            "recovery": _recovery(group),
        }
    return report


def _metric_node(block: dict, metric: str) -> dict:
    """Resolve a §97 metric key to its node in the per-configuration block."""
    path = {
        "task_success": ("task_success",),
        "false_success": ("false_success",),
        "unnecessary_intervention": ("intervention", "unnecessary_intervention"),
        "missed_error": ("intervention", "missed_error"),
        "system_2_calls": ("system_2_calls",),
        "latency_ttft_s": ("latency_ttft_s",),
        "latency_total_s": ("latency_total_s",),
        "tokens_total": ("tokens_total",),
        "cost_usd": ("cost_usd",),
        "recovery_ok": ("recovery",),
    }[metric]
    node = block[path[0]]
    for part in path[1:]:
        node = node[part]
    return node


def count_unmeasurable(report: dict) -> int:
    """How many metric rows resolved to 'no denominator' (never reported as a zero)."""
    return sum(
        1
        for block in report["configurations"].values()
        for metric in METRIC_KEYS
        if _metric_node(block, metric).get("value") is None
    )


def _print_human(report: dict, stream) -> None:
    print(
        f"benchmark evaluation — {report['runs_read']} raw runs ({report['schema_ref']})",
        file=stream,
    )
    for arm, present in report["arms_present"].items():
        if not present:
            missing = ", ".join(report["arms_missing"][arm])
            print(
                f"  [{arm}] NO ARM PRESENT (missing: {missing}) — nothing to compare",
                file=stream,
            )
    for name, block in report["configurations"].items():
        print(f"\n[{name}] runs={block['runs']}", file=stream)
        for metric in METRIC_KEYS:
            node = _metric_node(block, metric)
            value = node.get("value")
            if value is None:
                value = node.get("total")
            if value is None:
                print(f"  {metric}: NOT MEASURABLE — {node.get('reason')}", file=stream)
            else:
                detail = (
                    f"{node.get('numerator')}/{node.get('denominator')}"
                    if "numerator" in node
                    else f"n={node.get('n')}"
                )
                print(f"  {metric}: {value}  ({detail})", file=stream)


def _selftest_runs() -> list[dict]:
    """Synthetic fixture for the metric MATH only — never a product measurement."""
    return [
        {
            "run_id": "fixture-A-1",
            "configuration": "A_system2_triage",
            "ground_truth": {"intervention_expected": True},
            "observed": {
                "task_succeeded": True,
                "completion_claimed": True,
                "evidence_satisfied": True,
                "interventions": 2,
                "system_2_calls": 4,
                "recovery_ok": True,
            },
            "latency": {"ttft_s": 1.0, "total_s": 10.0},
            "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01},
        },
        {
            "run_id": "fixture-A-2",
            "configuration": "A_system2_triage",
            "ground_truth": {"intervention_expected": False},
            "observed": {
                "task_succeeded": False,
                "completion_claimed": True,
                "evidence_satisfied": False,
                "interventions": 1,
                "system_2_calls": 3,
                "recovery_ok": False,
            },
            "latency": {"ttft_s": 2.0, "total_s": 20.0},
            "usage": {"input_tokens": 200, "output_tokens": 100, "cost_usd": 0.02},
        },
        {
            "run_id": "fixture-D-1",
            "configuration": "D_jev_active_system2",
            "ground_truth": {"intervention_expected": True},
            "observed": {
                "task_succeeded": True,
                "completion_claimed": True,
                "evidence_satisfied": True,
                "interventions": 1,
                "system_2_calls": 1,
                "recovery_ok": True,
            },
            "latency": {"ttft_s": 0.5, "total_s": 8.0},
            "usage": {"input_tokens": 90, "output_tokens": 40, "cost_usd": 0.008},
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate raw benchmark runs (§97 metrics).")
    parser.add_argument("paths", nargs="*", help="raw JSONL/JSON run files (globs allowed)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run the metric math against an in-memory SYNTHETIC fixture (not a measurement)",
    )
    args = parser.parse_args()

    if args.selftest:
        print(
            "!! SYNTHETIC FIXTURE — this runs the metric MATH only. "
            "It is NOT a product measurement and must never be quoted as one.",
            file=sys.stderr,
        )
        report = evaluate_runs(_selftest_runs())
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            _print_human(report, sys.stdout)
        return 0

    if not args.paths:
        print(
            "no runs — no raw benchmark JSONL was given. Nothing is measured, "
            "nothing will be printed as zero. Pass the raw files explicitly.",
            file=sys.stderr,
        )
        return 2

    try:
        runs = load_runs(args.paths)
    except FileNotFoundError as exc:
        print(f"no runs — {exc}", file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"raw runs unreadable: {exc}", file=sys.stderr)
        return 3

    if not runs:
        print(
            "no runs — the given files parsed to zero run records. "
            "No metric can be reported from an empty run set.",
            file=sys.stderr,
        )
        return 2

    report = evaluate_runs(runs)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report, sys.stdout)
        print(
            f"\n{count_unmeasurable(report)} of "
            f"{len(report['configurations']) * len(METRIC_KEYS)} metric rows have no denominator "
            "and are reported as NOT MEASURABLE instead of as a zero.",
            file=sys.stdout,
        )

    if not report["arms_present"]["voice_triage"] and not report["arms_present"]["computer_use"]:
        print(
            "no comparison arm recognised in the raw runs — §97 needs at least the A/B arms",
            file=sys.stderr,
        )
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
