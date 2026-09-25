"""The host-release gate: a report is not a proof, and a stale proof is not current.

GOAL §3: "Die Host-Freigabe muss einen aktuellen Nachweis des Stop-Pfads konsumieren und
bei Backend-/Sitzungswechsel erneut prüfen. Fehlender Nachweis bleibt unbekannt/blockiert.
Ein bloß erzeugter Report reicht nicht."

Each of those sentences is one test here.
"""

from __future__ import annotations

import json

from skaldir.stop_release_gate import StopReleaseGate, load_report


def _write(path, **data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_without_a_proof_the_desktop_stays_blocked(tmp_path):
    """'Fehlender Nachweis bleibt unbekannt/blockiert' — absence is never 'fine'."""
    gate = StopReleaseGate(tmp_path / "missing.json", measure=lambda: (True, "x11"))
    decision = gate.release(backend="x11", session="s1")
    assert decision.allowed is False
    assert decision.status == "blocked"
    assert "no stop-path proof" in decision.reason
    assert "'no report' is not 'no problem'" in decision.reason


def test_an_incomplete_proof_cannot_release_the_desktop(tmp_path):
    report = _write(tmp_path / "report.json", double_escape_proven=True)  # no backend/session
    gate = StopReleaseGate(report, measure=lambda: (True, "x11"))
    decision = gate.release(backend="x11", session="s1")
    assert decision.allowed is False
    assert decision.status == "unknown"
    assert "incomplete" in decision.reason


def test_a_written_report_alone_is_not_proof(tmp_path):
    """'Ein bloß erzeugter Report reicht nicht.'"""
    report = _write(
        tmp_path / "report.json",
        double_escape_proven=True,      # the file *claims* it
        backend="x11",
        session="s1",
    )
    # ... but the current measurement says otherwise
    gate = StopReleaseGate(report, measure=lambda: (False, "x11"))
    decision = gate.release(backend="x11", session="s1")
    assert decision.allowed is False
    assert decision.status == "blocked"
    assert "not proven on this host" in decision.reason
    assert decision.rechecked is True, "the stored claim was trusted without re-measuring"


def test_a_proof_for_another_backend_is_re_checked_not_reused(tmp_path):
    """'bei Backend-/Sitzungswechsel erneut prüfen'"""
    report = _write(
        tmp_path / "report.json", double_escape_proven=True, backend="x11", session="s1"
    )
    gate = StopReleaseGate(report, measure=lambda: (True, "wayland"))
    decision = gate.release(backend="wayland", session="s1")
    assert decision.allowed is False
    assert decision.status == "unknown"
    assert "re-check on a backend change" in decision.reason


def test_a_proof_for_another_session_is_re_checked_not_reused(tmp_path):
    report = _write(
        tmp_path / "report.json", double_escape_proven=True, backend="x11", session="s1"
    )
    gate = StopReleaseGate(report, measure=lambda: (True, "x11"))
    decision = gate.release(backend="x11", session="s2")
    assert decision.allowed is False
    assert decision.status == "unknown"
    assert "re-check on a session change" in decision.reason


def test_a_backend_that_moves_during_the_recheck_invalidates_the_proof(tmp_path):
    report = _write(
        tmp_path / "report.json", double_escape_proven=True, backend="x11", session="s1"
    )
    gate = StopReleaseGate(report, measure=lambda: (True, "x11-then-wayland"))
    decision = gate.release(backend="x11-then-wayland", session="s1")
    # the measured backend differs from the one asked for in the *stored* sense only if
    # it differs from the requested one; here they match, so the proof stands on that axis
    assert decision.backend == "x11-then-wayland"

    gate = StopReleaseGate(report, measure=lambda: (True, "wayland"))
    decision = gate.release(backend="x11", session="s1")
    assert decision.allowed is False
    assert "the backend changed under us" in decision.reason
    assert decision.rechecked is True


def test_a_failing_recheck_is_unknown_not_allowed(tmp_path):
    """A measurement that throws must never be read as a pass."""
    report = _write(
        tmp_path / "report.json", double_escape_proven=True, backend="x11", session="s1"
    )

    def boom():
        raise RuntimeError("probe unavailable")

    gate = StopReleaseGate(report, measure=boom)
    decision = gate.release(backend="x11", session="s1")
    assert decision.allowed is False
    assert decision.status == "unknown"
    assert "re-check failed" in decision.reason


def test_the_desktop_is_released_only_when_the_stop_path_is_actually_proven(tmp_path):
    report = _write(
        tmp_path / "report.json", double_escape_proven=True, backend="x11", session="s1"
    )
    gate = StopReleaseGate(report, measure=lambda: (True, "x11"))
    decision = gate.release(backend="x11", session="s1")
    assert decision.allowed is True
    assert decision.status == "allowed"
    assert decision.proof_proven is True and decision.rechecked is True
    assert decision.to_dict()["status"] == "allowed"


def test_a_gate_cannot_certify_itself(tmp_path):
    """The gate never writes a proof — only the host measurement can produce one."""
    gate = StopReleaseGate(tmp_path / "missing.json", measure=lambda: (True, "x11"))
    gate.release(backend="x11", session="s1")
    assert load_report(tmp_path / "missing.json") is None, "the gate wrote its own proof"
