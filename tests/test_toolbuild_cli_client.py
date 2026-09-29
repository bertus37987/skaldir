"""Wave 5b — the CLI hands the tool builder the configured provider client (§1.8).

``skaldir tools fulfill`` / ``skaldir tools build`` constructed ``ToolBuildRunner`` /
``ToolBuilder`` without ``llm=``, so a provider that WAS configured still answered
``blocked: no_source_no_client``. The CLI now resolves the provider through the shared
``cli_shared.client_and_status`` seam (the same one ``agent_loop_cli`` uses) and injects
it when the provider reports ready; without a configured provider the honest blocker
is kept (no invented client, no 401 noise).
"""

from __future__ import annotations

import argparse

import pytest

from skaldir import cli
from skaldir.capability import STATUS_OPEN, CapabilityRequest, CapabilityStore
from skaldir.llm import ScriptedLLM
from skaldir.toolbuilder import ToolBuilder

DRAFT = (
    '{"name": "csv_stats_report", "title": "CSV stats report", "description":'
    ' "composes a report from the existing dataset statistics", "tags": ["report"],'
    ' "kind": "composition", "compose_decision": "compose",'
    ' "steps": [{"tool": "dataset_stats", "note": "gives the numbers"}],'
    ' "rationale": "dataset_stats already covers the numbers"}'
)

READY = {"ready": True, "provider": "local"}
NOT_READY = {"ready": False, "provider": "none"}


def _args(**kw) -> argparse.Namespace:
    base = {"runtime": "", "project": "", "db": "", "request_id": "", "json": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _open_request(runtime) -> CapabilityRequest:
    store = CapabilityStore(runtime)
    request = store.add(
        CapabilityRequest(
            capability="report statistics from csv exports",
            name="stats_report",
            reason="wiring proof",
            requested_by="cli",
            status=STATUS_OPEN,
        )
    )
    return request


@pytest.fixture()
def ready_provider(monkeypatch):
    """Pretend a ready provider is configured (no real call)."""
    import skaldir.cli_shared as cli_shared

    monkeypatch.setattr(cli_shared, "client_and_status", lambda _a: (object(), READY))


class TestTheCliInjectsTheProviderClient:
    def test_fulfill_wires_the_ready_provider_into_the_runner(
        self, tmp_path, monkeypatch, ready_provider
    ):
        import skaldir.capability as capability

        _open_request(tmp_path)
        seen: dict = {}

        class Spy(capability.ToolBuildRunner):
            def __init__(self, **kwargs):
                seen.update(kwargs)
                super().__init__(**kwargs)

        monkeypatch.setattr(capability, "ToolBuildRunner", Spy)
        cli._cmd_tools_fulfill(_args(runtime=str(tmp_path)))
        assert seen, "fulfill must construct the runner"
        assert seen.get("llm") is not None, seen

    def test_fulfill_without_a_provider_passes_no_client(self, tmp_path, monkeypatch):
        import skaldir.capability as capability
        import skaldir.cli_shared as cli_shared

        _open_request(tmp_path)
        seen: dict = {}

        class Spy(capability.ToolBuildRunner):
            def __init__(self, **kwargs):
                seen.update(kwargs)
                super().__init__(**kwargs)

        monkeypatch.setattr(capability, "ToolBuildRunner", Spy)
        monkeypatch.setattr(cli_shared, "client_and_status", lambda _a: (None, NOT_READY))
        cli._cmd_tools_fulfill(_args(runtime=str(tmp_path)))
        assert seen, "fulfill must construct the runner"
        assert seen.get("llm") is None, seen

    def test_build_wires_the_ready_provider_into_the_builder(
        self, tmp_path, monkeypatch, ready_provider
    ):
        import skaldir.toolbuilder as tb_mod

        seen: dict = {}

        class SpyBuilder(ToolBuilder):
            def __init__(self, runtime, *, store=None, llm=None, **kw):
                seen["llm"] = llm
                # keep the real lazy init (flow store etc.) — the spy only records
                super().__init__(runtime, store=store, llm=llm, **kw)

        monkeypatch.setattr(tb_mod, "ToolBuilder", SpyBuilder)
        cli._cmd_tools_build(
            _args(
                runtime=str(tmp_path), capability="x", reason="", requested_by="",
                kind="", name="", source="", tests="", module="", entrypoint="",
                sample="", research="", research_file=None, secret=[], domain=[],
                dependency=[], validation_case=[],
            )
        )
        assert seen["llm"] is not None, seen

    def test_build_without_a_provider_passes_no_client(self, tmp_path, monkeypatch):
        import skaldir.cli_shared as cli_shared
        import skaldir.toolbuilder as tb_mod

        seen: dict = {}

        class SpyBuilder(ToolBuilder):
            def __init__(self, runtime, *, store=None, llm=None, **kw):
                seen["llm"] = llm
                # keep the real lazy init (flow store etc.) — the spy only records
                super().__init__(runtime, store=store, llm=llm, **kw)

        monkeypatch.setattr(cli_shared, "client_and_status", lambda _a: (None, NOT_READY))
        monkeypatch.setattr(tb_mod, "ToolBuilder", SpyBuilder)
        cli._cmd_tools_build(
            _args(
                runtime=str(tmp_path), capability="x", reason="", requested_by="",
                kind="", name="", source="", tests="", module="", entrypoint="",
                sample="", research="", research_file=None, secret=[], domain=[],
                dependency=[], validation_case=[],
            )
        )
        assert seen["llm"] is None, seen


class TestTheWiredPathActuallyDrafts:
    def test_a_build_with_a_ready_client_reaches_the_draft(self, tmp_path, monkeypatch):
        """End to end: a ready provider means the build no longer stops at
        no_source_no_client — the draft goes to the client (ScriptedLLM here)."""
        import skaldir.cli_shared as cli_shared

        client = ScriptedLLM(responses={"tool_spec_draft": DRAFT})
        monkeypatch.setattr(cli_shared, "client_and_status", lambda _a: (client, READY))
        cli._cmd_tools_build(
            _args(
                runtime=str(tmp_path), capability="report statistics from csv exports",
                name="stats_report", reason="wiring",
                requested_by="", kind="", source="", tests="", module="",
                entrypoint="", sample="", research="", research_file=None,
                secret=[], domain=[], dependency=[], validation_case=[],
                json=True,
            )
        )
        assert client.calls, "the draft must be asked of the wired client"
        assert client.calls[0]["prompt_id"] == "tool_spec_draft"
