#!/usr/bin/env python3
"""Declared check ``build`` — package the library and use it from the artifact.

Nothing is written into the workspace: the artifact is built in a throwaway temp dir
(inside the sandbox's private ``/tmp``), imported from there in a fresh subprocess and
asked for a slug. Prints ``PASS: …`` on success, ``FAIL: <reason>`` + exit 1 otherwise.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "textkit"
PROBE = "import textkit; print(textkit.slugify('Hello, World!'))"


def main() -> int:
    sources = sorted(PACKAGE.rglob("*.py"))
    if not sources:
        print(f"FAIL: no python sources under {PACKAGE}")
        return 1
    for path in sources:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            print(f"FAIL: {path.name} does not compile: {exc}")
            return 1

    with tempfile.TemporaryDirectory() as tmp:
        wheel = Path(tmp) / "textkit.zip"
        with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sources:
                archive.write(path, str(path.relative_to(ROOT)))
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-c", PROBE],
            cwd=tmp,
            env={"PYTHONPATH": str(wheel), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=60,
        )
        got = (proc.stdout or "").strip()
        if proc.returncode != 0 or got != "hello-world":
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            print(
                "FAIL: the packaged artifact does not expose slugify correctly: "
                f"printed {got!r} (exit {proc.returncode}) {detail[-1] if detail else ''}"
            )
            return 1
        print(
            f"PASS: textkit.zip ({wheel.stat().st_size} bytes, {len(sources)} modules) "
            "imports from the artifact and slugifies correctly"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
