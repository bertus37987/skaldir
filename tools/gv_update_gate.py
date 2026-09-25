#!/usr/bin/env python3
"""God's Voice — update gate: ``hermes update`` → build-app → start → anchors + contract.

The nine steps of the T7 plan, as one repeatable script (plan A.3/A.4):

  0 preflight        read the stock update marker (never write/delete it); collect
                     stock + GV commits
  1 stock_update     adopt the desktop updater's receipts (``.hermes-update-result.json``,
                     handoff log); with ``--with-update`` run ``hermes update --yes`` itself
  2 anchor_dryrun    run the overlay anchors against a /tmp COPY of the anchor files —
                     seconds, no build, no stock mutation. Mismatch → the loud
                     ``anchor found Nx, expected Mx`` artifact
  3 build_app        full ``appbuild.build(force=True)`` via the Python API so the
                     overlay report becomes the receipt (the CLI does not print it)
  4 app_start        start the GV binary with the production env (``gods_voice_env()``)
                     and a debug port; wait (bounded, 90 s) for CDP
  5 ui_e2e           ``node tools/gv_ui_e2e.mjs <port>`` against the real app
  6 plugin_contract  ``node tools/check_desktop_plugin.mjs`` (no app needed)
  7 pytest_appbuild  ``.venv/bin/python -m pytest tests/test_appbuild.py -q``
  8 cleanup          stop ONLY the debug instance this gate started, relaunch normally

Modes:
  ``--check``/``--dry-run``  steps 0, 1, 2, 6, 7 — marker + anchors + contract +
                             unit tests. No build, no app start, no E2E, no cleanup.
  default                    the full chain (heavy: build ~100 s, Electron start, E2E).

Exit codes (aggregated from the first failing step, in step order):
  0 OK · 20 stock update in flight (marker alive) · 21 update failed/skipped ·
  22 anchor mismatch (dry run OR build) · 23 app start / CDP timeout ·
  24 UI-E2E red · 25 plugin contract red · 26 pytest red.

Receipt: ``<receipts>/receipt.json`` (schema below) + one ``<nr>-*.log``/``.exit``
artifact per step. ``steps`` values are the step exit codes; ``null`` = deliberately
not run in this mode (see ``skipped_steps``). Ordering rule from the stock
orchestrator: the effect (build/start/E2E) comes first, the receipt last — never
"ok" before the artifact exists.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from godsv import appbuild, desktop

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

# Reuse, don't duplicate: the PID safety net + kill logic live in gv_crash_recovery.
from gv_crash_recovery import (  # noqa: E402
    CrashError,
    _proc_cmdline,
    crash,
    gv_pids,
    is_pid_alive,
    wait_cdp,
)

REPO_DEFAULT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 9333

EXIT_OK = 0
EXIT_UPDATE_IN_FLIGHT = 20
EXIT_UPDATE_FAILED = 21
EXIT_ANCHOR = 22
EXIT_APP_START = 23
EXIT_UI_E2E = 24
EXIT_PLUGIN_CONTRACT = 25
EXIT_PYTEST = 26

EXIT_CODES = {
    EXIT_OK: "gate green",
    EXIT_UPDATE_IN_FLIGHT: "stock update in flight (marker alive) — gate refused to run",
    EXIT_UPDATE_FAILED: "hermes update failed or was skipped",
    EXIT_ANCHOR: "anchor mismatch (dry run OR build)",
    EXIT_APP_START: "app start / CDP timeout",
    EXIT_UI_E2E: "real-app UI E2E red",
    EXIT_PLUGIN_CONTRACT: "desktop plugin contract red",
    EXIT_PYTEST: "tests/test_appbuild.py red",
}

STEP_EXITS = {
    "preflight": EXIT_UPDATE_IN_FLIGHT,
    "stock_update": EXIT_UPDATE_FAILED,
    "anchor_dryrun": EXIT_ANCHOR,
    "build_app": EXIT_ANCHOR,
    "app_start": EXIT_APP_START,
    "ui_e2e": EXIT_UI_E2E,
    "plugin_contract": EXIT_PLUGIN_CONTRACT,
    "pytest_appbuild": EXIT_PYTEST,
    "cleanup": EXIT_OK,  # cleanup never changes the verdict
}

# step → steps that must have succeeded first (skipped otherwise)
DEPENDENCIES = {
    "app_start": ("build_app",),
    "ui_e2e": ("app_start",),
    "cleanup": ("app_start",),
}

CHECK_MODE_STEPS = (
    "preflight",
    "stock_update",
    "anchor_dryrun",
    "plugin_contract",
    "pytest_appbuild",
)
FULL_MODE_STEPS = (
    "preflight",
    "stock_update",
    "anchor_dryrun",
    "build_app",
    "app_start",
    "ui_e2e",
    "plugin_contract",
    "pytest_appbuild",
    "cleanup",
)


# --- small helpers -----------------------------------------------------------


def hermes_home() -> Path:
    return desktop.hermes_home()


def default_receipt_dir() -> Path:
    return (
        hermes_home()
        / "godsv"
        / "receipts"
        / "update-gate"
        / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def run_command(cmd: list[str], *, cwd: str | None = None, timeout: float = 600, env=None) -> dict:
    """Run one step command; a non-zero exit is data, never an exception."""
    try:
        proc = subprocess.run(  # nosec B603 — argv list, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return {"exit": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"exit": 127, "stdout": "", "stderr": str(exc)}


def gitsha(path: Path) -> str:
    result = run_command(["git", "-C", str(path), "rev-parse", "HEAD"], timeout=30)
    return result["stdout"].strip() if result["exit"] == 0 else ""


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


def fetch_json(url: str, *, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — loopback
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return None


def _load_patch_app(repo: Path):
    """Import app-overlay/patch_app.py at runtime (anchor list + apply_patches)."""
    import importlib.util  # noqa: PLC0415

    path = Path(repo) / "app-overlay" / "patch_app.py"
    spec = importlib.util.spec_from_file_location("gv_gate_patch_app", path)
    if spec is None or spec.loader is None:  # pragma: no cover — broken checkout
        raise RuntimeError(f"cannot load overlay module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- step 0: preflight (stock update marker) ---------------------------------


def read_update_marker(
    home: Path, *, alive=is_pid_alive, now=None, max_age_s: float = 20 * 60
) -> dict | None:
    """Live stock update marker, or None.

    Semantics ported from the stock ``update-marker.ts``: 2-line body (pid, unix
    seconds), 20-minute ceiling, pid liveness. The gate only READS — it never
    writes or deletes the marker (that protocol belongs to the stock updater).
    """
    path = Path(home) / ".hermes-update-in-progress"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    pid_line, _, rest = raw.partition("\n")
    started_line = rest.split("\n", 1)[0]
    pid = int(pid_line.strip()) if pid_line.strip().isdigit() else 0
    started = int(started_line.strip()) if started_line.strip().isdigit() else 0
    now_s = float(now if now is not None else time.time())
    age = (now_s - started) if started else float("inf")
    if not pid or not alive(pid) or age > max_age_s:
        return None
    return {"path": str(path), "pid": pid, "age_seconds": round(age, 1)}


def step_preflight(
    repo: Path, home: Path, receipt_dir: Path, *, marker_reader=read_update_marker, commit=gitsha
) -> dict:
    marker = marker_reader(home)
    stock = Path(home) / "hermes-agent"
    before = commit(stock)
    gv = commit(repo)
    write_text(receipt_dir / "00-stock-commit-before.txt", f"{before}\n")
    write_text(receipt_dir / "00-gv-commit.txt", f"{gv}\n")
    write_json(receipt_dir / "00-update-marker.json", {"marker": marker})
    if marker:
        return {
            "step": "preflight",
            "exit": EXIT_UPDATE_IN_FLIGHT,
            "marker": marker,
            "stock_commit_before": before,
            "gv_commit": gv,
            "detail": f"stock update in flight (pid {marker['pid']}, age {marker['age_seconds']}s)",
        }
    return {
        "step": "preflight",
        "exit": EXIT_OK,
        "marker": None,
        "stock_commit_before": before,
        "gv_commit": gv,
        "detail": "no live update marker",
    }


# --- step 1: stock update artifacts (or the update itself) --------------------


def step_stock_update(
    repo: Path,
    home: Path,
    receipt_dir: Path,
    *,
    with_update: bool = False,
    run=run_command,
    hermes_bin: str | None = None,
    commit=gitsha,
) -> dict:
    """Variant a: adopt the desktop updater receipts. Variant b: run the CLI update."""
    outcome: dict = {"step": "stock_update", "exit": EXIT_OK, "ran_update": False, "result": None}
    result_json = Path(home) / ".hermes-update-result.json"
    if result_json.exists():
        shutil.copyfile(result_json, receipt_dir / "10-update-result.json")
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        outcome["result"] = data
        if isinstance(data, dict) and data.get("ok") is False:
            outcome["exit"] = EXIT_UPDATE_FAILED
            outcome["detail"] = (
                f"previous update result reports ok:false (exit {data.get('exit_code')})"
            )
        else:
            outcome["detail"] = "adopted .hermes-update-result.json"
    handoff = Path(home) / "logs" / "desktop-update-handoff.log"
    if handoff.exists():
        write_text(
            receipt_dir / "10-handoff.log",
            tail(handoff.read_text(encoding="utf-8", errors="replace")) + "\n",
        )

    if with_update:
        binary = hermes_bin or desktop.hermes_binary()
        if not binary:
            outcome.update(
                exit=EXIT_UPDATE_FAILED, detail="hermes CLI not found — cannot run the update"
            )
            return outcome
        proc = run([binary, "update", "--yes"], timeout=3600)
        write_text(receipt_dir / "10-hermes-update.log", proc["stdout"] + proc["stderr"])
        exit_code = EXIT_OK if proc["exit"] == 0 else EXIT_UPDATE_FAILED
        write_text(receipt_dir / "10-hermes-update.exit", f"HERMES_UPDATE_EXIT {proc['exit']}\n")
        outcome.update(ran_update=True, update_exit=proc["exit"], exit=exit_code)
        if exit_code != EXIT_OK:
            outcome["detail"] = (
                f"hermes update exited {proc['exit']} (skipped updates are not green)"
            )
    after = commit(Path(home) / "hermes-agent")
    write_text(receipt_dir / "10-stock-commit-after.txt", f"{after}\n")
    outcome["stock_commit_after"] = after
    if not outcome.get("detail"):
        outcome["detail"] = "no update receipts found (desktop updater has not run?)"
    return outcome


# --- step 2: anchor dry run against a /tmp copy -------------------------------


def step_anchor_dryrun(
    repo: Path,
    stock_root: Path,
    receipt_dir: Path,
    *,
    workdir: Path | None = None,
    patch_module=None,
) -> dict:
    """Apply the overlay anchors to a /tmp COPY — seconds, no build, no stock writes."""
    workdir = Path(workdir or tempfile.mkdtemp(prefix="gv-anchor-dryrun-"))
    module = patch_module if patch_module is not None else _load_patch_app(repo)
    desktop_src = Path(stock_root) / "apps" / "desktop"
    tree = workdir / "tree"
    copied: list[str] = []
    missing: list[str] = []
    for item in module.PATCHES:
        rel = item[0]
        src = desktop_src / rel
        dst = tree / "apps" / "desktop" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copyfile(src, dst)
            copied.append(rel)
        else:
            dst.write_text("", encoding="utf-8")
            missing.append(rel)
    for _src_rel, dst_rel in getattr(module, "FILES", []):
        (tree / dst_rel).parent.mkdir(parents=True, exist_ok=True)

    report = {"patched": {}, "copied": [], "removed": [], "errors": []}
    error = ""
    try:
        report = module.apply_patches(tree, repo)
    except RuntimeError as exc:
        error = str(exc)
    anchors = sum(report.get("patched", {}).values())
    log_lines = [
        f"stock desktop tree : {desktop_src}",
        f"anchor files copied: {len(copied)} (missing upstream: {', '.join(missing) or 'none'})",
        f"patched            : {anchors} anchor(s) in {len(report.get('patched', {}))} file(s)",
        f"file copies        : {len(report.get('copied', []))}, "
        f"removals: {len(report.get('removed', []))}",
        f"errors             : {report.get('errors', [])}",
    ]
    if error:
        log_lines.append(f"OVERLAY FAILED: {error}")
    exit_code = EXIT_OK if not error else EXIT_ANCHOR
    write_text(receipt_dir / "20-anchor-dryrun.log", "\n".join(log_lines) + "\n")
    write_text(receipt_dir / "20-anchor-dryrun.exit", f"DRYRUN_EXIT {exit_code}\n")
    return {
        "step": "anchor_dryrun",
        "exit": exit_code,
        "anchors": int(anchors),
        "files": len(report.get("patched", {})),
        "patched": report.get("patched", {}),
        "copied": len(report.get("copied", [])),
        "removed": len(report.get("removed", [])),
        "errors": report.get("errors", []),
        "missing_sources": missing,
        "workdir": str(workdir),
        "detail": error
        or f"dry-run overlay OK: {anchors} patches in {len(report.get('patched', {}))} files",
    }


# --- step 3: full build via the Python API ------------------------------------


def step_build_app(receipt_dir: Path, *, build=appbuild.build, force: bool = True) -> dict:
    try:
        result = build(force=force)
    except Exception as exc:  # noqa: BLE001 — an anchor mismatch raises by design
        write_json(receipt_dir / "30-build-app.json", {"ok": False, "error": str(exc)})
        write_text(receipt_dir / "30-build-app.exit", f"BUILD_EXIT {EXIT_ANCHOR}\n")
        return {
            "step": "build_app",
            "exit": EXIT_ANCHOR,
            "ok": False,
            "detail": f"build raised: {exc}",
        }
    if not isinstance(result, dict):
        result = {"ok": bool(result)}
    write_json(receipt_dir / "30-build-app.json", result)
    ok = bool(result.get("ok"))
    write_text(receipt_dir / "30-build-app.exit", f"BUILD_EXIT {EXIT_OK if ok else EXIT_ANCHOR}\n")
    return {
        "step": "build_app",
        "exit": EXIT_OK if ok else EXIT_ANCHOR,
        "ok": ok,
        "stage": result.get("stage", ""),
        "binary": result.get("binary", ""),
        "seconds": result.get("seconds"),
        "overlay": result.get("overlay", {}),
        "detail": f"stage={result.get('stage', '')} binary={result.get('binary', '')}",
    }


# --- step 4: start the app (production env + debug port) ----------------------


def step_start_app(
    receipt_dir: Path,
    *,
    port: int = DEFAULT_PORT,
    binary_fn=desktop.hermes_app_binary,
    env_fn=desktop.gods_voice_env,
    popen=subprocess.Popen,
    wait=wait_cdp,
    fetch=fetch_json,
    timeout: float = 90.0,
) -> dict:
    binary = binary_fn()
    if not binary:
        write_text(receipt_dir / "40-app-start.exit", f"APP_START_EXIT {EXIT_APP_START}\n")
        return {
            "step": "app_start",
            "exit": EXIT_APP_START,
            "detail": "no packaged app binary — build-app first",
        }
    try:
        popen(
            [
                binary,
                f"--class={desktop.WM_CLASS}",
                f"--remote-debugging-port={int(port)}",
                f"hermes://open/{desktop.PANEL_ROUTE}",
            ],
            start_new_session=True,
            env=env_fn(),
        )
    except OSError as exc:
        write_text(receipt_dir / "40-app-start.exit", f"APP_START_EXIT {EXIT_APP_START}\n")
        return {"step": "app_start", "exit": EXIT_APP_START, "detail": f"spawn failed: {exc}"}
    if not wait(port, timeout=timeout):
        write_text(receipt_dir / "40-app-start.exit", f"APP_START_EXIT {EXIT_APP_START}\n")
        return {
            "step": "app_start",
            "exit": EXIT_APP_START,
            "detail": f"CDP port {port} not up within {timeout:.0f}s",
        }
    targets = fetch(f"http://127.0.0.1:{int(port)}/json")
    write_json(receipt_dir / "40-cdp-targets.json", {"port": int(port), "targets": targets or []})
    write_text(receipt_dir / "40-app-start.exit", "APP_START_EXIT 0\n")
    return {
        "step": "app_start",
        "exit": EXIT_OK,
        "port": int(port),
        "binary": str(binary),
        "targets": len(targets or []),
        "detail": f"CDP up on {port} with {len(targets or [])} target(s)",
    }


# --- steps 5/6: node scripts --------------------------------------------------


def step_node_script(
    repo: Path,
    script: str,
    prefix: str,
    *,
    name: str,
    exit_code: int,
    extra: tuple = (),
    receipt_dir: Path,
    node: str | None = None,
    run=run_command,
    timeout: float = 600,
) -> dict:
    node = node or shutil.which("node")
    if not node:
        return {"step": name, "exit": None, "skipped": True, "detail": "node not installed"}
    proc = run(
        [node, str(Path(repo) / script), *[str(item) for item in extra]],
        cwd=str(repo),
        timeout=timeout,
    )
    write_text(receipt_dir / f"{prefix}.log", proc["stdout"] + proc["stderr"])
    code = EXIT_OK if proc["exit"] == 0 else exit_code
    write_text(receipt_dir / f"{prefix}.exit", f"{prefix.upper().replace('-', '_')}_EXIT {code}\n")
    return {
        "step": name,
        "exit": code,
        "returncode": proc["exit"],
        "skipped": False,
        "detail": tail(proc["stdout"] + proc["stderr"], 3) or f"returncode={proc['exit']}",
    }


# --- step 7: pytest net -------------------------------------------------------


def step_pytest(repo: Path, receipt_dir: Path, *, run=run_command, timeout: float = 1800) -> dict:
    venv_python = Path(repo) / ".venv" / "bin" / "python"
    exe = str(venv_python) if venv_python.exists() else sys.executable
    proc = run(
        [exe, "-m", "pytest", "tests/test_appbuild.py", "-q"], cwd=str(repo), timeout=timeout
    )
    write_text(receipt_dir / "70-pytest-appbuild.log", proc["stdout"] + proc["stderr"])
    code = EXIT_OK if proc["exit"] == 0 else EXIT_PYTEST
    write_text(receipt_dir / "70-pytest-appbuild.exit", f"APPTEST_EXIT {code}\n")
    return {
        "step": "pytest_appbuild",
        "exit": code,
        "returncode": proc["exit"],
        "detail": tail(proc["stdout"] + proc["stderr"], 3) or f"returncode={proc['exit']}",
    }


# --- step 8: cleanup (own debug instance only) --------------------------------


def step_cleanup(
    receipt_dir: Path,
    *,
    port: int = DEFAULT_PORT,
    pids_fn=gv_pids,
    cmdline=_proc_cmdline,
    crash_fn=crash,
    relaunch=desktop.launch,
) -> dict:
    """Stop ONLY the instance this gate started (debug port), then relaunch normally."""
    pids = [pid for pid in pids_fn() if f"--remote-debugging-port={int(port)}" in cmdline(pid)]
    detail = ""
    if pids:
        try:
            crash_fn(pids)
        except CrashError as exc:  # cleanup never changes the verdict, but is recorded
            detail = f"cleanup kill failed: {exc}"
    relaunch_exit = relaunch()
    write_text(
        receipt_dir / "80-cleanup.json",
        json.dumps({"stopped": pids, "detail": detail}, indent=2) + "\n",
    )
    return {
        "step": "cleanup",
        "exit": EXIT_OK,
        "stopped": pids,
        "relaunch_exit": relaunch_exit,
        "detail": detail or f"stopped {len(pids)} debug PID(s); relaunch exit {relaunch_exit}",
    }


# --- aggregation / receipt ----------------------------------------------------


def aggregate_exit(steps: dict) -> int:
    """First failing step (in step order) decides the gate exit code."""
    for name in (
        "preflight",
        "stock_update",
        "anchor_dryrun",
        "build_app",
        "app_start",
        "ui_e2e",
        "plugin_contract",
        "pytest_appbuild",
    ):
        step = steps.get(name)
        if not step or step.get("skipped"):
            continue
        if step.get("exit") not in (None, EXIT_OK):
            return int(step["exit"])
    return EXIT_OK


def overlay_summary(overlay: dict) -> dict:
    patched = overlay.get("patched") or {}
    return {
        "patched_files": len(patched),
        "patched_anchors": int(sum(patched.values())) if patched else 0,
        "copied": len(overlay.get("copied") or []),
        "removed": len(overlay.get("removed") or []),
        "errors": list(overlay.get("errors") or []),
    }


def build_receipt(
    *,
    receipt_dir: Path,
    steps: dict,
    mode_steps: tuple,
    dry_run: bool,
    started: float,
) -> dict:
    code = aggregate_exit(steps)
    overlay = {"patched_files": 0, "patched_anchors": 0, "copied": 0, "removed": 0, "errors": []}
    if steps.get("build_app", {}).get("overlay"):
        overlay = overlay_summary(steps["build_app"]["overlay"])
    elif steps.get("anchor_dryrun", {}).get("exit") == EXIT_OK:
        dry = steps["anchor_dryrun"]
        overlay = {
            "patched_files": dry.get("files", 0),
            "patched_anchors": dry.get("anchors", 0),
            "copied": dry.get("copied", 0),
            "removed": dry.get("removed", 0),
            "errors": dry.get("errors", []),
        }
    preflight = steps.get("preflight", {})
    stock_update = steps.get("stock_update", {})
    return {
        "kind": "gv-update-gate",
        "ok": code == EXIT_OK,
        "dry_run": dry_run,
        "exit_code": code,
        "message": EXIT_CODES.get(code, ""),
        "started_at": started,
        "finished_at": time.time(),
        "stock_commit_before": preflight.get("stock_commit_before", ""),
        "stock_commit_after": stock_update.get("stock_commit_after", ""),
        "gv_commit": preflight.get("gv_commit", ""),
        "steps": {name: steps[name]["exit"] for name in steps},
        "skipped_steps": [name for name in FULL_MODE_STEPS if name not in mode_steps],
        "overlay": overlay,
        "artifacts": sorted(path.name for path in receipt_dir.glob("*") if path.is_file()),
    }


def write_receipt(receipt_dir: Path, receipt: dict) -> None:
    write_json(Path(receipt_dir) / "receipt.json", receipt)


# --- CLI ----------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    epilog = "\n".join(f"  {code}: {text}" for code, text in EXIT_CODES.items())
    parser = argparse.ArgumentParser(
        prog="gv_update_gate.py",
        description=(
            "God's Voice update gate: stock update markers + overlay anchors + build + "
            "app start + UI E2E + plugin contract + anchor unit tests, with a JSON receipt."
        ),
        epilog="Exit codes:\n" + epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        "--dry-run",
        action="store_true",
        dest="check",
        help="cheap, app-safe part only (no build/start/E2E)",
    )
    parser.add_argument(
        "--with-update", action="store_true", help="run `hermes update --yes` (full mode only)"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="debug port for the started app"
    )
    parser.add_argument("--receipts", default="", help="receipt directory")
    parser.add_argument("--repo", default="", help="God's Voice repo (default: this script's repo)")
    parser.add_argument(
        "--stock", default="", help="stock Hermes tree (default $HERMES_HOME/hermes-agent)"
    )
    return parser.parse_args(argv)


def run_steps(repo: Path, home: Path, receipt_dir: Path, args: argparse.Namespace) -> dict:
    steps: dict = {}

    def record(result: dict) -> dict:
        steps[result["step"]] = result
        return result

    preflight = record(step_preflight(repo, home, receipt_dir))
    if preflight["exit"] != EXIT_OK:
        return steps

    record(
        step_stock_update(repo, home, receipt_dir, with_update=args.with_update and not args.check)
    )
    record(step_anchor_dryrun(repo, args.stock or appbuild.hermes_source(), receipt_dir))
    if args.check:
        record(
            step_node_script(
                repo,
                "tools/check_desktop_plugin.mjs",
                "60-plugin-contract",
                name="plugin_contract",
                exit_code=EXIT_PLUGIN_CONTRACT,
                receipt_dir=receipt_dir,
            )
        )
        record(step_pytest(repo, receipt_dir))
        return steps

    build = record(step_build_app(receipt_dir))
    if build["exit"] == EXIT_OK:
        started = record(step_start_app(receipt_dir, port=args.port))
        if started["exit"] == EXIT_OK:
            record(
                step_node_script(
                    repo,
                    "tools/gv_ui_e2e.mjs",
                    "50-ui-e2e",
                    name="ui_e2e",
                    exit_code=EXIT_UI_E2E,
                    extra=(args.port,),
                    receipt_dir=receipt_dir,
                )
            )
    record(
        step_node_script(
            repo,
            "tools/check_desktop_plugin.mjs",
            "60-plugin-contract",
            name="plugin_contract",
            exit_code=EXIT_PLUGIN_CONTRACT,
            receipt_dir=receipt_dir,
        )
    )
    record(step_pytest(repo, receipt_dir))
    if "app_start" in steps and not steps["app_start"].get("skipped"):
        record(step_cleanup(receipt_dir, port=args.port))
    return steps


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    receipt_dir = Path(args.receipts) if args.receipts else default_receipt_dir()
    receipt_dir.mkdir(parents=True, exist_ok=True)
    repo = Path(args.repo).resolve() if args.repo else REPO_DEFAULT
    home = hermes_home()
    started = time.time()
    mode_steps = CHECK_MODE_STEPS if args.check else FULL_MODE_STEPS
    steps = run_steps(repo, home, receipt_dir, args)
    receipt = build_receipt(
        receipt_dir=receipt_dir,
        steps=steps,
        mode_steps=mode_steps,
        dry_run=bool(args.check),
        started=started,
    )
    write_receipt(receipt_dir, receipt)
    print(
        json.dumps(
            {key: receipt[key] for key in ("ok", "exit_code", "dry_run", "message")}, indent=2
        )
    )
    for name, step in steps.items():
        print(f"  {name}: exit={step.get('exit')} — {step.get('detail', '')}")
    return int(receipt["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
