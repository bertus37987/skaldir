"""RF-02 contract — ONE request-wide budget bounds the logical request.

Chapter 09 §3B/§4 (RF-02): a silent provider, a ping-only stream and a
non-cooperative abort must not exceed the configured TOTAL deadline by more than a
justified cleanup tolerance (≤5s in the local pilot), and no lower layer may invent
a fresh full deadline of its own. Red-first evidence on the live engine
(`fb56a7e06d`), where no such deadline exists at all:

    FAIL A budget 3s / stale 300s → kill at the budget — no kill within 12s

Hermetic like the RF-03 test: the versioned overlay is applied to a COPY of the two
engine files and the REAL monitor loop is driven, so CI never depends on the host
being patched. ``test_overlays_compose_in_either_order`` additionally pins that the
RF-02 and RF-03 overlays do not fight over the same anchors.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = Path.home() / ".hermes" / "hermes-agent"
OVERLAY_RF02 = REPO / "host-overlay" / "patch_rf02_request_deadline.py"
OVERLAY_RF03 = REPO / "host-overlay" / "patch_rf03_monotonic_stream_clock.py"
PROBE_RF02 = REPO / "tools" / "host_rf02_deadline_probe.py"
PROBE_RF03 = REPO / "tools" / "host_rf_monotonic_probe.py"
HELPERS = ENGINE / "agent" / "chat_completion_helpers.py"
MONITOR = ENGINE / "agent" / "chat_completion_stream_monitor.py"

MODEL_METADATA_STUB = '''"""Test stub: the monitor only needs the local-endpoint predicate."""


def is_local_endpoint(url: str = "") -> bool:
    return False
'''

pytestmark = pytest.mark.skipif(
    not (HELPERS.is_file() and MONITOR.is_file()),
    reason="host hermes-agent checkout not present",
)


def _engine_copy(tmp_path: Path) -> Path:
    agent = tmp_path / "agent"
    agent.mkdir(parents=True)
    (agent / "__init__.py").write_text("", encoding="utf-8")
    (agent / "model_metadata.py").write_text(MODEL_METADATA_STUB, encoding="utf-8")
    shutil.copy(HELPERS, agent / HELPERS.name)
    shutil.copy(MONITOR, agent / MONITOR.name)
    return tmp_path


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def test_overlay_anchors_still_match_the_engine():
    proc = _run([sys.executable, str(OVERLAY_RF02), "--check", str(ENGINE)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "would patch" in proc.stdout


def test_overlay_is_idempotent(tmp_path):
    tree = _engine_copy(tmp_path)
    assert _run([sys.executable, str(OVERLAY_RF02), str(tree)]).returncode == 0
    second = _run([sys.executable, str(OVERLAY_RF02), str(tree)])
    assert second.returncode == 0 and "already patched" in second.stdout


def test_overlay_refuses_to_patch_an_unknown_revision(tmp_path):
    tree = _engine_copy(tmp_path)
    monitor = tree / "agent" / MONITOR.name
    monitor.write_text(
        monitor.read_text(encoding="utf-8").replace(
            "    def _heartbeat(self, waiting_secs: int) -> None:",
            "    def _heartbeat(self, waiting_secs: int) -> None:  # moved by a future engine",
        ),
        encoding="utf-8",
    )
    proc = _run([sys.executable, str(OVERLAY_RF02), str(tree)])
    assert proc.returncode == 1
    assert "anchor missing" in proc.stdout and "Nothing written" in proc.stdout


def test_patched_engine_bounds_the_request_by_the_deadline(tmp_path):
    tree = _engine_copy(tmp_path)
    assert _run([sys.executable, str(OVERLAY_RF02), str(tree)]).returncode == 0
    probe = _run([sys.executable, str(PROBE_RF02), str(tree)], timeout=240)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.count("PASS") == 3


def test_pristine_engine_has_no_request_deadline(tmp_path):
    """Non-vacuity: without the overlay the probe must report the missing bound."""
    tree = _engine_copy(tmp_path)
    probe = _run([sys.executable, str(PROBE_RF02), str(tree)], timeout=240)
    assert probe.returncode == 1
    assert "FAIL A" in probe.stdout


def test_overlays_compose_in_either_order(tmp_path):
    """RF-02 + RF-03 must not fight over anchors, in either application order."""
    for order in ((OVERLAY_RF02, OVERLAY_RF03), (OVERLAY_RF03, OVERLAY_RF02)):
        tree = _engine_copy(tmp_path / order[0].stem)
        for overlay in order:
            applied = _run([sys.executable, str(overlay), str(tree)])
            assert applied.returncode == 0, applied.stdout + applied.stderr
        deadline = _run([sys.executable, str(PROBE_RF02), str(tree)], timeout=240)
        monotonic = _run([sys.executable, str(PROBE_RF03), str(tree)], timeout=120)
        assert deadline.returncode == 0, deadline.stdout + deadline.stderr
        assert monotonic.returncode == 0, monotonic.stdout + monotonic.stderr
