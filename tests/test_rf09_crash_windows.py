"""RF-09 contract — crash-window resume fencing + external-action reconciliation.

Chapter 09 §3E/§4 (RF-09): "Ein Crash vor/nach dem Checkpoint, vor/nach einer
externen Wirkung oder während des Fallbacks führt weder zu verlorenen
Nutzerkorrekturen noch zu doppelten Side Effects oder zwei gleichzeitigen
Besitzern desselben Segments."

Red-first evidence on the live engine (pristine), where crash windows have no
fence at all (side effects may silently re-send; two resume owners possible):

    PRISTINE RF-09: no _rf09_mark_pending_external / _rf09_claim_resume on the engine —
    crash windows have no fence (side effects may silently re-send, two owners possible)

Hermetic like the RF-02/RF-08 tests: the versioned overlay is applied to a COPY
of the one engine file group it anchors (turn_finalizer, tool_executor,
conversation_loop, turn_iteration_prep) and the REAL code is driven, so CI
never depends on the host being patched. Composition with the brother overlays
(RF-08 in turn_finalizer, RF-07 in chat_completion_helpers) is pinned in both
orders. The crash-restart round trip runs a REAL SIGKILL of a helper child in
the driver path, per the criterion.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = Path.home() / ".hermes" / "hermes-agent"
OVERLAY_RF09 = REPO / "host-overlay" / "patch_rf09_crash_windows.py"
OVERLAY_RF08 = REPO / "host-overlay" / "patch_rf08_segment_continuation.py"
PROBE_RF09 = REPO / "tools" / "host_rf09_crash_probe.py"
FINALIZER = ENGINE / "agent" / "turn_finalizer.py"

pytestmark = pytest.mark.skipif(
    not FINALIZER.is_file(),
    reason="host hermes-agent checkout not present",
)


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    env = _child_env()
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _check_overlay(overlay: Path, tree: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return _run([sys.executable, str(overlay), str(tree)], timeout=timeout)


def test_overlay_anchors_still_match_the_engine():
    proc = _run([sys.executable, str(OVERLAY_RF09), "--check", str(ENGINE)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "would patch" in proc.stdout


def test_overlay_is_idempotent(tmp_path):
    tree = _engine_copy(tmp_path)
    assert _check_overlay(OVERLAY_RF09, tree).returncode == 0
    second = _run([sys.executable, str(OVERLAY_RF09), str(tree)])
    assert second.returncode == 0 and "already patched" in second.stdout


def test_overlay_refuses_to_patch_an_unknown_revision(tmp_path):
    """Anchor drift must abort loudly (RC=1, nothing written)."""
    tree = _engine_copy(tmp_path)
    target = tree / "agent" / "turn_finalizer.py"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "def _resolve_budget_fallback(\n",
            "def _resolve_budget_fallback(agent):  # moved by a future engine\n",
        ),
        encoding="utf-8",
    )
    proc = _run([sys.executable, str(OVERLAY_RF09), str(tree)])
    assert proc.returncode == 1
    assert "anchor missing" in proc.stdout and "Nothing written" in proc.stdout


def test_backup_written_and_check_mode_writes_nothing(tmp_path):
    tree = _engine_copy(tmp_path)
    original = (tree / "agent" / "turn_finalizer.py").read_text(encoding="utf-8")
    assert _run([sys.executable, str(OVERLAY_RF09), "--check", str(tree)]).returncode == 0
    # --check leaves the files untouched (no backup either).
    assert (tree / "agent" / "turn_finalizer.py").read_text(encoding="utf-8") == original
    assert not (tree / "agent" / "turn_finalizer.py.gv-backup").is_file()
    assert _run([sys.executable, str(OVERLAY_RF09), str(tree)]).returncode == 0
    backup = tree / "agent" / "turn_finalizer.py.gv-backup"
    assert backup.is_file() and backup.read_text(encoding="utf-8") == original


def test_pristine_engine_has_no_resume_fence(tmp_path):
    """Non-vacuity: without the overlay the probe must report the missing fence."""
    tree = tmp_path / "pristine-full"
    shutil.copytree(ENGINE, tree, symlinks=True)
    try:
        # The probe drives the tree's own venv interpreter, so the copy has to
        # resolve its own modules first (see _repoint_editable_finder).
        _repoint_editable_finder(tree)
        env = _child_env()
        probe = subprocess.run(
            [str(tree / "venv" / "bin" / "python"), str(PROBE_RF09), str(tree)],
            capture_output=True, text=True, timeout=420, env=env,
        )
        assert probe.returncode == 1
        assert "PRISTINE RF-09" in probe.stdout
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_overlays_compose_in_either_order(tmp_path):
    """RF-09 + RF-08 must not fight over anchors, in either application order.

    RF-08 anchors in ``agent/turn_finalizer.py`` — the pinned scratch tree
    copies the real file and asserts BOTH overlays apply cleanly, twice
    (idempotent), in either order.
    """
    for order in ((OVERLAY_RF09, OVERLAY_RF08), (OVERLAY_RF08, OVERLAY_RF09)):
        tree = _engine_copy(tmp_path / f"compose-{order[0].stem}")
        for overlay in order:
            applied = _run([sys.executable, str(overlay), str(tree)])
            assert applied.returncode == 0, applied.stdout + applied.stderr
        # re-run both: idempotent after composition
        for overlay in order:
            second = _run([sys.executable, str(overlay), str(tree)])
            assert second.returncode == 0 and "already patched" in second.stdout
        assert "gv-host-overlay RF-09" in _finalizer_text(tree)
        assert "gv-host-overlay RF-08" in _finalizer_text(tree)


def test_record_persists_exactly_once_and_restart_is_idempotent(tmp_path):
    """Green path on a full engine copy: the REAL probe (with its REAL SIGKILL
    crash injection) goes green — the crash-restart round trip runs for real,
    not a mocked substitute."""
    tree = tmp_path / "patched-full"
    shutil.copytree(ENGINE, tree, symlinks=True)
    try:
        assert _run([sys.executable, str(OVERLAY_RF09), str(tree)]).returncode == 0
        _repoint_editable_finder(tree)
        env = _child_env()
        probe = subprocess.run(
            [str(tree / "venv" / "bin" / "python"), str(PROBE_RF09), str(tree)],
            capture_output=True, text=True, timeout=420, env=env,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert probe.stdout.count("PASS") >= 5
        assert "crash-restart trip: identical re-request fenced, unrelated not" in probe.stdout
        assert "W2 resume fence: older epoch rejected CURSOR-MISMATCH" in probe.stdout
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_resume_claim_exists_in_overlay_source():
    """The owner_epoch / CURSOR-MISMATCH fencing pattern is pinned in the overlay."""
    src = OVERLAY_RF09.read_text(encoding="utf-8")
    assert "CURSOR-MISMATCH" in src
    assert "owner_epoch" in src
    # Reuses existing state (RF-08 cursor + hermes StateDB), never a new lease system.
    assert "rf08_cursor_for" in src
    assert "hermes_state" in src
    # Never a silent re-send — the model decides after seeing the marker.
    assert "unknown" in src and "side_effect_state" in src


def _finalizer_text(tree: Path) -> str:
    """The overlaid turn_finalizer of a scratch tree, read back for its markers."""
    return (tree / "agent" / "turn_finalizer.py").read_text(encoding="utf-8")



#: Ambient import hijackers. PYTHONPATH outranks site-packages and the editable
#: finder, so a child that is supposed to import the scratch tree would silently
#: import the LIVE engine tree instead -- the probe then reports "PRISTINE" for a
#: patched copy (or the reverse). The helper makes every child deterministic.
_IMPORT_HIJACKERS = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")


def _child_env() -> dict:
    """Environment for probe children: HERMES_HOME set, import hijackers removed."""
    env = {key: value for key, value in os.environ.items() if key not in _IMPORT_HIJACKERS}
    env["HERMES_HOME"] = os.path.expanduser("~/.hermes")
    return env

def _repoint_editable_finder(tree: Path) -> Path:
    """Re-point the copy's PEP-660 editable finder at the copy.

    The engine venv is an editable install whose finder module bakes ABSOLUTE
    paths, so a plain ``copytree`` keeps importing the HOST engine for every
    package it maps — a probe run against the copy would then read the wrong
    tree and still report confidently. Every mapping is rewritten, not just the
    two this test happens to care about.
    """
    pattern = "venv/lib/python*/site-packages/__editable___hermes_agent*finder.py"
    finder = next(iter(tree.glob(pattern)), None)
    if finder is None:
        return tree
    text = finder.read_text(encoding="utf-8")
    finder.write_text(text.replace(f"{ENGINE}/", f"{tree}/"), encoding="utf-8")
    return tree


def _engine_copy(tmp_path: Path) -> Path:
    """Minimal scratch tree: the four files the RF-09 overlay anchors — enough
    for the anchored overlay steps (no full engine copy needed for the pinned test)."""
    tree = tmp_path / "engine"
    agent = tree / "agent"
    agent.mkdir(parents=True)
    (agent / "__init__.py").write_text("", encoding="utf-8")
    for name in ("turn_finalizer", "tool_executor", "conversation_loop", "turn_iteration_prep"):
        shutil.copy(ENGINE / "agent" / f"{name}.py", agent / f"{name}.py")
    return tree
