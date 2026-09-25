"""RF-10 contract — stop answers within 2 s in the engine's local wait phases.

Chapter 09 §3B/§5 (RF-10): a stop arriving during engine-side wait phases
(compression-retry pause, primary-transport recovery wait) must take effect
within 2 s. Blocked ``time.sleep`` calls that ignore the interrupt flag are
the defect.

Red-first evidence on the live engine copy:
    FAIL A pristine blocked sleep ignores stop — elapsed=2.001s, stop visible
    only after the full sleep (RC=1).

Hermetic like test_rf02_request_deadline.py: the versioned overlay is applied
to a COPY of the two engine files and the REAL slice helper is driven with a
monotonic clock; CI never depends on the host being patched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = Path.home() / ".hermes" / "hermes-agent"
OVERLAY = REPO / "host-overlay" / "patch_rf10_stop_responsiveness.py"
PROBE = REPO / "tools" / "host_rf10_stop_probe.py"
TURN_OVERFLOW = ENGINE / "agent" / "turn_overflow.py"
RUNTIME_HELPERS = ENGINE / "agent" / "agent_runtime_helpers.py"

pytestmark = pytest.mark.skipif(
    not (TURN_OVERFLOW.is_file() and RUNTIME_HELPERS.is_file()),
    reason="host hermes-agent checkout not present",
)


def _run(cmd, timeout: int = 300):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def test_overlay_anchors_still_match_the_engine():
    proc = _run([sys_exe(), str(OVERLAY), "--check", str(ENGINE)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "would patch" in proc.stdout


def test_overlay_is_idempotent(tmp_path):
    tree = _engine_copy(tmp_path)
    assert _run([sys_exe(), str(OVERLAY), str(tree)]).returncode == 0
    second = _run([sys_exe(), str(OVERLAY), str(tree)])
    assert second.returncode == 0 and "already patched" in second.stdout


def test_overlay_refuses_to_patch_an_unknown_revision(tmp_path):
    tree = _engine_copy(tmp_path)
    victim = tree / "agent" / TURN_OVERFLOW.name
    victim.write_text(
        victim.read_text(encoding="utf-8").replace(
            "_RETRY_HINT = ",
            "_RETRY_HINT_MOVED_BY_A_FUTURE_ENGINE = ",
        ),
        encoding="utf-8",
    )
    proc = _run([sys_exe(), str(OVERLAY), str(tree)])
    assert proc.returncode == 1
    assert "ANCHOR MISSING/AMBIGUOUS" in proc.stdout and "Nothing written" in proc.stdout


def test_patched_engine_stops_within_2s(tmp_path):
    tree = _engine_copy(tmp_path)
    assert _run([sys_exe(), str(OVERLAY), str(tree)]).returncode == 0
    probe = _run([engine_python(), str(PROBE), str(tree)], timeout=240)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.count("PASS") == 2


def test_pristine_engine_ignores_stop_during_blocked_sleep(tmp_path):
    """Non-vacuity: without the overlay the stop is honored only after the sleep."""
    tree = _engine_copy(tmp_path)
    probe = _run([engine_python(), str(PROBE), str(tree)], timeout=240)
    assert probe.returncode == 1
    assert "FAIL A" in probe.stdout


def sys_exe() -> str:
    import sys
    return sys.executable


def engine_python() -> str:
    return str(ENGINE / "venv" / "bin" / "python")


def _engine_copy(tmp_path: Path) -> Path:
    """Copy only the files the probe touches (engine venv python loads agent.*
    from the copy via sys.path anchor; the probes import agent.turn_overflow
    and agent.agent_runtime_helpers plus their stdlib-only dependencies)."""
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(TURN_OVERFLOW, tmp_path / "agent" / TURN_OVERFLOW.name)
    shutil.copy(RUNTIME_HELPERS, tmp_path / "agent" / RUNTIME_HELPERS.name)
    return tmp_path
