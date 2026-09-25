"""Spotlighting wrapper for untrusted external content (injection defense).

Any external text (web, docs, tool output) that reaches a model's context is
wrapped in explicit UNTRUSTED markers and stripped of invisible/control
characters. Policy: external content is data, never instructions
(cf. arXiv 2403.14720, docs/SECURITY.md).
"""

from __future__ import annotations

import unicodedata

MAX_LEN = 20_000
TRUNCATION_MARKER = "\n[…truncated…]"


def sanitize(text: str, max_len: int = MAX_LEN) -> str:
    """Remove zero-width/format/control characters (keep newline and tab), truncate."""
    kept: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf":  # format characters (zero-width, bidi overrides, …)
            continue
        if category == "Cc" and char not in "\n\t":  # control characters
            continue
        kept.append(char)
    clean = "".join(kept)
    if len(clean) > max_len:
        clean = clean[:max_len] + TRUNCATION_MARKER
    return clean


def spotlight(text: str, source: str, retrieved_at: str | None = None) -> str:
    """Wrap ``text`` in explicit untrusted markers for safe inclusion in prompts."""
    head = f"<<<UNTRUSTED source={source}"
    if retrieved_at:
        head += f" retrieved={retrieved_at}"
    head += ">>>"
    return f"{head}\n{sanitize(text)}\n<<<END UNTRUSTED>>>"
