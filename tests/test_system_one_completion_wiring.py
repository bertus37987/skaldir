"""Wave 3 — the §91 advisory reading is reachable in the product (it was dead code).

Measured 17.09. at HEAD: `godsv.completion.completion_precheck` and
`godsv.system_one_flags.load_system_one_flags` had **no production caller** — the §98 flags, the
§33 facade and the §91/JEV-13 preflight reading never ran on any real path, so the whole System-One
advisory chain was inert however green its own suites were.

The visible verify route (`completion_verify_payload`, the panel's "verify now") is where a
completion claim is decided, so the advisory is computed there — next to the deterministic verdict
and never inside it. These tests pin the honesty of that wiring:

* flags off (the default) → `available: false` with the reason, no invented reading;
* flags on → the structural reading, bound to the contract revision, `advisory_only: true`,
  `certifies_completion: false`, and no evidence ⇒ a signal/missing entry;
* the advisory never upgrades the verdict (a failing contract stays unfinished);
* the reading is recorded as a `system_one_decision` runtime event (the feed's kind);
* a malformed settings section keeps System One off (fail-closed).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from skaldir.completion import CompletionStore, DodItem

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)

FLAGS_ON = "system_one:\n  enabled: true\n  completion_precheck: true\n  provider: llm_adapter\n"


def _load_panel(name: str = "godsv_panel_system_one"):
    spec = importlib.util.spec_from_file_location(name, PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return _load_panel()


def _runtime(tmp_path: Path) -> Path:
    return tmp_path / "godsv"


def _settings(tmp_path: Path, body: str) -> None:
    path = _runtime(tmp_path) / "settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _contract(tmp_path: Path, session: str = "s1") -> CompletionStore:
    store = CompletionStore(_runtime(tmp_path), session_id=session)
    store.begin_goal(
        session,
        "wave-3 wiring: make the advisory reading reachable",
        [DodItem(id="c1", text="the criterion nobody proved yet", blocking=True, verification="")],
        task_base=str(tmp_path),
    )
    return store


class TestTheAdvisoryIsReachable:
    def test_with_the_flags_off_the_payload_says_so(self, panel, tmp_path):
        _contract(tmp_path)
        result = panel.completion_verify_payload({"session_id": "s1", "text": "done"})
        pre = result["preflight"]
        assert pre["available"] is False
        assert pre["reading"] is None
        assert pre["flags"]["enabled"] is False
        assert "flags off" in pre["reason"]
        # the deterministic gate still ran and produced its own verdict
        assert result["state"] and result["last_verdict"]["status"] is not None

    def test_the_flags_on_path_yields_the_structural_reading(self, panel, tmp_path):
        _settings(tmp_path, FLAGS_ON)
        _contract(tmp_path)
        pre = panel.completion_verify_payload({"session_id": "s1", "text": "done"})["preflight"]
        assert pre["available"] is True, pre
        reading = pre["reading"]
        assert reading["advisory_only"] is True
        assert reading["certifies"] is False
        assert reading["certifies_completion"] is False
        assert reading["revision"] == 1, "the reading must be bound to the contract revision"
        assert reading["missing"] or reading["signals"], reading
        # honest about the missing provider client in this path
        assert "structural reading only" in pre["reason"]

    def test_the_advisory_never_upgrades_the_verdict(self, panel, tmp_path):
        _settings(tmp_path, FLAGS_ON)
        _contract(tmp_path)
        result = panel.completion_verify_payload({"session_id": "s1", "text": "done"})
        assert result["state"] != "VERIFIED_COMPLETE"
        assert result["preflight"]["reading"]["certifies_completion"] is False

    def test_the_reading_is_recorded_as_a_runtime_event(self, panel, tmp_path):
        from skaldir.events import EventStore

        _settings(tmp_path, FLAGS_ON)
        _contract(tmp_path)
        panel.completion_verify_payload({"session_id": "s1", "text": "done"})
        rows = [
            row
            for row in EventStore(_runtime(tmp_path) / "events.db").iter_session("s1")
            if row.kind == "system_one_decision"
        ]
        assert rows, "the advisory must be visible in the runtime events"
        assert rows[0].payload["purpose"] == "completion_preflight"
        assert rows[0].payload["advisory_only"] is True
        assert "provider" in rows[0].payload

    def test_a_malformed_section_keeps_system_one_off(self, panel, tmp_path):
        _settings(tmp_path, "system_one: 'yes'\n")
        _contract(tmp_path)
        pre = panel.completion_verify_payload({"session_id": "s1", "text": "done"})["preflight"]
        assert pre["available"] is False
        assert pre["flags"]["enabled"] is False
