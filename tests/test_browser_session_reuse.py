"""VAL-WEB-003 — one central browser session lifecycle, shared by CLI and agent path.

The gap this closes (measured in scrutiny m3 round 2, and reproduced failing-first
against a ``git worktree`` of the pre-fix HEAD — see ``logs/browser-reuse/``): the
browser handler built a ``SessionManager`` and used ONLY ``.state_dir`` — it never
drove ``ensure()``/``discover()``/the cross-process start lock, and its state dir
(``<run runtime>/browser``) diverged from the one ``skaldir desk browser`` reads
(``<CLI runtime>/browser``). So on the agent path the "run-scoped lock" property was
unexercised and no demand ever merged into one session.

The behaviors pinned here (``expectedBehavior`` of feature browser-session-reuse):

* 10 concurrent ensure demands merge into exactly ONE browser start (counters on the
  transport double — the lifecycle under test is real, only the process edge is stubbed);
* an empty observation moves the state to ``unconfirmed`` with a diagnosis and starts
  NOTHING further (BR-02);
* agent-owned vs user-owned tabs are distinguished, and cleanup never touches a tab
  the agent did not create (BR-13);
* the ``browser`` tool is present in the measured agent toolset (F08 class);
* the agent path and the CLI resolve the SAME session state dir (one lock, real reuse).

Hermetic by construction: the backend is a stub, no browser, no GUI, no network.
The live proofs with the real ``agent-browser`` binary are recorded separately under
``logs/browser-reuse/`` and summarized in ``docs/audit/DROID-BROWSER-SESSION-REUSE.md``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from skaldir.browser_layer import (
    OPERATIONS,
    PRODUCT_SESSION_NAME,
    BackendKind,
    BrowserCapability,
    SessionManager,
    TabInfo,
    session_state_dir,
)
from skaldir.core_capabilities import core_capability_tools


class _CountingBackend:
    """Transport double with honest counters — the lifecycle under test is real.

    Only the process boundary is stubbed (the agent-browser binary is a subprocess);
    the SessionManager, its lock, its state file and the handler run for real.
    """

    kind = BackendKind.AGENT_BROWSER

    def __init__(self, *, ok: bool = True, alive: bool = True, content: str = "stub body") \
            -> None:
        self.launch_calls: list[dict[str, str]] = []
        self.alive_calls: list[str] = []
        self.read_calls: list[dict[str, str]] = []
        self.act_calls: list[dict[str, Any]] = []
        self.tab_list_calls = 0
        self._ok = ok
        self._alive = alive
        self._content = content

    def probe(self) -> BrowserCapability:
        return BrowserCapability(self.kind, available=True, version="stub-0.38.1",
                                 binary="/stub/agent-browser", supports=OPERATIONS)

    def launch(self, *, task: str = "", run_id: str = "", session: str = "") -> dict[str, Any]:
        self.launch_calls.append({"task": task, "run_id": run_id, "session": session})
        return {"ok": self._ok, "backend": self.kind.value, "session_id": session,
                "tabs": [], "detail": "stub launch"}

    def session_alive(self, session_id: str) -> bool | None:
        self.alive_calls.append(session_id)
        return self._alive

    def tabs(self, *, session: str = "") -> dict[str, Any]:
        self.tab_list_calls += 1
        return {"ok": True, "backend": self.kind.value, "tabs": [], "detail": ""}

    def read(self, *, url: str = "", session: str = "") -> dict[str, Any]:
        self.read_calls.append({"url": url, "session": session})
        return {"ok": True, "backend": self.kind.value, "url": url,
                "content": self._content, "session_id": session}

    def act(self, action: dict[str, Any], *, session: str = "") -> dict[str, Any]:
        self.act_calls.append({"action": dict(action), "session": session})
        return {"ok": True, "backend": self.kind.value, "action": dict(action)}


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A hermetic installation runtime: both the CLI resolution and the agent path
    resolve their browser state dir through ``default_runtime_dir()``, which honours
    ``SKALDIR_HOME`` — so the test never touches the real installation state."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SKALDIR_HOME", str(home))
    return home


@pytest.fixture()
def browser_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real assembled toolset's browser tool, transport-stubbed."""
    backend = _CountingBackend()

    def _factory(*_args: Any, **_kw: Any) -> _CountingBackend:
        return backend

    import skaldir.browser_layer as bl

    monkeypatch.setattr(bl, "AgentBrowserBackend", _factory)
    ws = tmp_path / "ws"
    ws.mkdir()
    tools = core_capability_tools(workspace=ws, runtime=tmp_path / "runtime",
                                  run_id="run-reuse", include=("browser",))
    assert [t.name for t in tools] == ["browser"], tools
    return tools[0], backend, ws


