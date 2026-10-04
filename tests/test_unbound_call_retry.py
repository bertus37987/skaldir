"""BLOCK-M3-03 addendum — a runner TypeError must never widen a run's workspace.

Defect (measured at ba2d46a, audit ``HARNESS-AUSFUEHRUNGSPROZESSE``/BLOCK-M3-03
addendum): ``tools_core.bridge._call`` wrapped the registry seam in
``except TypeError`` and re-ran a failed call WITHOUT the bound root — the
documented intent was "a registry without a ``root`` parameter", but the seam
(``tools_core.registry.call``) takes ``root`` as an explicit keyword argument,
so every TypeError that can reach this handler comes from the *runner's own
code*. The historical proof is real: the dead ``read_file`` revision guard
raised ``TypeError: 'bool' object is not callable`` and the retry then executed
the read again with ``root=None`` — ``resolve_root(None)`` falls back to
``Path.cwd()``, the process cwd, so a run's call silently ran outside its
bound workspace (and could report ``ok=True`` from it).

Pinned here, failing-first:

* a runner that raises TypeError for an unrelated reason runs ONCE, inside the
  bound workspace; the error surfaces (named ``TypeError``) and the process cwd
  is never used as a fallback root (no marker file appears there, and the
  recorded bound root never changes);
* an unbound caller keeps the old behaviour (the TypeError propagates);
* legitimate bound calls keep passing ``root`` to the seam — root-requiring
  registry entries are unaffected by the removal of the retry.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from skaldir.tools_core import bridge
from skaldir.tools_core import files as files_module


def _marker_name() -> str:
    return f"retry-cwd-fallback-{uuid.uuid4().hex}.txt"


def _install_buggy_runner(monkeypatch: pytest.MonkeyPatch, marker: str) -> list[str]:
    """Replace ONLY the runner behind a core tool — the real runner seam.

    The runner is a real handler-shaped function: it records the root it was
    actually bound to, writes its effect marker file there (so the binding is a
    measured effect, not a log line) and then raises TypeError for a reason that
    has nothing to do with a ``root`` parameter — exactly the class of runner
    defect the historical unbound retry swallowed.
    """
    seen_roots: list[str] = []

    def buggy_runner(arguments: dict[str, Any], ctx: Any) -> Any:
        root = Path(ctx.root)
        (root / marker).write_text("retry-probe", encoding="utf-8")
        seen_roots.append(str(root))
        raise TypeError(
            "simulated runner defect: 'bool' object is not callable (not a root problem)"
        )

    monkeypatch.setattr(
        files_module, "RUNNERS", {**files_module.RUNNERS, "read_file": buggy_runner}
    )
    return seen_roots


def _no_tool_workspace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SKALDIR_TOOL_WORKSPACE", raising=False)
    monkeypatch.delenv("GODSV_TOOL_WORKSPACE", raising=False)


def test_a_runner_typeerror_is_never_retried_unbound(tmp_path: Path, monkeypatch):
    """The headline regression: the bound root survives, the cwd is not consumed."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = tmp_path / "rt"
    marker = _marker_name()
    seen_roots = _install_buggy_runner(monkeypatch, marker)
    _no_tool_workspace_env(monkeypatch)

    try:
        with bridge.RunContext(root=workspace, runtime=runtime, run_id="run-a"):
            with pytest.raises(TypeError) as caught:
                bridge.read_file(path="doc.txt")

        assert "simulated runner defect" in str(caught.value), caught.value
        # the call ran once, bound: the retry must not exist — the unbound retry
        # re-ran the runner from the process cwd, which is the recorded bug.
        assert seen_roots == [str(workspace)], seen_roots
        assert (workspace / marker).exists(), "the first bound call must leave its effect"
        assert not Path.cwd().joinpath(marker).exists(), (
            "the process cwd was used as a fallback root — the unbound retry ran"
        )
    finally:
        stray = Path.cwd() / marker
        if stray.exists():
            stray.unlink()


def test_an_unbound_caller_keeps_the_old_typeerror_propagation(tmp_path: Path, monkeypatch):
    """A corrupted runner fails loudly for callers that never bind at all."""
    marker = _marker_name()
    seen_roots = _install_buggy_runner(monkeypatch, marker)
    _no_tool_workspace_env(monkeypatch)

    try:
        with pytest.raises(TypeError):
            bridge.read_file(path="doc.txt")

        assert seen_roots == [str(Path.cwd())], seen_roots
    finally:
        stray = Path.cwd() / marker
        if stray.exists():
            stray.unlink()


