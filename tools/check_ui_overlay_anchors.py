#!/usr/bin/env python3
"""Pre-build anchor check for the UI correction overlay (patch_app_ui.py).

Why this exists: the overlay's whole safety story is "every anchor hits exactly
once, or the build fails loudly". A full `godsvoice desktop build-app` takes
minutes and rsyncs a tree, so this checks the SAME anchors against a Hermes
checkout (default: $HERMES_HOME/hermes-agent, override with --source) in under a
second — the right place to catch a Hermes update that moved a line, before a
build starts.

Reports, per patch: file, expected vs found hits, and the first mismatching
anchor (truncated). Exit 0 only when every anchor is exactly where it belongs.

Usage:
  .venv/bin/python tools/check_ui_overlay_anchors.py
  .venv/bin/python tools/check_ui_overlay_anchors.py --source ~/.hermes/hermes-agent
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app-overlay"))

from patch_app_ui import UI_FILES, UI_PATCHES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(Path.home() / ".hermes" / "hermes-agent"))
    args = ap.parse_args()

    tree = Path(args.source).expanduser().resolve() / "apps" / "desktop"
    if not (tree / "src" / "styles.css").is_file():
        print(f"✗ not a Hermes desktop tree: {tree}")
        return 2

    failures: list[str] = []
    for rel, old, _new, expected in UI_PATCHES:
        path = tree / rel
        if not path.is_file():
            failures.append(f"{rel}: file missing")
            print(f"✗ {rel}: file missing")
            continue
        found = path.read_text(encoding="utf-8").count(old)
        mark = "✓" if found == expected else "✗"
        head = old.strip().splitlines()[0][:70] if old.strip() else old[:70]
        print(f"{mark} {rel}: {found}/{expected} — {head!r}")
        if found != expected:
            failures.append(f"{rel}: {found} hit(s), expected {expected}: {head!r}")

    for src_rel, dst_rel in UI_FILES:
        path = REPO / src_rel
        mark = "✓" if path.is_file() else "✗"
        print(f"{mark} file {src_rel} -> {dst_rel}")
        if not path.is_file():
            failures.append(f"missing overlay file: {src_rel}")

    print()
    if failures:
        print(f"✗ {len(failures)} anchor/file problem(s) — the build would refuse this overlay:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print(f"✓ all {len(UI_PATCHES)} anchors hit exactly once and all {len(UI_FILES)} files exist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