# --------------------------------------------------------------------------- #
# BR-01/VAL-WEB-003: concurrent "provide browser" demands merge into ONE start  #
# --------------------------------------------------------------------------- #


def test_ten_concurrent_ensure_calls_start_exactly_one_browser(isolated_home):
    backend = _CountingBackend()
    manager = SessionManager(session_state_dir(), backend=backend)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(
            lambda i: manager.ensure(task="research", run_id=f"run-{i}"), range(10)))
    assert len(backend.launch_calls) == 1, "ten asks must not become ten windows"
    launched = [r for r in results if r["action"] == "launched"]
    assert len(launched) == 1, [r["action"] for r in results]
    assert len({r["session_id"] for r in results}) == 1
    # the merged session is the product's OWN named one, never agent-browser's default
    assert backend.launch_calls[0]["session"] == PRODUCT_SESSION_NAME


def test_the_start_counter_is_true_across_processes(isolated_home):
    """The counter lives in the state file, not in one process's memory."""
    dir_ = session_state_dir()
    backend = _CountingBackend()
    SessionManager(dir_, backend=backend).ensure(task="a", run_id="r1")
    second = SessionManager(dir_, backend=_CountingBackend())
    result = second.ensure(task="b", run_id="r2")
    assert result["action"] == "reused"
    assert result["starts"] == 1, "a new process must still report the real start count"
    assert len(backend.launch_calls) == 1  # the second manager never launched


# --------------------------------------------------------------------------- #
# BR-02/VAL-WEB-003: an empty observation is a diagnosis, never a relaunch      #
# --------------------------------------------------------------------------- #


def test_an_empty_observation_marks_unconfirmed_and_starts_nothing(isolated_home,
                                                                  browser_tool):
    tool, backend, _ws = browser_tool
    backend._content = ""  # the session answers but renders nothing (measured: about:blank)

    first = tool.handler(do="read", url="about:blank")
    assert first["ok"] is True
    assert first["state"] == "unconfirmed", first
    assert "diagnosis" in first and first["diagnosis"], first
    assert "never" in first["diagnosis"] or "diagnose" in first["diagnosis"].lower()
    assert len(backend.launch_calls) == 1
    state_file = session_state_dir() / "browser-session.json"
    assert '"unconfirmed"' in state_file.read_text(encoding="utf-8")

    # the next demand does NOT launch to fix the unknown — it reconciles by asking
    # the recorded session whether it is alive (verdict True → no launch)
    second = tool.handler(do="read", url="https://example.invalid/page")
    assert second["session_action"] == "reconciled", second
    assert len(backend.launch_calls) == 1, "an empty observation must never relaunch"
    assert second["state"] == "unconfirmed"  # this read was empty again — honest state


def test_an_unconfirmed_state_with_an_unknown_verdict_refuses_without_starting(
    isolated_home,
):
    backend = _CountingBackend(alive=None)  # the transport cannot say
    manager = SessionManager(session_state_dir(), backend=backend)
    assert manager.ensure(task="t", run_id="r")["action"] == "launched"
    manager.mark_unconfirmed("the start timed out")
    result = manager.ensure(task="t2", run_id="r2")
    assert result["action"] == "unconfirmed"
    assert "diagnose" in result["reason"]
    assert len(backend.launch_calls) == 1


def test_a_diagnosed_dead_session_is_reconciled_not_blindly_retried(isolated_home):
    backend = _CountingBackend(alive=False)
    manager = SessionManager(session_state_dir(), backend=backend)
    assert manager.ensure(task="t", run_id="r")["action"] == "launched"
    manager.mark_unconfirmed("the start timed out")
    result = manager.ensure(task="t2", run_id="r2")
    assert result["action"] == "launched", (
        "after a NEGATIVE liveness verdict the reconciliation has concluded: the state "
        "was cleared, so a new session may be created deliberately")
    assert result["starts"] == 2
    assert len(backend.launch_calls) == 2


def test_a_refused_demand_names_its_diagnosis(isolated_home, browser_tool):
    """The handler's not-ready answer carries the state and the next step, never a crash."""
    tool, backend, _ws = browser_tool
    backend._ok = False  # the launch fails → unconfirmed
    first = tool.handler(do="read", url="https://example.invalid/page")
    assert first["ok"] is False
    assert first["error"] == "browser_session_not_ready"
    assert first["action"] == "unconfirmed"
    assert first["diagnosis"], first
    backend._ok = True
    second = tool.handler(do="read", url="https://example.invalid/page")
    # the failed start stays `unconfirmed` until a diagnosis reconciles it — and with
    # an alive session the verdict lifts it back to ready without any launch
    assert second["session_action"] == "reconciled", second
    assert len(backend.launch_calls) == 1


