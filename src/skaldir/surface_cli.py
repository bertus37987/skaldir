"""``skaldir tui …`` / ``skaldir stop …`` / ``skaldir desk …`` — the confirmed surface.

One CLI owner for three groups, on purpose: they are the three halves of one confirmed
decision set (``03-TUI-ENTSCHEIDUNGEN.md``, ``01-MASTERPLAN.md`` §8.2 DESK-01..04 and §8.3
STOP-01..05) and they share one state vocabulary. Three register calls in ``cli.py`` would
be fine too, but one owner keeps the wiring to the single documented line below.

WIRING — ``register_cli`` is the whole contract (the group owns its verbs *and* their
``cli_contract.COMMANDS`` entries, like ``host_cli``/``act_cli``)::

    from .surface_cli import register_cli as _register_surface

    _register_surface(sub)

Verbs (all ``--json`` through the product envelope):

    tui preview   [--width N --height N]   the confirmed layout rendered as text
    tui layout    [--width N --height N]   the pane geometry, as data
    tui figure    [--at S]                 the animated figure at a clock reading
    tui approve   [--run ID]               the approval mode and what Tab does next
    stop report   [--out PATH]             the measured double-Escape feasibility report
    stop detect   --gap-ms N ...           run a press sequence through the detector
    desk status                            the desktop lease, queue and corner indicator
    desk acquire/release/stop/resume       the lease lifecycle (writes the lease file only)

Nothing here executes a tool, moves the mouse or opens a window. ``stop report`` reads the
environment; everything else is pure rendering or a small JSON file in the runtime dir.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import Any

from . import browser_layer as BL
from . import desktop_indicator as DI
from . import desktop_lease as DL
from . import stop_signal as SS
from . import tui as TUI
from .approval_modes import ApprovalMode, ApprovalModeStore, policy_for
from .cli_shared import (
    as_subparsers,
    blocked,
    dump,
    envelope_mode,
    install_contract_entries,
    product_contract,
    report_envelope,
    runtime_dir,
)

__all__ = [
    "COMMAND_EVIDENCE",
    "contract_entries",
    "register_cli",
]

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_NOT_FOUND = 5

_CONTRACT = product_contract()

COMMAND_EVIDENCE: dict[str, str] = {
    "tui.preview": "src/skaldir/tui.py:Screen.render",
    "tui.layout": "src/skaldir/tui.py:layout",
    "tui.figure": "src/skaldir/tui.py:figure_frame",
    "tui.approve": "src/skaldir/approval_modes.py:ApprovalModeStore",
    "stop.report": "src/skaldir/stop_signal.py:write_report",
    "stop.detect": "src/skaldir/stop_signal.py:DoubleEscapeDetector",
    "desk.status": "src/skaldir/desktop_lease.py:DesktopLeaseStore",
    "browser.probe": "src/skaldir/browser_layer.py:agent_browser_probe",
    "schedule.define": "src/skaldir/scheduler.py:ScheduleStore.define",
    "schedule.activate": "src/skaldir/scheduler.py:ScheduleStore.activate",
    "schedule.pause": "src/skaldir/scheduler.py:ScheduleStore.pause",
    "schedule.delete": "src/skaldir/scheduler.py:ScheduleStore.delete",
    "schedule.list": "src/skaldir/scheduler.py:ScheduleStore.all",
    "schedule.due": "src/skaldir/scheduler.py:ScheduleStore.due",
    "schedule.run": "src/skaldir/scheduler_worker.py:SchedulerWorker.tick",
    "sources.add": "src/skaldir/data_sources.py:SourceRegistry.attach",
    "sources.report": "src/skaldir/data_sources.py:SourceRegistry.report",
    "voice.rebrief": "src/skaldir/voice_rebrief.py:validate_rebrief",
    "browser.discover": "src/skaldir/browser_layer.py:SessionManager.discover",
    "browser.ensure": "src/skaldir/browser_layer.py:SessionManager.ensure",
    "browser.tabs": "src/skaldir/browser_layer.py:BrowserLayer.tabs",
    "browser.detach": "src/skaldir/browser_layer.py:BrowserLayer.detach",
    "desk.indicator": "src/skaldir/desktop_indicator.py:HostIndicator",
    "desk.indicator-show": "src/skaldir/desktop_indicator.py:HostIndicator.show",
    "desk.indicator-hide": "src/skaldir/desktop_indicator.py:HostIndicator.hide",
    "desk.acquire": "src/skaldir/desktop_lease.py:DesktopLeaseStore.acquire",
    "desk.release": "src/skaldir/desktop_lease.py:DesktopLeaseStore.release",
    "desk.stop": "src/skaldir/desktop_lease.py:DesktopLeaseStore.request_stop",
    "desk.resume": "src/skaldir/desktop_lease.py:DesktopLeaseStore.resume",
}


def _store(args: argparse.Namespace) -> DL.DesktopLeaseStore:
    return DL.DesktopLeaseStore(runtime_dir(args) / "desktop-lease.json")


def _modes(args: argparse.Namespace) -> ApprovalModeStore:
    return ApprovalModeStore(runtime_dir(args) / "approval-modes.json")


# --------------------------------------------------------------------------- #
# tui
# --------------------------------------------------------------------------- #


def _cmd_tui_preview(args: argparse.Namespace) -> int:
    """Render the full-screen TUI.

    With ``--transcript`` this draws a **real** run instead of the built-in example: the
    renderer is fed from actual run/tool events through
    :mod:`skaldir.tui_feed`, which is the GOAL §4 connection ("vorhandenen Renderer an
    echte Run-/Tool-Events anschließen"). The example stays for layout work.
    """
    width = int(getattr(args, "width", 100) or 100)
    height = int(getattr(args, "height", 30) or 30)
    transcript = str(getattr(args, "transcript", "") or "")
    if transcript:
        from .tui_feed import state_from_transcript

        state = state_from_transcript(transcript)
        state.project = str(getattr(args, "project", "") or "")
        state.approval = _modes(args).mode_for(str(getattr(args, "run", "") or "preview"))
        state.desktop = _store(args).indicator()
        lines = TUI.render(state, width, height,
                           unicode=not bool(getattr(args, "ascii", False)),
                           motion=not bool(getattr(args, "no_motion", False)))
        report_envelope({"transcript": transcript, "run_id": state.run_id,
                         "calls": len(state.calls), "chat": len(state.chat),
                         "cursor": state.cursor},
                        [{"ref": COMMAND_EVIDENCE.get("tui.preview", ""), "kind": "code", "note": ""}])
        for line in lines:
            print(line)
        return EXIT_OK
    state = TUI.TuiState(
        project=str(getattr(args, "project", "") or "example"),
        run_id="42",
        chat=["You: Research orbital data centers", "", "Skaldir: Comparing sources …"],
        calls=[
            TUI.CallRow(call_id="#18", label="web_search", state=TUI.CallState.DONE,
                        duration_s=0.78, preview="Comparing sources …",
                        args={"q": "orbital data centers"},
                        details="3 results from 2 domains", exit_code=0),
            TUI.CallRow(call_id="#19", label="page.read", state=TUI.CallState.RUNNING,
                        preview="reading", args={"url": "https://example.org/a"}),
        ],
        goal=TUI.GoalPanel(
            title="Research orbital data centers", phase="compare", run_state="running",
            criteria=[(True, "Sources visited"), (False, "Compare claims"),
                      (False, "Verify report")],
            verification="unknown", revision=2,
        ),
        approval=_modes(args).mode_for(str(getattr(args, "run", "") or "preview")),
        desktop=_store(args).indicator(),
        elapsed_s=84.0,
    )
    lines = TUI.render(state, width, height,
                       unicode=not bool(getattr(args, "ascii", False)),
                       motion=not bool(getattr(args, "no_motion", False)))
    payload = {
        "kind": "skaldir.tui-preview",
        "width": width,
        "height": height,
        "layout": TUI.layout(width, height).__dict__,
        "lines": lines,
        "every_line_exactly_width_cells": all(len(line) == width for line in lines),
        "line_count": len(lines),
    }
    report_envelope(payload, [
        {"ref": COMMAND_EVIDENCE["tui.preview"], "kind": "code",
         "note": "the renderer that produced these lines"},
        {"ref": "docs/masterplan/03-TUI-ENTSCHEIDUNGEN.md", "kind": "doc",
         "note": "the confirmed layout these lines implement"},
    ])
    if envelope_mode():
        dump(payload)
    else:
        for line in lines:
            print(line.rstrip())
    return EXIT_OK


def _cmd_tui_live(args: argparse.Namespace) -> int:
    """The interactive loop, driven by live events (Paket 5 / audit F10).

    Not a transcript preview: events are pushed into the loop and the frame is drawn while
    they arrive. ``--steps`` bounds it so this is usable as a check; the real terminal runs
    unbounded.
    """
    from .tui_live import LiveTui, LiveTuiConfig

    tui = LiveTui(config=LiveTuiConfig(
        width=int(getattr(args, "width", 100) or 100),
        height=int(getattr(args, "height", 30) or 30),
        unicode=not bool(getattr(args, "ascii", False)),
        motion=not bool(getattr(args, "no_motion", False)),
        project=str(getattr(args, "project", "") or ""),
    ))
    transcript = str(getattr(args, "transcript", "") or "")
    if transcript:
        import json as _json

        try:
            lines = Path(transcript).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return blocked(f"cannot read {transcript!r}: {exc}", reason="unreadable")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = _json.loads(line)
            except ValueError:
                continue
            kind = str(event.get("kind", ""))
            tui.push_seq(int(event.get("seq", 0) or 0), kind,
                         **{k: v for k, v in event.items() if k not in ("kind", "seq")})

    steps = tui.run(max_steps=int(getattr(args, "steps", 3) or 3), timeout_s=2.0)
    frame = tui.render()
    payload = {
        "kind": "skaldir.tui-live",
        "steps": steps,
        "frames": tui.frame_count,
        "calls": len(tui.state.calls),
        "chat": len(tui.state.chat),
        "cursor": tui.state.cursor,
        "approval": tui.state.approval.value,
        "lines": frame,
        "every_line_exactly_width_cells": all(len(l) == tui.config.width for l in frame),
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("tui.preview", ""),
                               "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_tui_layout(args: argparse.Namespace) -> int:
    width = int(getattr(args, "width", 100) or 100)
    height = int(getattr(args, "height", 30) or 30)
    geom = TUI.layout(width, height, blocked=bool(getattr(args, "blocked", False)))
    payload = dict(geom.__dict__)
    payload["regime"] = "split" if geom.split else ("compact" if geom.compact else "tiny")
    payload["panes"] = {
        "chat": {"x": geom.chat[0], "y": geom.chat[1], "w": geom.chat[2], "h": geom.chat[3]},
        "tools": {"x": geom.tools[0], "y": geom.tools[1], "w": geom.tools[2], "h": geom.tools[3]},
        "goal": {"x": geom.goal[0], "y": geom.goal[1], "w": geom.goal[2], "h": geom.goal[3]},
        "input": {"x": geom.input[0], "y": geom.input[1], "w": geom.input[2], "h": geom.input[3]},
        "status": {"x": geom.status[0], "y": geom.status[1], "w": geom.status[2],
                   "h": geom.status[3]},
    }
    payload["confirmed_order"] = {
        "chat_is_left": geom.chat[0] == 0 and geom.chat[2] >= geom.tools[2],
        "tools_above_goal": geom.tools[1] < geom.goal[1],
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["tui.layout"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_tui_figure(args: argparse.Namespace) -> int:
    at = float(getattr(args, "at", 0.0) or 0.0)
    motion = not bool(getattr(args, "no_motion", False))
    payload = {
        "frame": TUI.figure_frame(at, motion=motion),
        "at": at,
        "frames": list(TUI.FIGURE_FRAMES),
        "frames_per_second": 8,
        "model_calls_per_frame": 0,
        "humor": TUI.humor_at(at),
        "progress_percent_shown": False,
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["tui.figure"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_tui_approve(args: argparse.Namespace) -> int:
    run_id = str(getattr(args, "run", "") or "default")
    store = _modes(args)
    if getattr(args, "cycle", False):
        mode = store.cycle(run_id)
    elif getattr(args, "set", None):
        try:
            mode = store.set_mode(run_id, str(args.set))
        except ValueError as exc:
            dump({"error": str(exc)})
            return EXIT_USAGE
    else:
        mode = store.mode_for(run_id)
    payload = {
        "run_id": run_id,
        "mode": mode.value,
        "label": mode.label,
        "description": mode.describe(),
        "tab_action_at_empty_prompt": "cycle_approval",
        "tab_action_with_completion": "accept_completion",
        "next_mode_after_tab": mode.next().label,
        "all_modes": [m.label for m in ApprovalMode],
        "policy": policy_for(mode),
        "default_for_new_runs": store.default().label,
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["tui.approve"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# stop
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# voice rebrief (VOICE-03)
# --------------------------------------------------------------------------- #


def _cmd_voice_rebrief(args: argparse.Namespace) -> int:
    """Validate a Voice rebrief against VOICE-03 before a pause is ended by it."""
    from .voice_rebrief import Rebrief, RebriefInvalid, validate_rebrief

    rebrief = Rebrief(
        finding=str(getattr(args, "finding", "") or ""),
        evidence=str(getattr(args, "evidence", "") or ""),
        criteria=tuple(str(c) for c in (getattr(args, "criterion", []) or [])),
        next_step=str(getattr(args, "next_step", "") or ""),
        goal=str(getattr(args, "goal", "") or ""),
    )
    try:
        validate_rebrief(rebrief)
    except RebriefInvalid as exc:
        payload = {"ok": False, "reason": str(exc), "rebrief": rebrief.to_dict(),
                   "valid": False}
        report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("voice.rebrief", ""),
                                   "kind": "code", "note": ""}])
        dump(payload)
        return EXIT_BLOCKED
    payload = {"ok": True, "valid": True, "rebrief": rebrief.to_dict(),
               "note": ("VOICE-03: finding, evidence, affected criteria and a concrete "
                        "next step — a rebrief never rewrites the goal and never widens "
                        "rights")}
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("voice.rebrief", ""),
                               "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# data sources (DOCKER-05)
# --------------------------------------------------------------------------- #


def _sources(args) -> Any:
    """The declared sources, held in the runtime dir so they persist across runs."""
    from .data_sources import DataSource, SourceKind, SourceRegistry

    store = SourceRegistry()
    path = runtime_dir(args) / "data-sources.json"
    try:
        import json as _json

        raw = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — no file yet is not an error
        raw = {}
    for name, record in (raw.get("sources", {}) or {}).items():
        store.register_source(DataSource(
            name=name,
            kind=SourceKind(record.get("kind", "free")),
            provider=str(record.get("provider", "")),
            terms_url=str(record.get("terms_url", "")),
            licence=str(record.get("licence", "")),
            requires=tuple(record.get("requires", ()) or ()),
        ))
    for tool, names in (raw.get("tools", {}) or {}).items():
        for name in names:
            source = store.source(name)
            if source is not None:
                try:
                    store.attach(tool, source, allow_paid=True, approved_by="persisted")
                except Exception:  # noqa: BLE001
                    pass
    return store, path


def _cmd_sources_add(args: argparse.Namespace) -> int:
    """Declare a source. DOCKER-05: a money-costing one must be named explicitly."""
    from .data_sources import DataSource, PaidSourceNotAllowed, SourceKind

    store, path = _sources(args)
    kind = SourceKind(str(getattr(args, "kind", "") or "free"))
    source = DataSource(
        name=str(getattr(args, "name", "") or ""),
        kind=kind,
        provider=str(getattr(args, "provider", "") or ""),
        terms_url=str(getattr(args, "terms", "") or ""),
        licence=str(getattr(args, "licence", "") or ""),
        requires=tuple(str(r) for r in (getattr(args, "require", []) or [])),
    )
    try:
        store.register_source(source)
        if getattr(args, "tool", ""):
            store.attach(
                str(args.tool), source,
                allow_paid=bool(getattr(args, "allow_paid", False)),
                approved_by=str(getattr(args, "approved_by", "") or ""),
            )
    except PaidSourceNotAllowed as exc:
        # dump() rather than blocked(): the refusal is the *result* of this command and
        # has to reach stdout regardless of which contract layer is installed
        payload = {"ok": False, "reason": str(exc), "source": source.to_dict(),
                   "paid_source_not_allowed": True}
        report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.define", ""),
                                   "kind": "code", "note": ""}])
        dump(payload)
        return EXIT_BLOCKED
    _save_sources(path, store)
    payload = {"source": source.to_dict(), "tool": getattr(args, "tool", "")}
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.define", ""), "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _save_sources(path: Any, store: Any) -> None:
    import json as _json

    data = {"sources": {s.name: s.to_dict() for t in store.all_tools() for s in t.sources},
            "tools": {t.tool: [s.name for s in t.sources] for t in store.all_tools()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _cmd_sources_report(args: argparse.Namespace) -> int:
    """The honest report — including what has NOT been measured (DOCKER-05)."""
    store, _path = _sources(args)
    tool = str(getattr(args, "tool", "") or "")
    payload = store.report(tool) if tool else {
        "tools": [t.to_dict() for t in store.all_tools()],
        "disclaimer": ("Online availability is not universal and is not claimed here. "
                       "Access rights, data availability and each provider's terms apply."),
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.define", ""), "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# schedule (SCHED-01..04)
# --------------------------------------------------------------------------- #


def _sched_store(args) -> Any:
    from .scheduler import ScheduleStore

    return ScheduleStore(runtime_dir(args) / "schedules.json", owner="cli")


def _cmd_schedule_define(args: argparse.Namespace) -> int:
    """SCHED-04: defining is NOT activating — nothing fires after this."""
    from .scheduler import CatchUp, Schedule

    schedule = Schedule(
        schedule_id=str(getattr(args, "id", "") or ""),
        workflow_id=str(getattr(args, "workflow", "") or ""),
        workflow_version=str(getattr(args, "version", "") or ""),
        timezone=str(getattr(args, "timezone", "") or "Europe/Berlin"),
        at_local=str(getattr(args, "at", "") or "00:00"),
        parameters=json.loads(str(getattr(args, "params", "") or "{}")),
        policy=json.loads(str(getattr(args, "policy", "") or "{}")),
        catch_up=str(getattr(args, "catch_up", "") or CatchUp.ONCE),
    )
    saved = _sched_store(args).define(schedule)
    payload = {"schedule": saved.to_dict(), "active": False,
               "note": "SCHED-04: defined but NOT active — run `skaldir schedule activate` to enable it"}
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.define", ""), "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_schedule_lifecycle(args: argparse.Namespace) -> int:
    """activate / pause / delete — deliberately separate verbs (SCHED-04)."""
    store = _sched_store(args)
    schedule_id = str(getattr(args, "id", "") or "")
    action = str(getattr(args, "lifecycle", "") or "")
    try:
        if action == "activate":
            result = store.activate(schedule_id).to_dict()
        elif action == "pause":
            result = store.pause(schedule_id).to_dict()
        elif action == "delete":
            result = {"deleted": store.delete(schedule_id), "schedule_id": schedule_id,
                      "note": "the execution ledger is kept: what already ran stays recorded"}
        else:
            return usage(f"unknown schedule action {action!r}")
    except KeyError as exc:
        return blocked(str(exc), reason="not_found")
    payload = {"action": action, "result": result}
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.define", ""), "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_schedule_list(args: argparse.Namespace) -> int:
    store = _sched_store(args)
    payload = {"schedules": [s.to_dict() for s in store.all()],
               "executions": [c.to_dict() for c in store.executions()]}
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.define", ""), "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_schedule_run(args: argparse.Namespace) -> int:
    """The scheduler EXECUTION SERVICE (Paket 5 / audit F09).

    The store was data; this is the worker that picks due terms up and actually starts a
    workflow. Headless by design — the acceptance is "bei geschlossener TUI".
    """
    import datetime as _dt

    from .scheduler_worker import SchedulerWorker

    store = _sched_store(args)
    evidence = runtime_dir(args) / "scheduler-worker.json"
    worker = SchedulerWorker(
        store,
        interval_s=float(getattr(args, "interval", 0) or 30.0),
        owner="cli",
        evidence_path=evidence,
    )
    ticks = int(getattr(args, "ticks", 0) or 0)
    if ticks:
        worker.run_forever(max_ticks=ticks)
    else:
        worker.tick(now=_dt.datetime.now(_dt.timezone.utc))
    payload = {
        "report": worker.report.to_dict(),
        "evidence": str(evidence),
        "executions": [c.to_dict() for c in store.executions()],
        "note": ("the worker executes only schedules that are already ACTIVE; starting "
                 "it never turns a paused schedule on, and a schedule never grants money"),
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.due", ""),
                               "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_schedule_due(args: argparse.Namespace) -> int:
    """What should fire now — SCHED-01/02, honouring activation, overlap and catch-up."""
    import datetime as _dt

    store = _sched_store(args)
    now = _dt.datetime.now(_dt.timezone.utc)
    due = store.due(now=now)
    payload = {
        "now": now.isoformat(),
        "due": [{"schedule_id": s.schedule_id, "workflow_id": s.workflow_id,
                 "workflow_version": s.workflow_version, "occurrence": o.key,
                 "local": o.local.isoformat()} for s, o in due],
        "blocked_by_overlap": [s.schedule_id for s in store.all()
                               if s.active and s.serialize and store.is_running(s.schedule_id)],
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE.get("schedule.define", ""), "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_stop_report(args: argparse.Namespace) -> int:
    """The measured stop-path feasibility report (STOP-05).

    ``--proof FILE`` hands in the live evidence for one backend (what was observed, from
    which device, on which session). Without it the report says the stop is unproven — it
    never claims a proof it was not given.
    """
    out = Path(str(getattr(args, "out", "") or "")) if getattr(args, "out", "") else (
        runtime_dir(args) / "stop-feasibility.json"
    )
    proof = None
    proof_path = str(getattr(args, "proof", "") or "")
    if proof_path:
        try:
            proof = json.loads(Path(proof_path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — an unreadable proof is not a proof
            return blocked(f"cannot read the proof at {proof_path!r}: {exc}",
                           reason="proof_unreadable")
    report = SS.write_report(out, proof=proof)
    report["report_path"] = str(out)
    report_envelope(report, [{"ref": COMMAND_EVIDENCE["stop.report"], "kind": "code", "note": ""}])
    dump(report)
    return EXIT_OK


def _cmd_stop_detect(args: argparse.Namespace) -> int:
    """Feed a press sequence through the detector. ``--gap-ms`` repeats = the gaps."""
    gaps = [float(g) for g in (getattr(args, "gap_ms", []) or [])]
    origins = [str(o) for o in (getattr(args, "origin", []) or [])] or ["user"] * (len(gaps) + 1)
    repeats = {int(i) for i in (getattr(args, "autorepeat_at", []) or [])}
    events: list[SS.InputEvent] = []
    clock = 0.0
    for index in range(len(gaps) + 1):
        if index > 0:
            clock += gaps[index - 1] / 1000.0
        events.append(SS.InputEvent(
            key="esc", at=clock,
            origin=SS.Origin(origins[index]) if origins[index] in SS.Origin._value2member_map_
            else SS.Origin.UNKNOWN,
            autorepeat=index in repeats,
        ))
    det = SS.DoubleEscapeDetector(
        window_ms=int(getattr(args, "window_ms", SS.DEFAULT_WINDOW_MS) or SS.DEFAULT_WINDOW_MS),
        min_gap_ms=int(getattr(args, "min_gap_ms", SS.DEFAULT_MIN_GAP_MS) or SS.DEFAULT_MIN_GAP_MS),
    )
    fired = None
    for event in events:
        fired = det.feed(event)
        if fired is not None:
            break
    payload = {
        "presses": len(events),
        "gaps_ms": gaps,
        "origins": origins,
        "autorepeat_at": sorted(repeats),
        "window_ms": det.window_ms,
        "min_gap_ms": det.min_gap_ms,
        "stopped": fired is not None,
        "signal": fired.to_dict() if fired is not None else None,
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["stop.detect"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# desk
# --------------------------------------------------------------------------- #


def _desk_payload(store: DL.DesktopLeaseStore) -> dict[str, Any]:
    lease = store.current()
    info = store.indicator()
    return {
        "state": store.state(),
        "generation": store.generation(),
        "lease": lease.to_dict() if lease else None,
        "waiters": store.waiters(),
        "indicator": info.to_dict(),
        "indicator_lines": DL.indicator_lines(info),
        "stop_hint": "Esc, Esc to pause",
    }


def _browser_sessions(args: argparse.Namespace) -> BL.SessionManager:
    return BL.SessionManager(runtime_dir(args) / "browser")


def _cmd_browser_probe(args: argparse.Namespace) -> int:
    """§9.4: measured capability, and a visible refusal instead of a download loop."""
    payload = BL.agent_browser_probe().to_dict()
    payload["node"] = _node_version()
    payload["note"] = ("this probe never installs anything; it reports the exact pinned "
                       "install command when the tool is absent (§9.2)")
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["browser.probe"], "kind": "code",
                               "note": "the measured capability"}])
    dump(payload)
    return EXIT_OK if payload["available"] else EXIT_BLOCKED


def _node_version() -> str:
    import shutil as _sh
    import subprocess as _sp

    node = _sh.which("node")
    if not node:
        return "not installed"
    try:
        proc = _sp.run([node, "--version"], capture_output=True, text=True, timeout=10,
                       check=False)
        return (proc.stdout or proc.stderr or "").strip()[:40] or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _cmd_browser_discover(args: argparse.Namespace) -> int:
    """§6: what exists right now. Read-only; never creates anything."""
    payload = _browser_sessions(args).discover()
    payload["capability"] = BL.agent_browser_probe().to_dict()
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["browser.discover"], "kind": "code",
                               "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_browser_ensure(args: argparse.Namespace) -> int:
    """§6: the one entry point that may start a browser — and only once."""
    result = _browser_sessions(args).ensure(
        task=str(getattr(args, "task", "") or ""), run_id=str(getattr(args, "run", "") or ""),
        allow_create=not bool(getattr(args, "no_create", False)))
    report_envelope(result, [{"ref": COMMAND_EVIDENCE["browser.ensure"], "kind": "code",
                              "note": ""}])
    dump(result)
    return EXIT_OK if result.get("action") in ("reused", "launched") else EXIT_BLOCKED


def _cmd_browser_tabs(args: argparse.Namespace) -> int:
    sessions = _browser_sessions(args)
    inventory = sessions.discover()
    tabs = [dict(t) for t in (sessions._read().get("tabs") or [])]
    payload = {"tabs": tabs, "agent_tabs": inventory["agent_tabs"],
               "user_tabs": inventory["user_tabs"],
               "rule": "only agent-created tabs may be closed; there is no close --all (§6)"}
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["browser.tabs"], "kind": "code",
                               "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_browser_detach(args: argparse.Namespace) -> int:
    sessions = _browser_sessions(args)
    inventory = sessions.discover()
    report = sessions.close_agent_tabs([str(t) for t in (getattr(args, "tab", []) or [])])
    payload = {
        "detached": True,
        "foreign_browsers_closed": False,
        "closed_agent_tabs": [t["tab_id"] for t in report["closed"]],
        "refused_user_tabs": [t["tab_id"] for t in report["refused_user_tabs"]],
        "left_open": inventory.get("tab_count", 0),
        "rule": "a foreign browser is never closed by a detach (BR-13)",
    }
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["browser.detach"], "kind": "code",
                               "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_desk_indicator(args: argparse.Namespace) -> int:
    """What the corner indicator can do *here* — measured, never assumed (DESK-03)."""
    payload = DI.status()
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.indicator"], "kind": "code",
                              "note": "the measured host backend"}])
    dump(payload)
    return EXIT_OK


def _cmd_desk_indicator_show(args: argparse.Namespace) -> int:
    """Put the corner bar on screen for this run's task. Read the status back."""
    store = _store(args)
    info = store.indicator()
    if getattr(args, "task", ""):
        info = DL.IndicatorState(state=info.state, task=str(args.task), run_id=info.run_id)
    indicator = DI.HostIndicator(corner=str(getattr(args, "corner", "") or "bottom-right"))
    result = indicator.show(info)
    hold = float(getattr(args, "hold", 0.0) or 0.0)
    shown = bool(result.visible)   # the show's own verdict — not the post-hold state
    if hold > 0 and result.visible:
        # The window belongs to this process: it disappears when the command exits. A
        # real run holds it open for the duration of its control; `--hold` makes that
        # visible from the CLI so the bar can actually be looked at.
        import time as _time

        _time.sleep(min(hold, 3600.0))
        indicator.hide()
        indicator.close()
        result = DI.IndicatorStatus(state="hidden", reason=f"held for {hold:g}s",
                                    backend=result.backend, geometry=result.geometry,
                                    lines=result.lines, since=result.since)
    payload = result.to_dict()
    payload["lease_state"] = store.state()
    payload["note"] = (
        "the window is owned by this process; `desk indicator-hide` (or the process "
        "ending) takes it away"
    )
    payload["shown"] = shown
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.indicator-show"],
                               "kind": "code", "note": ""}])
    dump(payload)
    # `shown` is the question this command answers: did the bar go on screen? A bar that
    # was visible for `--hold` and then taken away is a success, not a blocker.
    return EXIT_OK if shown else EXIT_BLOCKED


