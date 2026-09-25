"""RF-03 contract — stream durations are measured on ONE clock.

Chapter 09 §4 (RF-03) requires that a slow-but-healthy stream is not falsely
aborted and that *wall-clock jumps never falsify runtime limits*. The stale
detector derived its elapsed time from the last real chunk while stamping and
measuring that chunk with ``time.time()``, so an NTP step / suspend-resume
appeared as silence. Red-first evidence on the live engine
(``~/.hermes/hermes-agent`` @ fb56a7e):

    FAIL A wall-clock jump of +3600s is inert (timeout 10.0s) — kills=[3600.3]

This test is hermetic on purpose: it applies the versioned overlay
``host-overlay/patch_rf03_monotonic_stream_clock.py`` to a COPY of the two engine
files and drives the REAL monitor loop, so CI never depends on the host being
patched — while ``test_overlay_anchors_still_match_the_engine`` fails loudly when
Hermes moves and the overlay must be re-anchored. The full-tree verification
against the engine's own interpreter is
``tools/host_engine_regression_probe.py`` (documented in docs/RF-STATUS.md).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = Path.home() / ".hermes" / "hermes-agent"
OVERLAY = REPO / "host-overlay" / "patch_rf03_monotonic_stream_clock.py"
PROBE = REPO / "tools" / "host_rf_monotonic_probe.py"
HELPERS = ENGINE / "agent" / "chat_completion_helpers.py"
MONITOR = ENGINE / "agent" / "chat_completion_stream_monitor.py"

#: The monitor's only non-stdlib import. The probe drives a non-local endpoint,
#: so the stub is the honest minimal seam here (the real function is exercised by
#: the engine-side run recorded in docs/RF-STATUS.md).
MODEL_METADATA_STUB = '''"""Test stub: the monitor only needs the local-endpoint predicate."""


def is_local_endpoint(url: str = "") -> bool:
    return False
'''

pytestmark = pytest.mark.skipif(
    not (HELPERS.is_file() and MONITOR.is_file()),
    reason="host hermes-agent checkout not present",
)


def _engine_copy(tmp_path: Path) -> Path:
    """A minimal importable tree holding the engine's two files, unmodified."""
    agent = tmp_path / "agent"
    agent.mkdir(parents=True)
    (agent / "__init__.py").write_text("", encoding="utf-8")
    (agent / "model_metadata.py").write_text(MODEL_METADATA_STUB, encoding="utf-8")
    shutil.copy(HELPERS, agent / HELPERS.name)
    shutil.copy(MONITOR, agent / MONITOR.name)
    return tmp_path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def test_overlay_anchors_still_match_the_engine():
    """The versioned overlay must stay applicable to the installed engine."""
    proc = _run([sys.executable, str(OVERLAY), "--check", str(ENGINE)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "would patch" in proc.stdout


def test_overlay_is_idempotent(tmp_path):
    tree = _engine_copy(tmp_path)
    first = _run([sys.executable, str(OVERLAY), str(tree)])
    assert first.returncode == 0, first.stdout + first.stderr
    second = _run([sys.executable, str(OVERLAY), str(tree)])
    assert second.returncode == 0
    assert "already patched" in second.stdout


def test_overlay_refuses_to_patch_an_unknown_revision(tmp_path):
    """A moved anchor must fail loudly, never silently no-op (overlay contract)."""
    tree = _engine_copy(tmp_path)
    target = tree / "agent" / MONITOR.name
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "def _poll_local_load_notice(self, now: float) -> bool:",
            "def _poll_local_load_notice(self, now: float) -> bool:  # moved by a future engine",
        ),
        encoding="utf-8",
    )
    proc = _run([sys.executable, str(OVERLAY), str(tree)])
    assert proc.returncode == 1
    assert "anchor missing" in proc.stdout
    assert "Nothing written" in proc.stdout


def test_patched_engine_measures_stream_time_monotonically(tmp_path):
    tree = _engine_copy(tmp_path)
    assert _run([sys.executable, str(OVERLAY), str(tree)]).returncode == 0
    probe = _run([sys.executable, str(PROBE), str(tree)])
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "PASS A" in probe.stdout and "PASS B" in probe.stdout


def test_pristine_engine_still_shows_the_defect(tmp_path):
    """The probe is not vacuous: without the overlay it must report the defect.

    Keeps the red-first evidence honest — if this ever passes on a pristine copy,
    the probe stopped observing the clock coupling and the fix is no longer
    demonstrated by it.
    """
    tree = _engine_copy(tmp_path)
    probe = _run([sys.executable, str(PROBE), str(tree)])
    assert probe.returncode == 1
    assert "FAIL A" in probe.stdout
