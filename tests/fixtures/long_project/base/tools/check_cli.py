#!/usr/bin/env python3
"""Declared check ``run`` — the project's own CLI, driven for real.

The check reads the CLI's stdout and compares it with the documented contract; it
prints an explicit ``PASS`` line on success and ``FAIL: <reason>`` + exit 1 otherwise
(the result contract every declared check obeys).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CASES = (
    ("Hello, World!", "hello-world"),
    ("  Café 42  ", "cafe-42"),
)


def main() -> int:
    for text, expected in CASES:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [sys.executable, str(ROOT / "cli.py"), "slugify", text],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        got = (proc.stdout or "").strip()
        if proc.returncode != 0 or got != expected:
            print(
                f"FAIL: cli.py slugify {text!r} printed {got!r} "
                f"(exit {proc.returncode}), expected {expected!r}"
            )
            return 1
    print(f"PASS: cli.py printed the documented slug for {len(CASES)} inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