def _cmd_desk_indicator_hide(args: argparse.Namespace) -> int:
    result = DI.HostIndicator().hide()
    payload = result.to_dict()
    payload["note"] = ("a hide only reaches a window this process created; a bar left by a "
                       "previous run disappears when that process ends")
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.indicator-hide"],
                               "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_desk_status(args: argparse.Namespace) -> int:
    payload = _desk_payload(_store(args))
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.status"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_desk_acquire(args: argparse.Namespace) -> int:
    store = _store(args)
    run_id = str(getattr(args, "run", "") or "")
    if not run_id:
        dump({"error": "--run is required"})
        return EXIT_USAGE
    try:
        lease = store.acquire(run_id, task=str(getattr(args, "task", "") or ""),
                              session=str(getattr(args, "session", "") or ""),
                              force=bool(getattr(args, "force", False)))
    except DL.LeaseRefused as exc:
        return blocked(str(exc), reason="desktop_lease_held",
                       details=_desk_payload(store))
    payload = _desk_payload(store)
    payload["acquired"] = lease.to_dict()
    payload["fencing_token"] = lease.owner_token
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.acquire"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_desk_release(args: argparse.Namespace) -> int:
    """Release the host desktop — but only against a *current* stop-path proof.

    GOAL §3: the release must consume a current stop-path proof and re-check on a backend
    or session change; a missing proof stays unknown/blocked and a mere report is not a
    proof. :class:`~skaldir.stop_release_gate.StopReleaseGate` is that decision, and this
    command is where it is actually reached.
    """
    from .stop_release_gate import StopReleaseGate

    store = _store(args)
    run_id = str(getattr(args, "run", "") or "")
    backend = str(getattr(args, "backend", "") or "")
    session = str(getattr(args, "session", "") or "")
    report_path = Path(str(getattr(args, "proof", "") or "")) if getattr(args, "proof", "") else (
        runtime_dir(args) / "stop-feasibility.json"
    )

    gate = StopReleaseGate(report_path, measure=_measure_stop_path)
    decision = gate.release(backend=backend, session=session)

    payload = _desk_payload(store)
    payload["stop_path"] = decision.to_dict()
    payload["proof_path"] = str(report_path)
    payload["released"] = False
    if not decision.allowed:
        # dump() rather than blocked(): the refusal is the *result* of this command and
        # has to reach the caller's stdout no matter which contract layer is installed
        payload["ok"] = False
        payload["reason"] = f"the desktop is not released: {decision.reason}"
        report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.release"], "kind": "code", "note": ""}])
        dump(payload)
        return EXIT_BLOCKED

    ok = store.release(run_id, reason=str(getattr(args, "reason", "") or ""))
    payload["released"] = bool(ok)
    if not ok:
        return blocked(f"run {run_id!r} does not hold the desktop lease",
                       reason="not_lease_owner", details=payload)
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.release"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _measure_stop_path() -> tuple[bool, str]:
    """Re-measure the stop path at release time.

    The gate must not trust a stored claim, so this re-runs the probe. Until a backend has
    actually shown a physical double-Escape (STOP-05) the answer is ``False`` — which is
    the honest one, and it is what keeps the desktop with the run.
    """
    probes = SS.probe_backends()
    usable = [p for p in probes
              if p.availability is SS.Availability.AVAILABLE and p.name != "agent-injection"]
    proven = bool(usable) and any(bool(getattr(p, "double_escape", False)) for p in usable)
    backend = usable[0].name if usable else ""
    return proven, backend


