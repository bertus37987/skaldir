"""TDD voice_cockpit: one payload of status + latest triage + next best action."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skaldir import events as events_mod
from skaldir import voice_cockpit as vc
from skaldir import voice_service


def _runtime(tmp: Path) -> Path:
    return tmp / "rt"


def _seed_events(runtime: Path, session_id: str = "sess-1") -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    store = events_mod.EventStore(runtime / "events.db")
    store.append(session_id, 1, "thought", {"text": "start"})
    store.append(session_id, 2, "tool_call", {"name": "list_dir", "arguments": {}})
    store.append(session_id, 3, "tool_result", {"name": "list_dir", "ok": True})
    store.close()


class TestCockpitPayload(unittest.TestCase):
    def test_empty_runtime_gives_honest_payload_no_crash(self):
        with tempfile.TemporaryDirectory() as d:
            payload = vc.cockpit_payload("no-such-session", runtime=Path(d) / "missing")
        self.assertEqual(payload["status"]["live"], False)
        self.assertEqual(payload["triage"]["checked"], False)
        self.assertEqual(payload["next_best_action"]["action"], "none")
        self.assertIn("why", payload["next_best_action"])

    def test_seeded_session_gives_full_payload(self):
        with tempfile.TemporaryDirectory() as d:
            rt = _runtime(Path(d))
            _seed_events(rt)
            payload = vc.cockpit_payload("sess-1", runtime=rt)
        self.assertEqual(payload["status"]["live"], True)
        self.assertEqual(payload["status"]["session_id"], "sess-1")
        self.assertEqual(payload["status"]["events"], 3)
        self.assertEqual(payload["triage"]["checked"], True)
        self.assertIn(payload["triage"]["decision"], vc.AVAILABLE_DECISIONS)
        self.assertIn("action", payload["next_best_action"])
        self.assertIn("why", payload["next_best_action"])

    def test_latest_session_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            rt = _runtime(Path(d))
            _seed_events(rt)
            payload = vc.cockpit_payload(runtime=rt)
        self.assertEqual(payload["status"]["session_id"], "sess-1")

    def test_pure_function_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            rt = _runtime(Path(d))
            _seed_events(rt)
            before = sorted(p.name for p in rt.rglob("*")) if rt.is_dir() else []
            vc.cockpit_payload("sess-1", runtime=rt)
            after = sorted(p.name for p in rt.rglob("*")) if rt.is_dir() else []
        # the -wal/-shm sidecars may flip in and out (sqlite); no *content* file is added
        added = set(after) - set(before)
        self.assertFalse([n for n in added if 'wal' not in n and 'shm' not in n], added)

    def test_import_only_use_of_voice_service(self):
        with tempfile.TemporaryDirectory() as d:
            rt = _runtime(Path(d))
            _seed_events(rt)
            # pure/read-only: the triage runs with record=False (no Jev writes)
            payload = vc.cockpit_payload("sess-1", runtime=rt)
            self.assertIsInstance(payload, dict)
            self.assertNotIn("jev_recorded", payload["triage"])


if __name__ == "__main__":
    unittest.main()
