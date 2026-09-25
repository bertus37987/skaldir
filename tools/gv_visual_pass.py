#!/usr/bin/env python3
"""Skaldir (godsv) — numeric summarizer for the visual pass.

Reads the evidence a `tools/gv_visual_pass.sh` run leaves behind and turns it into
one honest numeric block:

  * how many surfaces were shot (and how many were expected)
  * the worst horizontal overflow in px (from the UI audit)
  * dead controls (nav targets whose activation changed no route)
  * axe-core violations, split by impact
  * renderer console error/exception lines from the console-stream check

Nothing here renders or launches anything — it only reads files, so it can never
touch the owner's app. `summary.json` is written next to the evidence and the exit
code is 0 only when every numeric criterion in MVP/11-visual-pass.md holds.

Usage:
  .venv/bin/python tools/gv_visual_pass.py --outdir outputs/visual-pass/<stamp> [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

#: A blank/black frame from this app is a few KB; a real painted surface is far
#: larger (measured 17.09.: 46 KB chat-free shell, 60 KB with content). 20 KB
#: separates "the compositor produced a frame" from "we captured nothing".
MIN_PNG_BYTES = 20_000

#: The audit's own rule: horizontal overflow above 8 px is a P1 finding.
MAX_OVERFLOW_PX = 8

CONSOLE_ERROR_RE = re.compile(r"^\[(error|exception)\]", re.IGNORECASE)


def read_tsv(path: Path) -> dict[str, str]:
    """`key<TAB>value` lines → dict (missing file → empty dict)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        out[key.strip()] = value.strip()
    return out