# --------------------------------------------------------------------------- #
# BR-13: ownership is real — only agent-created tabs are ever closable          #
# --------------------------------------------------------------------------- #


def test_recorded_ownership_separates_agent_tabs_from_user_tabs(isolated_home):
    manager = SessionManager(session_state_dir(), backend=_CountingBackend())
    manager.ensure(task="t", run_id="r1")
    manager.note_agent_tab("t-agent", url="https://example.invalid/agent-page",
                           run_id="r1")
    inventory = manager.discover()
    assert [t["tab_id"] for t in inventory["agent_tabs"]] == ["t-agent"]
    assert inventory["user_tabs"] == []
    report = manager.close_agent_tabs(["t-agent", "t-unknown"])
    assert [t["tab_id"] for t in report["closed"]] == ["t-agent"]
    assert report["closed_all"] is False, "there is no close --all here"
    assert manager.discover()["tab_count"] == 0


def test_a_user_tab_is_refused_not_closed(isolated_home):
    manager = SessionManager(session_state_dir(), backend=_CountingBackend())
    manager.ensure(task="t", run_id="r1")
    with_variants_user_tab = {"tab_id": "t-user", "url": "https://example.invalid/user",
                              "owner": "user"}
    manager.record_tabs([])
    data_file = session_state_dir() / "browser-session.json"
    import json

    data = json.loads(data_file.read_text(encoding="utf-8"))
    data["tabs"] = [with_variants_user_tab]
    manager._write(data)
    report = manager.close_agent_tabs(["t-user"])
    assert report["refused_user_tabs"], report
    assert report["closed"] == []
    assert manager.discover()["tab_count"] == 1, "the user's tab survived"


def test_detach_reports_what_stayed_open(isolated_home):
    from skaldir.browser_layer import BrowserLayer

    manager = SessionManager(session_state_dir(), backend=_CountingBackend())
    manager.ensure(task="t", run_id="r1")
    manager.record_tabs([
        TabInfo(tab_id="t-agent", url="https://example.invalid/agent", owner="agent",
                run_id="r1"),
        TabInfo(tab_id="t-user", url="https://example.invalid/user", owner="user"),
    ])
    layer = BrowserLayer(manager, _CountingBackend(), run_id="r1")
    result = layer.detach()
    assert result.ok is True
    data = result.to_dict().get("data") or {}
    assert data["tabs_left_open"] == 2
    assert data["user_tabs_left_open"] == 1
    assert "foreign browsers were not closed" in result.preview


# --------------------------------------------------------------------------- #
# F08/VAL-WEB-003: the browser tool is in the measured agent toolset, callable   #
# --------------------------------------------------------------------------- #


def test_the_browser_tool_is_offered_by_the_real_agent_toolset(isolated_home, tmp_path):
    """Measured, not assumed: the surfaces a plain run/chat uses offer ``browser``."""
    from skaldir.agent_loop import tool_set_for
    from skaldir.agent_toolset import build_agent_toolset

    ws = tmp_path / "ws"
    ws.mkdir()
    for tools in (
        build_agent_toolset(workspace=ws, runtime=tmp_path / "runtime"),
        tool_set_for(workspace=ws, runtime=tmp_path / "runtime").tools,
    ):
        names = [t.name for t in tools]
        assert names.count("browser") == 1, names
    # and the toolset built without an explicit runtime (the fallback entry) too
    names = [t.name for t in build_agent_toolset(workspace=ws)]
    assert names.count("browser") == 1, names


def test_the_browser_schema_matches_the_handler_and_admits_a_typed_call(
    isolated_home, browser_tool,
):
    """The m3 rule: every declared property type is accepted by the handler, and a
    non-trivial typed argument survives to the transport."""
    import inspect
    import typing

    from skaldir.agent_loop import _argument_problem

    tool, backend, _ws = browser_tool
    schema = dict(tool.input_schema)
    hints = typing.get_type_hints(tool.handler)
    params = inspect.signature(tool.handler).parameters
    for pname, spec in (schema.get("properties") or {}).items():
        assert pname in params, f"{pname} declared but the handler cannot take it"
        if spec.get("type") == "string":
            assert hints.get(pname, params[pname].annotation) is str, pname
    args = {"do": "read", "url": "https://example.invalid/probe-page"}
    assert _argument_problem(schema, args) == "", args
    result = tool.handler(**args)
    assert result["ok"] is True
    assert backend.read_calls[-1]["url"] == args["url"], backend.read_calls
    assert backend.read_calls[-1]["session"] == PRODUCT_SESSION_NAME, (
        "the read must run on the centrally provisioned session, never the default one")
