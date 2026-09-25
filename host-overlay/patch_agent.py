"""Host-Overlay: macht das godsv-Completion-Gate unabhängig von Dateiänderungen.

Problem (unabhängiger Audit, 13.09.2026): `agent/turn_stop_gates.py::_pre_verify_nudge`
rief den `pre_verify`-Hook NUR auf, wenn `_turn_file_mutation_paths` nicht leer war.
Eine falsche Abschlussbehauptung ohne Dateiänderung (frischer/resumierter Turn) kam
damit nie vor das Goal-Gate — der Lauf endete, obwohl der Vertrag WORKING war.

Fix: Der Hook wird bei jeder Textantwort gefragt (attempt < Cap). Ob etwas passiert,
entscheidet der Hook selbst — godsv's `guard_pre_verify` beschäftigt sich ausschließlich
mit einem AKTIVEN Vertrag; normale Antworten ohne Goal bleiben unberührt (Rückgabe None).

Verankert als versioniertes Overlay (nicht als manueller Eingriff in einen
installierten Hermes): `python host-overlay/patch_agent.py [--check] [HERMES_SRC]`.
Idempotent; bricht laut ab, wenn der Anker fehlt (kein stiller No-op).
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_SRC = Path.home() / ".hermes" / "hermes-agent"

OLD = '''    _edited = sorted(getattr(agent, "_turn_file_mutation_paths", set()) or [])
    try:
        from agent.verify_hooks import max_verify_nudges
        from hermes_cli.lifecycle import has_hook
        from hermes_cli.plugins import get_pre_verify_continue_message

        if _edited and has_hook("pre_verify") and attempt < max_verify_nudges():'''
NEW = '''    _edited = sorted(getattr(agent, "_turn_file_mutation_paths", set()) or [])
    try:
        from agent.verify_hooks import max_verify_nudges
        from hermes_cli.lifecycle import has_hook
        from hermes_cli.plugins import get_pre_verify_continue_message

        # gv-host-overlay: ALWAYS ask the hook — an active goal must reach the
        # completion gate even in a fresh/resumed turn without file edits. The
        # hook itself decides (None = nothing happens; normal answers without a
        # goal are unaffected). Pause/cancel/approval/budget stay host-owned.
        if has_hook("pre_verify") and attempt < max_verify_nudges():'''
OLD2 = '''def _pre_verify_nudge(agent, final_response, attempt: int) -> Optional[str]:
    """After code edits a registered ``pre_verify`` hook may keep the agent going one
    more turn; no default continuation cost."""
    _edited = sorted(getattr(agent, "_turn_file_mutation_paths", set()) or [])'''
NEW2 = '''def _pre_verify_nudge(agent, final_response, attempt: int) -> Optional[str]:
    """After code edits a registered ``pre_verify`` hook may keep the agent going one
    more turn; no default continuation cost."""
    import os as _os

    _trace_path = _os.environ.get("HERMES_TURN_TRACE")
    if _trace_path:
        try:
            with open(_trace_path, "a", encoding="utf-8") as _fh:
                _fh.write(
                    f"pre_verify_nudge called attempt={attempt} "
                    f"session={getattr(agent, 'session_id', '') or ''}\n"
                )
        except OSError:
            pass
    _edited = sorted(getattr(agent, "_turn_file_mutation_paths", set()) or [])'''
# wörtlicher Marker für die Idempotenz-Prüfung
MARKER = "gv-host-overlay: ALWAYS ask the hook"
MARKER2 = "HERMES_TURN_TRACE"


def patch(target: Path, *, check: bool = False) -> int:
    path = target / "agent" / "turn_stop_gates.py"
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
    text = text.replace(OLD, NEW, 1)
    if MARKER2 not in text:
        if OLD2 not in text:
            print(f"✗ trace anchor missing in {path} — nothing written.")
            return 1
        text = text.replace(OLD2, NEW2, 1)
    path.write_text(text, encoding="utf-8")
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
