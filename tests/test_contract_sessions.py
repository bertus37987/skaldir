"""Etappe 2 (P1/A4–A6): shared goal contract, session isolation, revisions.

Fail before the hardening: contracts were global and leaked across sessions,
chat goal edits were invisible to the supervisor, revisions did not invalidate
old evidence, and the event feed mixed sessions.
"""

import importlib.util
import json
from pathlib import Path

import godsv_plugin
import pytest

from skaldir.completion import CompletionStore, CriterionConflict, DodItem, plan_verification
from skaldir.events import EventStore

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)


def _load_panel():
    spec = importlib.util.spec_from_file_location("godsv_panel_api_t2", PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


class TestSessionIsolation:
    def _begin(self, tmp: Path, sid: str, goal: str):
        store = CompletionStore(tmp, session_id=sid)
        return store.begin_goal(sid, goal, [DodItem("c1", goal, verification="pytest")])

    def test_two_sessions_never_share_a_contract(self, tmp_path):
        first = self._begin(tmp_path, "sA", "Goal A")
        assert first["goal"] == "Goal A" and first["revision"] == 1
        # a second session starts empty — it never reads the first one's goal
        empty = CompletionStore(tmp_path, session_id="sB").state()
        assert empty["goal"] == "" and empty["state"] == "WORKING"
        assert [row["session_id"] for row in CompletionStore.sessions(tmp_path)] == ["sA"]
        assert CompletionStore.latest(tmp_path).session_id == "sA"
        self._begin(tmp_path, "sB", "Goal B")
        assert CompletionStore(tmp_path, "sA").state()["goal"] == "Goal A"
        assert CompletionStore(tmp_path, "sB").state()["goal"] == "Goal B"

    def test_legacy_record_is_adopted_by_its_own_session_only(self, tmp_path):
        (tmp_path / "completion_state.json").write_text(
            json.dumps(
                {"state": "WORKING", "session_id": "sX", "goal": "Legacy goal", "dod": []}
            ),
            encoding="utf-8",
        )
        assert CompletionStore(tmp_path, "sX").state()["goal"] == "Legacy goal"
        assert CompletionStore(tmp_path, "sY").state()["goal"] == ""  # never adopted

    def test_store_refuses_foreign_writes(self, tmp_path):
        store = CompletionStore(tmp_path, session_id="sA")
        with pytest.raises(ValueError):
            store.begin_goal("sB", "someone else's goal", [])


class TestRevisions:
    def test_goal_edit_bumps_revision_and_reopens_items(self, tmp_path):
        store = CompletionStore(tmp_path, session_id="s1")
        store.begin_goal("s1", "First goal", [DodItem("c1", "First goal")])
        store.transition(
            "WORKING",
            dod=[
                {
                    "id": "c1",
                    "text": "First goal",
                    "blocking": True,
                    "verification": "pytest",
                    "status": "verified",
                }
            ],
        )
        edited = store.set_goal("Second goal")
        assert edited["goal"] == "Second goal"
        assert edited["revision"] == 2
        # evidence gathered against the old wording is no longer valid
        assert edited["dod"][0]["status"] == "open"

    def test_add_criteria_derives_verification_plans(self, tmp_path):
        store = CompletionStore(tmp_path, session_id="s1")
        store.begin_goal("s1", "Ship", [DodItem("c1", "Ship", verification="pytest")])
        updated, added = store.add_criteria(
            [
                "The UI shows a dashboard",
                "Login persists after restart",
                "pytest suite is green",
                "Everything is nice",
                "The UI shows a dashboard",  # duplicate — dropped
            ]
        )
        assert added == [
            "The UI shows a dashboard",
            "Login persists after restart",
            "pytest suite is green",
            "Everything is nice",
        ]
        plans = {item["text"]: item["verification"] for item in updated["dod"]}
        assert plans["The UI shows a dashboard"] == "ui_e2e"
        assert plans["Login persists after restart"] == "persistence_restart"
        assert plans["pytest suite is green"] == "pytest"
        assert plans["Everything is nice"] == ""  # explicit missing plan, no free pass
        assert updated["revision"] == 2
        assert [item["id"] for item in updated["dod"]] == ["c1", "c2", "c3", "c4", "c5"]

    def test_terminal_goal_refuses_edits(self, tmp_path):
        store = CompletionStore(tmp_path, session_id="s1")
        store.begin_goal("s1", "Done thing", [])
        store.transition("VERIFIED_COMPLETE")
        with pytest.raises(ValueError):
            store.set_goal("a new idea")


class TestPlanVerification:
    def test_derivation(self):
        assert plan_verification("the pytest suite is green") == "pytest"
        assert plan_verification("the UI shows a dashboard") == "ui_e2e"
        assert plan_verification("session persists after restart") == "persistence_restart"
        assert plan_verification("everything is nice") == ""


class TestHookReadsTheSharedContract:
    """: the supervisor judges the SAME goal the chat wrote (P1/A4)."""

    @pytest.fixture
    def ctx(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("GODSV_MODE", "whisper")
        monkeypatch.delenv("GODSV_GUARD_FORCE", raising=False)
        godsv_plugin._SESSIONS.clear()
        fake = _FakeCtx()
        godsv_plugin.register(fake)
        yield fake
        godsv_plugin._SESSIONS.clear()

    def test_chat_goal_reaches_the_hook(self, ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ctx.hooks["on_session_start"](session_id="r1")
        session = godsv_plugin._SESSIONS["r1"]
        # the chat wrote the contract: goal + an unmet DoD item
        session._completion_store().begin_goal(
            "r1",
            "User goal",
            [DodItem("c1", "Missing mandatory UI", verification="ui_e2e")],
        )
        verdict = ctx.hooks["pre_verify"](
            session_id="r1",
            attempt=0,
            final_response="The task is complete and everything works.",
        )
        # the supervisor sees the chat goal and its unmet DoD — it cannot verify
        assert isinstance(verdict, dict) and verdict["action"] == "continue"
        record = session._completion_store().state()
        assert record["goal"] == "User goal"  # the goal survives, never blanked
        assert record["dod"][0]["status"] != "verified"

    def test_second_session_cannot_read_the_firsts_verdict(self, ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ctx.hooks["on_session_start"](session_id="r1")
        first = godsv_plugin._SESSIONS["r1"]
        first._completion_store().begin_goal("r1", "Goal one", [])
        first._completion_store().transition("VERIFIED_COMPLETE")
        ctx.hooks["on_session_start"](session_id="r2")
        second = godsv_plugin._SESSIONS["r2"]
        assert second._completion_store().state()["state"] == "WORKING"
        # a claim in the new session is judged on its own evidence (none here)
        verdict = ctx.hooks["pre_verify"](
            session_id="r2",
            attempt=0,
            final_response="Everything is done and works.",
        )
        assert isinstance(verdict, dict)  # judged, not passively accepted
        # r1's VERIFIED_COMPLETE was never inherited; the claim was rejected
        assert second._completion_store().state()["state"] == "INCOMPLETE"


class TestEventFeedScoping:
    def test_feed_only_shows_the_named_session(self, tmp_path):
        db = tmp_path / "events.db"
        store = EventStore(db)
        store.append("sA", 1, "guard_check", {"claims": 1, "findings": 2})
        store.append("sB", 2, "guard_check", {"claims": 0, "findings": 0})
        store.append("sA", 3, "completion_verdict", {"status": "VERIFIED_COMPLETE"})
        store.close()
        panel = _load_panel()
        feed = panel.events_since_payload(events_db=db, session_id="sA")
        assert {event["session"] for event in feed["events"]} == {"sA"}
        assert feed["session_id"] == "sA"
        assert feed["events"][0]["seq"] == 3


class TestPlanVerificationToolWiring:
    """A criterion that NAMES a registered testing tool must be wired to it.

    Live-found 13.09.: a criterion whose text said `check_readme` but whose
    stored verification was `run` could never be certified — gate_dod matches
    the stored value alone (agent-documented pitfall, SKILL.md).
    """

    def test_registered_tool_name_wires_the_criterion(self):
        assert (
            plan_verification(
                "Das Tool check_readme laeuft gegen das Repo und besteht",
                tool_names=["check_readme"],
            )
            == "check_readme"
        )

    def test_without_registry_explicit_missing_plan(self):
        # no silent free pass: unwired stays an explicit missing plan
        assert plan_verification("Das Tool check_readme laeuft", tool_names=[]) == ""
        assert plan_verification("Das Tool check_readme laeuft") == ""

    def test_word_boundaries_no_substring_false_positive(self):
        assert plan_verification("threaded reader", tool_names=["read"]) == ""

class TestCriterionEditing:
    """Session-UI Stufe 3 (Plan §5/§6): revisionierter Einzelkriterien-Editor.

    update/remove share the store path with add — revision fence, idempotent
    request ids, and no door for a UI to fabricate verification status.
    """

    def _store(self, tmp_path):
        store = CompletionStore(tmp_path, session_id="s1")
        store.begin_goal(
            "s1", "Ship", [DodItem("c1", "pytest suite is green", verification="pytest")]
        )
        store.add_criteria(["The UI shows a dashboard"])
        return store

    def test_update_text_keeps_status_and_rebumps_plan(self, tmp_path):
        store = self._store(tmp_path)
        rev = store.state()["revision"]
        updated = store.update_criterion(
            "c2", text="The UI shows the shared dashboard", expected_revision=rev
        )
        item = next(i for i in updated["dod"] if i["id"] == "c2")
        assert item["text"] == "The UI shows the shared dashboard"
        assert item["status"] == "open"  # text edit is not evidence
        assert item["verification"] == "ui_e2e"  # plan re-derived from the new text
        assert updated["revision"] == rev + 1

    def test_update_requires_content(self, tmp_path):
        store = self._store(tmp_path)
        with pytest.raises(ValueError):
            store.update_criterion("c2")

    def test_stale_expected_revision_conflicts(self, tmp_path):
        store = self._store(tmp_path)
        with pytest.raises(CriterionConflict) as exc:
            store.update_criterion("c2", text="x", expected_revision=1)
        assert exc.value.current_revision == store.state()["revision"]

    def test_remove_drops_item_evidence_stays(self, tmp_path):
        store = self._store(tmp_path)
        updated = store.remove_criterion("c2")
        assert [i["id"] for i in updated["dod"]] == ["c1"]
        assert updated["revision"] == 3

    def test_unknown_id_rejected(self, tmp_path):
        store = self._store(tmp_path)
        with pytest.raises(ValueError):
            store.update_criterion("c99", text="x")
        with pytest.raises(ValueError):
            store.remove_criterion("c99")

    def test_terminal_contract_freezes_criteria(self, tmp_path):
        store = self._store(tmp_path)
        store.transition("VERIFIED_COMPLETE")
        with pytest.raises(ValueError):
            store.remove_criterion("c2")

    def test_request_id_is_idempotent(self, tmp_path):
        store = self._store(tmp_path)
        first = store.update_criterion("c2", text="once", request_id="req-1")
        assert first.get("replayed") is not True
        rev_after = first["revision"]
        second = store.update_criterion("c2", text="twice", request_id="req-1")
        assert second.get("replayed") is True
        assert second["revision"] == rev_after
        item = next(i for i in second["dod"] if i["id"] == "c2")
        assert item["text"] == "once"  # the retry did not re-apply

    def test_panel_op_ignores_status_field(self, tmp_path):
        panel = _load_panel()
        store = self._store(tmp_path)
        out = panel.criteria_op_payload(
            store,
            {"op": "update", "id": "c2", "text": "renamed", "status": "verified"},
        )
        assert out == {"op": "update", "id": "c2", "replayed": False}
        item = next(i for i in store.state()["dod"] if i["id"] == "c2")
        assert item["status"] == "open"

    def test_panel_add_path_unchanged(self, tmp_path):
        panel = _load_panel()
        store = self._store(tmp_path)
        out = panel.criteria_op_payload(store, {"criteria": ["Login persists after restart"]})
        assert out["op"] == "add"
        assert out["added"] == ["Login persists after restart"]