def test_a_bound_call_still_passes_a_root_to_the_seam(tmp_path: Path, monkeypatch):
    """Requirement: no behaviour change for registry entries that need a root.

    A probe runner without the defect records exactly what ``_call`` handed to
    the seam: a bound run must still see ``ctx.root`` equal to its workspace —
    removing the retry never weakened the binding itself.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime = tmp_path / "rt"
    observed: dict[str, Any] = {}

    def probe_runner(arguments: dict[str, Any], ctx: Any) -> Any:
        observed["root"] = str(ctx.root)
        observed["runtime"] = str(ctx.runtime)
        from skaldir.tools_core.contract import ToolResult

        return ToolResult(tool="read_file", ok=True, data={"root": str(ctx.root)})

    monkeypatch.setattr(
        files_module, "RUNNERS", {**files_module.RUNNERS, "read_file": probe_runner}
    )

    with bridge.RunContext(root=workspace, runtime=runtime, run_id="run-a"):
        result = bridge.read_file(path="doc.txt")

    assert observed["root"] == str(workspace), observed
    assert observed["runtime"] == str(runtime), observed
    assert result["ok"] is True, result


def test_an_unbound_call_still_runs_in_the_process_cwd(tmp_path: Path, monkeypatch):
    """The no-binding path keeps working (documented legacy behaviour)."""
    marker = _marker_name()
    seen_roots = _install_buggy_runner(monkeypatch, marker)
    monkeypatch.setenv("SKALDIR_TOOL_WORKSPACE", str(tmp_path))

    try:
        with pytest.raises(TypeError):
            bridge.read_file(path="doc.txt")

        assert seen_roots == [str(tmp_path)], seen_roots
    finally:
        stray = tmp_path / marker
        if stray.exists():
            stray.unlink()


# --------------------------------------------------------------------------- #
# the real loop: a runner TypeError surfaces as a named executor error,        #
# and the run's workspace binding is never widened by an unbound retry         #
# --------------------------------------------------------------------------- #


def test_the_real_loop_surfaces_a_runner_typeerror_once_and_bound(
    tmp_path: Path, monkeypatch
):
    """Productive proof over the real agent-loop chain (only the LLM is stubbed).

    The stub model issues a real ``read_file`` call; the real toolset's bridge
    function runs through ``_call`` with the run's bound workspace. A runner
    that raises TypeError must end up as a named failed outcome — executed
    once, inside the bound workspace — never as a second unbound call that
    reads/writes the process cwd.
    """
    from skaldir.agent_loop import STATUS_ANSWERED, AgentLoop, ToolSet, read_transcript
    from skaldir.core_capabilities import core_capability_tools
    from skaldir.llm_provider import OpenAICompatibleLLM, ProviderConfig
    from tests.test_agent_loop import answer, tool_step
    from tests.test_llm_provider import FAKE_KEY, Step, StubProvider

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "doc.txt").write_text("binding probe", encoding="utf-8")
    marker = _marker_name()
    seen_roots = _install_buggy_runner(monkeypatch, marker)
    _no_tool_workspace_env(monkeypatch)

    try:
        steps = [
            Step(body=tool_step(("r1", "read_file", {"path": "doc.txt"}))),
            Step(body=answer("done")),
        ]
        with StubProvider(steps) as stub:
            config = ProviderConfig(
                provider="openai-compatible",
                base_url=stub.base_url,
                model="stub-model",
                timeout_s=10.0,
                max_retries=0,
                api_key=FAKE_KEY,
            )
            loop = AgentLoop(
                OpenAICompatibleLLM(config),
                ToolSet.of(core_capability_tools(workspace=workspace)),
                state_dir=tmp_path / "runtime" / "agent",
                workspace=workspace,
            )
            result = loop.run("runner typeerror probe")

        assert result.status == STATUS_ANSWERED, result.message
        calls = read_transcript(result.transcript_path)["tool_calls"]
        read_file = next(c for c in calls if c["name"] == "read_file")
        assert read_file["executed"] is True, read_file
        assert read_file.get("ok") is not True, read_file
        assert "TypeError" in str(read_file.get("error") or ""), read_file
        # the runner ran exactly once, bound to the run's workspace: no retry,
        # no unbound effect in the process cwd.
        assert seen_roots == [str(workspace)], seen_roots
        assert not Path.cwd().joinpath(marker).exists(), (
            "the process cwd was used as a fallback root through the real loop"
        )
    finally:
        stray = Path.cwd() / marker
        if stray.exists():
            stray.unlink()
