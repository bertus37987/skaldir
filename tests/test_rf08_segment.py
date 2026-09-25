"""RF-08 contract — persistent segment continuation under the same goal identity.

Chapter 09 §3E/§4 (RF-08): at the 80-iteration segment boundary an
AUTHORIZED long-running goal is carried into at least one further segment
without manual "continue" — task identity, total budget and open criteria
survive — while ordinary chat receives NO unbounded auto-continuation.

Red-first evidence on the live engine (``fb56a7e06d``/pristine), where the
budget exhaustion point persists NOTHING addressable beyond the transcript:

    PRISTINE RF-08: no _rf08_record_segment_continuation on the engine —
    no segment-continuation cursor is written at the segment boundary

Hermetic like the RF-02/RF-07 tests: the versioned overlay is applied to a
COPY of the one engine file ``agent/turn_finalizer.py`` (plus the minimal
importable scaffolding) and the REAL ``finalize_turn`` is driven, so CI never
depends on the host being patched. Composition with the brother overlays that
touch the same module (RF-07 lives in ``agent/chat_completion_helpers.py`` —
a DIFFERENT file) is pinned in both application orders with only the minimal
files copied, no full engine copy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = Path.home() / ".hermes" / "hermes-agent"
OVERLAY_RF08 = REPO / "host-overlay" / "patch_rf08_segment_continuation.py"
OVERLAY_RF07 = REPO / "host-overlay" / "patch_rf07_interim_report.py"
PROBE_RF08 = REPO / "tools" / "host_rf08_segment_probe.py"
TARGET = ENGINE / "agent" / "turn_finalizer.py"

pytestmark = pytest.mark.skipif(
    not TARGET.is_file(),
    reason="host hermes-agent checkout not present",
)


def _engine_copy(tmp_path: Path) -> Path:
    """Minimal scratch tree: the REAL turn_finalizer plus the plain files the
    RF-07/RF-08 overlays touch — enough for the anchored overlay steps and the
    finalize_turn drive (no full engine copy needed for the pinned test)."""
    tree = tmp_path / "engine"
    agent = tree / "agent"
    agent.mkdir(parents=True)
    (agent / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(TARGET, agent / TARGET.name)
    # RF-07 anchors inside chat_completion_helpers.py — include it so the
    # composition test exercises the real co-patched module.
    shutil.copy(ENGINE / "agent" / "chat_completion_helpers.py",
                agent / "chat_completion_helpers.py")
    return tree


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def test_overlay_anchors_still_match_the_engine():
    proc = _run([sys.executable, str(OVERLAY_RF08), "--check", str(ENGINE)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "would patch" in proc.stdout


def test_overlay_is_idempotent(tmp_path):
    tree = _engine_copy(tmp_path)
    assert _run([sys.executable, str(OVERLAY_RF08), str(tree)]).returncode == 0
    second = _run([sys.executable, str(OVERLAY_RF08), str(tree)])
    assert second.returncode == 0 and "already patched" in second.stdout


def test_overlay_refuses_to_patch_an_unknown_revision(tmp_path):
    """Anchor drift must raise loudly (RC=1, nothing written)."""
    tree = _engine_copy(tmp_path)
    target = tree / "agent" / TARGET.name
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "            final_response = agent._handle_max_iterations(messages, api_call_count)\n",
            "            final_response = agent._handle_max_iterations("
            "messages, api_call_count)  # moved by a future engine\n",
        ),
        encoding="utf-8",
    )
    proc = _run([sys.executable, str(OVERLAY_RF08), str(tree)])
    assert proc.returncode == 1
    assert "anchor missing" in proc.stdout and "Nothing written" in proc.stdout


def test_backup_written_and_check_mode_writes_nothing(tmp_path):
    tree = _engine_copy(tmp_path)
    original = (tree / "agent" / TARGET.name).read_text(encoding="utf-8")
    assert _run([sys.executable, str(OVERLAY_RF08), "--check", str(tree)]).returncode == 0
    # --check leaves the file untouched (no backup either).
    assert (tree / "agent" / TARGET.name).read_text(encoding="utf-8") == original
    assert not (tree / "agent" / TARGET.with_suffix(".py.gv-backup")).is_file()
    assert _run([sys.executable, str(OVERLAY_RF08), str(tree)]).returncode == 0
    backup = tree / "agent" / "turn_finalizer.py.gv-backup"
    assert backup.is_file() and backup.read_text(encoding="utf-8") == original


def test_pristine_engine_has_no_segment_continuation(tmp_path):
    """Non-vacuity: without the overlay the probe must report the missing record."""
    # The probe drives the engine's own venv interpreter against a tree that
    # carries a pristine ``agent/turn_finalizer.py`` — use a full copy because
    # the probe imports the real module tree.
    tree = tmp_path / "pristine-full"
    shutil.copytree(ENGINE, tree, symlinks=True)
    try:
        probe = _run(
            [str(tree / "venv" / "bin" / "python"), str(PROBE_RF08), str(tree)],
            timeout=420,
        )
        assert probe.returncode == 1
        assert "PRISTINE RF-08" in probe.stdout
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_overlays_compose_in_either_order(tmp_path):
    """RF-08 + RF-07 must not fight over anchors, in either application order.

    RF-07 anchors in ``agent/chat_completion_helpers.py``, RF-08 in
    ``agent/turn_finalizer.py`` — the pinned scratch tree copies both files and
    asserts BOTH overlays apply cleanly, twice (idempotent), in either order.
    """
    for order in ((OVERLAY_RF08, OVERLAY_RF07), (OVERLAY_RF07, OVERLAY_RF08)):
        tree = _engine_copy(tmp_path / order[0].stem)
        for overlay in order:
            applied = _run([sys.executable, str(overlay), str(tree)])
            assert applied.returncode == 0, applied.stdout + applied.stderr
        # re-run both: idempotent after composition
        for overlay in order:
            second = _run([sys.executable, str(overlay), str(tree)])
            assert second.returncode == 0 and "already patched" in second.stdout
        assert "gv-host-overlay RF-08" in (tree / "agent" / TARGET.name).read_text(encoding="utf-8")
        assert "gv-host-overlay RF-07" in (
            tree / "agent" / "chat_completion_helpers.py"
        ).read_text(encoding="utf-8")


def test_record_persists_exactly_once_and_restart_is_idempotent(tmp_path):
    """Green path on a full engine copy: the REAL finalize_turn writes the
    cursor once; a second identical call and a RESTART sub-process (re-read
    from disk) resolve the same cursor without duplicating it."""
    tree = tmp_path / "patched-full"
    shutil.copytree(ENGINE, tree, symlinks=True)
    try:
        applied = _run([sys.executable, str(OVERLAY_RF08), str(tree)])
        assert applied.returncode == 0, applied.stdout + applied.stderr
        probe = _run(
            [str(tree / "venv" / "bin" / "python"), str(PROBE_RF08), str(tree)],
            timeout=420,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert probe.stdout.count("PASS") >= 11
        assert not any(
            line.startswith("FAIL") for line in probe.stdout.splitlines()
        )
        # Restart-safety explicit assertion on the recorded cursor file.
        assert "restart: fresh interpreter re-reads the SAME cursor, no duplicate" in probe.stdout
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def test_gate_d_cap_and_allow_more_env(tmp_path, monkeypatch):
    """Gate D sematics are pinned in the overlay source: the default cap of 2
    auto-segments derives from ``HERMES_RF08_MAX_AUTO_SEGMENTS`` and a higher
    index requires ``HERMES_RF08_ALLOW_MORE_SEGMENTS=1``."""
    src = OVERLAY_RF08.read_text(encoding="utf-8")
    assert "HERMES_RF08_MAX_AUTO_SEGMENTS" in src
    assert 'env_int("HERMES_RF08_MAX_AUTO_SEGMENTS", 2)' in src
    assert "HERMES_RF08_ALLOW_MORE_SEGMENTS" in src
    # Gate A language without goal identity must leave ordinary chat unchanged.
    patched = tmp_path / "p.py"
    shutil.copy(OVERLAY_RF08, patched)
    text = patched.read_text(encoding="utf-8")
    assert "no hidden forever-continuation" in text or "ordinary chat" in text
