"""Deterministic demo run of the V0 pipeline (pact → monitor → voice → inject).

``skaldir demo`` executes a scripted session: repeated failing test calls, a
tool loop, and sloppy edits. It shows which signals fire and which hints the
voice would deliver — no LLM calls, fully reproducible (asserted in tests).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .events import EventStore
from .inject import InjectionQueue
from .ledger import Ledger
from .monitor import Monitor
from .pact import Pact
from .voice import Voice

SCRIPT: list[tuple[int, str, dict]] = [
    (1, "thought", {"text": "I will start with a test run."}),
    (2, "tool_call", {"name": "terminal", "args": {"command": "pytest -q"}}),
    (3, "tool_result", {"exit_code": 1, "stderr": "ImportError: cannot import name 'cv_split'"}),
    (4, "tool_call", {"name": "terminal", "args": {"command": "pytest -q"}}),
    (5, "tool_result", {"exit_code": 1, "stderr": "ImportError: cannot import name 'cv_split'"}),
    (6, "tool_call", {"name": "terminal", "args": {"command": "pytest -q"}}),
    (
        7,
        "file_edit",
        {
            "path": "lib/utils.py",
            "added": (
                "def helper():\n"
                "    # TODO: implementieren\n"
                "    try:\n"
                "        x = 1\n"
                "    except:\n"
                "        pass\n"
            ),
        },
    ),
    (8, "file_edit", {"path": "src/train.py", "added": "# TBD\nassert True\n"}),
]


def run_demo(db_path: str | Path | None = None, verbose: bool = True) -> dict:
    """Run the scripted session; returns a summary dict (also used by tests)."""
    if db_path is None:
        handle = tempfile.NamedTemporaryFile(prefix="godsv_demo_", suffix=".db", delete=False)
        db_path = handle.name
        handle.close()
    store = EventStore(db_path)
    session = "demo-1"
    pact = Pact.example()
    ledger = Ledger.from_pact(pact)
    monitor = Monitor(pact)
    voice = Voice(budget=5, min_gap=1)
    queue = InjectionQueue()
    formatted_hints: list[str] = []
    hint_kinds: list[str] = []
    total_signals = 0

    for step, kind, payload in SCRIPT:
        event = store.append(session, step, kind, payload, ts=1000.0 + step)
        signals = monitor.ingest(event)
        total_signals += len(signals)
        hint = voice.consider(signals, step)
        if hint is not None:
            formatted_hints.append(queue.push(hint))
            hint_kinds.append(hint.kind)

    ledger.set_status("ac1", "in_progress")
    ledger.set_test_status("red (ImportError cv_split)")
    ledger.set_next_steps(["implement cv_split", "repeat the test run"])

    summary = {
        "events": store.count(),
        "signals": total_signals,
        "hints": formatted_hints,
        "hint_kinds": hint_kinds,
        "ledger_markdown": ledger.render_markdown(),
        "db_path": str(db_path),
    }
    if verbose:
        print(f"God's Voice — Demo ({summary['events']} Events, {summary['signals']} Signale)")
        for index, formatted in enumerate(formatted_hints, 1):
            print(f"\n--- Hint {index} ---")
            print(formatted)
        print("\n--- Ledger ---")
        print(summary["ledger_markdown"])
    store.close()
    return summary
