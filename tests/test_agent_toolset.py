"""AGENT toolset — all 8 layers + builtin+registry as AgentLoop tools.

Nothing faked: each tool wraps the real service with typed arguments, and every
failure surfaces as an honest tool error. No provider inference, no containers.
"""

from __future__ import annotations

from pathlib import Path

from skaldir.agent_loop import builtin_tools
from skaldir.tools import ToolStore


def test_modules_exports_agent_tools():
    import skaldir.agent_toolset as at

    assert callable(at.build_agent_toolset)


def test_build_agent_toolset_names(tmp_path: Path):
    import skaldir.agent_toolset as at

    names = {tool.name for tool in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    expected = {
        "skill_expand",
        "skill_act",
        "skill_compose",
        "skill_verify",
        "read_text",
        "list_dir",
        "sha256_file",
        "write_text",
    }
    missing = expected - names
    assert not missing, f"missing: {missing}"
    assert "skill_verify" in names


def test_verify_layer_typed(tmp_path: Path):
    import skaldir.agent_toolset as at

    tools = {tool.name: tool for tool in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    vt = tools["skill_verify"]
    assert vt.input_schema.get("type") == "object"


def test_verify_tool_unknown_not_ok(tmp_path: Path):
    import skaldir.agent_toolset as at

    tools = {tool.name: tool for tool in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    handler = tools["skill_verify"].handler
    record = handler(criterion="the package is installed", workspace=str(tmp_path))
    assert record["ok"] is False
    assert record["verdict"] == "UNKNOWN"


def test_act_tool_honest(tmp_path: Path):
    import skaldir.agent_toolset as at

    tools = {tool.name: tool for tool in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    handler = tools["skill_act"].handler
    record = handler(actions=[{"kind": "cli", "argv": ["true"],
                               "verify": {"expect_stdout_contains": ""}}])
    # no real bridge on CI: the record must be honest, not a fake pass
    assert record["status"] in ("verified", "verification_failed", "blocked", "usage")


def test_act_check_tool(tmp_path: Path):
    import skaldir.agent_toolset as at

    tools = {tool.name: tool for tool in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    report = tools["skill_act_check"].handler()
    assert "capabilities" in report


def test_expand_tool_matches(tmp_path: Path):
    import skaldir.agent_toolset as at
    from skaldir.tools import ToolSpec

    store = ToolStore(str(tmp_path / "tools.db"))
    store.upsert(ToolSpec(
        name="alpha_lookup", title="alpha lookup",
        description="looks up alpha records",
        tags=["lookup"], status="approved"))
    tools = {tool.name: tool for tool in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    candidates = tools["skill_expand"].handler(
        capability="alpha record lookup", workspace=str(tmp_path))
    assert candidates["decision"] in ("refuse", "hint", "clear")
    assert isinstance(candidates["candidates"], list)
    # the registry entry above overlaps, so it is seen (>=1 shared term)
    assert any(c["tool"] == "alpha_lookup" for c in candidates["candidates"])


def test_compose_tool(tmp_path: Path):
    import skaldir.agent_toolset as at
    from skaldir.tools import ToolSpec

    store = ToolStore(str(tmp_path / "tools.db"))
    store.upsert(ToolSpec(name="alpha_lookup", title="alpha lookup", description="python",
                          tags=["python"], status="approved", kind="python",
                          module="alpha_lookup", entrypoint="run"))
    tools = {tool.name: tool for tool in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    report = tools["skill_compose"].handler(
        payload={"name": "alpha_pipeline", "version": "1",
                 "steps": [{"id": "step1", "kind": "tool", "target": "alpha_lookup",
                            "outputs": ["out"], "precondition": "always",
                            "end_state": "always"}]})
    assert isinstance(report, dict)
    assert report["ok"] is True
    assert report["registered"] is True


def test_registry_tools_present(tmp_path: Path):
    import skaldir.agent_toolset as at
    from skaldir.tools import ToolSpec

    store = ToolStore(str(tmp_path / "tools.db"))
    builtin_names = {t.name for t in builtin_tools(str(tmp_path))}
    store.upsert(ToolSpec(name="pycalc", title="pycalc", description="adds numbers",
                          module="pypack", entrypoint="add", status="approved", kind="python"))
    names = {tool.name for tool in at.build_agent_toolset(
        workspace=str(tmp_path), kind="both", runtime=str(tmp_path), store=store)}
    assert builtin_names <= names
    assert "pycalc" in names


def test_verify_tool_pass(tmp_path: Path):
    """A criterion with a real, passing check is a PASS with ok=True."""
    import skaldir.agent_toolset as at

    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    (tmp_path / "artifact.txt").write_text("real bytes")
    record = tools["skill_verify"].handler(
        criteria=[{"criterion": "the artifact exists", "check": "artifact",
                   "paths": ["artifact.txt"], "min_bytes": 4}],
        workspace=str(tmp_path),
    )
    assert record["verdict"] == "PASS"
    assert record["ok"] is True


def test_verify_tool_fail(tmp_path: Path):
    import skaldir.agent_toolset as at

    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    record = tools["skill_verify"].handler(
        criteria=[{"criterion": "the artifact exists", "check": "artifact",
                   "paths": ["missing.txt"], "min_bytes": 4}],
        workspace=str(tmp_path),
    )
    assert record["verdict"] == "FAIL"
    assert record["ok"] is False


def test_verify_flows_tool_unknown_when_undeclared(tmp_path: Path):
    """No .godsv/flows.json — no flow ran: UNKNOWN, ok False (never a pass)."""
    import skaldir.agent_toolset as at

    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    record = tools["skill_verify_flows"].handler(workspace=str(tmp_path))
    assert record["verdict"] == "UNKNOWN"
    assert record["ok"] is False


def test_act_runner_falls_back_to_named_sandbox_env(tmp_path: Path, monkeypatch):
    """/SKALDIR_CU_SANDBOX names the runner an unnamed ACT run uses."""
    import skaldir.agent_toolset as at

    monkeypatch.setenv("SKALDIR_CU_SANDBOX", "sandbox")
    monkeypatch.setenv("SKALDIR_ACT_POLICIES", "cli")
    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    record = tools["skill_act"].handler(
        actions=[{"kind": "cli", "argv": ["echo", "agent-toolset"],
                  "verify": {"expect_stdout_contains": "agent-toolset"}}],
    )
    assert record["runner_kind"] == "sandbox"
    assert "cli" in record["allowed_kinds"]


def test_act_policy_kind_validation(tmp_path: Path):
    """A kind outside the policy stops before anything runs (usage, not executed)."""
    import skaldir.agent_toolset as at

    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    record = tools["skill_act"].handler(
        actions=[{"kind": "cli", "argv": ["true"],
                  "verify": {"expect_stdout_contains": ""}}],
        runner="no-such-runner",
    )
    assert record["status"] in ("blocked", "usage")


def test_expand_env_topn(tmp_path: Path, monkeypatch):
    """SKALDIR_TOOLBUILDER_TOPN tunes the candidate cap (legacy twin accepted)."""
    import skaldir.agent_toolset as at

    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    assert tools["skill_expand"].handler(capability="alpha lookup")["topn"] == 5
    monkeypatch.setenv("SKALDIR_TOOLBUILDER_TOPN", "2")
    assert tools["skill_expand"].handler(capability="alpha lookup")["topn"] == 2
    # the legacy GODSV_* twin keeps working (env.py rule: presence decides)
    monkeypatch.delenv("SKALDIR_TOOLBUILDER_TOPN")
    monkeypatch.setenv("GODSV_TOOLBUILDER_TOPN", "9")
    assert tools["skill_expand"].handler(capability="alpha lookup")["topn"] == 9
    # a non-integer is not a cap: the default wins rather than crashing
    monkeypatch.delenv("GODSV_TOOLBUILDER_TOPN")
    monkeypatch.setenv("SKALDIR_TOOLBUILDER_TOPN", "bogus")
    assert tools["skill_expand"].handler(capability="alpha lookup")["topn"] == 5


def test_expand_build_refuses_unknown_fields(tmp_path: Path):
    import skaldir.agent_toolset as at

    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    result = tools["skill_expand_build"].handler(request={"not_a_field": True})
    assert result["ok"] is False
    assert "not_a_field" in result["error"]


def test_compose_tool_blocks_bad_composite(tmp_path: Path):
    """A composite with a blocking problem is NOT registered (validated path only)."""
    import skaldir.agent_toolset as at

    tools = {t.name: t for t in at.build_agent_toolset(
        workspace=str(tmp_path), runtime=str(tmp_path))}
    report = tools["skill_compose"].handler(
        payload={"name": "alpha_pipeline", "version": "1",
                 "steps": [{"id": "s1", "kind": "not_a_kind", "target": "",
                            "outputs": [], "precondition": "always", "end_state": "always"}]})
    assert report["ok"] is False
    assert report["registered"] is False


def test_registry_tool_runs(tmp_path: Path):
    """A registered python tool, exposed to the loop, is runnable in the sandbox."""
    import skaldir.agent_toolset as at
    from skaldir.tools import ToolSpec

    store = ToolStore(str(tmp_path / "tools.db"))
    module = tmp_path / "generated_tools" / "pycalc"
    module.mkdir(parents=True)
    (module / "pycalc.py").write_text(
        "def add(a: int, b: int) -> dict:\n"
        '    return {"sum": a + b}\n'
    )
    (module / "manifest.yaml").write_text(
        "name: pycalc\nversion: 0.1.0\nscope: global\ntype: python\n",
        encoding="utf-8",
    )
    spec = ToolSpec(name="pycalc", title="pycalc", description="adds numbers",
                    module="pycalc", entrypoint="add",
                    test_command=[], sample_input={}, status="approved", kind="python")
    store.upsert(spec)
    tools = {tool.name: tool for tool in at.build_agent_toolset(
        workspace=str(tmp_path), kind="both", runtime=str(tmp_path), store=store)}
    result = tools["pycalc"].handler(a=2, b=3)
    assert result["sum"] == 5
