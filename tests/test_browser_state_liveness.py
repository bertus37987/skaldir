"""State honesty for the browser session lifecycle (browser-state-liveness-reconcile).

Measured defect (browser-research-acceptance 2026-10-06, logs/browser-research/step15+16):
after the session's browser died out-of-band, `skaldir browser discover` kept reporting
`state=ready / starts=1 / last_launch_attempt` frozen while no browser existed — and the
next transport call made agent-browser relaunch the browser per-call (`lifecycle.launched:
true`, fresh targetId) WITHOUT the product observing it, so `starts` missed a real launch.

Measured liveness signal (0.38.1, live 2026-10-06): `agent-browser session info --session
X --json` carries `data.active`, `data.pid` and `data.runtime.browserLaunched`; after a
graceful close AND after an out-of-band SIGKILL of the browser, `active` (and
`session list`) can stay stale for a moment while `runtime.browserLaunched` already says
`false` — that field is the honest, non-mutating liveness verdict. Every transport call
(`tab list`, `open`, `read`) carries `data.lifecycle.launched=true` exactly when it
auto-launched the browser itself.

The JSON envelopes below are the MEASURED ones (raw: logs/browser-liveness/), stubbed in
only at the process boundary; the verdict logic, the reconciliation and the state file run
for real.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from skaldir.browser_layer import (
    AgentBrowserBackend,
    BackendKind,
    BrowserBackend,
    BrowserCapability,
    BrowserLayer,
    SessionManager,
    TabInfo,
    session_state_dir,
)
from skaldir.core_capabilities import core_capability_tools

OPERATIONS = {
    "browser.discover", "browser.attach", "browser.tabs", "browser.observe",
    "browser.act", "browser.read", "browser.batch", "browser.verify", "browser.detach",
}

# -- measured envelopes (logs/browser-liveness/step03, step01) ----------------- #

INFO_ALIVE = {
    "success": True, "error": None,
    # pid: a live process id (the measurement recorded the session's real background
    # daemon; the verdict checks it, so the stub uses a pid that answers)
    "data": {"active": True, "pid": os.getpid(), "runtimeError": None,
             "runtime": {"backgroundPid": os.getpid(), "browserLaunched": True,
                         "pageCount": 1, "session": "skaldir"},
             "session": "skaldir"},
}
# measured t+0s AFTER `close` and t+1s AFTER an out-of-band SIGKILL of the browser:
# the daemon entry is stale (`active: true`) while the browser is already gone.
INFO_STALE_ACTIVE_BROWSER_GONE = {
    "success": True, "error": None,
    "data": {"active": True, "pid": os.getpid(), "runtimeError": None,
             "runtime": {"backgroundPid": os.getpid(), "browserLaunched": False,
                         "pageCount": 0, "session": "skaldir"},
             "session": "skaldir"},
}
INFO_NEVER_EXISTED = {
    "success": True, "error": None,
    "data": {"active": False, "pid": None, "runtime": None, "runtimeError": None,
             "session": "skaldir"},
}
# measured: `tab list` on a dead session AUTO-LAUNCHES the browser (fresh targetId)
TAB_LIST_RELAUNCH = {
    "success": True, "error": None,
    "data": {"lifecycle": {"launched": True, "relaunchedBrowser": False,
                           "effectiveLaunch": {"browserLaunched": True}},
             "tabs": [{"active": True, "tabId": "t1", "targetId": "3CA1D445",
                       "title": "", "type": "page", "url": "about:blank"}]},
}
# measured: `tab list` while the browser is already up
TAB_LIST_REUSE = {
    "success": True, "error": None,
    "data": {"lifecycle": {"launched": False, "relaunchedBrowser": False,
                           "effectiveLaunch": {"browserLaunched": True}, "reused": True},
             "tabs": [{"active": True, "tabId": "t1", "targetId": "C9E30DAC",
                       "title": "", "type": "page", "url": "about:blank"}]},
}


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SKALDIR_HOME", str(home))
    return home


class _LivenessBackend(BrowserBackend):
    """Transport double with honest counters; `session_alive` is the real seam."""

    kind = BackendKind.AGENT_BROWSER

    def __init__(self, *, alive: bool | None = True) -> None:
        self.launch_calls: list[dict[str, str]] = []
        self.alive_calls: list[str] = []
        self.close_calls: list[dict[str, str]] = []
        self._alive = alive

    def probe(self) -> BrowserCapability:
        return BrowserCapability(self.kind, available=True, version="stub-0.38.1",
                                 binary="/stub/agent-browser", supports=OPERATIONS)

    def launch(self, *, task: str = "", run_id: str = "", session: str = "") -> dict:
        self.launch_calls.append({"task": task, "run_id": run_id, "session": session})
        return {"ok": True, "backend": self.kind.value, "session_id": session,
                "tabs": [], "detail": "stub launch"}

    def read(self, *, url: str = "", session: str = "") -> dict:
        return {"ok": True, "backend": self.kind.value, "url": url,
                "content": "stub body", "relaunched": False, "session_id": session}

    def session_alive(self, session_id: str) -> bool | None:
        self.alive_calls.append(session_id)
        return self._alive
    def close_tab(self, *, tab: str = "", session: str = "") -> dict[str, Any]:
        """Counted transport close — the close must reach the transport (m4 honesty)."""
        self.close_calls.append({"tab": tab, "session": session})
        return {"ok": True, "backend": self.kind.value, "detail": ""}


# --------------------------------------------------------------------------- #
# the verdict: real session_alive against measured transport shapes             #
# --------------------------------------------------------------------------- #


def _backend_with_run(monkeypatch: pytest.MonkeyPatch, stdout: str, *, ok: bool = True) \
        -> AgentBrowserBackend:
    backend = AgentBrowserBackend(binary="/stub/agent-browser")
    monkeypatch.setattr(backend, "probe", lambda: BrowserCapability(
        backend.kind, available=True, version="stub-0.38.1",
        binary="/stub/agent-browser", supports=OPERATIONS))
    monkeypatch.setattr(backend, "run", lambda argv: {
        "ok": ok, "exit_code": 0 if ok else 2, "stdout": stdout, "stderr": "",
        "duration_ms": 5.0, "error_class": "" if ok else "backend",
    })
    return backend


def test_session_alive_says_true_for_the_measured_live_envelope(monkeypatch):
    backend = _backend_with_run(
        monkeypatch, json.dumps(INFO_ALIVE))
    assert backend.session_alive("skaldir") is True


def test_a_browser_closed_out_of_band_is_dead_despite_a_stale_active_flag(monkeypatch):
    """Measured t+0s after `close` and t+1s after SIGKILL: `active: true` lingers while
    `runtime.browserLaunched` already says false — the stale flag must not win."""
    backend = _backend_with_run(
        monkeypatch, json.dumps(INFO_STALE_ACTIVE_BROWSER_GONE))
    assert backend.session_alive("skaldir") is False


def test_a_session_that_never_existed_is_dead(monkeypatch):
    backend = _backend_with_run(monkeypatch, json.dumps(INFO_NEVER_EXISTED))
    assert backend.session_alive("skaldir") is False


def test_an_unusable_transport_cannot_say(monkeypatch):
    backend = _backend_with_run(monkeypatch, "agent-browser: daemon error", ok=False)
    assert backend.session_alive("skaldir") is None


def test_the_verdict_probe_launches_nothing(monkeypatch):
    """A liveness reading must never auto-launch the browser it asks about."""
    argvs: list[list[str]] = []

    def _run(argv: list[str]) -> dict[str, Any]:
        argvs.append(argv)
        return {"ok": True, "exit_code": 0,
                "stdout": json.dumps(INFO_STALE_ACTIVE_BROWSER_GONE), "stderr": "",
                "duration_ms": 5.0, "error_class": ""}

    backend = _backend_with_run(monkeypatch, "")
    monkeypatch.setattr(backend, "run", _run)
    backend.session_alive("skaldir")
    assert argvs, "the verdict asks the transport"
    assert all(a[:1] != ["open"] for a in argvs), argvs


# --------------------------------------------------------------------------- #
# the per-call relaunch is SURFACED by the transport, not hidden                #
# --------------------------------------------------------------------------- #


def test_tab_list_surfaces_the_measured_per_call_relaunch(monkeypatch):
    backend = _backend_with_run(monkeypatch, json.dumps(TAB_LIST_RELAUNCH))
    result = backend.tabs(session="skaldir")
    assert result["ok"] is True
    assert result["relaunched"] is True


def test_tab_list_without_a_launch_is_not_a_relaunch(monkeypatch):
    backend = _backend_with_run(monkeypatch, json.dumps(TAB_LIST_REUSE))
    result = backend.tabs(session="skaldir")
    assert result["relaunched"] is False


def test_read_surfaces_a_per_call_relaunch(monkeypatch):
    backend = _backend_with_run(monkeypatch, json.dumps(
        {"success": True, "error": None,
         "data": {"content": "rendered body", "url": "https://example.com/",
                  "status": 200,
                  "lifecycle": {"launched": True, "relaunchedBrowser": False}}}))
    result = backend.read(url="https://example.com/", session="skaldir")
    assert result["ok"] is True
    assert result["relaunched"] is True


# --------------------------------------------------------------------------- #
# discover()/ensure() reconcile a ready-but-dead session (never a bare ready)   #
# --------------------------------------------------------------------------- #


def test_discover_reports_a_dead_ready_session_not_a_bare_ready(isolated_home):
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    assert manager.ensure(task="t", run_id="r1")["action"] == "launched"
    backend._alive = False  # the browser died out-of-band, like the measured defect

    inventory = manager.discover()

    assert inventory["state"] != "ready", inventory
    assert inventory["live"] is False
    assert inventory["diagnosis"], inventory
    assert "skaldir" in inventory["diagnosis"] or inventory["session_id"] in \
        inventory["diagnosis"]
    # reconciliation is read-only in the launch sense: discover never starts a browser
    assert len(backend.launch_calls) == 1, (
        "discover added no launch — only the test's own ensure did", backend.launch_calls)


def test_discover_reconciliation_lands_in_the_state_file(isolated_home):
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")
    backend._alive = False
    manager.discover()
    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    assert data["state"] == "unknown"
    assert data.get("reconciled_dead_session") == "skaldir"
    assert data.get("reconciled_diagnosis")


def test_discover_keeps_a_live_ready_session_ready(isolated_home):
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")
    inventory = manager.discover()
    assert inventory["state"] == "ready"
    assert inventory["live"] is True
    assert inventory["starts"] == 1, "no spurious extra start for a live session"


def test_discover_without_a_backend_cannot_claim_liveness(isolated_home):
    manager = SessionManager(session_state_dir())
    # a pre-existing ready state (written by another process), no backend here
    manager._write({"state": "ready", "session_id": "skaldir", "starts": 1,
                    "last_launch_attempt": time.time()})
    inventory = manager.discover()
    assert inventory["state"] == "ready"
    assert inventory["live"] is None


# --------------------------------------------------------------------------- #
# ensure(): the product launches a dead session itself and COUNTS the launch    #
# --------------------------------------------------------------------------- #


def test_ensure_on_a_dead_ready_session_counts_its_own_real_launch(isolated_home):
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    assert manager.ensure(task="t", run_id="r1")["action"] == "launched"
    backend._alive = False  # the browser died out-of-band

    result = manager.ensure(task="t2", run_id="r2")

    assert result["action"] == "launched", result
    assert result["starts"] == 2, "a real product-observed launch must move the counter"
    assert len(backend.launch_calls) == 2
    state = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    assert state["state"] == "ready"
    assert state["starts"] == 2
    assert state["last_launch_attempt"] > state.get("reconciled_at", 0)
    assert result["last_launch_attempt"] > 1791305147.3, "the frozen timestamp moved"


def test_a_live_ready_session_is_reused_without_an_extra_start(isolated_home):
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")
    for _ in range(5):
        result = manager.ensure(task="t", run_id="r1")
        assert result["action"] == "reused", result
        assert result["live"] is True
        assert result["starts"] == 1, "no spurious extra start for a live session"
    assert len(backend.launch_calls) == 1


def test_the_concurrent_merge_property_survives_the_liveness_checks(isolated_home):
    """VAL-WEB-003's measuring instrument: 10 concurrent demands, exactly ONE start."""
    from concurrent.futures import ThreadPoolExecutor

    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(
            lambda i: manager.ensure(task="research", run_id=f"run-{i}"), range(10)))
    assert len(backend.launch_calls) == 1
    launched = [r for r in results if r["action"] == "launched"]
    assert len(launched) == 1, [r["action"] for r in results]
    assert len({r["session_id"] for r in results}) == 1
    assert manager.discover()["starts"] == 1


