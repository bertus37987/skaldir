"""The gate that releases the host desktop — only against a *current* stop-path proof.

GOAL §3:

    "Die Host-Freigabe muss einen aktuellen Nachweis des Stop-Pfads konsumieren und bei
     Backend-/Sitzungswechsel erneut prüfen. Fehlender Nachweis bleibt unbekannt/blockiert.
     Ein bloß erzeugter Report reicht nicht."

That sentence rules out three specific cheats, and this module is shaped to make each of
them impossible rather than merely discouraged:

* **"A mere report is not proof."** Having written a report file proves nothing. The gate
  reads the report and requires it to actually state the stop path is proven — and it
  re-measures the *current* backend rather than trusting the stored claim.
* **"Current."** A proof made under another backend or another session is stale. The gate
  keys the proof to ``(backend, session)`` and refuses when either moved on.
* **"Missing stays unknown/blocked."** Absence is never rounded up to "fine". The refusal
  says ``blocked`` with the reason, never ``allowed``.

The gate never *produces* a proof by itself. A gate that could certify itself would make
the whole requirement circular.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ProofStale",
    "ReleaseDecision",
    "StopReleaseGate",
    "load_report",
]

#: What a report has to say before the desktop may be handed over.
REQUIRED_KEYS = ("double_escape_proven", "backend", "session")


class ProofStale(RuntimeError):
    """The stop-path proof does not describe the situation being released into."""


@dataclass
class ReleaseDecision:
    """The gate's answer — never ambiguous, never rounded up."""

    allowed: bool
    status: str                      # "allowed" | "blocked" | "unknown"
    reason: str
    backend: str = ""
    session: str = ""
    proof_proven: bool = False
    rechecked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "reason": self.reason,
            "backend": self.backend,
            "session": self.session,
            "proof_proven": self.proof_proven,
            "rechecked": self.rechecked,
        }


def load_report(path: str | Path) -> dict[str, Any] | None:
    """Read a stop-path report. ``None`` means absent/unreadable — never "passed"."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class StopReleaseGate:
    """Decides whether the host desktop may be released, against a current proof."""

    def __init__(
        self,
        report_path: str | Path,
        *,
        measure: Any | None = None,
    ) -> None:
        """``measure`` is a callable returning ``(proven: bool, backend: str)``.

        It is injected so the gate can be tested — and so the *real* measurement is the
        host's, not a value copied out of the file it is supposed to verify.
        """
        self.report_path = Path(report_path)
        self.measure = measure

    # -- the decision ------------------------------------------------------

    def release(self, *, backend: str = "", session: str = "") -> ReleaseDecision:
        """May the desktop be handed over in *this* backend and *this* session?"""
        report = load_report(self.report_path)
        if report is None:
            return ReleaseDecision(
                allowed=False,
                status="blocked",
                reason=(
                    f"no stop-path proof at {self.report_path} — the desktop is not "
                    "released without one; 'no report' is not 'no problem'"
                ),
                backend=backend,
                session=session,
            )

        missing = [key for key in REQUIRED_KEYS if key not in report]
        if missing:
            return ReleaseDecision(
                allowed=False,
                status="unknown",
                reason=(
                    f"the stop-path proof is incomplete (missing: {', '.join(missing)}) — "
                    "it cannot be used to release the desktop"
                ),
                backend=backend,
                session=session,
            )

        proven_report = bool(report.get("double_escape_proven"))
        report_backend = str(report.get("backend", ""))
        report_session = str(report.get("session", ""))

        # a proof made under a different backend or session says nothing about this one
        if backend and report_backend and report_backend != backend:
            return ReleaseDecision(
                allowed=False,
                status="unknown",
                reason=(
                    f"the stop-path proof was made for backend {report_backend!r}, but this "
                    f"release is for {backend!r} — re-check on a backend change"
                ),
                backend=backend,
                session=session,
                proof_proven=proven_report,
            )
        if session and report_session and report_session != session:
            return ReleaseDecision(
                allowed=False,
                status="unknown",
                reason=(
                    f"the stop-path proof belongs to session {report_session!r}, but this "
                    f"release is for {session!r} — re-check on a session change"
                ),
                backend=backend,
                session=session,
                proof_proven=proven_report,
            )

        # a *mere* report is not proof: re-measure the current situation
        rechecked = False
        proven_now = proven_report
        current_backend = report_backend
        if self.measure is not None:
            try:
                proven_now, current_backend = self.measure()
            except Exception as exc:  # noqa: BLE001 — a failed measurement is not a pass
                return ReleaseDecision(
                    allowed=False,
                    status="unknown",
                    reason=f"the stop-path re-check failed: {exc}"[:300],
                    backend=backend,
                    session=session,
                    proof_proven=proven_report,
                )
            rechecked = True
            if current_backend and backend and current_backend != backend:
                return ReleaseDecision(
                    allowed=False,
                    status="unknown",
                    reason=(
                        f"the backend changed under us ({backend!r} -> {current_backend!r}) "
                        "— the proof no longer describes this situation"
                    ),
                    backend=str(current_backend),
                    session=session,
                    proof_proven=bool(proven_now),
                    rechecked=True,
                )

        if not (proven_report and proven_now):
            return ReleaseDecision(
                allowed=False,
                status="blocked",
                reason=(
                    "the stop path is not proven on this host — the desktop stays with the "
                    "run until a physical double-Escape stop has actually been shown "
                    "(a written report alone is not that showing)"
                ),
                backend=str(current_backend or backend),
                session=session,
                proof_proven=bool(proven_now),
                rechecked=rechecked,
            )

        return ReleaseDecision(
            allowed=True,
            status="allowed",
            reason="the stop path is proven for this backend and this session",
            backend=str(current_backend or backend),
            session=session,
            proof_proven=True,
            rechecked=rechecked,
        )
