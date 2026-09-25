"""P1 — session isolation on the panel/chat payload layer (regression pins).

Observed live in the owner's normal Hermes session: the goal strip showed
ANOTHER session's contract ("INCOMPLETE · P1 live proof …") because the chat
surface fetched /completion without an id and the API answered with the
newest contract in the store. Fix: every chat read/write is scoped to
host.state.focusedSessionId; the unscoped form stays the explicit panel
default (`latest`, documented) and a NAMED session never falls back to it.

Required scenarios, one test each:
1. two simultaneous sessions with different goals;
2. restarting one session (fresh objects, same runtime → same contract);
3. a fresh session with no goal → clean idle, never another session's state;
4. old test artifacts still present in the store → no leakage into other
   sessions.
"""

import importlib.util
import time
from pathlib import Path

from skaldir.completion import CompletionStore, DodItem

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)


def _load_panel():
    spec = importlib.util.spec_from_file_location("godsv_panel_api_iso", PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _panel(monkeypatch, tmp_path):
    # runtime_dir() reads HERMES_HOME per call — point it at the tmp runtime.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return _load_panel()


def _runtime(tmp_path) -> Path:
    return tmp_path / "godsv"


def _begin(tmp_path, sid: str, goal: str) -> CompletionStore:
    store = CompletionStore(_runtime(tmp_path), session_id=sid)
    store.begin_goal(sid, goal, [DodItem("c1", f"{goal} — check")])
    return store


class TestPayloadSessionIsolation:
    def test_two_simultaneous_sessions_keep_their_own_goals(self, monkeypatch, tmp_path):
        panel = _panel(monkeypatch, tmp_path)
        a = _begin(tmp_path, "sess-a", "Goal A")
        _begin(tmp_path, "sess-b", "Goal B")

        pa = panel.completion_payload(session_id="sess-a")
        pb = panel.completion_payload(session_id="sess-b")
        assert pa["goal"] == "Goal A" and pa["session"] == "sess-a"
        assert pb["goal"] == "Goal B" and pb["session"] == "sess-b"

        # A ends INCOMPLETE — B must not inherit any of it.
        a.transition("INCOMPLETE")
        pa = panel.completion_payload(session_id="sess-a")
        pb = panel.completion_payload(session_id="sess-b")
        assert pa["state"] == "INCOMPLETE" and pa["terminal"] is False
        assert pb["state"] == "WORKING" and pb["goal"] == "Goal B"

    def test_restarting_one_session_keeps_its_contract(self, monkeypatch, tmp_path):
        panel = _panel(monkeypatch, tmp_path)
        store = _begin(tmp_path, "restarted", "Survives a restart")
        store.transition("INCOMPLETE")

        # "Restart": brand-new objects over the same runtime dir.
        fresh = CompletionStore(_runtime(tmp_path), session_id="restarted")
        assert fresh.state()["state"] == "INCOMPLETE"
        assert fresh.state()["goal"] == "Survives a restart"
        payload = panel.completion_payload(session_id="restarted")
        assert payload["state"] == "INCOMPLETE" and payload["goal"] == "Survives a restart"

    def test_fresh_session_with_no_goal_is_idle(self, monkeypatch, tmp_path):
        panel = _panel(monkeypatch, tmp_path)
        old = _begin(tmp_path, "old-test", "P1 live proof: reply with ok")
        old.transition("INCOMPLETE")

        fresh = panel.completion_payload(session_id="brand-new-chat")
        # The exact leak the owner hit: a fresh session must never render
        # another session's INCOMPLETE/goal.
        assert fresh["goal"] == ""
        assert fresh["state"] == "WORKING"
        assert fresh["terminal"] is False
        assert fresh["verified_count"] == 0 and fresh["total_count"] == 0

    def test_old_test_artifacts_do_not_leak_into_other_sessions(self, monkeypatch, tmp_path):
        panel = _panel(monkeypatch, tmp_path)
        one = _begin(tmp_path, "prodrun-1", "P1 live proof: reply with ok")
        one.transition("INCOMPLETE")
        time.sleep(0.05)  # make the mtime order unambiguous
        three = _begin(tmp_path, "prodrun-3", "cold start probe")
        three.transition("INCOMPLETE")

        # An unrelated chat session stays clean even with artifacts present.
        assert panel.completion_payload(session_id="user-chat")["goal"] == ""
        # Each artifact is reachable by exactly its own session id.
        assert panel.completion_payload(session_id="prodrun-1")["goal"].startswith("P1 live proof")
        assert panel.completion_payload(session_id="prodrun-3")["goal"] == "cold start probe"
        # The unscoped form remains the documented panel default: the latest.
        assert panel.completion_payload(session_id="")["goal"] == "cold start probe"

    def test_events_payload_scopes_with_the_session(self, monkeypatch, tmp_path):
        panel = _panel(monkeypatch, tmp_path)
        from skaldir.events import EventStore

        _runtime(tmp_path).mkdir(parents=True, exist_ok=True)
        events = EventStore(_runtime(tmp_path) / "events.db")
        events.append("sess-a", 1, "completion_verdict", {"status": "INCOMPLETE"})
        events.append("sess-b", 2, "completion_verdict", {"status": "WORKING"})
        events.close()
        feed = panel.events_since_payload(
            events_db=_runtime(tmp_path) / "events.db", session_id="sess-b"
        )
        assert {row["session"] for row in feed["events"]} == {"sess-b"}
        assert feed["session_id"] == "sess-b"

    def test_test_sessions_live_in_their_own_namespace(self, monkeypatch, tmp_path):
        """prodtest-* contracts are invisible to production reads (P5)."""
        panel = _panel(monkeypatch, tmp_path)
        _begin(tmp_path, "user-chat-x", "Real goal")
        fake = _begin(tmp_path, "prodtest-9", "E2E probe")
        fake.transition("INCOMPLETE")

        # explicit access still works and is marked as a test session
        probe = panel.completion_payload(session_id="prodtest-9")
        assert probe["goal"] == "E2E probe" and probe["test"] is True
        assert panel.completion_payload(session_id="user-chat-x")["test"] is False

        # the panel default (latest) never surfaces test artifacts
        assert panel.completion_payload(session_id="")["goal"] == "Real goal"
        ids = [row["session_id"] for row in CompletionStore.sessions(_runtime(tmp_path))]
        assert "prodtest-9" not in ids and "user-chat-x" in ids
        # the contract really lives under the _tests namespace
        assert (_runtime(tmp_path) / "completion" / "_tests" / "prodtest-9.json").exists()
