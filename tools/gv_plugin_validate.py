#!/usr/bin/env python
"""Run the real Hermes plugin admission check against the installed godsv plugin.

Wave 1 proof: the manifest must declare what register() registers, and the capability
probe must succeed in the engine interpreter (no PYTHONPATH help).
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE = Path.home() / ".hermes" / "hermes-agent"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "lib"))

target = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / ".hermes" / "plugins" / "godsv")

from hermes_cli.plugin_validate import validate_plugin_dir  # noqa: E402

report = validate_plugin_dir(target)
print("plugin dir :", target)
print("PYTHONPATH :", "<unset>" if "PYTHONPATH" not in __import__("os").environ else "SET")
print("ok         :", report.ok)
for name, ok, detail in report.checks:
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}: {str(detail)[:160]}")
for warn in report.warnings:
    print("  [warn]", str(warn)[:170])
print("exit_code  :", report.exit_code)
