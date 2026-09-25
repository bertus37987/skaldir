"""Desktop-Panel-Backend (read-only): Aggregation über Live-DBs, defensiv gegen fehlende Quellen."""

import importlib.util
import json
from pathlib import Path

import pytest

from skaldir.completion import DodItem
from skaldir.events import EventStore
from skaldir.treasury import TIER_WHITELIST, SpendIntent, SpendLedger, SpendRules, TreasuryState

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)


def _load_panel():
    spec = importlib.util.spec_from_file_location("godsv_panel_api", PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def panel():
    return _load_panel()


def _seed_events(path: Path) -> None:
    store = EventStore(path)
    store.append("s1", 1, "tool_call", {"name": "terminal"})
    store.append("s1", 2, "pause_requested", {"pause": "p1", "kind": "voice"})
    store.append("s1", 3, "pause_delivered", {"pause": "p1"})
    store.append("s1", 4, "guard_check", {"claims": 1, "findings": 0})
    store.append("s1", 5, "claim", {"category": "done"})
    store.close()


class TestPanelSummary:
    def test_empty_runtime_is_graceful(self, panel, tmp_path):
        payload = panel.summary_payload(
            events_db=tmp_path / "events.db",
            pauses_db=tmp_path / "pauses.db",
            treasury_db=tmp_path / "treasury.db",
        )
        assert payload["events_present"] is False
        assert payload["kinds"] == {} and payload["recent"] == []
        assert payload["pauses"] == [] and payload["allowance"] is None
        assert payload["mode"] == "testgeld"

    def test_summary_reads_live_dbs(self, panel, tmp_path):
        _seed_events(tmp_path / "events.db")
        state = TreasuryState(tmp_path / "treasury.db")
        rules = SpendRules(tier=TIER_WHITELIST, per_tx_cap=5.0, whitelist=["m"])
        ledger = SpendLedger(state, rules)
        intent = SpendIntent(amount=4.0, currency="EUR", merchant="m", purpose="x")
        ledger.request(amount=4.0, merchant="m", purpose="x", intent=intent)
        state.close()

        payload = panel.summary_payload(
            events_db=tmp_path / "events.db",
            pauses_db=tmp_path / "pauses.db",
            treasury_db=tmp_path / "treasury.db",
        )
        assert payload["kinds"]["pause_requested"] == 1
        assert payload["kinds"]["pause_delivered"] == 1
        assert payload["kinds"]["guard_check"] == 1
        assert payload["recent"][0]["kind"] == "claim"
        assert payload["allowance"]["mode"] == "testgeld"
        assert payload["allowance"]["counts"]["approved"] == 1
        assert payload["allowance"]["by_status"]["approved"] == pytest.approx(4.0)

    def test_no_secret_material_in_payload(self, panel, tmp_path):
        _seed_events(tmp_path / "events.db")
        payload = panel.summary_payload(
            events_db=tmp_path / "events.db",
            pauses_db=tmp_path / "pauses.db",
            treasury_db=tmp_path / "treasury.db",
        )
        dumped = json.dumps(payload, ensure_ascii=False)
        assert "ciphertext" not in dumped and "secret://" not in dumped

    def test_real_runtime_dir_if_present(self, panel):
        payload = panel.summary_payload(limit=10)
        assert set(payload) >= {"runtime", "kinds", "recent", "pauses", "allowance"}


class TestPanelRouter:
    def test_router_routes_declared(self, panel):
        if panel.router is None:
            pytest.skip("fastapi not installed in this env (gateway-only)")
        paths = {route.path for route in panel.router.routes}
        assert {"/health", "/summary", "/memory", "/skills", "/treasury"} <= paths


def _seed_memory(path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE incidents (id TEXT, signature TEXT, title TEXT, kind TEXT, status TEXT,"
        " confidence REAL, occurrences INTEGER, first_seen REAL, last_seen REAL)"
    )
    conn.execute(
        "INSERT INTO incidents VALUES"
        " ('i1', 'sig-1', 'Wacom stall', 'io', 'active', 0.8, 3, 1.0, 2.0)"
    )
    conn.execute(
        "CREATE TABLE notes (id TEXT, topic TEXT, claim TEXT, source_type TEXT, source_url TEXT,"
        " trust TEXT, status TEXT, created REAL)"
    )
    conn.execute(
        "INSERT INTO notes VALUES ('n1', 'Desktop-Envs', 'userData ist pro Instanz', 'docs',"
        " 'https://example.invalid', 'high', 'active', 3.0)"
    )
    conn.execute(
        "CREATE TABLE skill_candidates (id TEXT, name TEXT, title TEXT, status TEXT,"
        " occurrences INTEGER, sessions INTEGER, applied INTEGER, succeeded INTEGER, updated REAL)"
    )
    conn.execute(
        "INSERT INTO skill_candidates VALUES"
        " ('s1', 'godsv-verify', 'Verify', 'promoted', 4, 2, 3, 3, 4.0)"
    )
    conn.commit()
    conn.close()


def _seed_treasury(path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE grants (id TEXT PRIMARY KEY, handle TEXT, purpose TEXT, ttl INTEGER,"
        " status TEXT, created REAL, decided REAL, by TEXT)"
    )
    conn.execute(
        "INSERT INTO grants VALUES"
        " ('g1', 'secret://demo', 'purpose', 900, 'active', 1.0, 2.0, 'owner')"
    )
    conn.execute(
        "CREATE TABLE spends (id TEXT PRIMARY KEY, amount REAL, currency TEXT, merchant TEXT,"
        " purpose TEXT, status TEXT, reason TEXT, approved_by TEXT, intent_id TEXT,"
        " intent_expires_at REAL, created REAL, completed_at REAL)"
    )
    conn.execute(
        "INSERT INTO spends VALUES ('sp1', 12.5, 'EUR', 'Testshop', 'Demo', 'approved',"
        " 'demo', NULL, NULL, NULL, 3.0, NULL)"
    )
    conn.commit()
    conn.close()


class TestPanelMemorySkillsTreasury:
    def test_memory_payload_reads_incidents_and_notes(self, panel, tmp_path):
        _seed_memory(tmp_path / "memory.db")
        payload = panel.memory_payload(memory_db=tmp_path / "memory.db")
        assert payload["db_present"] is True
        assert payload["incidents"][0]["title"] == "Wacom stall"
        assert payload["notes"][0]["topic"] == "Desktop-Envs"

    def test_memory_payload_missing_db_is_graceful(self, panel, tmp_path):
        payload = panel.memory_payload(memory_db=tmp_path / "nope.db")
        assert payload == {"incidents": [], "notes": [], "db_present": False}

    def test_skills_payload_reads_candidates(self, panel, tmp_path):
        _seed_memory(tmp_path / "memory.db")
        payload = panel.skills_payload(memory_db=tmp_path / "memory.db")
        assert payload["skills"][0]["name"] == "godsv-verify"
        assert payload["skills"][0]["status"] == "promoted"

    def test_treasury_payload_reads_spends_and_grants(self, panel, tmp_path):
        _seed_treasury(tmp_path / "treasury.db")
        payload = panel.treasury_payload(treasury_db=tmp_path / "treasury.db")
        assert payload["spends"][0]["amount"] == 12.5
        assert payload["spends"][0]["merchant"] == "Testshop"
        assert payload["grants"][0]["handle"] == "secret://demo"
        dumped = json.dumps(payload, ensure_ascii=False)
        # the panel may show handles and bookkeeping — never keys or ciphertext
        assert "ciphertext" not in dumped and "fernet" not in dumped.lower()

    def test_payloads_never_include_vault_material(self, panel, tmp_path):
        _seed_treasury(tmp_path / "treasury.db")
        dumped = json.dumps(panel.treasury_payload(treasury_db=tmp_path / "treasury.db"))
        assert "entries" not in dumped and "key" not in dumped.replace("queryKey", "")


class TestChatEventFeed:
    """GET /events — the quiet live feed the chat renders (§16/§22)."""

    def test_feed_keeps_only_chat_kinds_newest_first(self, panel, tmp_path):
        _seed_events(tmp_path / "events.db")
        payload = panel.events_since_payload(events_db=tmp_path / "events.db")
        kinds = [event["kind"] for event in payload["events"]]
        # pause_* are chat events; tool_call / guard_check / claim are not
        assert kinds == ["pause_delivered", "pause_requested"]
        assert payload["latest_seq"] == 5
        assert payload["events"][0]["seq"] > payload["events"][1]["seq"]

    def test_feed_since_returns_only_newer_events(self, panel, tmp_path):
        _seed_events(tmp_path / "events.db")
        first = panel.events_since_payload(events_db=tmp_path / "events.db")
        newest = first["events"][0]["seq"]
        older = panel.events_since_payload(after_seq=1, events_db=tmp_path / "events.db")
        newer = panel.events_since_payload(after_seq=newest, events_db=tmp_path / "events.db")
        assert [event["kind"] for event in older["events"]] == [
            "pause_delivered",
            "pause_requested",
        ]
        assert newer["events"] == []
        assert newer["latest_seq"] == 5

    def test_feed_missing_db_is_graceful(self, panel, tmp_path):
        payload = panel.events_since_payload(events_db=tmp_path / "events.db")
        assert payload["events"] == [] and payload["latest_seq"] == 0
        assert "completion_verdict" in payload["kinds"]

    def test_feed_payload_carries_no_secret_material(self, panel, tmp_path):
        _seed_events(tmp_path / "events.db")
        dumped = json.dumps(panel.events_since_payload(events_db=tmp_path / "events.db"))
        assert "secret://" not in dumped and "ciphertext" not in dumped


class TestApprovals:
    """The real engine approval mode (`/approvals`) behind the composer pill."""

    def test_payload_reports_availability_honestly(self, panel):
        payload = panel.approvals_payload()
        assert "mode" in payload and "available" in payload
        if payload["available"]:
            assert payload["mode"] in panel.APPROVAL_MODES
        else:
            assert payload["mode"] is None

    def test_set_validates_mode(self, panel):
        with pytest.raises(ValueError):
            panel.approvals_set_payload({"mode": "bogus"})
        with pytest.raises(ValueError):
            panel.approvals_set_payload({})


class TestVerificationFor:
    """A criterion naming a registered testing tool must be wired to its check id.

    Live-found 13.09.: everything without tests/ui/api wording fell back to
    `run`, which nothing can certify (gate_dod needs runner evidence with a
    matching check id) — tool-named criteria deadlocked.
    """

    def test_registered_tool_name_wires_the_check(self, panel, monkeypatch, tmp_path):
        tool = tmp_path / "generated_tools" / "check_readme"
        tool.mkdir(parents=True)
        (tool / "check_readme.py").write_text("def run(repo_root=None):\n    return 0\n")
        (tool / "manifest.yaml").write_text(
            "name: check_readme\ntype: testing_tool\nstatus: ready\n"
            "validation:\n  status: validated\n"
        )
        monkeypatch.setattr(panel, "runtime_dir", lambda: tmp_path)
        assert (
            panel._verification_for("Das Tool check_readme laeuft gegen das Repo.")
            == "check_readme"
        )

    def test_unknown_criterion_keeps_run_fallback(self, panel, monkeypatch, tmp_path):
        monkeypatch.setattr(panel, "runtime_dir", lambda: tmp_path)
        assert panel._verification_for("ein voellig anderes Kriterium") == "run"


class TestRepoRootDeclared:
    def test_goal_payload_records_repo_root_as_task_base(self, panel, monkeypatch, tmp_path):
        monkeypatch.setattr(panel, "runtime_dir", lambda: tmp_path)
        out = panel.completion_goal_payload(
            {
                "session_id": "s9",
                "goal": "g",
                "criteria": ["Die Tests laufen gruen"],
                "repo_root": "/home/willy/projects/gods-voice",
            }
        )
        rec = out["state_record"]
        assert rec["session_id"] == "s9"
        assert rec["task_base"] == "/home/willy/projects/gods-voice"

class TestCompletionPayloadCarriesDod:
    """The chat live block renders from the panel payload: it needs the real
    `dod` (with per-item status) and the contract `revision` (the editor's
    conflict fence). Live-found 13.09.: both were missing, so the block showed
    a goal without counters, criteria or edit affordances."""

    def test_payload_carries_dod_status_and_revision(self, tmp_path):
        panel = _load_panel()
        panel.runtime_dir = lambda: tmp_path
        store = panel._completion_store("sess-x")
        store.begin_goal(
            "sess-x", "Ship", [DodItem("c1", "pytest suite is green", verification="pytest")]
        )
        store.add_criteria(["The UI shows a dashboard"])
        payload = panel.completion_payload("sess-x")
        dod = payload["dod"]
        assert [item["id"] for item in dod] == ["c1", "c2"]
        assert dod[0]["status"] == "open"
        assert dod[0]["text"] == "pytest suite is green"
        assert payload["revision"] == 2
        # the derived counts stay consistent with the raw items
        assert payload["total_count"] == len(dod)
