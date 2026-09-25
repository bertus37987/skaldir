"""Wave 5 — the tool builder drafts on the caller's configured model client (§1.8).

Measured 17.09. on the real path: ``skaldir tools fulfill cap-e33785b01a`` answered
"no source supplied (builder does not invent code) and no model client is configured for a draft —
inject the existing configured client (ToolBuilder(llm=...))", because every ``ToolBuildRunner``
was constructed without ``llm=`` — including the plugin session's, although that session holds the
host-owned ``ctx.llm`` client and passes it to the research runner right next to it.

``ToolBuilder`` never builds a provider client of its own (§1.8): the caller injects the client it
already has, and "no client" is the caller's blocker to report honestly. These tests pin both
halves: the runner forwards what it was given, and the session gives the builder its own client.
"""

from __future__ import annotations

import json

import godsv_plugin
import pytest

from skaldir.capability import ToolBuildRunner
from skaldir.llm import ScriptedLLM
from skaldir.toolbuilder import ToolBuildRequest

DRAFT = json.dumps(
    {
        "name": "csv_stats_report",
        "title": "CSV stats report",
        "description": "composes a report from the existing dataset statistics",
        "tags": ["report"],
        "kind": "composition",
        "compose_decision": "compose",
        "steps": [{"tool": "dataset_stats", "note": "liefert Kennzahlen"}],
        "rationale": "dataset_stats already covers the numbers",
    }
)


class FakeHostLLM:
    """Stands in for Hermes' host-owned side-LLM (``ctx.llm``)."""

    def complete(self, messages, **kwargs):  # noqa: ARG002 — host signature
        return "ok"


class FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}
        self.llm = FakeHostLLM()

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    godsv_plugin._SESSIONS.clear()
    yield
    for session in list(godsv_plugin._SESSIONS.values()):
        try:
            session.end()
        except Exception:  # noqa: BLE001 — teardown must never mask the test result
            pass
    godsv_plugin._SESSIONS.clear()


class TestTheRunnerForwardsTheClient:
    def test_the_builder_runs_on_the_client_it_was_handed(self, tmp_path):
        client = ScriptedLLM(responses={"tool_spec_draft": DRAFT})
        runner = ToolBuildRunner(runtime=tmp_path, llm=client)
        assert runner.builder.llm is client

    def test_without_a_client_the_caller_s_blocker_is_reported(self, tmp_path):
        builder = ToolBuildRunner(runtime=tmp_path).builder
        result = builder.build(
            ToolBuildRequest(
                capability="resize an image to webp", name="webp_resize", reason="Wave 5"
            )
        )
        assert result["ok"] is False
        assert result.get("blocked") == "no_source_no_client"
        assert "model client" in result["error"].lower()
        assert "draft" not in result          # nothing was invented

    def test_the_draft_really_goes_to_the_injected_client(self, tmp_path):
        client = ScriptedLLM(responses={"tool_spec_draft": DRAFT})
        builder = ToolBuildRunner(runtime=tmp_path, llm=client).builder
        result = builder.build(
            ToolBuildRequest(
                capability="report statistics from csv exports",
                name="stats_report",
                reason="Wave 5",
            )
        )
        assert client.calls, "the draft must be asked of the injected client"
        assert client.calls[0]["prompt_id"] == "tool_spec_draft"
        assert result["draft"]["name"] == "csv_stats_report"
        assert result["draft"]["kind"] == "composition"
        # the measured blocker is gone: a draft exists, the missing source is the remaining gap
        assert "model client" not in result["error"].lower()


class TestTheSessionHandsOverItsOwnClient:
    def test_the_plugin_session_gives_the_tool_builder_its_ctx_llm(self, monkeypatch):
        monkeypatch.setenv("GODSV_LLM", "1")
        monkeypatch.setenv("GODSV_PAUSE", "1")
        seen: dict = {}
        import skaldir.capability as capability

        real = capability.ToolBuildRunner

        class Spy(real):
            def __init__(self, **kwargs):
                seen.update(kwargs)
                super().__init__(**kwargs)

        monkeypatch.setattr(capability, "ToolBuildRunner", Spy)
        godsv_plugin.register(FakeCtx())
        godsv_plugin._session("wave5-wiring")
        assert seen, "the session must build the in-pause tool builder"
        assert seen.get("llm") is not None, seen
        assert type(seen["llm"]).__name__ == "ContextLLM"

    def test_without_the_llm_switch_the_session_reports_no_client(self, monkeypatch):
        """The switch stays a product decision — without it the builder reports the gap."""
        monkeypatch.delenv("GODSV_LLM", raising=False)
        seen: dict = {}
        import skaldir.capability as capability

        real = capability.ToolBuildRunner

        class Spy(real):
            def __init__(self, **kwargs):
                seen.update(kwargs)
                super().__init__(**kwargs)

        monkeypatch.setattr(capability, "ToolBuildRunner", Spy)
        godsv_plugin.register(FakeCtx())
        godsv_plugin._session("wave5-wiring-off")
        assert seen.get("llm") is None
