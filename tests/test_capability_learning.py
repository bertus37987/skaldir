"""Block 6 — capability learning: semantic generalization, search-before-build,
holdout validation, health lifecycle, live insertion.

These tests are the behavioural contract of the Block-6 loop. They use tmp
HERMES_HOME, real registries and the real builder pipeline — no Blender, no
network, no host roundtrip.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from skaldir.capability import project_id_for
from skaldir.capability_mining import (
    CandidateStore,
    TraceStep,
    generalize_traces,
    mine_from_traces,
)

# --------------------------------------------------------------------------- #
# (1) causal semantic generalization (not string equality)
# --------------------------------------------------------------------------- #

BLENDER_A = [
    TraceStep(tool="terminal", operation="exec", args={"command": "blender A.blend"}),
    TraceStep(tool="terminal", operation="wait", args={"seconds": 8}),
    TraceStep(tool="computer_use", operation="screenshot", args={"path": "/tmp/a.png"}),
    TraceStep(tool="computer_use", operation="inspect", args={"target": "A.blend"}),
]
BLENDER_B = [
    TraceStep(tool="terminal", operation="exec", args={"command": "blender B.blend"}),
    TraceStep(tool="terminal", operation="wait", args={"seconds": 8}),
    TraceStep(tool="computer_use", operation="screenshot", args={"path": "/tmp/b.png"}),
    TraceStep(tool="computer_use", operation="inspect", args={"target": "B.blend"}),
]


class TestSemanticGeneralization:
    def test_two_literal_runs_collapse_to_one_parametric_capability(self):
        """The order-of-magnitude case: A.blend + B.blend → ONE capability."""
        result = generalize_traces([("s1", BLENDER_A), ("s2", BLENDER_B)])
        assert result is not None
        assert result.occurrences == 2
        assert result.sessions == 2
        # not two literals: the varying input is a parameter
        assert "A.blend" not in result.steps_signature
        assert "B.blend" not in result.steps_signature
        assert result.parameters
        assert "project_path" in result.parameters
        assert result.name.startswith("inspect_blender") or "blender" in result.name

    def test_different_shapes_do_not_merge(self):
        """Generalization must not be a bucket that swallows everything."""
        other = [
            TraceStep(tool="web_search", operation="search", args={"query": "weather"}),
            TraceStep(tool="web_search", operation="fetch", args={"url": "https://x/y"}),
        ]
        assert generalize_traces([("s1", BLENDER_A), ("s1", other)]) is None

    def test_single_occurrence_is_not_a_recurring_capability(self):
        assert generalize_traces([("s1", BLENDER_A)]) is None

    def test_constant_steps_survive_as_fixed_positions(self):
        result = generalize_traces([("s1", BLENDER_A), ("s2", BLENDER_B)])
        assert result is not None
        assert len(result.steps_signature.split("|")) == len(BLENDER_A)
        # the wait step is constant in both traces → stays a literal
        assert "wait" in result.steps_signature

    def test_mine_from_traces_records_one_candidate_not_two(self, tmp_path):
        candidates = mine_from_traces([("s1", BLENDER_A), ("s2", BLENDER_B)])
        assert len(candidates) == 1
        row = CandidateStore(tmp_path).merge(candidates[0])
        assert row["occurrences"] == 2
        assert "[" in row["steps_signature"] or "{" in row["steps_signature"]

    def test_parameter_types_are_inferred(self):
        result = generalize_traces([("s1", BLENDER_A), ("s2", BLENDER_B)])
        assert result is not None
        parameter = result.parameters["project_path"]
        assert parameter["type"] in ("path", "string")
        assert parameter["values_sample"]


# --------------------------------------------------------------------------- #
# (2) search-before-build
# --------------------------------------------------------------------------- #


# Distinct capability wording per tool so search-before-build does not (correctly)
# refuse the second tool as a duplicate of the first.
_DOMAINS = {
    "inspect_blender_project": "blender scene",
    "tool_0": "kubernetes rollout",
    "tool_1": "postgres migration",
    "tool_2": "terraform plan",
    "tool_3": "oauth token exchange",
    "tool_4": "graphviz rendering",
    "inspect_blender_project_copy": "blender scene",
    "inspect_blender_project_again": "blender scene",
}


def _build_real_tool(
    runtime: Path, name: str = "inspect_blender_project", project_id: str = ""
):
    from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

    domain = _DOMAINS.get(name, name.replace("_", " "))
    return ToolBuilder(runtime).build(
        ToolBuildRequest(
            capability=f"Inspect the {domain}: open it, screenshot it, verify the state",
            name=name,
            project_id=project_id,
            source_code=(
                "def main(project_path: str = '') -> dict:\n"
                "    return {'project': project_path, 'ok': True}\n"
            ),
            module=name,
            entrypoint="main",
            sample_input={"project_path": "/tmp/x.blend"},
        )
    )


def _runtime_with_project(tmp_path: Path, *, name: str = "inspect_blender_project"):
    """A runtime whose tool carries the real project id of this workspace.

    Learned capabilities are project-scoped by default (§7), so a test that
    wants to discover one must carry the same project context — otherwise the
    scope filter correctly hides it.
    """
    from skaldir.capability import project_id_for

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    project_id = project_id_for(tmp_path)
    build = _build_real_tool(runtime, name=name, project_id=project_id)
    return runtime, project_id, build


class TestSearchBeforeBuild:
    def test_existing_equivalent_capability_is_reused_not_duplicated(self, tmp_path):
        from skaldir.capability_mining import search_before_build

        runtime = tmp_path / "runtime"
        build = _build_real_tool(runtime)
        assert build["ok"] is True, build

        verdict = search_before_build(
            runtime,
            "inspect a blender project and screenshot the viewport",
            name="inspect_blender_project_again",
        )
        assert verdict["decision"] == "reuse"
        assert verdict["match"]["name"] == "inspect_blender_project"
        assert verdict["searched"]  # the search surface is reported, not implied

    def test_duplicate_is_refused_by_the_builder(self, tmp_path):
        """A duplicate build must be rejected, not silently created twice."""
        from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True

        builder = ToolBuilder(runtime)
        result = builder.build(
            ToolBuildRequest(
                capability="Inspect a Blender project: open it, screenshot it, verify state",
                name="inspect_blender_project_copy",
                source_code=(
                    "def main(project_path: str = '') -> dict:\n"
                    "    return {'project': project_path, 'ok': True}\n"
                ),
                module="inspect_blender_project_copy",
                entrypoint="main",
                sample_input={"project_path": "/tmp/y.blend"},
            )
        )
        assert result["ok"] is False
        assert "reuse" in result["error"].lower()
        assert result["reuse_recommended"]["name"] == "inspect_blender_project"

    def test_genuinely_new_capability_still_builds(self, tmp_path):
        from skaldir.capability_mining import search_before_build

        runtime = tmp_path / "runtime"
        _build_real_tool(runtime)
        verdict = search_before_build(runtime, "rotate gpg subkeys and re-encrypt a vault")
        assert verdict["decision"] == "build"
        assert verdict["match"] is None

    def test_search_covers_tools_skills_mcp_testing_and_hub(self, tmp_path):
        from skaldir.capability_mining import search_before_build

        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        skills = tmp_path / "skills" / "blender-recap"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(
            "---\nname: blender-recap\n"
            "description: Inspect a blender project and screenshot it\n---\n",
            encoding="utf-8",
        )
        verdict = search_before_build(
            runtime, "inspect blender project screenshot", skills_dir=tmp_path / "skills"
        )
        assert verdict["decision"] == "reuse"
        assert verdict["match"]["kind"] == "skill"
        assert "skills" in verdict["searched"]


# --------------------------------------------------------------------------- #
# (3) holdout validation
# --------------------------------------------------------------------------- #


HONEST_CHECKER = (
    "import sys\n"
    "from pathlib import Path\n"
    "\n"
    "def check(target: str = '') -> int:\n"
    "    ok = (Path(target) / 'README.md').exists()\n"
    "    print('PASS' if ok else 'FAIL')\n"
    "    return 0 if ok else 1\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    sys.exit(check(sys.argv[1] if len(sys.argv) > 1 else ''))\n"
)


def _plant_checker(runtime: Path, name: str = "check_readme") -> Path:
    tool_dir = runtime / "generated_tools" / name
    tool_dir.mkdir(parents=True)
    (tool_dir / f"{name}.py").write_text(HONEST_CHECKER, encoding="utf-8")
    (tool_dir / "manifest.yaml").write_text(
        f"name: {name}\n"
        "type: testing_tool\n"
        "status: ready\n"
        "purpose: prove a target ships a README\n"
        "validation:\n"
        "  status: validated\n"
        "  checks:\n"
        "  - case: broken\n"
        "    ok: true\n",
        encoding="utf-8",
    )
    return tool_dir


def _make_working(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("hi", encoding="utf-8")
    return root


def _make_broken(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return root


class TestHoldoutValidation:
    def test_testing_tool_passes_working_and_fails_broken_target(self, tmp_path):
        from skaldir.capability import register_testing_tools
        from skaldir.capability_mining import validate_testing_tool_holdout
        from skaldir.completion import execute_check

        runtime = tmp_path / "runtime"
        _plant_checker(runtime)
        assert register_testing_tools(runtime) == ["check_readme"]

        result = validate_testing_tool_holdout(
            lambda target: (execute_check("check_readme", target) is not None)
            and bool(execute_check("check_readme", target).ok),
            [
                (_make_working(tmp_path / "working"), True),
                (_make_broken(tmp_path / "broken"), False),
            ],
        )
        assert result["status"] == "validated", result
        assert all(check["ok"] for check in result["checks"])

    def test_a_lying_checker_cannot_be_validated(self, tmp_path):
        from skaldir.capability_mining import validate_testing_tool_holdout

        result = validate_testing_tool_holdout(
            lambda target: {"passed": True, "detail": "always"},
            [(tmp_path / "broken", False)],
        )
        assert result["status"] == "invalid"
        assert result["checks"][0]["ok"] is False

    def test_holdout_examples_must_differ_from_synthesis_examples(self):
        from skaldir.capability_mining import holdout_ok

        assert holdout_ok(["a", "b"], ["c", "d"]) is True
        assert holdout_ok(["a", "b"], ["a", "b"]) is False
        assert holdout_ok(["a"], ["a", "c"]) is False  # overlap is a fail
        assert holdout_ok(["a", "b"], []) is False  # no holdout at all is a fail

    def test_general_tool_answers_an_unseen_input_with_structured_output(self, tmp_path):
        from skaldir.capability_mining import run_general_tool_holdout

        runtime, project_id, build = _runtime_with_project(tmp_path)
        assert build["ok"] is True, build
        name = build["tool"]
        # the synthesis examples never contained this path
        result = run_general_tool_holdout(
            runtime,
            name,
            project_id=project_id,
            inputs=[{"project_path": "/never/seen/before.blend"}],
            expected_keys={"project", "ok"},
        )
        assert result["status"] == "validated", result
        assert result["checks"][0]["output"]["project"] == "/never/seen/before.blend"

    def test_builder_refuses_holdout_that_reuses_synthesis_examples(self, tmp_path):
        from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

        runtime = tmp_path / "runtime"
        working = _make_working(tmp_path / "same")
        result = ToolBuilder(runtime).build(
            ToolBuildRequest(
                capability="check readme holdout",
                kind="testing_tool",
                name="check_holdout",
                module="check_holdout",
                entrypoint="check",
                source_code=HONEST_CHECKER,
                # synthesis case IS the validation case → no holdout
                validation_cases=[(str(working), True), (str(working), True)],
                requested_by="test",
            )
        )
        assert result["ok"] is False
        assert "holdout" in result["error"].lower()


# --------------------------------------------------------------------------- #
# (4) health lifecycle
# --------------------------------------------------------------------------- #


class TestHealthLifecycle:
    def test_lifecycle_states_are_complete_and_ordered(self):
        from skaldir import capability_health as health

        for state in (
            "CANDIDATE",
            "BUILDING",
            "VERIFIED",
            "HEALTHY",
            "DEGRADED",
            "BROKEN",
            "QUARANTINED",
            "RETIRED",
        ):
            assert state in health.STATES, state
        # the auto-routing predicate: only these may be auto-routed
        assert health.AUTO_ROUTABLE == {"HEALTHY", "VERIFIED"}

    def test_verified_tool_becomes_healthy_and_is_routed(self, tmp_path):
        from skaldir import capability_health as health
        from skaldir.capability_mining import route_candidates

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True
        state = health.record_verification(runtime, "inspect_blender_project", ok=True)
        assert state == "HEALTHY"
        routed = route_candidates(runtime, "inspect blender project screenshot")
        assert [entry["name"] for entry in routed] == ["inspect_blender_project"]

    @pytest.mark.parametrize(
        "trigger",
        ["wrong_output", "timeout", "auth_error", "schema_mismatch", "false_pass"],
    )
    def test_degradation_triggers_take_the_tool_out_of_auto_routing(self, tmp_path, trigger):
        from skaldir import capability_health as health
        from skaldir.capability_mining import route_candidates

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        assert route_candidates(runtime, "inspect blender project")

        row = health.record_trigger(
            runtime, "inspect_blender_project", trigger, detail=f"observed {trigger}"
        )
        assert row["state"] in ("DEGRADED", "BROKEN", "QUARANTINED")
        assert row["reasons"]
        # out of auto-routing until re-verified
        assert route_candidates(runtime, "inspect blender project") == []

    def test_quarantine_is_reached_after_repeated_breakage(self, tmp_path):
        from skaldir import capability_health as health

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        for _ in range(health.QUARANTINE_AFTER):
            row = health.record_trigger(runtime, "inspect_blender_project", "timeout")
            assert row["state"] != "QUARANTINED"
        row = health.record_trigger(runtime, "inspect_blender_project", "timeout")
        assert row["state"] == "QUARANTINED"
        assert health.file_state(runtime, "inspect_blender_project")["state"] == "QUARANTINED"

    def test_quarantined_tool_can_be_retested_and_repaired(self, tmp_path):
        from skaldir import capability_health as health
        from skaldir.capability_mining import route_candidates

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        for _ in range(health.QUARANTINE_AFTER + 1):
            health.record_trigger(runtime, "inspect_blender_project", "false_pass")
        assert route_candidates(runtime, "inspect blender project") == []

        retest = health.retest(runtime, "inspect_blender_project")
        assert retest["state"] == "VERIFIED"
        repaired = health.record_verification(runtime, "inspect_blender_project", ok=True)
        assert repaired == "HEALTHY"
        assert [e["name"] for e in route_candidates(runtime, "inspect blender project")] == [
            "inspect_blender_project"
        ]

    def test_code_change_invalidates_the_verification_evidence(self, tmp_path):
        from skaldir import capability_health as health

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        source = (
            runtime
            / "generated_tools"
            / "inspect_blender_project"
            / "inspect_blender_project.py"
        )
        before = health.file_state(runtime, "inspect_blender_project")["digest"]
        source.write_text(source.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        row = health.recheck(runtime, "inspect_blender_project")
        assert row["state"] in ("DEGRADED", "BROKEN")
        assert row["digest"] != before

    def test_retired_tool_is_never_routed(self, tmp_path):
        from skaldir import capability_health as health

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        health.retire(runtime, "inspect_blender_project")
        assert health.file_state(runtime, "inspect_blender_project")["state"] == "RETIRED"

    def test_health_survives_a_fresh_process_view(self, tmp_path):
        from skaldir import capability_health as health

        runtime = tmp_path / "runtime"
        assert _build_real_tool(runtime)["ok"] is True
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        health.record_trigger(runtime, "inspect_blender_project", "auth_error")
        # nothing cached in memory: a new reader sees the same state
        fresh = health.file_state(runtime, "inspect_blender_project")
        assert fresh["state"] == "DEGRADED"
        assert any("auth_error" in reason for reason in fresh["reasons"])


# --------------------------------------------------------------------------- #
# (5) live insertion — same process, no restart
# --------------------------------------------------------------------------- #


class TestLiveInsertion:
    def test_verified_capability_is_discoverable_and_callable_in_the_same_process(
        self, tmp_path
    ):
        from skaldir.capability_mining import route_candidates
        from skaldir.liveinsert import CapabilityIndex, discover_tool_call

        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        project_id = project_id_for(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        # a view of the running server, bound BEFORE the capability exists
        server_handle = index.runtime_view()
        assert server_handle.list_tools() == []

        build = _build_real_tool(runtime, project_id=project_id)
        assert build["ok"] is True, build
        inserted = index.insert(build["tool"], capability="inspect a blender project")
        assert inserted["inserted"] is True, inserted
        assert inserted["hot"] is True  # in the relevant HOT set

        # same process, same view, no restart: real discovery
        assert "inspect_blender_project" in server_handle.list_tools()
        # and a real tool call returns a structured result
        call = discover_tool_call(
            index, "inspect_blender_project", {"project_path": "/tmp/live.blend"}
        )
        assert call["ok"] is True, call
        assert call["result"]["project"] == "/tmp/live.blend"
        assert route_candidates(runtime, "inspect blender project")[0]["name"] == (
            "inspect_blender_project"
        )

    def test_hot_set_is_capped_at_three_new_capabilities(self, tmp_path):
        from skaldir.liveinsert import HOT_LIMIT, CapabilityIndex

        assert HOT_LIMIT == 3
        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        project_id = project_id_for(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        for i in range(5):
            _build_real_tool(runtime, name=f"tool_{i}", project_id=project_id)
            inserted = index.insert(f"tool_{i}", capability=f"capability number {i}")
            assert inserted["inserted"] is True, inserted
        assert len(index.hot_set()) == HOT_LIMIT
        # the newest three — not an arbitrary five
        assert index.hot_set() == ["tool_4", "tool_3", "tool_2"]

    def test_degraded_capability_leaves_the_hot_set(self, tmp_path):
        from skaldir import capability_health as health
        from skaldir.liveinsert import CapabilityIndex

        runtime, project_id, build = _runtime_with_project(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        assert index.insert(build["tool"], capability="inspect a blender project")["inserted"]
        assert index.hot_set() == ["inspect_blender_project"]
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        health.record_trigger(runtime, "inspect_blender_project", "timeout")
        assert index.still_available("inspect_blender_project") is False

    def test_live_insertion_happens_at_a_safe_boundary_only(self, tmp_path):
        """Never mid-call: the index applies pending insertions at a boundary."""
        from skaldir.liveinsert import CapabilityIndex

        runtime, project_id, build = _runtime_with_project(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        assert index.insert(build["tool"], capability="inspect a blender project")["inserted"]
        view = index.runtime_view()
        # a running side effect is in flight → the boundary is not safe yet
        index.begin_call("inspect_blender_project")
        assert index.apply_pending(view)["applied"] == []
        index.end_call("inspect_blender_project")
        applied = index.apply_pending(view)
        assert applied["applied"] == ["inspect_blender_project"]

    def test_insertion_reports_a_real_error_for_an_unknown_tool(self, tmp_path):
        from skaldir.liveinsert import CapabilityIndex

        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        index = CapabilityIndex(runtime)
        result = index.insert("does_not_exist", capability="nothing")
        assert result["inserted"] is False
        assert result["reason"]


# --------------------------------------------------------------------------- #
# MCP surface — the actual host-facing tool discovery
# --------------------------------------------------------------------------- #


def test_mcp_server_lists_the_live_inserted_tool(tmp_path):
    """The host-facing surface: same server object, no restart."""
    pytest.importorskip("fastmcp", reason="MCP tests need the extra: uv pip install 'godsv[mcp]'")
    from fastmcp import Client

    from skaldir.liveinsert import CapabilityIndex

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    project_id = project_id_for(tmp_path)
    index = CapabilityIndex(runtime, project_id=project_id)
    server = index.mcp_server(project_id=project_id)

    async def listing():
        async with Client(server) as client:
            return {tool.name for tool in await client.list_tools()}

    before = asyncio.run(listing())
    build = _build_real_tool(runtime, project_id=project_id)
    assert build["ok"] is True, build
    inserted = index.insert(build["tool"], capability="inspect a blender project")
    assert inserted["inserted"] is True, inserted
    assert inserted["servers_updated"], "the live server must have been updated"
    after = asyncio.run(listing())
    assert "inspect_blender_project" in after
    assert set(before) <= set(after)


# --------------------------------------------------------------------------- #
# (6) host wiring — sync at a boundary, health-gated selection, native surface
# --------------------------------------------------------------------------- #


class TestHostWiringSync:
    """§1.3/§1.6: the boundary sync picks up another process's build, health acts."""

    def test_sync_registry_inserts_a_tool_the_builder_wrote_before_the_sync(self, tmp_path):
        from skaldir.liveinsert import CapabilityIndex, discover_tool_call

        runtime, project_id, build = _runtime_with_project(tmp_path)
        assert build["ok"] is True, build
        # the index was bound BEFORE the tool existed — exactly the live case
        index = CapabilityIndex(runtime, project_id=project_id)
        view = index.runtime_view()
        assert index.inserted() == []

        report = index.sync_registry()
        assert report["inserted"] == ["inspect_blender_project"], report
        assert report["blocked"] == {}
        assert index.hot_set() == ["inspect_blender_project"]
        assert view.has("inspect_blender_project")
        call = discover_tool_call(
            index, "inspect_blender_project", {"project_path": "/tmp/sync.blend"}
        )
        assert call["ok"] is True, call
        assert call["result"]["project"] == "/tmp/sync.blend"

    def test_sync_registry_is_idempotent(self, tmp_path):
        from skaldir.liveinsert import CapabilityIndex

        runtime, project_id, _build = _runtime_with_project(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        first = index.sync_registry()
        assert first["inserted"] == ["inspect_blender_project"]
        second = index.sync_registry()
        assert second["inserted"] == []
        assert index.inserted() == ["inspect_blender_project"]

    def test_sync_registry_names_a_quarantined_tool_instead_of_routing_it(self, tmp_path):
        """A capability the health ledger excluded is reported, never inserted."""
        from skaldir import capability_health as health
        from skaldir.liveinsert import CapabilityIndex

        runtime, project_id, _build = _runtime_with_project(tmp_path)
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        for _ in range(health.QUARANTINE_AFTER + 1):
            health.record_trigger(runtime, "inspect_blender_project", "false_pass")
        assert health.file_state(runtime, "inspect_blender_project")["state"] == "QUARANTINED"

        index = CapabilityIndex(runtime, project_id=project_id)
        report = index.sync_registry()
        assert report["inserted"] == []
        assert "inspect_blender_project" in report["blocked"]
        assert "QUARANTINED" in report["blocked"]["inspect_blender_project"]
        assert index.inserted() == []

    def test_sync_registry_prefers_the_newest_build_for_the_hot_set(self, tmp_path):
        """The hot set is 'most recently built', never 'alphabetically last'."""
        from skaldir.liveinsert import HOT_LIMIT, CapabilityIndex

        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        project_id = project_id_for(tmp_path)
        for index_name in ("alpha", "bravo", "charlie"):
            assert _build_real_tool(runtime, name=index_name, project_id=project_id)["ok"] is True
        # built LAST but sorts LAST: only recency can put it into the hot set
        assert _build_real_tool(runtime, name="zulu_new", project_id=project_id)["ok"] is True

        index = CapabilityIndex(runtime, project_id=project_id)
        report = index.sync_registry()
        assert "zulu_new" in report["inserted"]
        assert len(index.hot_set()) == HOT_LIMIT
        assert "zulu_new" in index.hot_set(), index.hot_set()

    def test_mcp_surface_hides_a_quarantined_tool_and_keeps_the_audit_view(self, tmp_path):
        """§1.6: health acts on the exported surface; the audit view stays explicit."""
        from skaldir import capability_health as health
        from skaldir.mcpserver import approved_python_specs
        from skaldir.tools import ToolStore

        runtime, project_id, _build = _runtime_with_project(tmp_path)
        store = ToolStore(runtime / "tools.db")
        try:
            def exported(**kwargs):
                return [
                    spec.name
                    for spec in approved_python_specs(
                        store, runtime=runtime, project_id=project_id, include_legacy=True, **kwargs
                    )
                ]

            assert exported() == ["inspect_blender_project"]
            health.record_verification(runtime, "inspect_blender_project", ok=True)
            for _ in range(health.QUARANTINE_AFTER + 1):
                health.record_trigger(runtime, "inspect_blender_project", "false_pass")
            assert health.file_state(runtime, "inspect_blender_project")["state"] == "QUARANTINED"
            assert exported() == []
            assert exported(apply_health=False) == ["inspect_blender_project"]
        finally:
            store.close()


class TestNativeSurface:
    """§1.3: the schema/handler a host registers for one live capability."""

    def test_native_schema_derives_its_properties_from_the_build_sample(self, tmp_path):
        from skaldir.liveinsert import native_schema

        runtime, project_id, build = _runtime_with_project(tmp_path)
        from skaldir.liveinsert import _approved_specs

        spec = _approved_specs(runtime, project_id)["inspect_blender_project"]
        schema = native_schema(spec)
        assert schema["name"] == "inspect_blender_project"
        assert schema["description"]
        assert schema["parameters"]["properties"]["project_path"]["type"] == "string"
        assert schema["parameters"]["additionalProperties"] is True

    def test_native_handler_calls_through_the_same_boundary_and_returns_a_string(self, tmp_path):
        from skaldir.liveinsert import CapabilityIndex, native_handler

        runtime, project_id, _build = _runtime_with_project(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        assert index.sync_registry()["inserted"] == ["inspect_blender_project"]
        handler = native_handler(index, "inspect_blender_project")
        result = handler({"project_path": "/tmp/native.blend"})
        assert isinstance(result, str)  # the registry dispatch contract wants a string
        payload = json.loads(result)
        assert payload["project"] == "/tmp/native.blend"
        # the call was bracketed: nothing is left in flight afterwards
        assert index.in_flight() == []

    def test_native_handler_reports_a_degraded_capability_as_structured_error(self, tmp_path):
        from skaldir import capability_health as health
        from skaldir.liveinsert import CapabilityIndex, native_handler

        runtime, project_id, _build = _runtime_with_project(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        index.sync_registry()
        health.record_verification(runtime, "inspect_blender_project", ok=True)
        health.record_trigger(runtime, "inspect_blender_project", "timeout")
        result = json.loads(native_handler(index, "inspect_blender_project")({}))
        assert "error" in result
        assert "not available" in result["error"]

    def test_surface_plan_keeps_the_newest_three_and_never_drops_an_in_flight_call(self, tmp_path):
        from skaldir.liveinsert import HOT_LIMIT, CapabilityIndex, surface_plan

        runtime = tmp_path / "runtime"
        runtime.mkdir(parents=True)
        project_id = project_id_for(tmp_path)
        index = CapabilityIndex(runtime, project_id=project_id)
        for i in range(4):
            _build_real_tool(runtime, name=f"tool_{i}", project_id=project_id)
            assert index.insert(f"tool_{i}", capability=f"capability {i}")["inserted"] is True

        plan = surface_plan(index, live=["tool_0", "tool_1", "tool_2"])
        assert plan["add"] == ["tool_3"]
        assert plan["remove"] == ["tool_0"]  # the oldest left the hot set
        assert plan["wanted"] == ["tool_3", "tool_2", "tool_1"]
        assert len(plan["wanted"]) == HOT_LIMIT

        # an in-flight capability survives its eviction — a running call keeps it
        index.begin_call("tool_0")
        protected = surface_plan(index, live=["tool_1", "tool_2", "tool_3"])
        assert "tool_0" in protected["wanted"]
        assert "tool_0" not in protected["remove"]
