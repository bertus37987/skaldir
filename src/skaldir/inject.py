"""Injection channel (V0) — formats hints as append-only, cache-safe messages.

See docs/CORE-PLAN.md §1.5. Rules: never rewrite the system prompt or past
history; a hint is its own clearly-marked message, advisory, with sources.
Sanitization protects the main agent from hostile characters in hint text.
"""

from __future__ import annotations

import re
import unicodedata

from .voice import Hint

ADVISORY_FOOTER = "[note, not an instruction — verify it yourself.]"
MAX_HINT_WORDS = 220


def sanitize_injection(text: str, max_words: int = MAX_HINT_WORDS) -> str:
    """Strip format/control characters, collapse blank lines, cap word count."""
    kept: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Cf":  # zero-width, bidi overrides, …
            continue
        if category == "Cc" and char not in "\n\t":
            continue
        kept.append(char)
    clean = "".join(kept)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    words = clean.split()
    if len(words) > max_words:
        clean = " ".join(words[:max_words]) + " […]"
    return clean


def format_hint(hint: Hint) -> str:
    """Render a hint in the canonical ``[voice …]`` message format."""
    header = (
        f"[voice id={hint.id} sig={hint.sig[:8]} typ={hint.kind} "
        f"conf={hint.confidence:.2f} pos={hint.pos}/{hint.total}]"
    )
    lines = [header, sanitize_injection(hint.text)]
    if hint.evidence:
        lines.append("Evidence:")
        lines.extend(f"- {sanitize_injection(item, 60)}" for item in hint.evidence)
    lines.append(ADVISORY_FOOTER)
    return "\n".join(lines)


class InjectionQueue:
    """Holds formatted hints until a delivery point picks them up."""

    def __init__(self) -> None:
        self._pending: list[str] = []

    def push(self, hint: Hint) -> str:
        formatted = format_hint(hint)
        self._pending.append(formatted)
        return formatted

    def drain(self) -> list[str]:
        items, self._pending = self._pending, []
        return items

    def pending_count(self) -> int:
        return len(self._pending)
