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
    "register_contract_entries",
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
    width = int(getattr(args, "width", 100) or 100)
    height = int(getattr(args, "height", 30) or 30)
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


def _cmd_stop_report(args: argparse.Namespace) -> int:
    out = Path(str(getattr(args, "out", "") or "")) if getattr(args, "out", "") else (
        runtime_dir(args) / "stop-feasibility.json"
    )
    report = SS.write_report(out)
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
    store = _store(args)
    run_id = str(getattr(args, "run", "") or "")
    ok = store.release(run_id, reason=str(getattr(args, "reason", "") or ""))
    payload = _desk_payload(store)
    payload["released"] = bool(ok)
    if not ok:
        return blocked(f"run {run_id!r} does not hold the desktop lease",
                       reason="not_lease_owner", details=payload)
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.release"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK


def _cmd_desk_stop(args: argparse.Namespace) -> int:
    store = _store(args)
    result = store.request_stop(reason=str(getattr(args, "reason", "") or "double-escape"))
    payload = _desk_payload(store)
    payload["stop"] = result
    report_envelope(payload, [{"ref": COMMAND_EVIDENCE["desk.stop"], "kind": "code", "note": ""}])
    dump(payload)
    return EXIT_OK if result.get("ok") else EXIT_BLOCKED


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
    return {
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


def register_contract_entries() -> list[str]:
    return install_contract_entries(contract_entries(), _CONTRACT)


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
    prev.set_defaults(func=_cmd_tui_preview)

    lay = tui_sub.add_parser("layout", help="the pane geometry at a terminal size, as data")
    lay.add_argument("--width", type=int, default=100)
    lay.add_argument("--height", type=int, default=30)
    lay.add_argument("--blocked", action="store_true", help="reserve the blocker row")
    lay.set_defaults(func=_cmd_tui_layout)

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
    rep.set_defaults(func=_cmd_stop_report)

    det = stop_sub.add_parser("detect", help="run a press sequence through the detector")
    det.add_argument("--gap-ms", dest="gap_ms", type=float, action="append", default=[])
    det.add_argument("--origin", action="append", default=[],
                     help="user | agent | synthetic | unknown (repeatable)")
    det.add_argument("--autorepeat-at", dest="autorepeat_at", type=int, action="append", default=[])
    det.add_argument("--window-ms", dest="window_ms", type=int, default=SS.DEFAULT_WINDOW_MS)
    det.add_argument("--min-gap-ms", dest="min_gap_ms", type=int, default=SS.DEFAULT_MIN_GAP_MS)
    det.set_defaults(func=_cmd_stop_detect)

    desk = sub.add_parser("desk", help="the host desktop lease and its corner indicator")
    desk_sub = desk.add_subparsers(dest="desk_command", required=True)

    st = desk_sub.add_parser("status", help="lease, queue and indicator")
    st.set_defaults(func=_cmd_desk_status)

    ac = desk_sub.add_parser("acquire", help="take the desktop")
    ac.add_argument("--run", required=True)
    ac.add_argument("--task", default="")
    ac.add_argument("--session", default="")
    ac.add_argument("--force", action="store_true", help="deliberate owner change")
    ac.set_defaults(func=_cmd_desk_acquire)

    re_ = desk_sub.add_parser("release", help="give the desktop back")
    re_.add_argument("--run", required=True)
    re_.add_argument("--reason", default="")
    re_.set_defaults(func=_cmd_desk_release)

    sp = desk_sub.add_parser("stop", help="emergency stop: pause the owner and bump the fence")
    sp.add_argument("--reason", default="double-escape")
    sp.set_defaults(func=_cmd_desk_stop)

    rs = desk_sub.add_parser("resume", help="explicit user resume after a stop")
    rs.add_argument("--run", required=True)
    rs.set_defaults(func=_cmd_desk_resume)

    register_contract_entries()
    return tui
