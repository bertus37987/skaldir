"""The browser backend wired to the real `agent-browser` verbs (§5 vertical prototype).

Most of these are contract tests that need no network. The last group is deliberately
marked as a *real* invocation: it runs the pinned binary and is skipped when the tool is
absent, because a stub test cannot prove a transport works.
"""

from __future__ import annotations

import json

import pytest

from skaldir.browser_layer import AgentBrowserBackend, ErrorClass, _unwrap_json


def test_unwrapping_a_json_envelope_yields_the_page_content():
    envelope = {"success": True, "data": {"content": "# Title\n\nBody", "status": 200,
                                          "source": "html-fallback", "contentType": "text/html"}}
    env, content = _unwrap_json(json.dumps(envelope))
    assert content == "# Title\n\nBody"
    assert env["data"]["status"] == 200


def test_plain_text_output_is_not_an_error():
    env, content = _unwrap_json("just some prose")
    assert env == {}
    assert content == "just some prose"


def test_an_empty_answer_is_empty_not_a_crash():
    assert _unwrap_json("") == ({}, "")


def test_an_unsupported_action_is_refused_by_name():
    """§4: "Nur tatsächlich unterstützte Operationen anbieten"."""
    b = AgentBrowserBackend(root="/nonexistent")
    result = b.act({"kind": "teleport"})
    assert result["ok"] is False
    assert result["error_class"] == ErrorClass.UNSUPPORTED.value
    assert "teleport is not supported" in result["detail"]
    assert "known kinds:" in result["detail"]


def test_close_all_is_always_refused_because_of_foreign_tabs():
    """BR-13: Nutzertabs bleiben erhalten."""
    b = AgentBrowserBackend(root="/nonexistent")
    for attempt in ("--all", "*"):
        result = b.close_tab(tab=attempt)
        assert result["ok"] is False
        assert "BR-13" in result["detail"] or "user's own tabs" in result["detail"]


def test_close_tab_without_a_name_is_refused_not_a_wildcard():
    b = AgentBrowserBackend(root="/nonexistent")
    result = b.close_tab(tab="")
    assert result["ok"] is False
    assert "no 'close everything' here" in result["detail"]


def test_the_action_vocabulary_is_the_tools_actual_verbs():
    """The map must not invent verbs the tool does not have."""
    b = AgentBrowserBackend(root="/nonexistent")
    verbs = set(b.ACTION_VERBS.values())
    assert {"open", "click", "type", "fill", "press", "hover", "select", "drag",
            "upload", "download", "scroll", "wait"} <= verbs
    assert "--all" not in verbs
    assert "close" not in verbs, "closing is not an 'action', it is a separate, guarded call"


# --------------------------------------------------------------------------- #
# real invocation — the vertical prototype (§5)
# --------------------------------------------------------------------------- #

def _real() -> AgentBrowserBackend:
    return AgentBrowserBackend()


def test_the_real_binary_reports_a_version():
    cap = _real().probe()
    if not cap.available:
        pytest.skip(f"agent-browser is not installed here: {cap.reason}")
    assert cap.version, "an installed tool that cannot name its version is unusable (§9.2)"
    assert "agent-browser" in cap.version


def test_a_real_read_returns_the_page_content_not_the_json_envelope():
    """BR-07: strukturierte selektive Seiteninhalte — measured against the real tool."""
    b = _real()
    if not b.probe().available:
        pytest.skip("agent-browser is not installed here")
    result = b.read(url="https://example.com", session="pytest-read")
    assert result["ok"] is True, result.get("detail")
    assert "Example Domain" in result["content"], f"unexpected content: {result['content'][:200]}"
    assert not result["content"].lstrip().startswith("{"), "the JSON envelope was not unwrapped"
    assert result["url"].startswith("https://example.com")
    assert result["duration_ms"] is not None
    assert result["source"].startswith("agent-browser read")


def test_a_real_launch_does_not_close_anything_and_reports_itself():
    b = _real()
    if not b.probe().available:
        pytest.skip("agent-browser is not installed here")
    result = b.launch(session="pytest-launch", url="https://example.com")
    assert result["backend"] == "agent-browser"
    assert result["session_id"] == "pytest-launch"
    # and the guard still refuses the global close afterwards
    assert b.close_tab(tab="--all", session="pytest-launch")["ok"] is False