def read_surfaces(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, shot, size = parts[0], parts[1], parts[2]
        try:
            size_i = int(size)
        except ValueError:
            size_i = -1
        rows.append({"surface": name, "path": shot, "bytes": size_i})
    return rows


def newest_audit_report(outdir: Path) -> Path | None:
    """The audit writes `<out>/<stamp>-<label>/report.json`; take the newest."""
    candidates = sorted(outdir.glob("audit/*/report.json"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def count_console_errors(path: Path) -> tuple[int, int]:
    """(total lines, error/exception lines) from the console-stream capture."""
    if not path.is_file():
        return 0, 0
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    # the tool's own trailer is not renderer output
    payload = [ln for ln in lines if not ln.startswith("---")]
    errors = [ln for ln in payload if CONSOLE_ERROR_RE.match(ln)]
    return len(payload), len(errors)


def summarize(outdir: Path) -> dict:
    run = read_tsv(outdir / "run.tsv")
    surfaces = read_surfaces(outdir / "surfaces.tsv")
    report_path = newest_audit_report(outdir)

    audit: dict = {}
    findings: list[dict] = []
    meas: dict = {}
    if report_path:
        try:
            audit = json.loads(report_path.read_text(encoding="utf-8"))
            findings = audit.get("findings") or []
            meas = audit.get("measurements") or {}
        except (OSError, json.JSONDecodeError):
            audit = {}
    else:
        report_path = None

    severity = {"P0": 0, "P1": 0, "P2": 0}
    for f in findings:
        sev = str(f.get("severity", "P2"))
        severity[sev] = severity.get(sev, 0) + 1

    # overflow: every `overflow_<WxH>` measurement list, worst horizontal entry
    overflow_hard: list[dict] = []
    for key, value in meas.items():
        if not key.startswith("overflow_") or not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("kind") == "horizontal":
                overflow_hard.append({"viewport": key.replace("overflow_", ""), **item})
    overflow_max = max((int(o.get("over", 0)) for o in overflow_hard), default=0)

    axe = meas.get("axe_violations")
    axe_counts = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0, "other": 0}
    if isinstance(axe, list):
        for v in axe:
            impact = str((v or {}).get("impact") or "other")
            axe_counts[impact if impact in axe_counts else "other"] += 1
    axe_ran = isinstance(axe, list) or bool(meas.get("axe_error"))

    nav = meas.get("nav_reachability")
    dead = [r for r in nav or [] if isinstance(r, dict) and r.get("clicked") and not r.get("changed")] \
        if isinstance(nav, list) else []

    _raw_surface = meas.get("surface")
    surface_state: dict = _raw_surface if isinstance(_raw_surface, dict) else {}
    total_lines, error_lines = count_console_errors(outdir / "console.log")

    # identical PNGs mean a surface click changed nothing — a real defect class
    digests: dict[str, list[str]] = {}
    for s in surfaces:
        p = Path(s["path"])
        if not p.is_file():
            continue
        try:
            digests.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(s["surface"])
        except OSError:
            continue
    duplicated = [names for names in digests.values() if len(names) > 1]

    panel_mounted = run.get("panel_mounted") == "true"
    measured_surface = run.get("measured_surface") or "unknown"

    shot_ok = [s for s in surfaces if s["bytes"] >= MIN_PNG_BYTES]
    expected = int(run.get("surfaces_expected") or len(surfaces) or 0)
    leftovers = int(run.get("teardown_leftovers") or 0)
    ready = run.get("readiness_ok") == "true"
    cdp_port = run.get("cdp_port") or "?"
    mode = run.get("mode") or "?"

    # Panel-dependent measurements are marked `na` when the pass could not reach the
    # panel: their numbers still get reported (they are real), but they must never be
    # read as panel results, and they must not decide the overall outcome.
    def panel_check(check: dict) -> dict:
        if not panel_mounted:
            check["na"] = True
            check["detail"] = f"[not a panel result — measured on: {measured_surface}] " + check["detail"]
        return check

    checks = {
        "readiness_reached": {
            "ok": ready,
            "detail": f"CDP answered and the document rendered within {run.get('readiness_seconds', '?')}s (port {cdp_port})",
        },
        "surfaces_shot": {
            "ok": len(surfaces) == expected and expected > 0,
            "detail": f"{len(surfaces)}/{expected} surfaces captured",
        },
        "surfaces_painted": {
            "ok": len(shot_ok) == len(surfaces) and bool(surfaces),
            "detail": f"{len(shot_ok)}/{len(surfaces)} PNGs >= {MIN_PNG_BYTES} bytes "
                      f"(smallest {min([s['bytes'] for s in surfaces], default=0)} bytes)",
        },
        "frames_distinct": {
            "ok": not duplicated,
            "detail": f"{len(duplicated)} duplicated frame group(s)"
                      + (": " + "; ".join("=".join(g) for g in duplicated) if duplicated else ""),
        },
        "panel_surface_measured": {
            "ok": panel_mounted,
            "detail": f"measured surface: {measured_surface} · onboarding: {run.get('onboarding') or 'n/a'}",
        },
        "panel_surface_mounted": panel_check({
            "ok": bool(surface_state.get("panelMounted")),
            "detail": f"panel markers {surface_state.get('skaldirMarkers')} · title "
                      f"{surface_state.get('documentTitle')!r}",
        }),
        "audit_p0_p1_findings": panel_check({
            "ok": report_path is not None and severity.get("P0", 0) == 0 and severity.get("P1", 0) == 0,
            "detail": f"P0={severity.get('P0', 0)} P1={severity.get('P1', 0)} P2={severity.get('P2', 0)}",
        }),
        "axe_critical_serious": panel_check({
            "ok": axe_ran and axe_counts["critical"] == 0 and axe_counts["serious"] == 0,
            "detail": f"critical={axe_counts['critical']} serious={axe_counts['serious']} "
                      f"moderate={axe_counts['moderate']} minor={axe_counts['minor']}",
        }),
        "overflow_px": panel_check({
            "ok": overflow_max <= MAX_OVERFLOW_PX,
            "detail": f"worst horizontal overflow {overflow_max}px (limit {MAX_OVERFLOW_PX}px)",
        }),
        "dead_controls": panel_check({
            "ok": isinstance(nav, list) and not dead,
            "detail": f"{len(dead)} dead nav target(s)"
                      + (": " + ", ".join(str(d.get("label")) for d in dead[:6]) if dead else ""),
        }),
        "console_errors": {
            "ok": error_lines == 0,
            "detail": f"{error_lines} error/exception line(s) of {total_lines} renderer lines",
        },
        "teardown_sigterm": {
            "ok": leftovers == 0,
            "detail": f"{leftovers} process(es) left after SIGTERM",
        },
    }

    return {
        "mode": mode,
        "display": run.get("display") or "",
        "user_data_dir": run.get("user_data_dir") or "",
        "cdp_port": cdp_port,
        "git_head": run.get("git_head") or "",
        "isolated_workspace_ready": run.get("isolated_ready") or "unknown",
        "numbers": {
            "surfaces_shot": len(surfaces),
            "surfaces_expected": expected,
            "surfaces_painted": len(shot_ok),
            "overflow_max_px": overflow_max,
            "overflow_hard_count": len(overflow_hard),
            "dead_controls": len(dead),
            "axe": {"ran": axe_ran, **axe_counts,
                    "error": meas.get("axe_error") or None},
            "console_lines": total_lines,
            "console_errors": error_lines,
            "findings": severity,
            "teardown_leftovers": leftovers,
            "readiness_seconds": run.get("readiness_seconds") or "",
            "panel_mounted": panel_mounted,
            "measured_surface": measured_surface,
            "onboarding": run.get("onboarding") or "n/a",
            "duplicated_frames": len(duplicated),
        },
        "audit_report": str(report_path) if report_path else None,
        "surfaces": surfaces,
        "checks": checks,
        # a check marked `na` is a measurement of the wrong surface: reported, not decisive
        "pass": all(c["ok"] for c in checks.values() if not c.get("na")),
    }


def print_human(summary: dict) -> None:
    n = summary["numbers"]
    print("\n=== visual pass summary ===")
    print(f"mode                       : {summary['mode']}")
    print(f"display                    : {summary['display'] or '(none)'}")
    print(f"user data dir              : {summary['user_data_dir'] or '(n/a)'}")
    print(f"CDP port                   : {summary['cdp_port']}")
    print(f"isolated X11 workspace     : {summary['isolated_workspace_ready']}")
    print(f"measured surface           : {n['measured_surface']}")
    print(f"first-run overlay          : {n['onboarding']}")
    print(f"git head                   : {summary['git_head'] or '(unknown)'}")
    print("--")
    print(f"surfaces shot              : {n['surfaces_shot']} / {n['surfaces_expected']} "
          f"(painted {n['surfaces_painted']})")
    print(f"overflow px (worst horizontal): {n['overflow_max_px']}  "
          f"[{n['overflow_hard_count']} element(s) over the rule]")
    print(f"dead controls              : {n['dead_controls']}")
    print(f"axe violations             : critical={n['axe']['critical']} serious={n['axe']['serious']} "
          f"moderate={n['axe']['moderate']} minor={n['axe']['minor']}"
          + (f" (axe did not run: {n['axe']['error']})" if n['axe']['error'] else ""))
    print(f"console errors             : {n['console_errors']} of {n['console_lines']} lines")
    print(f"audit findings             : P0={n['findings'].get('P0', 0)} P1={n['findings'].get('P1', 0)} "
          f"P2={n['findings'].get('P2', 0)}")
    print(f"teardown leftovers         : {n['teardown_leftovers']}")
    print(f"duplicated frames          : {n['duplicated_frames']}")
    for s in summary["surfaces"]:
        print(f"  surface {s['surface']:<16} {s['bytes']:>8} bytes  {s['path']}")
    print("--")
    for name, c in summary["checks"].items():
        mark = "n/a " if c.get("na") else ("PASS" if c["ok"] else "FAIL")
        print(f"{mark} {name:<24} {c['detail']}")
    print(f"=== overall: {'PASS' if summary['pass'] else 'FAIL'} ===")


def main() -> int:
    ap = argparse.ArgumentParser(description="Numeric summary for a visual-pass run")
    ap.add_argument("--outdir", required=True, help="run directory written by gv_visual_pass.sh")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()

    outdir = Path(args.outdir).expanduser().resolve()
    if not outdir.is_dir():
        print(f"no such run directory: {outdir}", file=sys.stderr)
        return 2

    summary = summarize(outdir)
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print_human(summary)
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