def _cmd_desk_stop(args: argparse.Namespace) -> int:
    """Run the whole emergency-stop chain, not only the fence bump.

    GOAL §3 asks for detector → queue → fence invalidation → abort of further input →
    paused run → visible status. :class:`~skaldir.stop_dispatch.StopDispatcher` is that
    chain; this command is where a human can invoke it.
    """
    from .stop_dispatch import StopDispatcher

    store = _store(args)
    run_id = str(getattr(args, "run", "") or "") or (store.current().run_id if store.current() else "")
    statuses: list[str] = []
    dispatcher = StopDispatcher(store, on_status=statuses.append)
    receipt = dispatcher.stop(run_id, reason=str(getattr(args, "reason", "") or "double-escape"))

    payload = _desk_payload(store)
    payload["stop"] = receipt.to_dict()
    payload["status_trace"] = statuses
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.stop"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK if receipt.applied else EXIT_BLOCKED


def _cmd_desk_resume(args: argparse.Namespace) -> int:
    store = _store(args)
    run_id = str(getattr(args, "run", "") or "")
    try:
        lease = store.resume(run_id)
    except DL.LeaseRefused as exc:
        return blocked(str(exc), reason="not_lease_owner", details=_desk_payload(store))
    payload = _desk_payload(store)
    payload["resumed"] = lease.to_dict()
    payload["new_fencing_token"] = lease.owner_token
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.resume"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entries for all three groups."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    reads = (EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND)

    def entry(path: str, group: str, what: str, schema: dict[str, Any], notes: str = "") -> Any:
        return spec(group, COMMAND_EVIDENCE[path], what, exit_codes=reads,
                    data_schema=schema, notes=notes)

    object_schema = {"type": "object", "properties": {}}

    # SCHED-01..04 and DOCKER-05 (added with the scheduler and the data-source
    # registry). `schedule.activate|pause|delete` deliberately share one schema with
    # `define`: they are the *same* shape of answer, and the contract says so.
    schedule_schema = {
        "type": "object",
        "properties": {
            "schedule_id": {"type": "string"},
            "workflow_id": {"type": "string"},
            "workflow_version": {"type": "string"},
            "timezone": {"type": "string"},
            "at_local": {"type": "string"},
            "catch_up": {"type": "string"},
            "active": {"type": "boolean"},
        },
        "required": ["schedule_id", "workflow_id", "active"],
    }
    due_schema = {
        "type": "object",
        "properties": {
            "now": {"type": "string"},
            "due": {"type": "array", "items": {"type": "object"}},
            "blocked_by_overlap": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["now", "due"],
    }
    source_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "kind": {"type": "string"},
            "terms_url": {"type": "string"},
            "licence": {"type": "string"},
            "costs_money": {"type": "boolean"},
            "availability": {"type": "string"},
        },
        "required": ["name", "kind", "costs_money"],
    }
    source_report_schema = {
        "type": "object",
        "properties": {
            "tool": {"type": "string"},
            "declared": {"type": "boolean"},
            "unknown_availability": {"type": "array", "items": {"type": "string"}},
            "disclaimer": {"type": "string"},
        },
        "required": ["tool"],
    }
    return {
        "schedule.define": entry(
            "schedule.define", "kernel",
            "define a schedule; it is NOT active afterwards (SCHED-04)",
            schedule_schema,
            "definition, activation, pause and deletion are separate actions; a defined "
            "schedule fires nothing until it is activated"),
        "schedule.activate": entry(
            "schedule.activate", "kernel", "activate a defined schedule (SCHED-04)",
            schedule_schema, "the deliberate act that lets a schedule fire"),
        "schedule.pause": entry(
            "schedule.pause", "kernel", "pause an active schedule (SCHED-04)",
            schedule_schema, "paused schedules never fire; runs already recorded stay"),
        "schedule.delete": entry(
            "schedule.delete", "kernel",
            "delete a schedule; the execution ledger is kept",
            object_schema,
            "deleting a definition must not erase what it already ran"),
        "schedule.run": entry(
            "schedule.run", "kernel",
            "the scheduler execution service: run due terms headless (Paket 5)",
            due_schema,
            "executes only ACTIVE schedules; never turns a paused one on, and a schedule "
            "never grants money (SCHED-03)"),
        "schedule.list": entry(
            "schedule.list", "kernel", "all schedules and the execution ledger",
            object_schema, "the persisted execution ids are what prevent double starts"),
        "schedule.due": entry(
            "schedule.due", "kernel",
            "what should fire now, honouring activation, overlap and catch-up (SCHED-01/02)",
            due_schema,
            "catch-up runs once by default; 'all' must be asked for by name"),
        "sources.add": entry(
            "sources.add", "kernel",
            "declare a data source; paid ones must be explicit (DOCKER-05)",
            source_schema,
            "a money-costing source cannot be attached silently and needs a named "
            "approver (APR-02)"),
        "voice.rebrief": entry(
            "voice.rebrief", "kernel",
            "validate a Voice rebrief before it ends a pause (VOICE-03)",
            {"type": "object",
             "properties": {"finding": {"type": "string"}, "evidence": {"type": "string"},
                            "criteria": {"type": "array", "items": {"type": "string"}},
                            "next_step": {"type": "string"}, "valid": {"type": "boolean"}},
             "required": ["finding", "evidence", "criteria", "next_step", "valid"]},
            "a rebrief names the finding, the evidence, the affected goal criteria and a "
            "concrete next step; it never rewrites the goal and never widens rights"),
        "sources.report": entry(
            "sources.report", "kernel",
            "what is honestly known about a tool's data sources (DOCKER-05)",
            source_report_schema,
            "availability is a measurement; an unmeasured source reports 'unknown', and "
            "the report carries the disclaimer that online availability is not universal"),
        "tui.preview": entry(
            "tui.preview", "kernel", "the confirmed full-screen layout rendered as text",
            {"type": "object",
             "properties": {"width": {"type": "integer"}, "height": {"type": "integer"},
                            "lines": {"type": "array", "items": {"type": "string"}},
                            "layout": {"type": "object"},
                            "every_line_exactly_width_cells": {"type": "boolean"},
                            "line_count": {"type": "integer"}},
             "required": ["width", "height", "lines", "line_count"]},
            "chat left, tools upper-right, goal lower-right; every line is exactly "
            "--width cells. This renders a fixture state: it proves the layout, not a run"),
        "tui.layout": entry(
            "tui.layout", "kernel", "the pane geometry at a terminal size, as data",
            {"type": "object",
             "properties": {"width": {"type": "integer"}, "height": {"type": "integer"},
                            "split": {"type": "boolean"}, "compact": {"type": "boolean"},
                            "regime": {"type": "string"}, "panes": {"type": "object"}},
             "required": ["width", "height", "regime", "panes"]},
            "split above 72x24, stacked below, compact below 40x10"),
        "tui.figure": entry(
            "tui.figure", "kernel", "the animated figure at a clock reading",
            object_schema, "deterministic in --at; 0 model calls per frame; never a percentage"),
        "tui.approve": entry(
            "tui.approve", "kernel", "the approval mode for a run and what Tab does",
            {"type": "object",
             "properties": {"run_id": {"type": "string"}, "mode": {"type": "string"},
                            "label": {"type": "string"}, "policy": {"type": "object"},
                            "next_mode_after_tab": {"type": "string"}},
             "required": ["run_id", "mode", "label", "policy"]},
            "exactly three modes (APR-01); spending and irreversible effects need approval "
            "in all of them (APR-02)"),
        "stop.report": entry(
            "stop.report", "kernel",
            "the measured double-Escape feasibility report for this host",
            {"type": "object",
             "properties": {"backends": {"type": "array"}, "verdict": {"type": "object"}},
             "required": ["backends", "verdict"]},
            "STOP-04/STOP-05: backends are measured, never assumed, and the verdict does "
            "not claim the double press is proven while no backend delivers it"),
        "stop.detect": entry(
            "stop.detect", "kernel", "run a press sequence through the stop detector",
            {"type": "object",
             "properties": {"presses": {"type": "integer"}, "stopped": {"type": "boolean"},
                            "signal": {"type": ["object", "null"]}},
             "required": ["presses", "stopped"]},
            "pure logic; --gap-ms sets the gaps, --origin marks a synthetic press"),
        "browser.probe": entry(
            "browser.probe", "kernel",
            "the measured browser backend: version, licence, supported operations",
            object_schema, "§9.2/§9.4: never installs anything; a missing tool reports the "
            "exact pinned install command, and a tool that will not name its version is "
            "refused as unpinnable"),
        "browser.discover": entry(
            "browser.discover", "kernel",
            "what browser state exists right now — read-only, never creates anything",
            object_schema, "§6: 'window not observable' is never read as 'browser missing'"),
        "browser.ensure": entry(
            "browser.ensure", "kernel",
            "the single entry point that may start a browser, and only once",
            object_schema, "§6/BR-01: repeated calls reuse one instance; a start lock "
            "prevents parallel launches; an unconfirmed state is diagnosed, never relaunched"),
        "browser.tabs": entry(
            "browser.tabs", "kernel", "tab inventory with owner and run id",
            object_schema, "§4/§6: stable tab ids; only agent-created tabs may be closed"),
        "browser.detach": entry(
            "browser.detach", "kernel", "release the session; never close a foreign browser",
            object_schema, "BR-13: user tabs and foreign browsers stay open"),
        "desk.indicator": entry(
            "desk.indicator", "kernel",
            "what the host corner indicator can do on this machine, measured",
            object_schema, "DESK-03: reports `unavailable` with a reason instead of "
            "pretending; the backend is named (x11-xwayland, not a native Wayland surface)"),
        "desk.indicator-show": entry(
            "desk.indicator-show", "kernel", "put the corner bar on screen",
            object_schema, "read the status back: `visible` is the only state in which "
            "anything is on screen"),
        "desk.indicator-hide": entry(
            "desk.indicator-hide", "kernel", "take the corner bar away",
            object_schema, ""),
        "desk.status": entry(
            "desk.status", "kernel", "the desktop lease, its queue and the corner indicator",
            {"type": "object",
             "properties": {"state": {"type": "string"}, "generation": {"type": "integer"},
                            "lease": {"type": ["object", "null"]}, "waiters": {"type": "array"},
                            "indicator": {"type": "object"}},
             "required": ["state", "generation", "waiters", "indicator"]},
            "DESK-01/DESK-03: one owner, a visible queue, and the confirmed corner status bar"),
        "desk.acquire": entry(
            "desk.acquire", "kernel", "take the desktop lease (writes the lease file only)",
            object_schema, "refused with exit 3 while another run owns it; the generation is "
            "the fencing token the input adapter checks (DESK-02)"),
        "desk.release": entry(
            "desk.release", "kernel", "give the desktop back",
            object_schema, "the generation is never reused"),
        "desk.stop": entry(
            "desk.stop", "kernel", "the emergency stop: pause the owner and bump the fence",
            object_schema, "DESK-02: actions queued with the older generation are dropped, "
            "already executed ones are not undone"),
        "desk.resume": entry(
            "desk.resume", "kernel", "explicit user resume after a stop",
            object_schema, "always bumps the fencing generation (RUN-03)"),
    }


