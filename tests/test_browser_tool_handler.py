"""BLOCK-M3-02 — the browser tool handler answers with honest state, not a TypeError.

Defect (commit add4d67, scrutiny m3-harness): ``core_capabilities._browser_tool``
built the browser handler against interfaces that do not exist. Measured at the
defect's HEAD:

* ``SessionManager()`` was constructed without its required ``state_dir``
  positional argument (``browser_layer.py:321``) — every browser tool call raised
  ``TypeError`` from every entry (direct handler call AND a full AgentLoop run);
* on this host ``AgentBrowserBackend().probe().available`` is True, so the guarded
  ``browser_unavailable`` path was never taken — the loop reported the crash as
  ``decision='ALLOW', executed=True`` with the raw ``TypeError``: a crash surfacing
  as success;
* the handler's ``layer.read(url=...)`` / ``layer.act(payload)`` calls did not match
  any real signature either (``BrowserLayer.read`` takes ``tab_id``; the transport
  that matches these shapes is ``AgentBrowserBackend.read``/``act``);
* ``state_dir`` is where the cross-process browser start lock lives, so the missing
  argument was a run-scoping gap, not just a signature slip.

Pinned here, failing-first (measured before the fix), and deliberately hermetic:
the backend is a stub, no browser is launched, no GUI, no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from skaldir.agent_loop import STATUS_ANSWERED, AgentLoop, ToolSet, read_transcript
from skaldir.browser_layer import BackendKind, BrowserCapability
from skaldir.core_capabilities import core_capability_tools
from tests.test_llm_provider import FAKE_KEY, Step, StubProvider


class _StubBrowserBackend:
    """The transport edge, stubbed: the probe answers available, reads answer state.

    The handler under test is the unit; the real agent-browser CLI edge is a process
    boundary and may be stubbed (no browser, no GUI, no network anywhere below). Since
    the handler now drives the central ensure lifecycle (VAL-WEB-003), the stub carries
    the whole transport edge the lifecycle talks to: probe, launch, liveness, tabs,
    read and act.
    """

    kind = BackendKind.AGENT_BROWSER

    def __init__(self) -> None:
        self.launches: list[dict[str, str]] = []

    def probe(self) -> BrowserCapability:
        return BrowserCapability(
            self.kind, available=True, version="stub-1.2.3", binary="/stub/agent-browser"
        )

    def launch(self, *, task: str = "", run_id: str = "", session: str = "") -> dict[str, Any]:
        self.launches.append({"task": task, "run_id": run_id, "session": session})
        return {"ok": True, "backend": self.kind.value, "session_id": session,
                "tabs": [], "detail": "stub launch"}

    def session_alive(self, session_id: str) -> bool | None:
        return bool(session_id)

    def tabs(self, *, session: str = "") -> dict[str, Any]:
        return {"ok": True, "backend": self.kind.value, "tabs": [], "detail": ""}

    def read(self, *, url: str = "", session: str = "") -> dict[str, Any]:
        return {"ok": True, "backend": self.kind.value, "state": "rendered",
                "url": url, "session_id": session, "content": "stub page body"}

    def snapshot(self, *, interactive: bool = True, session: str = "") -> dict[str, Any]:
        return {"ok": True, "backend": self.kind.value, "state": "snapshot",
                "session_id": session, "content": "- button \"Play\" [ref=e1]"}

    def act(self, action: dict[str, Any], *, session: str = "") -> dict[str, Any]:
        return {"ok": True, "backend": self.kind.value, "state": "acted",
                "action": dict(action), "session_id": session}


@pytest.fixture()
def stubbed_backend(monkeypatch: pytest.MonkeyPatch):
    import skaldir.browser_layer as bl

    monkeypatch.setattr(bl, "AgentBrowserBackend", _StubBrowserBackend)


def _browser_tool(ws: Path, rt: Path | None) -> Any:
    tools = core_capability_tools(workspace=ws, runtime=rt, run_id="run-test",
                                  include=("browser",))
    assert [t.name for t in tools] == ["browser"], tools
    return tools[0]


# --------------------------------------------------------------------------- #
# (a) the direct handler call: a real state object, never a TypeError           #
# --------------------------------------------------------------------------- #


def test_browser_handler_returns_its_own_state_not_a_typeerror(
    tmp_path: Path, stubbed_backend
):
    """Failing-first (BLOCK-M3-02): the handler call answered a raw TypeError."""
    rt = tmp_path / "runtime"
    rt.mkdir()
    tool = _browser_tool(tmp_path / "ws", rt)
    result = tool.handler(do="read", url="https://example.invalid/page")
    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["url"] == "https://example.invalid/page"
    assert "TypeError" not in str(result)


def test_browser_act_argument_reaches_the_transport(tmp_path: Path, stubbed_backend):
    """A non-trivial typed argument survives to the transport, not just do=read."""
    tool = _browser_tool(tmp_path / "ws", tmp_path / "runtime")
    result = tool.handler(do="act", kind="click", target="#submit", value="")
    assert result["ok"] is True
    assert result["action"]["kind"] == "click"
    assert result["action"]["target"] == "#submit"


# --------------------------------------------------------------------------- #
# (b) one state dir: the agent path shares the CLI's browser session state      #
#                                                                              #
#     Nachtrag (2026-10-05, feature browser-session-reuse): the run-scoped      #
#     state dir THIS SECTION once pinned turned out to be the divergence —      #
#     `skaldir desk browser` reads <CLI runtime>/browser while the agent wrote  #
#     <run runtime>/browser, so the two could never reuse one session and the   #
#     cross-process start lock was unexercised on the agent path. The handler   #
#     now drives the central ensure lifecycle (browser_layer.session_state_dir) #
#     and the run's own runtime dir is no longer the browser state dir.         #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SKALDIR_HOME", str(home))
    return home


def test_browser_state_dir_is_the_shared_central_resolution(
    tmp_path: Path, stubbed_backend, isolated_home: Path
):
    rt = tmp_path / "runtime"
    tool = _browser_tool(tmp_path / "ws", rt)
    tool.handler(do="read", url="https://example.invalid/page")
    assert (isolated_home / "browser").is_dir(), (
        "the shared browser session state dir was not created"
    )
    assert not (rt / "browser").exists(), (
        "the browser session state must not diverge into the run's own runtime dir — "
        "that divergence is the m3-round-2 gap (one state dir, one start lock, real reuse)"
    )


def test_the_handler_shares_the_session_the_cli_resolution_reads(
    tmp_path: Path, stubbed_backend, isolated_home: Path
):
    tool = _browser_tool(tmp_path / "ws", tmp_path / "runtime")
    tool.handler(do="read", url="https://example.invalid/page")
    from skaldir.browser_layer import SessionManager, session_state_dir

    inventory = SessionManager(session_state_dir()).discover()
    assert inventory["state"] == "ready"
    assert inventory["starts"] == 1, "one handler demand = one launch, counted in state"
    # a second demand (another handler object, same state) reuses — never launches
    tool2 = _browser_tool(tmp_path / "ws2", None)
    result = tool2.handler(do="read", url="https://example.invalid/page2")
    assert result["session_action"] == "reused", result


# --------------------------------------------------------------------------- #
# (c) productive path: a real tool call through the assembled AgentLoop toolset #
# --------------------------------------------------------------------------- #


def test_browser_tool_call_through_the_real_loop_answers_honest_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The loop executes ``browser(do=read, url=...)`` and records the handler's own
    honest state — not a crash, not a fake ``ALLOW`` around a TypeError."""
    import skaldir.browser_layer as bl

    monkeypatch.setattr(bl, "AgentBrowserBackend", _StubBrowserBackend)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SKALDIR_HOME", str(home))

    from tests.test_agent_loop import answer, tool_step
    from tests.test_core_schema_type_mapping import _result_for

    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    state = runtime / "agent"
    state.mkdir(parents=True)
    steps = [
        tool_step(("b1", "browser", {"do": "read",
                                     "url": "https://example.invalid/page"})),
        answer("the browser state is recorded"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        from skaldir.llm_provider import OpenAICompatibleLLM, ProviderConfig

        llm = OpenAICompatibleLLM(ProviderConfig(
            provider="openai-compatible", base_url=stub.base_url, model="stub-model",
            timeout_s=10.0, max_retries=0, api_key=FAKE_KEY,
        ))
        loop = AgentLoop(
            llm,
            ToolSet.of(core_capability_tools(workspace=workspace, runtime=runtime)),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("read the page and tell me the state")

    assert result.status == STATUS_ANSWERED, result.message
    calls = read_transcript(result.transcript_path)["tool_calls"]
    browser_calls = [c for c in calls if c.get("name") == "browser"]
    assert browser_calls, calls
    entry = browser_calls[0]
    assert entry["executed"] is True, entry
    assert entry["ok"] is True, entry
    # the model's own view: the second stub turn carries the browser handler's state
    payload = _result_for(stub.requests[1]["body"], "b1")
    payload = payload["result"] if isinstance(payload.get("result"), dict) else payload
    assert payload["ok"] is True, payload
    assert payload["url"] == "https://example.invalid/page"
    assert "TypeError" not in str(payload)
    # the central lifecycle held on the productive path too: one shared state dir with
    # the CLI's resolution, one counted launch — and NOT the run's own runtime dir
    assert (home / "browser" / "browser-session.json").is_file()
    assert not (runtime / "browser").exists()


def test_browser_act_open_launches_on_desktop(tmp_path: Path, stubbed_backend, monkeypatch):
    """When browser tool is called with act open, it triggers desktop browser launch."""
    import subprocess

    launched: list[list[str]] = []

    def _fake_popen(cmd, **kwargs):
        launched.append(cmd)

        class _FakeProc:
            pid = 9999

        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    # Temporarily remove PYTEST_CURRENT_TEST to simulate live agent run
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    tool = _browser_tool(ws, tmp_path / "runtime")
    result = tool.handler(do="act", kind="open", url="https://www.youtube.com")
    assert result["ok"] is True
    assert ["xdg-open", "https://www.youtube.com"] in launched


def test_browser_snapshot_returns_interactive_elements(tmp_path: Path, stubbed_backend):
    """Calling browser with do=snapshot returns interactive element tree."""
    tool = _browser_tool(tmp_path / "ws", tmp_path / "runtime")
    result = tool.handler(do="snapshot")
    assert result["ok"] is True
    assert "- button \"Play\" [ref=e1]" in result["content"]


def test_browser_act_key_verb_is_accepted_and_routed(tmp_path: Path, stubbed_backend):
    """Calling browser with kind=key or kind=press routes key/value properly."""
    tool = _browser_tool(tmp_path / "ws", tmp_path / "runtime")
    result = tool.handler(do="act", kind="key", target="body", value="CTRL+L")
    assert result["ok"] is True
    assert result["action"]["kind"] == "key"
    assert result["action"]["value"] == "CTRL+L"

    result_press = tool.handler(do="act", kind="press", key="Enter")
    assert result_press["ok"] is True
    assert result_press["action"]["kind"] == "press"
    assert result_press["action"]["value"] == "Enter"


def test_browser_observe_includes_interactive_elements(tmp_path: Path, stubbed_backend):
    """Calling browser with do=observe enriches read result with interactive elements."""
    tool = _browser_tool(tmp_path / "ws", tmp_path / "runtime")
    result = tool.handler(do="observe", url="https://www.youtube.com/watch?v=xyz")
    assert result["ok"] is True
    assert "interactive_elements" in result
    assert "- button \"Play\" [ref=e1]" in result["interactive_elements"]

