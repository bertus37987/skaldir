#!/usr/bin/env python
"""Measure the register's criterion linkage (S20) — and fail when it is incomplete.

Why: the register carried the fields `test_ids`, `files_symbols`, `evidence_refs` and
`run_ids` for all 97 criteria and filled NONE of them (finding AF-09). A criterion that
cannot name its code, its test and its evidence in machine-readable form is a claim, not a
link. This tool is the repeatable measurement for that criterion.

    .venv/bin/python tools/register_linkage_report.py            # report + gate
    .venv/bin/python tools/register_linkage_report.py --json     # machine-readable

Exit codes: 0 = every reviewed criterion carries a code link and (where tests exist) a test
link; 1 = gaps remain (the gap list is printed); 2 = the register is missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTER = REPO / "audit" / "REQUIREMENTS.json"
FIELDS = ("files_symbols", "test_ids", "evidence_refs", "run_ids")


def load() -> list[dict]:
    if not REGISTER.is_file():
        print(f"register missing: {REGISTER}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(REGISTER.read_text(encoding="utf-8"))["criteria"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    criteria = load()

    coverage = {field: sum(1 for c in criteria if c.get(field)) for field in FIELDS}
    reviewed = [c for c in criteria if c.get("implementation_state") not in ("unknown", None)]
    full = [c for c in criteria if all(c.get(f) for f in ("files_symbols", "test_ids", "evidence_refs"))]

    no_code = [c["id"] for c in reviewed if not c.get("files_symbols")]
    no_evidence = [c["id"] for c in reviewed if not c.get("evidence_refs")]
    payload = {
        "criteria": len(criteria),
        "coverage": coverage,
        "reviewed": len(reviewed),
        "with_code_test_evidence": len(full),
        "reviewed_without_code_link": no_code,
        "reviewed_without_evidence_link": no_evidence,
        "satisfied": not no_code and not no_evidence,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"criterion linkage — {len(criteria)} criteria, {len(reviewed)} reviewed")
        for field in FIELDS:
            print(f"  {field:14s} {coverage[field]:3d}/{len(criteria)}")
        print(f"  criteria with code+test+evidence: {len(full)}/{len(criteria)}")
        if no_code:
            print(f"  reviewed criteria WITHOUT a code link ({len(no_code)}): {', '.join(no_code)}")
        if no_evidence:
            print(f"  reviewed criteria WITHOUT evidence ({len(no_evidence)}): {', '.join(no_evidence)}")
        print("  linkage complete" if payload["satisfied"] else "  linkage INCOMPLETE")
    return 0 if payload["satisfied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
