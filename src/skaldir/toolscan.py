"""Static security scan for candidate tool code (CORE-PLAN §4 — the Scan gate).

Deterministic, stdlib-only pattern audit of tool sources *before* they may be
gated, approved or registered. High findings block; medium/low are reported
for review. The scanner covers dynamic code execution, shell/process
spawning, raw sockets/network clients, ctypes/pickle and embedded secrets
(reusing ``secretscan``), plus an informational file-write check.

This is the cheap first gate — it cannot prove a tool safe, but it reliably
stops the obvious classes of "generated tool" abuse (T5 in SECURITY.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .secretscan import scan as scan_secrets

SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"

_PATTERNS: list[tuple[str, str, re.Pattern[str], str]] = [
    ("eval", SEV_HIGH, re.compile(r"\beval\s*\("), "dynamic code execution (eval)"),
    ("exec", SEV_HIGH, re.compile(r"\bexec\s*\("), "dynamic code execution (exec)"),
    ("dunder_import", SEV_HIGH, re.compile(r"__import__\s*\("), "dynamischer Import"),
    ("os_system", SEV_HIGH, re.compile(r"\bos\.(system|popen)\s*\("), "shell execution"),
    (
        "subprocess_shell",
        SEV_HIGH,
        re.compile(r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True", re.S),
        "Shell=True in subprocess",
    ),
    (
        "ctypes",
        SEV_HIGH,
        re.compile(r"\b(import\s+ctypes|from\s+ctypes\b)"),
        "ctypes (native Speicherzugriffe)",
    ),
    ("socket", SEV_MEDIUM, re.compile(r"\b(import\s+socket|from\s+socket\b)"), "Rohe Sockets"),
    (
        "network",
        SEV_MEDIUM,
        re.compile(r"\b(import\s+(requests|httpx|urllib|aiohttp)|from\s+(requests|httpx|urllib|aiohttp)\b)"),
        "network client (check for data exfiltration)",
    ),
    (
        "pickle",
        SEV_MEDIUM,
        re.compile(r"\b(pickle\.loads?|import\s+pickle)\b"),
        "pickle-Deserialisierung",
    ),
    (
        "file_write",
        SEV_LOW,
        re.compile(r"open\s*\([^)]*[\"']w"),
        "filesystem write access (check scope)",
    ),
]


@dataclass
class ToolFinding:
    rule: str
    severity: str
    message: str
    line: int


def scan_tool_source(text: str) -> list[ToolFinding]:
    findings: list[ToolFinding] = []
    for rule, severity, pattern, message in _PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(ToolFinding(rule=rule, severity=severity, message=message, line=line))
    for secret in scan_secrets(text):
        line = text.count("\n", 0, secret.start) + 1
        findings.append(
            ToolFinding(
                rule="secret",
                severity=SEV_HIGH,
                message=f"secret in code ({secret.kind})",
                line=line,
            )
        )
    return findings


def scan_tool_file(path: str | Path) -> list[ToolFinding]:
    return scan_tool_source(Path(path).read_text(encoding="utf-8"))


def has_blocking(findings: list[ToolFinding]) -> bool:
    return any(finding.severity == SEV_HIGH for finding in findings)
