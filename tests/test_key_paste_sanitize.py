"""A pasted key must reach the endpoint as typed — terminal artifacts stripped.

A modern terminal wraps a paste in bracketed-paste markers (``ESC[200~`` … ``ESC[201~``).
Read raw by ``getpass``, those escape bytes stick to the credential, so the value sent
is ``ESC[200~<key>ESC[201~`` — never the user's key — and every endpoint answers
``401 Invalid API Key``. Copy-paste is how every provider's key is entered, so this
broke *all* of them at once.
"""
from __future__ import annotations

import pytest


def test_bracketed_paste_markers_are_stripped() -> None:
    from skaldir.setup_flow import refuse_key_value

    pasted = "\x1b[200~sk-test-key-123\x1b[201~"
    assert refuse_key_value(pasted) == "sk-test-key-123"


def test_ansi_colour_codes_are_stripped() -> None:
    from skaldir.setup_flow import refuse_key_value

    assert refuse_key_value("\x1b[31msk-test\x1b[0m") == "sk-test"


def test_non_ascii_visual_separators_are_stripped() -> None:
    from skaldir.setup_flow import refuse_key_value

    # a `·` (or any non-ASCII) copied from a key display as a visual separator must
    # not survive — httpx normalises headers as ascii and would refuse to send it.
    assert refuse_key_value("sk-abc123·def456") == "sk-abc123def456"


def test_plain_key_is_untouched() -> None:
    from skaldir.setup_flow import refuse_key_value

    assert refuse_key_value("  sk-plain-key_42  ") == "sk-plain-key_42"


def test_a_key_that_is_only_terminal_noise_is_still_refused() -> None:
    from skaldir.setup_flow import SetupRefused, refuse_key_value

    with pytest.raises(SetupRefused):
        refuse_key_value("\x1b[200~\x1b[201~")