def register_cli(subparsers: Any) -> Any:
    """Add the ``tui``/``stop``/``desk`` groups to the product CLI."""
    sub = as_subparsers(subparsers)

    tui = sub.add_parser("tui", help="the full-screen terminal UI: preview, layout, figure")
    tui_sub = tui.add_subparsers(dest="tui_command", required=True)

    prev = tui_sub.add_parser("preview", help="render the confirmed layout as text")
    prev.add_argument("--width", type=int, default=100)
    prev.add_argument("--height", type=int, default=30)
    prev.add_argument("--project", default="")
    prev.add_argument("--run", default="")
    prev.add_argument("--ascii", action="store_true", help="ASCII only, no Unicode glyphs")
    prev.add_argument("--no-motion", action="store_true", help="static figure")
    prev.add_argument("--transcript", default="",
                      help="render a REAL run from this transcript instead of the example")
    prev.set_defaults(func=_cmd_tui_preview)

    lay = tui_sub.add_parser("layout", help="the pane geometry at a terminal size, as data")
    lay.add_argument("--width", type=int, default=100)
    lay.add_argument("--height", type=int, default=30)
    lay.add_argument("--blocked", action="store_true", help="reserve the blocker row")
    lay.set_defaults(func=_cmd_tui_layout)

    lv = tui_sub.add_parser("live", help="the interactive loop over live events (Paket 5)")
    lv.add_argument("--width", type=int, default=100)
    lv.add_argument("--height", type=int, default=30)
    lv.add_argument("--project", default="")
    lv.add_argument("--transcript", default="", help="feed these events in, then loop")
    lv.add_argument("--steps", type=int, default=3, help="bounded so this is a check")
    lv.add_argument("--ascii", action="store_true")
    lv.add_argument("--no-motion", action="store_true")
    lv.set_defaults(func=_cmd_tui_live)

    fig = tui_sub.add_parser("figure", help="the animated figure at a clock reading")
    fig.add_argument("--at", type=float, default=0.0)
    fig.add_argument("--no-motion", action="store_true")
    fig.set_defaults(func=_cmd_tui_figure)

    app = tui_sub.add_parser("approve", help="the approval mode and what Tab does next")
    app.add_argument("--run", default="default")
    app.add_argument("--cycle", action="store_true", help="Tab: advance the mode one step")
    app.add_argument("--set", default="", help="ask | auto | always")
    app.set_defaults(func=_cmd_tui_approve)

    stop = sub.add_parser("stop", help="the global emergency stop (double-Escape)")
    stop_sub = stop.add_subparsers(dest="stop_command", required=True)

    rep = stop_sub.add_parser("report", help="measure the double-Escape feasibility here")
    rep.add_argument("--out", default="")
    rep.add_argument("--proof", default="",
                     help="JSON file with the live evidence for one backend (STOP-05)")
    rep.set_defaults(func=_cmd_stop_report)

    # SCHED-04: definition, activation, pause and deletion are separate commands
    src = sub.add_parser("sources", help="data sources for container tools (DOCKER-05)")
    src_sub = src.add_subparsers(dest="sources_cmd")
    sa = src_sub.add_parser("add", help="declare a data source (paid ones must be explicit)")
    sa.add_argument("--name", required=True)
    sa.add_argument("--kind", default="free",
                    choices=["free", "free_account", "paid", "metered"])
    sa.add_argument("--provider", default="")
    sa.add_argument("--terms", default="", help="URL of the provider's terms of use")
    sa.add_argument("--licence", default="")
    sa.add_argument("--require", action="append", default=[],
                    help="what the caller must supply (api_key, region=…)")
    sa.add_argument("--tool", default="", help="attach it to this tool as well")
    sa.add_argument("--allow-paid", dest="allow_paid", action="store_true",
                    help="required for paid/metered sources (DOCKER-05)")
    sa.add_argument("--approved-by", dest="approved_by", default="",
                    help="who approved the spend (APR-02)")
    sa.set_defaults(func=_cmd_sources_add)
    sr = src_sub.add_parser("report", help="what is honestly known about a tool's sources")
    sr.add_argument("--tool", default="")
    sr.set_defaults(func=_cmd_sources_report)

    sch = sub.add_parser("schedule", help="persistent schedules (SCHED-01..04)")
    sch_sub = sch.add_subparsers(dest="schedule_cmd")
    sd = sch_sub.add_parser("define", help="define a schedule (NOT active afterwards)")
    sd.add_argument("--id", default="")
    sd.add_argument("--workflow", required=True)
    sd.add_argument("--version", default="", help="the ACTIVATED workflow version")
    sd.add_argument("--timezone", default="Europe/Berlin")
    sd.add_argument("--at", default="00:00", help="local time of day, HH:MM")
    sd.add_argument("--params", default="{}", help="parameters as JSON")
    sd.add_argument("--policy", default="{}", help="policy as JSON")
    sd.add_argument("--catch-up", dest="catch_up", default="once", choices=["skip", "once", "all"])
    sd.set_defaults(func=_cmd_schedule_define)
    for name in ("activate", "pause", "delete"):
        sl = sch_sub.add_parser(name, help=f"{name} a schedule (a separate action, SCHED-04)")
        sl.add_argument("--id", required=True)
        sl.set_defaults(func=_cmd_schedule_lifecycle, lifecycle=name)
    sls = sch_sub.add_parser("list", help="all schedules and the execution ledger")
    sls.set_defaults(func=_cmd_schedule_list)
    sdu = sch_sub.add_parser("due", help="what should fire now")
    sdu.set_defaults(func=_cmd_schedule_due)
    srun = sch_sub.add_parser(
        "run", help="the execution service: pick due terms up and run them (headless)")
    srun.add_argument("--ticks", type=int, default=0,
                      help="poll this many times and exit (0 = one poll)")
    srun.add_argument("--interval", type=float, default=30.0)
    srun.set_defaults(func=_cmd_schedule_run)

    det = stop_sub.add_parser("detect", help="run a press sequence through the detector")
    det.add_argument("--gap-ms", dest="gap_ms", type=float, action="append", default=[])
    det.add_argument("--origin", action="append", default=[],
                     help="user | agent | synthetic | unknown (repeatable)")
    det.add_argument("--autorepeat-at", dest="autorepeat_at", type=int, action="append", default=[])
    det.add_argument("--window-ms", dest="window_ms", type=int, default=SS.DEFAULT_WINDOW_MS)
    det.add_argument("--min-gap-ms", dest="min_gap_ms", type=int, default=SS.DEFAULT_MIN_GAP_MS)
    det.set_defaults(func=_cmd_stop_detect)

    br = sub.add_parser("browser",
                        help="the browser layer: probe, discover, ensure, tabs, detach")
    br_sub = br.add_subparsers(dest="browser_command", required=True)

    bp = br_sub.add_parser("probe", help="what browser support exists here, measured")
    bp.set_defaults(func=_cmd_browser_probe)

    bd = br_sub.add_parser("discover", help="what browser state exists right now (read-only)")
    bd.set_defaults(func=_cmd_browser_discover)

    be = br_sub.add_parser("ensure", help="the one call that may start a browser")
    be.add_argument("--task", default="")
    be.add_argument("--run", default="")
    be.add_argument("--no-create", action="store_true",
                    help="never start one; report what is there")
    be.set_defaults(func=_cmd_browser_ensure)

    bt = br_sub.add_parser("tabs", help="tab inventory with owner and run")
    bt.set_defaults(func=_cmd_browser_tabs)

    bde = br_sub.add_parser("detach", help="release; never close a foreign browser")
    bde.add_argument("--tab", action="append", default=[])
    bde.set_defaults(func=_cmd_browser_detach)

    desk = sub.add_parser("desk", help="the host desktop lease and its corner indicator")
    desk_sub = desk.add_subparsers(dest="desk_command", required=True)

    st = desk_sub.add_parser("status", help="lease, queue and indicator")
    st.set_defaults(func=_cmd_desk_status)

    ind = desk_sub.add_parser("indicator", help="what the corner indicator can do here")
    ind.set_defaults(func=_cmd_desk_indicator)

    ind_show = desk_sub.add_parser("indicator-show",
                                   help="put the corner bar on screen for this run")
    ind_show.add_argument("--task", default="")
    ind_show.add_argument("--corner", default="bottom-right",
                          choices=("top-left", "top-right", "bottom-left", "bottom-right"))
    ind_show.add_argument("--hold", type=float, default=0.0,
                          help="keep the bar on screen for N seconds, then take it away")
    ind_show.set_defaults(func=_cmd_desk_indicator_show)

    ind_hide = desk_sub.add_parser("indicator-hide", help="take the corner bar away")
    ind_hide.set_defaults(func=_cmd_desk_indicator_hide)

    ac = desk_sub.add_parser("acquire", help="take the desktop")
    ac.add_argument("--run", required=True)
    ac.add_argument("--task", default="")
    ac.add_argument("--session", default="")
    ac.add_argument("--force", action="store_true", help="deliberate owner change")
    ac.set_defaults(func=_cmd_desk_acquire)

    re_ = desk_sub.add_parser("release", help="give the desktop back")
    re_.add_argument("--run", required=True)
    re_.add_argument("--reason", default="")
    re_.add_argument("--backend", default="", help="the display backend being released into")
    re_.add_argument("--session", default="", help="the session being released")
    re_.add_argument("--proof", default="", help="path to the stop-path proof (default: runtime stop-feasibility.json)")
    re_.set_defaults(func=_cmd_desk_release)

    sp = desk_sub.add_parser("stop", help="emergency stop: drain input, bump the fence, pause the owner")
    sp.add_argument("--reason", default="double-escape")
    sp.add_argument("--run", default="", help="the run to stop (default: the current owner)")
    sp.set_defaults(func=_cmd_desk_stop)

    rs = desk_sub.add_parser("resume", help="explicit user resume after a stop")
    rs.add_argument("--run", required=True)
    rs.set_defaults(func=_cmd_desk_resume)

    # `cli_shared.install_contract_entries` is the single home for this; a second local
    # wrapper would be the duplicate `tests/test_cli_shared.py` refuses.
    install_contract_entries(contract_entries(), _CONTRACT)
    return tui
