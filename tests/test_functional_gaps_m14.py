"""Regression tests for M14 functional gap & residual fixes (Steps 1–5)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from skaldir import browser_layer, container_adapter, docker_runtime, toolbuilder
from skaldir.voice_rebrief import Rebrief, validate_rebrief

# --------------------------------------------------------------------------- #
# Step 1: Browser Layer Single Liveness Probe, Persistent Diagnosis & Tabs
# --------------------------------------------------------------------------- #


class _FakeBrowserBackend:
    kind = browser_layer.BackendKind.AGENT_BROWSER

    def __init__(self, *, live_tabs: list[dict] | None = None) -> None:
        self.live_tabs = list(
            live_tabs or [{"tab_id": "t-live-1", "url": "https://example.org", "title": "Example"}]
        )
        self.alive = True
        self.alive_calls = 0

    def probe(self) -> browser_layer.BrowserCapability:
        return browser_layer.BrowserCapability(
            available=True,
            backend=browser_layer.BackendKind.AGENT_BROWSER,
            reason="ok",
            supports=tuple(op.value for op in browser_layer.Operation),
        )

    def session_alive(self, session: str = "") -> bool | None:
        self.alive_calls += 1
        return self.alive

    def launch(self, *, task: str = "", run_id: str = "", session: str = "") -> dict:
        return {"ok": True, "session_id": session or "skaldir-browser"}

    def act(self, payload: dict, *, session: str = "") -> dict:
        return {"ok": True, "session_id": session or "skaldir-browser"}

    def tabs(self, session: str = "") -> dict:
        return {"ok": True, "tabs": list(self.live_tabs)}

    def close_tab(self, tab_id: str, session: str = "") -> dict:
        self.live_tabs = [t for t in self.live_tabs if t.get("tab_id") != tab_id]
        return {"ok": True, "closed": tab_id}


def test_step1_ensure_reused_probes_liveness_once_and_preserves_reconciled_diagnosis(
    tmp_path: Path,
) -> None:
    backend = _FakeBrowserBackend()
    mgr = browser_layer.SessionManager(tmp_path / "browser", backend=backend)

    first = mgr.ensure(task="first", run_id="r1")
    assert first["action"] == "launched"

    backend.alive_calls = 0
    reused = mgr.ensure(task="second", run_id="r1")
    assert reused["action"] == "reused"
    assert reused["live"] is True
    # Only ONE liveness probe inside ensure() when reusing a live session
    assert backend.alive_calls == 1

    # Simulate dead transport -> discover reconciles -> subsequent discover preserves diagnosis
    backend.alive = False
    disc1 = mgr.discover()
    assert disc1["state"] == "unknown"
    assert disc1["reconciled_dead_session"]
    assert disc1["reconciled_diagnosis"]
    disc2 = mgr.discover()
    assert disc2["reconciled_diagnosis"] == disc1["reconciled_diagnosis"]
    assert disc2["reconciled_dead_session"] == disc1["reconciled_dead_session"]


def test_step1_browser_tabs_merges_live_transport_and_recorded_ownership(
    tmp_path: Path,
) -> None:
    backend = _FakeBrowserBackend(
        live_tabs=[
            {"tab_id": "tab-a", "url": "https://a.example", "title": "A"},
            {"tab_id": "tab-b", "url": "https://b.example", "title": "B"},
        ]
    )
    sessions = browser_layer.SessionManager(tmp_path / "browser", backend=backend)
    layer = browser_layer.BrowserLayer(sessions, backend)
    sessions.ensure(task="open", run_id="r1")
    sessions.note_agent_tab("tab-a", url="https://a.example", title="A", run_id="r1")

    tabs_report = layer.tabs()
    assert tabs_report.ok is True
    assert tabs_report.data["inventory_source"] == "transport"
    by_id = {t["tab_id"]: t for t in tabs_report.data["tabs"]}
    assert set(by_id) == {"tab-a", "tab-b"}
    assert by_id["tab-a"]["owner"] == "agent"
    assert by_id["tab-b"]["owner"] == "user"


# --------------------------------------------------------------------------- #
# Step 2: Voice Rebrief Integration in Goal Run Strategy-Change Interventions
# --------------------------------------------------------------------------- #


def test_step2_voice_rebrief_contract_and_goal_run_wiring(tmp_path: Path) -> None:
    rebrief = validate_rebrief(
        Rebrief(
            finding="Progress guard detected pattern_no_new_evidence on turn t1-plan.",
            evidence="verdict=FAILED; no_progress_streak=1; blockers=check c1 failed",
            criteria=("c1",),
            next_step="Run verification check (pytest) and address blocker: check c1 failed",
            goal="Pass all unit tests",
        )
    )
    wire = rebrief.to_dict()
    assert wire["finding"].startswith("Progress guard detected")
    assert wire["criteria"] == ["c1"]
    assert "Run verification check" in wire["next_step"]


# --------------------------------------------------------------------------- #
# Step 3: RunConfig.validate Idempotency & Port-Gate Clarity
# --------------------------------------------------------------------------- #


def test_step3_run_config_validate_is_idempotent_with_port_bindings() -> None:
    digest = "sha256:" + "a" * 64
    rc = docker_runtime.RunConfig(image_digest=digest, port_bindings={8080: 18080})
    assert rc.network == "none"
    # First validation promotes none -> bridge
    assert rc.validate() == []
    assert rc.network == "bridge"
    # Second validation (or policy_problems() call) must remain clean!
    assert rc.validate() == []
    assert rc.policy_problems() == []

    # Explicit non-none network with port_bindings is still refused with a clear hint
    bad = docker_runtime.RunConfig(
        image_digest=digest, network="host", port_bindings={8080: 18080}
    )
    problems = bad.policy_problems()
    assert "network-must-be-none-to-publish-ports" in problems
    refusal = docker_runtime.policy_refusal(problems)
    assert "promotes none -> bridge" in refusal.detail


# --------------------------------------------------------------------------- #
# Step 4: Subcommand-Aware Container Verb Matching & Rendering
# --------------------------------------------------------------------------- #


def test_step4_subcommand_verb_matches_base_binary_and_splits_argv() -> None:
    surface = {
        "inspected": True,
        "cli": {"verbs": ["python3", "git", "sh"], "count": 3},
        "os": {"ID": "alpine"},
    }
    subcmd_template = {
        "id": "python3-json-tool",
        "terms": ("format json", "pretty json"),
        "verb": "python3 -m json.tool",
        "argv_template": [{"verb": True}, "{text}"],
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": {"formatted": {"type": "string", "from": "stdout", "pattern": r"^[\s\S]*$"}},
        "oracle": lambda payload: {"values": {"formatted": payload["text"]}},
        "sample_input": {"text": '{"a":1}'},
    }
    adapter = toolbuilder.generate_container_adapter(
        "format json payload",
        name="json_fmt",
        image="alpine:3.20",
        image_digest="sha256:" + "b" * 64,
        surface=surface,
        template=subcmd_template,
    )
    assert adapter["verb"] == "python3 -m json.tool"
    argv = container_adapter.render(adapter, {"text": '{"a":1}'})
    assert argv == ["python3", "-m", "json.tool", '{"a":1}']


# --------------------------------------------------------------------------- #
# Step 5: Desktop Browser URL Tracking in SessionManager
# --------------------------------------------------------------------------- #


def test_step5_desktop_browser_open_records_agent_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skaldir import core_capabilities

    state_dir = tmp_path / "browser_state"
    backend = _FakeBrowserBackend(live_tabs=[])
    monkeypatch.setattr(browser_layer, "session_state_dir", lambda: state_dir)
    monkeypatch.setattr(browser_layer, "AgentBrowserBackend", lambda: backend)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    popen_calls: list[list[str]] = []
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **_kw: popen_calls.append(list(cmd)) or SimpleNamespace(pid=1234),
    )

    tool = core_capabilities._browser_tool(tmp_path, state_dir, "run-m14")
    assert tool is not None
    res = tool.handler(do="act", kind="open", url="https://news.ycombinator.com")
    assert res["ok"] is True
    assert popen_calls == [["xdg-open", "https://news.ycombinator.com"]]

    sessions = browser_layer.SessionManager(state_dir, backend=backend)
    truth = sessions.transport_truth()
    urls = [t["url"] for t in truth["tabs"]]
    assert "https://news.ycombinator.com" in urls
