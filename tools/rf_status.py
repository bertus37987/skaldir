#!/usr/bin/env python3
"""Which RF recovery fixes does a source tree actually carry? (§Wave 8)

The overlays in `host-overlay/` are versioned patches, each with a `--check` mode that changes
nothing. This runs them against one tree — by default the LIVE engine that is running right now —
and prints one line per item, so "RF fixed" is a measurement instead of a claim about a patched
copy somewhere else.

Read-only: `--check` never writes. Nothing here patches anything.

Usage:
    python tools/rf_status.py [--src ~/.hermes/hermes-agent] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OVERLAYS = sorted((REPO / "host-overlay").glob("patch_rf*.py"))
LIVE_ENGINE = Path.home() / ".hermes" / "hermes-agent"

# Applied or not, per item, read off the overlay's own check output. "would patch" means the tree
# still carries the unpatched anchor — the fix is NOT in that runtime.
WOULD_PATCH = ("would patch",)
APPLIED = ("already patched", "applied:")


def check_patch(patch: Path, src: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(patch), "--check", str(src)],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
    )
    output = (proc.stdout or proc.stderr or "").strip()
    last = output.splitlines()[-1] if output else ""
    applied = any(marker in last for marker in APPLIED)
    pending = any(marker in last for marker in WOULD_PATCH)
    return {
        "item": patch.stem.replace("patch_", "").replace("_", "-"),
        "patch": patch.name,
        "applied": applied,
        "pending": pending,
        "returncode": proc.returncode,
        "detail": last[:200],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default=str(LIVE_ENGINE), help="source tree to check (read-only)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    src = Path(args.src).expanduser()
    if not src.exists():
        print(f"source tree not found: {src}", file=sys.stderr)
        return 2
    rows = [check_patch(patch, src) for patch in OVERLAYS]
    if args.json:
        print(json.dumps({"src": str(src), "items": rows}, indent=2, ensure_ascii=False))
        return 0
    print(f"RF recovery status against {src}")
    for row in rows:
        state = "IN THIS RUNTIME" if row["applied"] else ("PENDING" if row["pending"] else
            "UNKNOWN")
        print(f"  {row['item']:<26} {state:<16} {row['detail']}")
    applied = sum(1 for row in rows if row["applied"])
    print(f"\n{applied}/{len(rows)} carried by this tree; the rest live only as overlays.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
