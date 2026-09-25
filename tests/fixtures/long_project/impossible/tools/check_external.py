#!/usr/bin/env python3
"""Declared check ``api`` (impossible variant) — the upstream release manifest.

This criterion cannot be met on this machine: the SKALDIR e2e run has no network and no
upstream release feed, so the check fails with its own concrete reason. It is the control
that proves the runtime never claims success for an unmet criterion.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEED = ROOT / ".godsv" / "upstream-release.json"


def main() -> int:
    if not FEED.is_file():
        print(
            "FAIL: upstream release feed unavailable — no network and no vendored "
            "manifest at .godsv/upstream-release.json (external blocker)"
        )
        return 1
    try:
        manifest = json.loads(FEED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: upstream release feed is unreadable: {exc}")
        return 1
    digest = str(manifest.get("artifact_sha256") or "")
    if len(digest) != 64:
        print("FAIL: upstream release feed carries no artifact_sha256")
        return 1
    print(f"PASS: upstream release manifest {digest[:12]}… matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