# --------------------------------------------------------------------------- #
# a relaunch agent-browser performs per-call is RECORDED by the product         #
# --------------------------------------------------------------------------- #


def test_an_observed_relaunch_is_counted_in_the_state_file(isolated_home):
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")
    before = manager.discover()
    stamp = time.time() + 5

    manager.record_observed_relaunch(session_id="skaldir",
                                     detail="tab list auto-launched the browser",
                                     now=stamp)

    inventory = manager.discover()
    assert inventory["starts"] == before["starts"] + 1, inventory
    assert inventory["last_launch_attempt"] == pytest.approx(stamp)
    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    assert data["last_relaunch_observed"]["detail"], data
    assert data["state"] == "ready", "a relaunched browser IS ready"


def test_the_handler_records_a_relaunch_it_observed_in_a_transport_result(
    isolated_home, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The agent path (the product path that drives the transport) observes the measured
    per-call relaunch and records it — `starts` counts real launches."""
    import skaldir.browser_layer as bl

    kind = BackendKind.AGENT_BROWSER
    backend = _LivenessBackend()

    def _tabs(*, session: str = "") -> dict[str, Any]:
        return {"ok": True, "backend": kind.value, "relaunched": True,
                "tabs": [{"tab_id": "t1", "url": "about:blank", "title": "",
                          "active": True}],
                "detail": ""}

    backend.tabs = _tabs  # type: ignore[method-assign]

    def _factory(*_args: Any, **_kw: Any) -> _LivenessBackend:
        return backend

    monkeypatch.setattr(bl, "AgentBrowserBackend", _factory)
    ws = tmp_path / "ws"
    ws.mkdir()
    (tool,) = core_capability_tools(workspace=ws, runtime=tmp_path / "runtime",
                                    run_id="run-live", include=("browser",))
    result = tool.handler(do="read", url="https://example.invalid/page")

    assert result["ok"] is True, result
    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    assert data["starts"] == 2, (
        "the transport reported a per-call relaunch; the product must count it", data)
    assert data["last_relaunch_observed"]["session_id"] == "skaldir"


# --------------------------------------------------------------------------- #
# the recorded inventory after a relaunch: no phantom tabs of the dead browser  #
# (browser-inventory-honesty-after-relaunch)                                    #
# --------------------------------------------------------------------------- #


def _state_with_agent_tab_of_a_dead_browser() -> SessionManager:
    """A ready state whose recorded tab belonged to the PREVIOUS browser process."""
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")
    manager.note_agent_tab("t-old", url="https://old.example/page",
                           title="old browser's tab", run_id="r1")
    return manager, backend


def test_an_observed_relaunch_leaves_the_recorded_inventory_honest(isolated_home):
    """Failing-first: after a recorded relaunch the state file must not keep claiming
    tabs of the dead browser — the relaunched browser is a NEW process with new tabs."""
    manager, _backend = _state_with_agent_tab_of_a_dead_browser()
    assert manager.discover()["tab_count"] == 1

    manager.record_observed_relaunch(session_id="skaldir",
                                     detail="tab list auto-launched the browser")

    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    assert data["tabs"] == [], (
        "the recorded inventory must not claim tabs of the dead browser", data)
    assert data["last_relaunch_observed"]["superseded_tabs"] == 1
    assert data["last_relaunch_observed"]["superseded_tab_ids"] == ["t-old"]


def test_tabs_and_discover_report_no_phantom_tabs_after_a_recorded_relaunch(
    isolated_home,
):
    """`skaldir browser tabs` reads the recorded inventory directly (BrowserLayer.tabs
    -> sessions._read()['tabs']) — after a relaunch it must not list the dead
    browser's tabs, and discover's tab_count must agree."""
    manager, backend = _state_with_agent_tab_of_a_dead_browser()
    manager.record_observed_relaunch(session_id="skaldir", detail="auto-launch")

    layer = BrowserLayer(manager, backend)
    tabs_result = layer.tabs()
    assert [str(t.get("tab_id") or "") for t in tabs_result.data["tabs"]] == []
    assert tabs_result.data["agent_tabs"] == []
    assert tabs_result.data["user_tabs"] == []
    inventory = layer.discover()
    assert inventory.data["tab_count"] == 0
    assert inventory.preview.endswith("0 tab(s)"), inventory.preview


def test_a_real_inventory_refresh_after_the_relaunch_repopulates(isolated_home):
    """The supersession is not a hole in the inventory: the next real inventory read
    (what the handler does after launch/act) repopulates the state file."""
    manager, _backend = _state_with_agent_tab_of_a_dead_browser()
    manager.record_observed_relaunch(session_id="skaldir", detail="auto-launch")
    assert manager.discover()["tab_count"] == 0

    manager.record_tabs([
        TabInfo(tab_id="t-fresh", url="about:blank", title="", owner="agent",
                run_id="r2"),
        TabInfo(tab_id="t-user", url="https://user.example/", owner="user"),
    ])

    inventory = manager.discover()
    assert inventory["tab_count"] == 2
    assert [t["tab_id"] for t in inventory["agent_tabs"]] == ["t-fresh"]
    assert [t["tab_id"] for t in inventory["user_tabs"]] == ["t-user"]


def test_ownership_rules_survive_the_inventory_supersession(isolated_home):
    """No new ownership semantics: after a relaunch-supersession and a repopulating
    refresh, only agent-recorded tabs are closable and there is still no close --all."""
    manager, _backend = _state_with_agent_tab_of_a_dead_browser()
    manager.record_observed_relaunch(session_id="skaldir", detail="auto-launch")
    manager.record_tabs([
        TabInfo(tab_id="t-user", url="https://user.example/", owner="user"),
        TabInfo(tab_id="t-fresh", url="about:blank", owner="agent", run_id="r2"),
    ])

    report = manager.close_agent_tabs(["t-user", "t-fresh"])

    assert [t["tab_id"] for t in report["closed"]] == ["t-fresh"]
    assert [t["tab_id"] for t in report["refused_user_tabs"]] == ["t-user"]
    assert report["closed_all"] is False


def test_a_handler_read_that_observes_a_relaunch_supersedes_the_dead_tabs(
    isolated_home, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The agent path: a transport read that observed the per-call relaunch must leave
    no tabs of the PREVIOUS browser in the state file the CLI's `tabs` verb reads."""
    import skaldir.browser_layer as bl

    kind = BackendKind.AGENT_BROWSER
    backend = _LivenessBackend()

    manager = SessionManager(session_state_dir(), backend=backend)
    manager._write({
        "state": "ready", "session_id": "skaldir", "starts": 1,
        "last_launch_attempt": time.time(),
        "tabs": [TabInfo(tab_id="t-old", url="https://old.example/page",
                         title="dead browser's tab", owner="agent",
                         run_id="r0").to_dict()],
    })

    def _read(*, url: str = "", session: str = "") -> dict[str, Any]:
        return {"ok": True, "backend": kind.value, "url": url,
                "content": "fresh browser body", "relaunched": True,
                "session_id": session}

    backend.read = _read  # type: ignore[method-assign]

    def _factory(*_args: Any, **_kw: Any) -> _LivenessBackend:
        return backend

    monkeypatch.setattr(bl, "AgentBrowserBackend", _factory)
    ws = tmp_path / "ws"
    ws.mkdir()
    (tool,) = core_capability_tools(workspace=ws, runtime=tmp_path / "runtime",
                                    run_id="run-live", include=("browser",))
    result = tool.handler(do="read", url="https://example.com/")

    assert result["ok"] is True, result
    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    assert data["tabs"] == [], (
        "a read that observed the relaunch must supersede the dead browser's tabs", data)
    assert data["last_relaunch_observed"]["superseded_tabs"] == 1


# --------------------------------------------------------------------------- #
# the post-act inventory refresh UPSERTS known ids (metadata staleness,         #
# measured live 2026-10-07: in-tab navigation left url/title frozen)            #
# --------------------------------------------------------------------------- #


def _act_ok(*, action: Any = None, session: str = "") -> dict[str, Any]:
    return {"ok": True, "backend": BackendKind.AGENT_BROWSER.value,
            "action": {"kind": str((action or {}).get("kind") or "")},
            "preview": "", "relaunched": False, "session_id": session}


def test_a_post_act_inventory_refresh_upserts_a_known_tabs_metadata(
    isolated_home, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Failing-first (measured live twice, orchestrator 2026-10-07): the handler's
    post-act inventory refresh SKIPPED re-noting known tab ids, so a recorded tab's
    url/title stayed frozen at its first note even after in-tab navigation. The tab
    still exists — this is metadata staleness, the same honesty class as the relaunch
    phantoms: the state file must not keep claiming a url the tab left."""
    import skaldir.browser_layer as bl

    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")           # ready, one real launch
    manager.note_agent_tab("t1", url="https://example.org/first",
                           title="First", run_id="r1")

    def _act(action: Any = None, *, session: str = "") -> dict[str, Any]:
        return _act_ok(action=action, session=session)

    def _tabs(*, session: str = "") -> dict[str, Any]:
        # the SAME tab t1 after in-tab navigation: new url/title, same id
        return {"ok": True, "backend": BackendKind.AGENT_BROWSER.value,
                "relaunched": False,
                "tabs": [{"tab_id": "t1", "url": "https://example.com/navigated",
                          "title": "Navigated", "active": True}],
                "detail": ""}

    backend.act = _act  # type: ignore[method-assign]
    backend.tabs = _tabs  # type: ignore[method-assign]

    def _factory(*_args: Any, **_kw: Any) -> _LivenessBackend:
        return backend

    monkeypatch.setattr(bl, "AgentBrowserBackend", _factory)
    ws = tmp_path / "ws"
    ws.mkdir()
    (tool,) = core_capability_tools(workspace=ws, runtime=tmp_path / "runtime",
                                    run_id="run-live", include=("browser",))
    result = tool.handler(do="act", kind="navigate", url="https://example.com/navigated")

    assert result["ok"] is True, result
    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    recorded = [t for t in data["tabs"] if str(t.get("tab_id") or "") == "t1"]
    assert len(recorded) == 1, ("no duplicate notes for the same id", data["tabs"])
    assert recorded[0]["url"] == "https://example.com/navigated", (
        "the recorded url must be refreshed after in-tab navigation", recorded)
    assert recorded[0]["title"] == "Navigated", (
        "the recorded title must be refreshed after in-tab navigation", recorded)
    assert recorded[0]["owner"] == "agent", (
        "a metadata refresh must not touch ownership", recorded)


def test_refresh_tab_metadata_updates_url_and_title_without_touching_ownership(
    isolated_home,
):
    """The upsert is metadata, not ownership: a user tab's url/title may be refreshed
    (truthful inventory) while it stays `owner=user` and is still refused at close."""
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")
    manager.record_tabs([
        TabInfo(tab_id="t-user", url="https://user.example/", title="User tab",
                owner="user"),
    ])
    manager.note_agent_tab("t-agent", url="https://agent.example/first",
                           title="Agent first", run_id="r1")

    changed_agent = manager.refresh_tab_metadata(
        "t-agent", url="https://agent.example/navigated", title="Agent navigated")
    changed_user = manager.refresh_tab_metadata(
        "t-user", url="https://user.example/other", title="User tab moved")
    unchanged = manager.refresh_tab_metadata("t-unknown", url="https://now.example/")

    assert changed_agent is True
    assert changed_user is True
    assert unchanged is False, "an unknown id changes nothing (it is nobody's tab)"
    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    by_id = {str(t.get("tab_id") or ""): t for t in data["tabs"]}
    assert by_id["t-agent"]["url"] == "https://agent.example/navigated"
    assert by_id["t-agent"]["title"] == "Agent navigated"
    assert by_id["t-agent"]["owner"] == "agent"
    assert by_id["t-user"]["url"] == "https://user.example/other"
    assert by_id["t-user"]["title"] == "User tab moved"
    assert by_id["t-user"]["owner"] == "user", (
        "a metadata refresh must never make a user tab the agent's", by_id["t-user"])
    report = manager.close_agent_tabs(["t-agent", "t-user"])
    assert [t["tab_id"] for t in report["closed"]] == ["t-agent"]
    assert [t["tab_id"] for t in report["refused_user_tabs"]] == ["t-user"]


def test_refresh_tab_metadata_an_empty_observation_clears_the_recorded_value(
    isolated_home,
):
    """m4 scrutiny round 1: the recorded value must reflect the LATEST observation.

    The old guards (`if url:` / `if title:`) kept a stale recorded value when the
    transport reported an empty url/title — the state file then asserted something the
    browser no longer says. Empty means "the transport sees nothing there": it clears.
    Ownership stays untouched, as everywhere in the metadata path.
    """
    backend = _LivenessBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    manager.ensure(task="t", run_id="r1")
    manager.note_agent_tab("t-agent", url="https://agent.example/first",
                           title="Agent first", run_id="r1")
    cleared = manager.refresh_tab_metadata("t-agent", url="", title="")
    assert cleared is True, "a change (to empty) is a change"
    data = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    by_id = {str(t.get("tab_id") or ""): t for t in data["tabs"]}
    assert by_id["t-agent"]["url"] == "", (
        "an empty observed url must clear the stale recorded one", by_id["t-agent"])
    assert by_id["t-agent"]["title"] == "", (
        "an empty observed title must clear the stale recorded one", by_id["t-agent"])
    assert by_id["t-agent"]["owner"] == "agent"
