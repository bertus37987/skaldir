#!/usr/bin/env python3
"""The §27 benchmark arms as a runnable scaffold — and nothing more.

    Voice            A existing System-2 triage
                     B official LLM System-One adapter
                     C Jev shadow
                     D Jev active + System-2 escalation
    Goal Completion  A current verifier path
                     B LLM System-One preflight
                     C Jev preflight + targeted verifier
    Computer Use     A System-2 at every semantic decision
                     B Jev reactive controller + System-2 escalation

Three rules are hard here, and they are the point of the script:

1. **No raw data means no numbers.** With no readable run files (or files that
   parse to zero runs) the script writes ``no raw data`` to stderr and exits
   non-zero. It never prints a zero, a dash or an empty table as if a
   measurement existed.
2. **Vendor numbers are never our measurement.** A record carrying
   ``vendor_claim: true`` (or ``source: vendor``) is excluded from every metric
   and listed separately as a vendor claim. If *only* vendor material was
   supplied, the script fails loudly instead of adopting the numbers.
3. **A missing arm is named, not scored.** Present/absent is reported per arm;
   a missing arm is never a zero.

The metric maths themselves live in ``tools/eval_benchmark.py`` (§97); this
script maps raw runs onto the §27 arms and refuses to overstate what the data
supports.

Usage::

    .venv/bin/python tools/jev_benchmark_arms.py --list
    .venv/bin/python tools/jev_benchmark_arms.py docs/audit/benchmark-runs/*.jsonl
    .venv/bin/python tools/jev_benchmark_arms.py --json runs.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # the metric maths this scaffold reuses (§97)
    from eval_benchmark import METRIC_KEYS, NoRunsError, evaluate_runs, load_runs
except ImportError as exc:  # pragma: no cover — tools/ must sit next to this file
    print(f"cannot load tools/eval_benchmark.py: {exc}", file=sys.stderr)
    raise SystemExit(3) from exc

#: §27: family → ordered arm names, with what each arm is.
BENCHMARK_ARMS: dict[str, dict[str, str]] = {
    "voice_triage": {
        "A_existing_system2_triage": "existing System-2 triage (no System One)",
        "B_llm_system_one_adapter": "official LLM System-One adapter (not live Jev)",
        "C_jev_shadow": "Jev in shadow mode (recorded, no product effect)",
        "D_jev_active_system2_escalation": "Jev active + System-2 escalation",
    },
    "goal_completion": {
        "A_current_verifier_path": "current verifier path",
        "B_llm_system_one_preflight": "LLM System-One preflight",
        "C_jev_preflight_targeted_verifier": "Jev preflight + targeted verifier",
    },
    "computer_use": {
        "A_system2_every_decision": "System-2 at every semantic decision",
        "B_jev_reactive_controller": "Jev reactive controller + System-2 escalation",
    },
}

#: Fields that mark a record as a vendor claim, not our own measurement.
VENDOR_CLAIM_FIELDS = ("vendor_claim", "vendor_number", "is_vendor_claim")
VENDOR_SOURCES = ("vendor", "vendor_benchmark", "typesafe", "marketing")

#: §27: what the arms are compared on. Named here so a report can never quietly
#: drop a metric it could not measure.
COMPARISON_METRICS: tuple[str, ...] = (
    "task_success",
    "false_success",
    "unnecessary_intervention",
    "missed_error",
    "wrong_gui_actions",
    "recovery_ok",
    "system_2_calls",
    "latency_total_s",
    "cost_usd",
    "tokens_total",
    "reobserves",
)

_SYNTHETIC_NOTE = (
    "benchmark arms — nothing here is a measurement until raw runs exist. These arms "
    "compare configurations; they do not assert that any of them is better."
)


class VendorOnlyError(RuntimeError):
    """Only vendor material was supplied — that is never our measurement."""


def arm_family(record: dict) -> str:
    arm = str(record.get("arm") or record.get("configuration") or "")
    for family, arms in BENCHMARK_ARMS.items():
        if arm in arms:
            return family
    for family, arms in BENCHMARK_ARMS.items():
        if any(arm and arm.startswith(name.split("_")[0]) for name in arms):
            return family
    return ""


def is_vendor_claim(record: dict) -> bool:
    """Is this a vendor claim rather than a run of ours?"""
    for field_name in VENDOR_CLAIM_FIELDS:
        if record.get(field_name) is True:
            return True
    source = str(record.get("source") or record.get("provenance") or "").lower()
    return any(marker in source for marker in VENDOR_SOURCES)


def split_records(runs: list[dict]) -> tuple[list[dict], list[dict]]:
    """``(our runs, vendor claims)`` — the claims never enter a metric."""
    ours: list[dict] = []
    vendor: list[dict] = []
    for record in runs:
        (vendor if is_vendor_claim(record) else ours).append(record)
    return ours, vendor


def arm_report(runs: list[dict]) -> dict:
    """Per-family arm presence plus the §97 metric block for our own runs."""
    ours, vendor = split_records(runs)
    if not ours:
        raise VendorOnlyError(
            "only vendor claims were supplied — a vendor benchmark number is never our "
            "own measurement (§34/§97)"
        )
    try:
        metrics = evaluate_runs(ours)
    except NoRunsError as exc:
        raise VendorOnlyError(f"no measurable runs: {exc}") from exc
    present: dict[str, list[str]] = {family: [] for family in BENCHMARK_ARMS}
    missing: dict[str, list[str]] = {family: [] for family in BENCHMARK_ARMS}
    for family, arms in BENCHMARK_ARMS.items():
        for arm in arms:
            if any(
                str(record.get("arm") or record.get("configuration") or "") == arm
                for record in ours
            ):
                present[family].append(arm)
            else:
                missing[family].append(arm)
    return {
        "runs_read": len(runs),
        "our_runs": len(ours),
        "vendor_claims_excluded": [
            {
                "arm": record.get("arm") or record.get("configuration"),
                "source": record.get("source"),
            }
            for record in vendor
        ],
        "arms_present": present,
        "arms_missing": missing,
        "arms": {family: dict(arms) for family, arms in BENCHMARK_ARMS.items()},
        "comparison_metrics": list(COMPARISON_METRICS),
        "metrics": metrics,
    }


def print_arm_catalogue(stream) -> None:
    print(_SYNTHETIC_NOTE, file=stream)
    for family, arms in BENCHMARK_ARMS.items():
        print(f"\n[{family}]", file=stream)
        for arm, description in arms.items():
            print(f"  {arm}: {description}", file=stream)
    print(
        "\ncompared on: " + ", ".join(COMPARISON_METRICS) + "\n"
        "(a metric with no denominator is reported as NOT MEASURABLE, never as 0)",
        file=stream,
    )


def print_report(report: dict, stream) -> None:
    print(
        f"\narms report — {report['our_runs']} of {report['runs_read']} raw runs are ours "
        f"({len(report['vendor_claims_excluded'])} vendor claim(s) excluded)",
        file=stream,
    )
    for family, names in report["arms_present"].items():
        if not names:
            print(
                f"  [{family}] NO ARM PRESENT "
                f"(missing: {', '.join(report['arms_missing'][family])})"
                " — nothing to compare",
                file=stream,
            )
            continue
        if report["arms_missing"][family]:
            print(
                f"  [{family}] arms with runs: {', '.join(names)} | missing: "
                f"{', '.join(report['arms_missing'][family])}",
                file=stream,
            )
        else:
            print(f"  [{family}] all arms have runs: {', '.join(names)}", file=stream)
    for name, block in report["metrics"]["configurations"].items():
        print(f"\n[{name}] runs={block['runs']}", file=stream)
        for metric in METRIC_KEYS:
            node = _metric_node(block, metric)
            value = node.get("value")
            if value is None:
                value = node.get("total")
            if value is None:
                print(f"  {metric}: NOT MEASURABLE — {node.get('reason')}", file=stream)
            else:
                print(f"  {metric}: {value}", file=stream)


def _metric_node(block: dict, metric: str):
    """Delegate to eval_benchmark's own resolver (no second metric definition)."""
    from eval_benchmark import _metric_node as resolve

    return resolve(block, metric)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="§27 benchmark arms (scaffold, fails loudly).")
    parser.add_argument("paths", nargs="*", help="raw JSONL/JSON run files (globs allowed)")
    parser.add_argument("--list", action="store_true", help="print the arm catalogue and exit")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    if args.list:
        print_arm_catalogue(sys.stdout)
        return 0

    if not args.paths:
        print(
            "no raw data — no run files were given. The arms cannot be compared, and "
            "nothing is printed as a zero. Pass the raw runs explicitly.",
            file=sys.stderr,
        )
        return 2

    try:
        runs = load_runs(args.paths)
    except FileNotFoundError as exc:
        print(f"no raw data — {exc}", file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"raw runs unreadable: {exc}", file=sys.stderr)
        return 3

    if not runs:
        print(
            "no raw data — the given files parsed to zero runs. No arm can be compared.",
            file=sys.stderr,
        )
        return 2

    try:
        report = arm_report(runs)
    except VendorOnlyError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 5

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print_report(report, sys.stdout)

    if not any(report["arms_present"].values()):
        print(
            "no §27 arm recognised in the raw runs — name the arm (e.g. "
            "A_existing_system2_triage) in each record",
            file=sys.stderr,
        )
        return 4
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry point
    raise SystemExit(main())
