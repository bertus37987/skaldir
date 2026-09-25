"""Secret scanner — regex-based V0 gate for the leak playbook.

Secrets must never enter the event store, logs, or prompts. ``scan()`` finds
candidates; ``redact()`` removes them while keeping a ``[REDACTED:kind]``
marker. Patterns are conservative; the generic rule requires key-like context
to limit false positives (see docs/SECURITY.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.")),
    (
        "generic_key",
        re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{20,})"),
    ),
]


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int
    preview: str


def _preview(value: str) -> str:
    if len(value) <= 10:
        return "***"
    return f"{value[:4]}…{value[-2:]}"


def scan(text: str) -> list[Finding]:
    """Return secret candidates found in ``text`` (sorted by position).

    Overlapping matches are deduplicated: one secret matched by several
    patterns (``api_key = "sk-…"`` hits ``generic_key`` *and* ``openai``)
    is ONE finding. Before, the same secret produced two findings — the
    gate reported two BLOCKERs for one leak (noise, never a false block).
    The first (leftmost, then widest) match wins, so the generic rule's
    surrounding context does not shadow the precise pattern.
    """
    findings: list[Finding] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            value = groups[1] if (kind == "generic_key" and len(groups) >= 2) else match.group(0)
            findings.append(
                Finding(kind=kind, start=match.start(), end=match.end(), preview=_preview(value))
            )
    findings.sort(key=lambda f: (f.start, f.end))
    unique: list[Finding] = []
    for finding in findings:
        if unique and finding.start < unique[-1].end:
            # overlap with the previous match: same secret, one finding.
            continue
        unique.append(finding)
    return unique


def redact(text: str) -> tuple[str, list[Finding]]:
    """Replace found secrets with ``[REDACTED:kind]`` markers."""
    findings = scan(text)
    if not findings:
        return text, []
    parts: list[str] = []
    cursor = 0
    for finding in findings:
        if finding.start < cursor:  # overlapping match with an earlier finding
            continue
        parts.append(text[cursor:finding.start])
        parts.append(f"[REDACTED:{finding.kind}]")
        cursor = finding.end
    parts.append(text[cursor:])
    return "".join(parts), findings
