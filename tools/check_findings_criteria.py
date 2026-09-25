#!/usr/bin/env python3
"""Fail-loud cross-check of audit/findings.json against the criteria register.

ABNAHME.md §113 requires every finding to name the criterion ids it touches. A
finding that points at an id nobody in the register knows is a broken evidence
chain (ch. 06 §8 / §20 auditability) — so this check does not warn, it fails.

    .venv/bin/python tools/check_findings_criteria.py [--findings PATH] [--register PATH]

Exit 0 only when
  * both files parse,
  * every finding carries the §113 fields,
  * every criterion_id exists in audit/REQUIREMENTS.json,
  * every finding's `state` is one of the four vocabulary values declared in the
    file itself and `severity` is P0..P3 or null (a pass/negative finding).

It is intentionally dependency-free (stdlib only) so it can run in CI next to
tools/build_audit_register.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: ABNAHME.md §113 finding contract.
REQUIRED_FIELDS = (
    "finding_id",
    "severity",
    "criterion_ids",
    "base_revision",
    "platform",
    "repro_steps",
    "expected",
    "actual",
    "files_symbols",
    "evidence",
    "cause_or_hypothesis",
    "fix_hint",
    "retest",
)

SEVERITIES = ("P0", "P1", "P2", "P3")
STATES = ("open", "suppressed", "fixed_retested", "pass_negative")


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"FAIL: missing file: {path}") from None
    except ValueError as exc:
        raise SystemExit(f"FAIL: {path} is not valid JSON: {exc}") from None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", type=Path, default=REPO / "audit" / "findings.json")
    parser.add_argument("--register", type=Path, default=REPO / "audit" / "REQUIREMENTS.json")
    args = parser.parse_args()

    findings_doc = load(args.findings)
    register = load(args.register)

    known = {c["id"] for c in register.get("criteria", [])}
    if not known:
        raise SystemExit(f"FAIL: {args.register} carries no criteria ids")

    findings = findings_doc.get("findings")
    if not isinstance(findings, list) or not findings:
        raise SystemExit(f"FAIL: {args.findings} carries no findings list")

    problems: list[str] = []
    seen_ids: set[str] = set()

    for index, finding in enumerate(findings):
        fid = finding.get("finding_id") or f"<index {index}>"
        if fid in seen_ids:
            problems.append(f"{fid}: duplicate finding_id")
        seen_ids.add(fid)

        for field in REQUIRED_FIELDS:
            if field not in finding:
                problems.append(f"{fid}: missing §113 field {field!r}")

        severity = finding.get("severity")
        if severity is not None and severity not in SEVERITIES:
            problems.append(f"{fid}: severity {severity!r} is not one of {SEVERITIES} or null")

        state = finding.get("state")
        if state not in STATES:
            problems.append(f"{fid}: state {state!r} is not one of {STATES}")

        ids = finding.get("criterion_ids")
        if not isinstance(ids, list) or not ids:
            problems.append(f"{fid}: criterion_ids must be a non-empty list")
            continue
        for cid in ids:
            if cid not in known:
                problems.append(
                    f"{fid}: criterion_id {cid!r} does not exist in "
                    f"{args.register.name} ({len(known)} criteria)"
                )

    if problems:
        print("findings cross-check FAILED:", *problems, sep="\n  ", file=sys.stderr)
        return 1

    referenced = sorted({cid for f in findings for cid in f["criterion_ids"]})
    print(
        f"findings cross-check OK — {len(findings)} findings, "
        f"{len(referenced)} distinct criterion ids, all present in {args.register.name} "
        f"({len(known)} criteria)"
    )
    print(f"referenced ids: {', '.join(referenced)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
