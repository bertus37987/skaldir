"""Cross-language contract: the panel's chat-feed texts and the runtime's CHAT_KINDS agree.

The gap this pins (found in the UI/UX inventory, `docs/audit/UI-FEATURE-MAP.md` §3):
a runtime kind that is NOT in ``CHAT_KINDS`` can never reach the chat, so a feed text
written for it is dead UI code — and a kind that DOES reach the chat without a text
would print the raw identifier to the user. Both directions are asserted here, so a new
event kind cannot silently miss its UI projection again.

Contract cites: §9 (Computer Use shows success only after verification), §10 (Jev shows
only the resulting action), §12 (after an approval the flow continues and shows the
outcome), §16 (a silently ineffective intervention must be visible; the chat stays quiet
otherwise).
"""

from __future__ import annotations

import re
from pathlib import Path

from skaldir.events import CHAT_KINDS

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "integrations" / "hermes" / "godsv_plugin" / "desktop" / "plugin.js"

#: outcome kinds that exist *because* the chat must be able to show a result — not only
#: the failures of a feature that works.
REQUIRED_OUTCOMES = (
    "cu_verified",
    "pause_stale",
    "spend_approved",
    "spend_completed",
    "system_one_decision",
    "system_one_blocked",
)

#: advisory signals stay in the panel — the chat feed stays quiet (§16).
ADVISORY_ONLY = ("pattern_tool_loop", "pattern_command_loop", "pattern_test_without_change")


def _source() -> str:
    return PLUGIN.read_text(encoding="utf-8")


def _feed_text_keys() -> set[str]:
    source = _source()
    start = source.index("const GV_FEED_TEXTS = {")
    block = source[start : source.index("\n}\n", start)]
    return set(re.findall(r"^  ([a-z][a-z0-9_]*):", block, re.M))


def test_every_feed_text_maps_to_a_kind_that_can_reach_the_chat():
    orphan = sorted(_feed_text_keys() - set(CHAT_KINDS))
    assert orphan == [], f"feed texts for kinds that never reach the chat: {orphan}"


def test_the_outcome_kinds_reach_the_chat_and_carry_a_text():
    keys = _feed_text_keys()
    for kind in REQUIRED_OUTCOMES:
        assert kind in CHAT_KINDS, f"{kind} cannot reach the chat"
        assert kind in keys, f"{kind} reaches the chat without a human-readable text"


def test_advisory_patterns_stay_out_of_the_chat_and_carry_no_text():
    keys = _feed_text_keys()
    for kind in ADVISORY_ONLY:
        assert kind not in CHAT_KINDS, f"{kind} must stay in the panel (§16)"
        assert kind not in keys


def test_the_feed_never_prints_a_raw_identifier_as_its_label():
    """An unmapped kind must be humanized, not shown as `tool_build_gates` (§16)."""
    source = _source()
    assert "GV_FEED_TEXTS[kind] || (() => gvHumanizeKind(kind))" in source
    assert "function gvHumanizeKind(kind)" in source
