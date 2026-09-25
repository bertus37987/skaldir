"""§7/§8: MCP-Export ist scope-aware.

Rot-first gegen die ks. Strecke: vorher nahm ``approved_python_specs`` /
``build_server`` keine Scope-Parameter — Projekt-Tools fremder Projekte
konnten nie gefiltert werden.
"""

import asyncio
from pathlib import Path

import pytest

from skaldir.mcpserver import approved_python_specs, build_server
from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore

pytest.importorskip("fastmcp", reason="MCP-Tests brauchen das Extra: uv pip install 'godsv[mcp]'")

MODULE = "def echo(text: str = '') -> dict:\n    return {'echo': text}\n"


def _write_manifest(runtime: Path, name: str, *, scope: str = "", project_id: str = "") -> None:
    d = runtime / "generated_tools" / name
    d.mkdir(parents=True, exist_ok=True)
    lines = ["status: ready", "type: testing", "version: 1"]
    if scope:
        lines.append(f"scope: {scope}")
    if project_id:
        lines.append(f"project_id: {project_id}")
    (d / "manifest.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    module_path = tmp_path / "scopetool.py"
    module_path.write_text(MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _write_manifest(runtime, "glob_tool", scope="global")
    _write_manifest(runtime, "a_tool", scope="project", project_id="proj-a")
    _write_manifest(runtime, "leg_tool")  # legacy: kein scope

    store = ToolStore(tmp_path / "tools.db")
    for name in ("glob_tool", "a_tool", "leg_tool", "draft_tool"):
        store.upsert(
            ToolSpec(
                name=name,
                title=name,
                description="x",
                module="scopetool",
                entrypoint="echo",
                status=STATUS_APPROVED if name != "draft_tool" else "proposed",
            )
        )
    yield store, runtime
    store.close()


async def _names(server) -> set[str]:
    from fastmcp import Client

    async with Client(server) as client:
        return {t.name for t in await client.list_tools()}


def _names_sync(server) -> set[str]:
    return asyncio.run(_names(server))


def test_without_runtime_the_surface_is_unchanged(rig):
    store, _runtime = rig
    specs = approved_python_specs(store)
    assert {s.name for s in specs} == {"glob_tool", "a_tool", "leg_tool"}


def test_context_free_export_only_exposes_global(rig):
    store, runtime = rig
    server = build_server(store, runtime=runtime)
    assert _names_sync(server) == {"glob_tool"}


def test_project_export_includes_own_tools_and_global(rig):
    store, runtime = rig
    server = build_server(store, runtime=runtime, project_id="proj-a")
    assert _names_sync(server) == {"glob_tool", "a_tool"}


def test_other_project_tools_never_leak(rig):
    store, runtime = rig
    server = build_server(store, runtime=runtime, project_id="proj-b")
    assert _names_sync(server) == {"glob_tool"}


def test_legacy_only_in_the_explicit_all_view(rig):
    store, runtime = rig
    assert "leg_tool" not in _names_sync(build_server(store, runtime=runtime, project_id="proj-a"))
    server = build_server(store, runtime=runtime, project_id="proj-a", include_legacy=True)
    assert _names_sync(server) == {"glob_tool", "a_tool", "leg_tool"}


def test_non_approved_tools_stay_out_even_in_scope(rig):
    store, runtime = rig
    server = build_server(store, runtime=runtime, project_id="proj-a", include_legacy=True)
    assert "draft_tool" not in _names_sync(server)


def test_scope_conflicts_are_reported_not_silent():
    """T8: unresolvable name collisions must be surfaced, never dropped quietly."""
    from skaldir.mcpserver import scope_conflicts

    entries = [
        {"name": "same", "scope": "global", "project_id": "", "version": 1},
        {"name": "same", "scope": "global", "project_id": "", "version": 1},
    ]
    conflicts = scope_conflicts(entries)
    assert [c["name"] for c in conflicts] == ["same"]
    # and a resolvable pair is NOT a conflict
    ok = [
        {"name": "same2", "scope": "project", "project_id": "a", "version": 1},
        {"name": "same2", "scope": "global", "project_id": "", "version": 1},
    ]
    assert scope_conflicts(ok) == []
