"""Wave 9 — no unread chat event may be dropped without saying so.

Audit AF-14 (measured): 60 chat events in the offline window, the chat's default ``limit`` is 40,
so the 20 oldest were never delivered and nothing in the payload said a window had been cut. The
feed now reports the cut (``truncated`` / ``remaining`` / ``oldest_seq``) and offers the cursor
(``next_before`` + ``before``) to walk the rest instead of losing it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from skaldir.events import EventStore

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)

CHAT = "pause_delivered"      # in CHAT_KINDS
OTHER_CHAT = "pause_requested"
QUIET = "tool_call"           # not in CHAT_KINDS, stays panel-only


def _load_panel(name: str = "godsv_panel_feed_cursor"):
    spec = importlib.util.spec_from_file_location(name, PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return _load_panel()


def _seed(
    path: Path, *, session: str = "s1", chat: int = 60, quiet: int = 4, foreign: int = 3
) -> None:
    store = EventStore(path)
    for i in range(quiet):
        store.append(session, i, QUIET, {"i": i})
    for i in range(chat):
        store.append(session, 100 + i, CHAT, {"i": i})
    for i in range(foreign):
        store.append("other-session", 200 + i, OTHER_CHAT, {"i": i})
    store.close()


def test_a_window_cut_by_limit_is_reported(panel, tmp_path):
    db = tmp_path / "events.db"
    _seed(db)
    payload = panel.events_since_payload(after_seq=0, limit=40, events_db=db, session_id="s1")
    assert len(payload["events"]) == 40
    assert payload["truncated"] is True, payload
    assert payload["remaining"] == 20, payload
    assert payload["oldest_seq"] == payload["events"][-1]["seq"]
    assert payload["next_before"] == payload["oldest_seq"]


def test_the_cut_window_is_walkable_and_loses_nothing(panel, tmp_path):
    db = tmp_path / "events.db"
    _seed(db)
    first = panel.events_since_payload(after_seq=0, limit=40, events_db=db, session_id="s1")
    second = panel.events_since_payload(
        after_seq=0, limit=40, events_db=db, session_id="s1", before_seq=first["next_before"]
    )
    assert second["truncated"] is False, second
    assert second["remaining"] == 0
    seen = {item["seq"] for item in first["events"]} | {item["seq"] for item in second["events"]}
    assert len(first["events"]) + len(second["events"]) == 60          # every chat event, once
    assert len(seen) == 60                                             # no duplicates across pages


def test_a_complete_window_is_not_truncated(panel, tmp_path):
    db = tmp_path / "events.db"
    _seed(db, chat=5)
    payload = panel.events_since_payload(after_seq=0, limit=40, events_db=db, session_id="s1")
    assert payload["truncated"] is False
    assert payload["remaining"] == 0 and payload["next_before"] == 0


def test_non_chat_events_do_not_inflate_the_backlog(panel, tmp_path):
    """`remaining` counts the feed's own kinds — a panel-only event is not an unread chat event."""
    db = tmp_path / "events.db"
    _seed(db, chat=41, quiet=25)
    payload = panel.events_since_payload(after_seq=0, limit=40, events_db=db, session_id="s1")
    assert payload["truncated"] is True
    assert payload["remaining"] == 1, payload


def test_another_sessions_events_are_not_counted(panel, tmp_path):
    """The feed is per session: a foreign backlog must not show up as this task's unread events."""
    db = tmp_path / "events.db"
    _seed(db, chat=10, foreign=30)
    payload = panel.events_since_payload(after_seq=0, limit=40, events_db=db, session_id="s1")
    assert payload["truncated"] is False
    assert len(payload["events"]) == 10
    assert {item["session"] for item in payload["events"]} == {"s1"}


def test_after_seq_scopes_the_backlog(panel, tmp_path):
    """Only events newer than the last seen cursor count as unread."""
    db = tmp_path / "events.db"
    _seed(db, chat=60)
    first = panel.events_since_payload(after_seq=0, limit=40, events_db=db, session_id="s1")
    newest = first["latest_seq"]
    settled = panel.events_since_payload(
        after_seq=newest, limit=40, events_db=db, session_id="s1"
    )
    assert settled["events"] == [] and settled["truncated"] is False
