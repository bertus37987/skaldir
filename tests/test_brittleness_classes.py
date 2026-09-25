"""P1-6 — complete brittleness classification (infrastructure ≠ code bugs).

Audit gap: only auth / rate_limit / timeout / invalid_args / unsupported
were classified. The new classes tell the voice (and the main agent) that
an infrastructure problem must not be debugged as a code bug.
"""

import pytest

from skaldir.monitor import ERROR_RECOVERY, classify_tool_error


class TestBrittlenessClasses:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Permission denied: /etc/foo", "permissions"),
            ("EACCES: permission denied, open '/x'", "permissions"),
            ("HTTP 503 Service Unavailable", "provider_outage"),
            ("upstream connect error or disconnect/reset", "provider_outage"),
            ("response did not match the expected schema", "schema_mismatch"),
            ("schema mismatch: field 'id' missing", "schema_mismatch"),
            ("MCP server not running", "mcp_unavailable"),
            ("server disconnected", "mcp_unavailable"),
            ("Target closed", "ui_failure"),
            ("net::ERR_CONNECTION_REFUSED", "ui_failure"),
            ("element not visible", "ui_failure"),
            ("API endpoint deprecated", "api_changed"),
            ("410 Gone", "api_changed"),
        ],
    )
    def test_new_classes_are_classified(self, text, expected):
        assert classify_tool_error(text) == expected

    def test_every_class_has_a_recovery_hint(self):
        for class_id in (
            "permissions",
            "provider_outage",
            "schema_mismatch",
            "mcp_unavailable",
            "ui_failure",
            "api_changed",
        ):
            assert ERROR_RECOVERY.get(class_id), f"missing hint for {class_id}"

    def test_ordinary_bug_is_not_misclassified(self):
        assert classify_tool_error("AssertionError: 1 != 2") is None
        assert classify_tool_error("pytest failed: expected 3 passed, got 2") is None

    def test_existing_classes_still_win_first(self):
        assert classify_tool_error("429 Too Many Requests") == "rate_limit"
        assert classify_tool_error("401 Unauthorized") == "auth"
