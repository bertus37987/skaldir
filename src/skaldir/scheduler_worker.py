"""The scheduler execution service — Paket 5 (audit F09).

The audit's wording is exact: "Vorhanden: Definition, Termine/DST, Anspruch, Abschluss,
Recovery und ``due``. In der untersuchten Produktionssuche kein Dienst/Worker gefunden,
der fällige Termine regelmäßig abholt und tatsächlich einen Workflow startet."

So the store was data and this is the missing service. What it does:

* polls :meth:`ScheduleStore.due` on an interval and **claims** each firing atomically, so
  two workers never both run the same term (the claim is the persisted execution id);
* runs the workflow through the product's own :func:`skaldir.workflows.run_workflow`;
* reports the result against the claim, so a finished run is visible in the ledger;
* **recovers crashed claims** on start — a dead owner's firing is released, but the ledger
  keeps the attempt so it never looks untouched;
* runs **headless**. The assignment's acceptance is "bei geschlossener TUI", which is what
  this is: nothing here needs a terminal, and nothing here opens one.

Two rules it keeps deliberately:

* **No production schedule is activated by this module.** A worker executes what is already
  active. Starting the service never turns a paused schedule on.
* **A schedule never grants money.** The run's own policy decides that; a timed start does
  not add an approval (SCHED-03).
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

__all__ = ["SchedulerWorker", "WorkerTick", "WorkerReport"]


@dataclass
class WorkerTick:
    """What one polling round did."""

    at: float
    seen: int = 0
    claimed: int = 0
    ran: int = 0
    failed: int = 0
    skipped: int = 0
    recovered: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "seen": self.seen,
            "claimed": self.claimed,
            "ran": self.ran,
            "failed": self.failed,
            "skipped": self.skipped,
            "recovered": self.recovered,
            "results": self.results,
        }


@dataclass
class WorkerReport:
    ticks: int = 0
    started: float = 0.0
    stopped: float = 0.0
    firings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticks": self.ticks,
            "started_at": self.started,
            "stopped_at": self.stopped,
            "elapsed_s": round(self.stopped - self.started, 3) if self.stopped else 0.0,
            "firings": self.firings,
        }


class SchedulerWorker:
    """Polls for due schedules and runs them. One instance per runtime."""

    def __init__(
        self,
        store: Any,
        *,
        runner: Callable[..., Any] | None = None,
        interval_s: float = 30.0,
        owner: str = "",
        on_event: Callable[[str, Mapping[str, Any]], None] | None = None,
        evidence_path: str | Path | None = None,
    ) -> None:
        self.store = store
        self.interval_s = float(interval_s)
        self.owner = owner or "scheduler-worker"
        self.on_event = on_event
        self.evidence_path = Path(evidence_path) if evidence_path else None
        # the runner is injected so the worker can be tested without a real workflow
        self.runner = runner or self._default_runner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.report = WorkerReport()

    # -- the default execution path ----------------------------------------

    @staticmethod
    def _default_runner(schedule: Any, occurrence: Any) -> dict[str, Any]:
        """Run the schedule's workflow through the product's own engine."""
        try:
            from .workflows import run_workflow
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__, "reason": str(exc)[:300]}
        try:
            result = run_workflow(
                schedule.workflow_id,
                params=dict(schedule.parameters or {}),
                session_id=f"sched-{schedule.schedule_id}",
            )
        except Exception as exc:  # noqa: BLE001 — a failed run is data, not a crash
            return {"ok": False, "error": type(exc).__name__, "reason": str(exc)[:300]}
        return result if isinstance(result, Mapping) else {"ok": True, "raw": str(result)[:400]}

    # -- one polling round --------------------------------------------------

    def tick(self, *, now: dt.datetime | None = None,
             since: dt.datetime | None = None) -> WorkerTick:
        """Poll once: recover, claim what is due, run it, report it.

        One tick is one unit of work, which is what makes the whole thing testable without
        sleeping through a real interval.
        """
        tick = WorkerTick(at=time.time())
        try:
            tick.recovered = len(self.store.recover())
        except Exception:  # noqa: BLE001 — recovery must not stop the service
            tick.recovered = 0

        try:
            due = self.store.due(now=now, since=since)
        except Exception as exc:  # noqa: BLE001
            self._emit("tick.error", {"error": f"{type(exc).__name__}: {exc}"})
            return tick

        tick.seen = len(due)
        for schedule, occurrence in due:
            claim = None
            try:
                claim = self.store.claim(schedule.schedule_id, occurrence)
            except Exception as exc:  # noqa: BLE001
                self._emit("claim.error", {
                    "schedule_id": schedule.schedule_id, "error": str(exc)[:200],
                })
                continue
            if claim is None:
                # somebody else already has this firing — no double delivery
                tick.skipped += 1
                continue
            tick.claimed += 1

            started = time.time()
            try:
                outcome = self.runner(schedule, occurrence)
            except Exception as exc:  # noqa: BLE001
                outcome = {"ok": False, "error": type(exc).__name__, "reason": str(exc)[:300]}

            ok = bool(outcome.get("ok"))
            detail = str(
                outcome.get("reason") or outcome.get("error") or ""
            )[:300]
            try:
                self.store.finish(claim, state="done" if ok else "failed", detail=detail)
            except Exception:  # noqa: BLE001
                pass

            row = {
                "schedule_id": schedule.schedule_id,
                "workflow_id": schedule.workflow_id,
                "workflow_version": schedule.workflow_version,
                "occurrence": occurrence.key if hasattr(occurrence, "key") else str(occurrence),
                "execution_id": claim.execution_id,
                "ok": ok,
                "duration_s": round(time.time() - started, 3),
                "detail": detail,
            }
            tick.results.append(row)
            self.report.firings.append(row)
            if ok:
                tick.ran += 1
            else:
                tick.failed += 1
            self._emit("firing.done", row)

        self.report.ticks += 1
        self._emit("tick.done", tick.to_dict())
        return tick

    # -- the service loop ---------------------------------------------------

    def run_forever(self, *, max_ticks: int = 0, timeout_s: float = 0.0) -> WorkerReport:
        """Poll until stopped. Headless — no terminal is opened.

        ``max_ticks``/``timeout_s`` bound it for tests; unbounded is the service case.
        """
        self.report.started = time.time()
        self._stop.clear()
        ticks = 0
        deadline = (time.time() + timeout_s) if timeout_s else 0.0
        try:
            while not self._stop.is_set():
                self.tick()
                ticks += 1
                if max_ticks and ticks >= max_ticks:
                    break
                if deadline and time.time() >= deadline:
                    break
                self._stop.wait(self.interval_s)
        finally:
            self.report.stopped = time.time()
            self._write_evidence()
        return self.report

    def start_background(self) -> threading.Thread:
        """Run the loop on a thread — the case where something else owns the process."""
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)

    # -- evidence ----------------------------------------------------------

    def _write_evidence(self) -> None:
        if self.evidence_path is None:
            return
        try:
            self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
            self.evidence_path.write_text(
                json.dumps(self.report.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _emit(self, kind: str, payload: Mapping[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, dict(payload))
            except Exception:  # noqa: BLE001
                pass
