"""Host-Overlay: der Between-Turns-Tool-Refresh deckt auch spät registrierte
Plugin-Tools ab.

Problem: `agent/turn_context.py::_refresh_mcp_tools_between_turns` refresht das
Tool-Snapshot eines Agenten nur, wenn im Prozess MCP-Tools registriert sind
(`has_registered_mcp_tools()`) UND `tools.mcp_tool` importiert wurde. Ein Tool,
das ein PLUGIN später registriert (godsv-Live-Insertion, §1.3), bleibt damit bis
zum Neustart der Session unsichtbar — obwohl der Agent sein Tool-Array pro Turn
aus der lebenden Registry neu ableitet.

Fix: Der Refresh feuert, sobald sich die Registry-Generation seit dem Snapshot
verschoben hat (`agent._tool_snapshot_generation`), plus der bisherige MCP-Pfad.
`preserve_prefix=True` bleibt unverändert: das Array wächst nur am Ende, der
Prompt-Cache-Prefix wird nicht umsortiert.

Verankert als versioniertes Overlay (nicht als manueller Eingriff in einen
installierten Hermes): `python host-overlay/patch_tool_refresh.py [--check] [HERMES_SRC]`.
Idempotent; bricht laut ab, wenn der Anker fehlt (kein stiller No-op).
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_SRC = Path.home() / ".hermes" / "hermes-agent"

OLD = '''def _refresh_mcp_tools_between_turns(agent: Any) -> None:
    """Late-connecting MCP servers land in THIS turn's snapshot, before the first API
    call assembles ``tools=``. ``preserve_prefix`` keeps the tool array append-only so a
    flapping ``check_fn`` can't fork the cache."""
    try:
        # Import-cost gate: MCP tools are only registered by code that already imported
        # ``tools.mcp_tool`` (~0.4s); not in sys.modules => nothing to do.
        if not getattr(agent, "_skip_mcp_refresh", False) and "tools.mcp_tool" in sys.modules:
            from tools.mcp_tool_discovery import has_registered_mcp_tools
            from tools.mcp_tool_agent import refresh_agent_mcp_tools
            if has_registered_mcp_tools():
                refresh_agent_mcp_tools(agent, quiet_mode=True, preserve_prefix=True)
    except Exception:
        logger.debug("between-turns MCP tool refresh skipped", exc_info=True)'''

NEW = '''def _refresh_mcp_tools_between_turns(agent: Any) -> None:
    """Late-connecting MCP servers land in THIS turn's snapshot, before the first API
    call assembles ``tools=``. ``preserve_prefix`` keeps the tool array append-only so a
    flapping ``check_fn`` can't fork the cache."""
    try:
        if getattr(agent, "_skip_mcp_refresh", False):
            return
        # gv-host-overlay: refresh when the registry MOVED since the snapshot, not only
        # when an MCP server is present. The original gate keyed on registered MCP tools,
        # so a tool registered later by a plugin (godsv live capability insertion) stayed
        # invisible until the session restarted.
        from tools.registry import registry as _gv_registry

        _gv_snapshot_gen = getattr(agent, "_tool_snapshot_generation", None)
        _gv_moved = (
            not isinstance(_gv_snapshot_gen, int)
            or _gv_snapshot_gen != _gv_registry._generation
        )
        _gv_needed = _gv_moved
        if not _gv_needed and "tools.mcp_tool" in sys.modules:
            # the historical case: MCP tools exist, a server may have connected late
            from tools.mcp_tool_discovery import has_registered_mcp_tools

            _gv_needed = has_registered_mcp_tools()
        if _gv_needed:
            from tools.mcp_tool_agent import refresh_agent_mcp_tools

            _gv_added = refresh_agent_mcp_tools(agent, quiet_mode=True, preserve_prefix=True)
            _gv_trace_tool_refresh(agent, _gv_added)
    except Exception:
        logger.debug("between-turns MCP tool refresh skipped", exc_info=True)


def _gv_trace_tool_refresh(agent: Any, added) -> None:
    """gv-host-overlay: neutral turn trace (``HERMES_TURN_TRACE``) for the refresh."""
    import os as _os

    _path = _os.environ.get("HERMES_TURN_TRACE")
    if not _path:
        return
    try:
        with open(_path, "a", encoding="utf-8") as _fh:
            _fh.write(
                "gv_tool_refresh session="
                f"{getattr(agent, 'session_id', '') or ''} "
                f"added={sorted(added or [])} "
                f"tools={len(getattr(agent, 'tools', []) or [])}\\n"
            )
    except OSError:
        pass'''

# wörtlicher Marker für die Idempotenz-Prüfung
MARKER = "gv-host-overlay: refresh when the registry MOVED"


def patch(target: Path, *, check: bool = False) -> int:
    path = Path(target) / "agent" / "turn_context.py"
    if not path.is_file():
        print(f"✗ not found: {path}")
        return 2
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"✓ already patched: {path}")
        return 0
    if OLD not in text:
        print(f"✗ anchor missing in {path} — Hermes version changed? Nothing written.")
        return 1
    if check:
        print(f"• would patch: {path}")
        return 0
    backup = path.with_suffix(".py.gv-backup")
    backup.write_text(text, encoding="utf-8")
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"✓ patched: {path} (backup: {backup.name})")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
