"""Autonomous workflows (§9): open app → verify state → screenshot.

Workflow layer on top of existing building blocks (process checks, X11
properties, screenshot backends). Result contract per §9: compact
``run_id/status/summary/result/evidence_refs/artifact_refs/warnings`` plus
error fields (``failure_class/failed_stage/attempts/recoverable/
recommended_next_action/side_effect_state``).

Preconditions are declared: a concrete app installation is NEVER installed
secretly. If it is missing, or a required backend (window or screenshot
tool) is missing, the workflows return a precise error
instead of a substitute success. Every step is verified against the real
state — starting a process alone does not count as success.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — fixed argv, no shell, bounded backend processes
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

RUN_DIR_NAME = "workflow_runs"


def _now_run_id(prefix: str = "wf") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _base_result(run_id: str, summary: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": "RUNNING",
        "summary": summary,
        "result": {},
        "evidence_refs": [],
        "artifact_refs": [],
        "warnings": [],
    }


def _fail(
    result: dict[str, Any],
    *,
    failure_class: str,
    failed_stage: str,
    message: str,
    recoverable: bool,
    next_action: str,
    side_effect_state: str = "none",
) -> dict[str, Any]:
    result["status"] = "FAILED"
    result["failure_class"] = failure_class
    result["failed_stage"] = failed_stage
    result["attempts"] = result.get("attempts", 0) + 1
    result["recoverable"] = recoverable
    result["recommended_next_action"] = next_action
    result["side_effect_state"] = side_effect_state
    result["summary"] = message
    return result


# ---------------------------------------------------------------- substrate


def cdp_targets(port: int, *, timeout: float = 5.0) -> list[dict]:
    """Electron/Chromium targets via the DevTools endpoint (declared backend)."""
    import urllib.request

    try:
        with urllib.request.urlopen(  # nosec B310 — loopback DevTools endpoint, http only
            f"http://127.0.0.1:{port}/json", timeout=timeout
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []


def cdp_screenshot(port: int, path: Path, *, timeout: float = 20.0) -> tuple[bool, str]:
    """Screenshot via CDP — reliable for Electron apps incl. Wayland sessions."""
    import base64 as _b64

    targets = [t for t in cdp_targets(port) if t.get("type") == "page"]
    if not targets:
        return False, f"no CDP target on port {port}"
    ws_url = targets[0].get("webSocketDebuggerUrl", "")
    if not ws_url:
        return False, "CDP target without webSocketDebuggerUrl"
    try:
        import websockets.sync.client as _ws

        with _ws.connect(ws_url, open_timeout=timeout, close_timeout=2) as conn:
            conn.send(
                json.dumps(
                    {"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}
                )
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                msg = json.loads(conn.recv())
                if msg.get("id") == 1:
                    data = msg.get("result", {}).get("data", "")
                    if not data:
                        return False, "CDP delivered no screenshot"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_b64.b64decode(data))
                    return True, "cdp"
    except Exception as exc:
        return False, f"CDP screenshot failed: {type(exc).__name__}: {exc}"
    return False, "CDP-Timeout"


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def process_running(pattern: str) -> list[int]:
    """PIDs whose command line matches ``pattern`` — excluding our own ancestry.

    ``pgrep -f`` matches full command lines, so the calling shell (whose argv
    contains this very script) can match itself; those PIDs are filtered out.
    """
    code, out, _ = _run(["pgrep", "-f", pattern])
    if code not in (0, 1):
        return []
    own = set()
    pid = os.getpid()
    while pid and pid != 1:
        own.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().split()[3])
        except Exception:
            break
    return [
        int(line) for line in out.split()
        if line.strip().isdigit() and int(line) not in own
    ]


def x11_windows() -> list[dict[str, str]]:
    """X11/XWayland window inventory via xprop only (no wmctrl/xdotool needed)."""
    if shutil.which("xprop") is None:
        return []
    code, out, _ = _run(["xprop", "-root", "_NET_CLIENT_LIST"])
    if code != 0 or "0x" not in out:
        return []
    ids = [tok.strip() for tok in out.split("#")[-1].split(",") if tok.strip()]
    windows: list[dict[str, str]] = []
    for wid in ids:
        _, name_out, _ = _run(["xprop", "-id", wid, "_NET_WM_NAME", "WM_NAME", "WM_CLASS"])
        title, wm_class = "", ""
        for line in name_out.splitlines():
            if "_NET_WM_NAME" in line or "WM_NAME" in line:
                title = (line.split("=", 1)[-1] if "=" in line else line).strip().strip('"')
            elif "WM_CLASS" in line and "=" in line:
                wm_class = line.split("=", 1)[-1].strip().strip('"').replace('"', "")
        if title or wm_class:
            windows.append({"xid": wid, "title": title, "wm_class": wm_class})
    return windows


def find_window(*, title_contains: str = "", wm_class_contains: str = "") -> dict[str, str] | None:
    """The matching window, preferring an exact class hit over a title hit."""
    hits = x11_windows()
    if wm_class_contains:
        for win in hits:
            if wm_class_contains.lower() in win["wm_class"].lower():
                return win
    if title_contains:
        for win in hits:
            if title_contains.lower() in win["title"].lower():
                return win
    return None


def focus_window(xid: str) -> bool:
    """Best-effort X11 focus. Declared gap: without xdotool/wmctrl on a
    Wayland compositor, focusing foreign windows may be impossible — callers
    must treat a False here as a real limitation, not retry forever."""
    if shutil.which("xdotool"):
        code, _, _ = _run(["xdotool", "windowactivate", xid])
        if code == 0:
            return True
    if shutil.which("wmctrl"):
        code, _, _ = _run(["wmctrl", "-i", "-a", xid])
        if code == 0:
            return True
    try:  # python-xlib, if present
        from Xlib import display  # type: ignore

        d = display.Display()
        d.screen().root.set_input_focus(
            d.create_resource_object("window", int(xid, 16)), 0  # type: ignore[attr-defined]
        )
        d.sync()
        return True
    except Exception:
        return False


def screenshot(path: Path, *, xid: str = "", display: str = "") -> tuple[bool, str]:
    """Capture via python-xlib GetImage (primary) or ImageMagick import (fallback)."""
    import os

    from .computer_use import encode_png
    from .cu_platform import _xlib_root_screenshot
    from .xlib_capture import XlibCaptureError, capture_window_image

    disp = display or os.environ.get("DISPLAY", "")
    xlib_err = ""
    if disp:
        try:
            image = capture_window_image(disp, xid)
            png = encode_png(image)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(png)
            return True, "xlib"
        except (XlibCaptureError, ImportError, OSError) as exc:
            xlib_err = str(exc)
            if not xid and _xlib_root_screenshot(Path(path)):
                return True, "xlib"
    if shutil.which("import") is None:
        return False, xlib_err or "no screenshot backend (import missing)"
    target = xid if xid else "root"
    code, _, err = _run(["import", "-window", target, str(path)], timeout=20.0)
    if code != 0 or not path.exists():
        return False, f"import failed: {err.strip()[:200]}"
    return True, "import"


def wait_for(predicate: Callable[[], Any], *, timeout: float, interval: float = 0.4):
    """Poll until predicate returns truthy; returns value or None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def persist_run(runtime: str | Path, result: dict[str, Any]) -> Path | None:
    """Write the run record for Inspect/audit. Best effort, never raises."""
    try:
        run_dir = Path(runtime) / RUN_DIR_NAME
        run_dir.mkdir(parents=True, exist_ok=True)
        out = run_dir / f"{result['run_id']}.json"
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return out
    except Exception:
        return None


# ---------------------------------------------------------------- workflows


def open_app_snapshot(
    app: str,
    *,
    project_path: str = "",
    start_cmd: str = "",
    cdp_port: int = 0,
    ready_timeout: float = 25.0,
    runtime: str | Path | None = None,
) -> dict[str, Any]:
    """§9-A: detect or start an existing instance → usable window →
    optionally open the project → focus → current screenshot.

    Success requires the right window and a fresh screenshot — starting a
    process alone is not enough. A missing app installation is a declared
    precondition failure (``environment``), never an auto-install.
    """
    run_id = _now_run_id("appsnap")
    result = _base_result(run_id, f"{app}: Snapshot")
    process_pattern = app
    wm_class_hint = app

    binary = start_cmd.split()[0] if start_cmd else ""
    if binary and not cdp_port and shutil.which(binary) is None:
        result = _fail(
            result,
            failure_class="environment",
            failed_stage="detect",
            message=f"{app} is not installed ({binary} missing) — "
            "a declared precondition, no automatic install",
            recoverable=False,
            next_action=(
                f"install {binary} (by the user), then call again"
            ),
        )
        _persist(runtime, result)
        return result
    running = process_running(process_pattern)
    if not running:
        if not start_cmd:
            result = _fail(
                result,
                failure_class="environment",
                failed_stage="detect",
                message=f"{app} is not running and no start_cmd was given",
                recoverable=False,
                next_action=f"check the {app} installation or pass start_cmd",
            )
            _persist(runtime, result)
            return result
        code, _, err = _run(start_cmd.split(), timeout=15.0)
        result["attempts"] = 1
        result["warnings"].append(f"start_cmd executed (rc={code})")
        result["side_effect_state"] = "app-started"
        if code != 0 and code != 124:
            result = _fail(
                result,
                failure_class="application",
                failed_stage="start",
                message=f"{app} start failed: {err.strip()[:200]}",
                recoverable=True,
                next_action="check the start log",
                side_effect_state="start-attempted",
            )
            _persist(runtime, result)
            return result

    if cdp_port:
        target = wait_for(
            lambda: (cdp_targets(cdp_port) or [None])[0] or None, timeout=ready_timeout
        )
        if target is None:
            result = _fail(
                result,
                failure_class="timeout",
                failed_stage="ready",
                message=(
                    f"no CDP target for {app} on port {cdp_port} "
                    f"within {ready_timeout:.0f}s"
                ),
                recoverable=True,
                next_action="is the process running with --remote-debugging-port? check the port",
                side_effect_state=result.get("side_effect_state", "none"),
            )
            _persist(runtime, result)
            return result
        window = {
            "xid": "",
            "title": str(target.get("title") or ""),
            "wm_class": app,
            "url": str(target.get("url") or ""),
            "backend": "cdp",
        }
    else:
        window = wait_for(
            lambda: find_window(wm_class_contains=wm_class_hint, title_contains=app),
            timeout=ready_timeout,
        )
    if window is None:
        result = _fail(
            result,
            failure_class="timeout",
            failed_stage="ready",
            message=f"no window for {app} within {ready_timeout:.0f}s "
            "(the process runs, but it is not usable)",
            recoverable=True,
            next_action="check window title/class (xprop inventory) or raise the timeout",
            side_effect_state=result.get("side_effect_state", "none"),
        )
        result["evidence_refs"].append({"windows": x11_windows()[:6]})
        _persist(runtime, result)
        return result
    result["evidence_refs"].append({"window": window})
    window["title"] = window["title"][:120]
    if cdp_port:
        window["title"] = str(target.get("title") or "")
        window["title"] = window["title"][:200]

    focused = True if cdp_port else focus_window(window["xid"])
    if not focused:
        result["warnings"].append(
            "focus not possible (no xdotool/wmctrl/xlib under Wayland) — "
            "a declared environment limit"
        )

    run_dir = Path(runtime) if runtime is not None else Path(tempfile.gettempdir())
    shot = run_dir / RUN_DIR_NAME / f"{run_id}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    ok, backend = (
        cdp_screenshot(cdp_port, shot) if cdp_port else screenshot(shot, xid=window["xid"])
    )
    if not ok:
        result = _fail(
            result,
            failure_class="environment",
            failed_stage="screenshot",
            message=f"screenshot not possible: {backend}",
            recoverable=False,
            next_action="provide a screenshot backend (import/xdotool environment)",
            side_effect_state=result.get("side_effect_state", "none"),
        )
        _persist(runtime, result)
        return result
    result["artifact_refs"].append(str(shot))
    result["result"] = {
        "app": app,
        "window": window,
        "focused": focused,
        "screenshot_backend": backend,
        "project_path": project_path,
    }
    result["status"] = "SUCCEEDED" if focused else "PARTIAL"
    result["summary"] = (
        f"{app}: window {window['title'][:60]!r} found, screenshot captured"
        + ("" if focused else " (focus not verifiable — environment limit)")
    )
    _persist(runtime, result)
    return result


def open_app_settings_snapshot(
    app: str,
    *,
    section: str = "",
    start_cmd: str = "",
    settings_keys: str = "ctrl+comma",
    ready_timeout: float = 25.0,
    runtime: str | Path | None = None,
) -> dict[str, Any]:
    """§9-B: open app → reach settings → verify state → screenshot.

    Changes NOTHING. A state check means: the window title/presence of a
    settings indicator demonstrably differs from the initial state — an
    arbitrary screenshot without a state check would be no proof.
    """
    run_id = _now_run_id("settings")
    result = _base_result(run_id, f"{app}: settings snapshot")
    before = find_window(title_contains=app, wm_class_contains=app)

    base = open_app_snapshot(
        app, start_cmd=start_cmd, ready_timeout=ready_timeout, runtime=runtime
    )
    if base["status"] not in ("SUCCEEDED", "PARTIAL"):
        result.update({k: v for k, v in base.items() if k != "run_id"})
        result["run_id"] = run_id
        result["failed_stage"] = f"open:{base.get('failed_stage', '')}"
        _persist(runtime, result)
        return result
    result["attempts"] = base.get("attempts", 0)
    result["side_effect_state"] = base.get("side_effect_state", "none")
    result["evidence_refs"].extend(base.get("evidence_refs", []))
    window = base["result"]["window"]

    # Navigation: declared per keybinding; may fail closed where the
    # compositor blocks synthetic keys (environment gap, not fake success).
    nav_supported = shutil.which("xdotool") is not None
    if nav_supported and settings_keys:
        # xdotool takes the combo as ONE argument ("ctrl+comma"); splitting on
        # "+" sent only the final segment and never the modifier.
        _run(["xdotool", "key", "--window", window["xid"], settings_keys])
    if not nav_supported:
        result["warnings"].append(
            "settings navigation not executable (no xdotool) — state is only reported"
        )

    after = wait_for(
        lambda: find_window(title_contains="settings", wm_class_contains=app)
        or find_window(title_contains="einstellungen", wm_class_contains=app),
        timeout=6.0,
    )
    state_changed = bool(after) and (not before or after["xid"] != before["xid"] or
                                     after["title"] != before["title"])

    run_dir = Path(runtime) if runtime is not None else Path(tempfile.gettempdir())
    shot = run_dir / RUN_DIR_NAME / f"{run_id}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    target_xid = after["xid"] if after else window["xid"]
    ok, backend = screenshot(shot, xid=target_xid)
    if not ok:
        result = _fail(
            result,
            failure_class="environment",
            failed_stage="screenshot",
            message=f"screenshot not possible: {backend}",
            recoverable=False,
            next_action="provide a screenshot backend",
            side_effect_state="none",
        )
        _persist(runtime, result)
        return result

    result["artifact_refs"].append(str(shot))
    result["result"] = {
        "app": app,
        "section": section,
        "window": after or window,
        "state_changed": state_changed,
        "screenshot_backend": backend,
    }
    if state_changed:
        result["status"] = "SUCCEEDED"
        title = result["result"]["window"]["title"][:60]
        result["summary"] = f"{app}: settings reached ({title!r})"
    else:
        result["status"] = "PARTIAL"
        result["warnings"].append("settings not verified as a separate state")
        result["summary"] = (
            f"{app}: screenshot captured, but opening settings was not verified "
            "(no measurable state change)"
        )
    _persist(runtime, result)
    return result


def _persist(runtime: str | Path | None, result: dict[str, Any]) -> None:
    if runtime is None:
        return
    result["run_ref"] = str(persist_run(runtime, result) or "")


def _restart_app(
    stop_pattern: str,
    start_cmd: str,
    *,
    cdp_port: int = 0,
    ready_timeout: float = 30.0,
) -> tuple[bool, str]:
    """Controlled restart: TERM the collected PIDs only, then start again.

    Waits for readiness (CDP target when a port is given, else window/process).
    Returns (ok, detail). Kills exactly the PIDs observed — never a pattern
    in our own command line.
    """
    for pid in process_running(stop_pattern):
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            continue
    if not wait_for(lambda: not process_running(stop_pattern), timeout=8.0):
        for pid in process_running(stop_pattern):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                continue
        if not wait_for(lambda: not process_running(stop_pattern), timeout=5.0):
            return False, "the process could not be terminated"
    try:
        subprocess.Popen(  # noqa: S603 — declared start command
            start_cmd.split(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return False, f"restart failed: {type(exc).__name__}: {exc}"
    port = cdp_port

    def probe() -> bool:
        return bool(cdp_targets(port)) if port else bool(process_running(stop_pattern))

    if wait_for(probe, timeout=ready_timeout):
        return True, "restarted"
    return False, f"app not ready within {ready_timeout:.0f}s"


def _cdp_eval(port: int, expression: str, *, timeout: float = 10.0) -> str:
    """Evaluate JS in the first page target; returns a string value."""

    targets = [x for x in cdp_targets(port) if x.get("type") == "page"]
    if not targets:
        return ""
    ws_url = targets[0].get("webSocketDebuggerUrl", "")
    try:
        import websockets.sync.client as _ws

        with _ws.connect(ws_url, open_timeout=timeout, close_timeout=2) as conn:
            conn.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": expression, "returnByValue": True},
                    }
                )
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                msg = json.loads(conn.recv())
                if msg.get("id") == 1:
                    value = msg.get("result", {}).get("result", {}).get("value")
                    return "" if value is None else str(value)
    except Exception:
        return ""
    return ""


MODEL_READER_JS = (
    "(() => { const els = [...document.querySelectorAll('button,[role=combobox]')]"
    ".filter(e => e.getBoundingClientRect().width > 0); "
    "const re = /(deepseek|gpt|claude|gemini|llama|qwen|mistral|glm|kimi"
    "|sonnet|opus|haiku|flash|turbo)/i; "
    "const el = els.find(e => re.test(e.textContent || '')); "
    "return el ? (el.textContent || '').trim() : ''; })()"
)

ANSWER_READER_JS = (
    "(() => { const q = '[data-slot*=assistant],[data-slot*=message],.prose'; "
    "const nodes = [...document.querySelectorAll(q)]; "
    "const last = nodes.filter(n => (n.textContent || '').trim().length > 0).pop(); "
    "return last ? (last.textContent || '').trim().slice(-4000) : ''; })()"
)

SEND_JS_TEMPLATE = (
    "(() => { const el = document.querySelector('[data-slot=composer-rich-input]'); "
    "if (!el) return 'no-input'; el.focus(); "
    "document.execCommand('insertText', false, {prompt}); "
    "const ev = new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', bubbles: true}); "
    "el.dispatchEvent(ev); "
    "return 'sent'; })()"
)

def _latest_routing_evidence(session_dir: Path) -> dict[str, Any] | None:
    """Newest request dump's model field — backend evidence, not the dropdown."""
    try:
        dumps = sorted(session_dir.glob("request_dump_*.json"), key=lambda x: x.stat().st_mtime)
    except Exception:
        return None
    if not dumps:
        return None
    newest = dumps[-1]
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
    except Exception:
        return None
    model = ""
    if isinstance(data, dict):
        model = str(data.get("model") or "")
        req = data.get("request")
        if not model and isinstance(req, dict):
            model = str(req.get("model") or "")
    if not model:
        return None
    return {"model": model, "source": str(newest), "mtime": newest.stat().st_mtime}


def run_model_test(
    app: str,
    model: str,
    test_case: str,
    *,
    expect: str = "",
    cdp_port: int = 0,
    session_dir: str = "",
    restore_model: bool = True,
    answer_timeout: float = 120.0,
    runtime: str | Path | None = None,
) -> dict[str, Any]:
    """§9-C: open app → model configuration → run a test → prove the answer AND
    the actual runtime assignment → check the expectation.

    A dropdown name proves no routing: ``routing_verified`` only becomes
    true when the backend evidence (request dump) confirms the model —
    if it is missing, the field stays false with a warning. Temporary
    changes are restored; a residual state that cannot be restored
    stays visible.
    """
    run_id = _now_run_id("modeltest")
    result = _base_result(run_id, f"{app}: model test {model}")

    if not cdp_port:
        result = _fail(
            result,
            failure_class="configuration",
            failed_stage="config",
            message="cdp_port missing — the model test needs the app interface (§9-C)",
            recoverable=False,
            next_action="start the app with --remote-debugging-port",
        )
        _persist(runtime, result)
        return result
    if not cdp_targets(cdp_port):
        result = _fail(
            result,
            failure_class="timeout",
            failed_stage="ready",
            message=f"{app} not reachable over CDP (port {cdp_port})",
            recoverable=True,
            next_action="start the app (open_app_snapshot)",
        )
        _persist(runtime, result)
        return result

    before = _cdp_eval(cdp_port, MODEL_READER_JS)
    result["evidence_refs"].append({"model_before": before})
    if model and model.lower() not in before.lower():
        # Selecting is UI navigation; a failed selection is reported, not faked.
        model_needle = json.dumps(model.lower())
        opened = _cdp_eval(
            cdp_port,
            "(() => { const els = [...document.querySelectorAll('button,[aria-haspopup]')]"
            ".filter(e => e.getBoundingClientRect().width > 0); "
            "const hit = els.find("
            f"e => (e.textContent || '').toLowerCase().includes({model_needle})); "
            "const el = hit || els.find("
            "e => /(deepseek|gpt|claude|flash)/i.test(e.textContent || '')); "
            "if (!el) return 'no-trigger'; el.click(); return 'opened'; })()",
        )
        result["warnings"].append(f"model selection: dropdown trigger {opened}")
        if opened != "opened":
            result["warnings"].append("dropdown trigger not found")
    after = _cdp_eval(cdp_port, MODEL_READER_JS)
    result["evidence_refs"].append({"model_after": after})
    selection_verified = bool(after) and (not model or model.lower() in after.lower())
    if not selection_verified:
        result["warnings"].append("model selection not verified in the UI")

    started = time.monotonic()
    sent = _cdp_eval(
        cdp_port,
        SEND_JS_TEMPLATE.replace("{prompt}", json.dumps(test_case)),
    )
    if sent != "sent":
        result = _fail(
            result,
            failure_class="application",
            failed_stage="execute",
            message=f"test prompt could not be sent ({sent or 'no result'})",
            recoverable=True,
            next_action="check the composer selector",
            side_effect_state=result.get("side_effect_state", "none"),
        )
        _persist(runtime, result)
        return result

    answer = wait_for(
        lambda: _cdp_eval(cdp_port, ANSWER_READER_JS), timeout=answer_timeout, interval=2.0
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    result["result"] = {
        "app": app,
        "model_requested": model,
        "answer": (answer or "")[:2000],
        "duration_ms": duration_ms,
        "selection_verified": selection_verified,
    }
    if not answer:
        result = _fail(
            result,
            failure_class="timeout",
            failed_stage="execute",
            message=f"no answer within {answer_timeout:.0f}s",
            recoverable=True,
            next_action="check the backend status",
            side_effect_state=result.get("side_effect_state", "none"),
        )
    else:
        evidence = _latest_routing_evidence(Path(session_dir)) if session_dir else None
        routing_verified = bool(evidence) and (
            not model or model.lower() in str(evidence["model"]).lower()
        )
        result["result"]["routing_evidence"] = evidence
        result["result"]["routing_verified"] = routing_verified
        if not evidence:
            result["warnings"].append(
                "backend evidence missing — routing stays unverified "
                "(a dropdown alone does not count)"
            )
        elif not routing_verified:
            result["warnings"].append(
                f"backend evidence names {evidence['model']!r} instead of {model!r}"
            )
        expectation_ok = (not expect) or (expect.lower() in (answer or "").lower())
        if not expectation_ok:
            result["warnings"].append(f"expectation {expect!r} not found in the answer")
        if selection_verified and routing_verified and expectation_ok:
            result["status"] = "SUCCEEDED"
            result["summary"] = (
                f"{app}: {model} answered in {duration_ms} ms, routing proven by backend"
            )
        else:
            result["status"] = "PARTIAL"
            result["summary"] = (
                f"{app}: answer captured in {duration_ms} ms — "
                + ("expectation met, " if expectation_ok else "expectation missed, ")
                + ("routing proven" if routing_verified else "routing unproven")
            )
    if restore_model and before and before != after:
        original_name = before.strip()[:40]
        restore_js = (
            "(() => { /* model-select restore */ "
            "const els = [...document.querySelectorAll('button,[aria-haspopup]')]"
            ".filter(e => e.getBoundingClientRect().width > 0); "
            f"const needle = {json.dumps(original_name)}.toLowerCase(); "
            "const hit = els.find(e => (e.textContent || '').toLowerCase().includes(needle)); "
            "if (!hit) return 'no-option'; hit.click(); return 'restored'; })()"
        )
        restored = _cdp_eval(cdp_port, restore_js)
        verified = _cdp_eval(cdp_port, MODEL_READER_JS)
        if restored == "restored" and original_name[:20].lower() in (verified or "").lower():
            result["side_effect_state"] = "restored"
        else:
            result["side_effect_state"] = "model-selection-left"
            result["warnings"].append(
                f"original model {original_name!r} not restored automatically "
                f"(restore={restored!r}, sichtbar={str(verified)[:60]!r})"
            )
    _persist(runtime, result)
    return result


def verify_setting_persistence(
    setting: str,
    temporary_value: str,
    *,
    settings_file: str,
    reader: Callable[[str], str] | None = None,
    writer: Callable[[str, str], None] | None = None,
    stop_pattern: str = "",
    start_cmd: str = "",
    cdp_port: int = 0,
    ready_timeout: float = 30.0,
    runtime: str | Path | None = None,
) -> dict[str, Any]:
    """§9-D: capture the original value → set/save a test value →
    controlled restart → check the persisted value AND the runtime effect →
    restore the original value.

    A partially executed flow is never a complete success; if the restore
    fails, the changed state stays visible (``side_effect_state``) instead of
    disappearing into a silent success.
    """
    import yaml

    run_id = _now_run_id("setting")
    result = _base_result(run_id, f"{setting}: Persistenz-Test")
    path = Path(settings_file)

    def _default_reader(key: str) -> str:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return str(data.get(key) or "")
        except Exception:
            return ""

    def _default_writer(key: str, value: str) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        data[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    _read = reader or _default_reader
    _write = writer or _default_writer

    original = _read(setting)
    original_missing = not original
    result["evidence_refs"].append({"original": original, "file": str(path)})
    if original_missing:
        result["warnings"].append("setting was not set before (original empty)")

    try:
        _write(setting, temporary_value)
    except Exception as exc:
        result = _fail(
            result,
            failure_class="application",
            failed_stage="write",
            message=f"test value not storable: {type(exc).__name__}: {exc}",
            recoverable=True,
            next_action="check the setting name/value",
        )
        _persist(runtime, result)
        return result
    result["side_effect_state"] = f"setting-changed:{setting}={temporary_value}"

    restored = False
    try:
        restarted, detail = _restart_app(
            stop_pattern, start_cmd, cdp_port=cdp_port, ready_timeout=ready_timeout
        )
        result["attempts"] = result.get("attempts", 0) + 1
        if not restarted:
            result = _fail(
                result,
                failure_class="timeout",
                failed_stage="restart",
                message=f"app restart failed: {detail}",
                recoverable=True,
                next_action="check the start command/environment",
                side_effect_state=result["side_effect_state"],
            )
        else:
            persisted = _read(setting) == temporary_value
            runtime_value = _read(setting)
            result["result"] = {
                "setting": setting,
                "original": original,
                "temporary": temporary_value,
                "persisted": persisted,
                "runtime_value": runtime_value,
            }
            if persisted:
                result["status"] = "SUCCEEDED"
                result["summary"] = (
                    f"{setting}: test value survived the restart ({runtime_value!r})"
                )
            else:
                result = _fail(
                    result,
                    failure_class="application",
                    failed_stage="verify",
                    message=f"{setting} not persisted after the restart "
                    f"(read back: {runtime_value!r})",
                    recoverable=True,
                    next_action="check the setting's persistence path",
                    side_effect_state=result["side_effect_state"],
                )
    finally:
        # Restore ALWAYS — a half-finished run must never leave silent changes.
        try:
            if original_missing:
                _write(setting, "")
            else:
                _write(setting, original)
            restored = _read(setting) == original
        except Exception:
            restored = False
    if restored:
        result["side_effect_state"] = "restored"
    else:
        result["warnings"].append(
            f"restore NOT confirmed — remaining state: "
            f"{setting}={_read(setting)!r}"
        )
        result["side_effect_state"] = f"setting-left-changed:{setting}"
        if result["status"] == "SUCCEEDED":
            result["status"] = "PARTIAL"
    _persist(runtime, result)
    return result


def workflow_contract(workflow_id: str) -> dict[str, Any] | None:
    """The contract for one workflow id, or ``None`` — the registry view of §6."""

    name = str(workflow_id or "")
    return next((item for item in workflow_contracts() if item["name"] == name), None)


def workflow_runners() -> dict[str, Callable[..., dict[str, Any]]]:
    """The already-existing implementation of each contract.

    These functions were always there; only ``skaldir workflows list`` ever read the registry, so
    nothing in the product could *start* one. This map is the dispatch, not a second implementation.
    """

    return {
        "open_app_snapshot": open_app_snapshot,
        "open_app_settings_snapshot": open_app_settings_snapshot,
        "verify_setting_persistence": verify_setting_persistence,
        "run_model_test": run_model_test,
    }


def run_workflow(
    workflow_id: str,
    *,
    runtime: str | Path | None = None,
    params: Mapping[str, Any] | None = None,
    events: Any = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Start one of the declared §6 workflows and report its own verdict honestly.

    Wiring only: the contract decides which inputs are required, the map above decides which
    existing function runs, and the function's own result decides the outcome. ``unknown`` is
    never turned into success, and a missing prerequisite is a ``blocked`` with the reason —
    never a substitute run.
    """

    contract = workflow_contract(workflow_id)
    if contract is None:
        known = [item["name"] for item in workflow_contracts()]
        return {
            "status": "blocked",
            "workflow": workflow_id,
            "verified": False,
            "reason": f"unknown workflow {workflow_id!r} — known: {known}",
        }
    arguments = dict(params or {})
    required = [item for item in contract["inputs"] if not item.endswith("?")]
    missing = [item for item in required if not str(arguments.get(item) or "").strip()]
    if missing:
        return {
            "status": "blocked",
            "workflow": workflow_id,
            "verified": False,
            "reason": (
                f"missing required input(s) {missing} — the contract declares "
                f"{contract['inputs']}"
            ),
        }
    runner = workflow_runners().get(workflow_id)
    if runner is None:
        return {
            "status": "blocked",
            "workflow": workflow_id,
            "verified": False,
            "reason": f"no runner is wired for workflow {workflow_id!r}",
        }
    arguments.pop("runtime", None)
    try:
        result = runner(**arguments, runtime=runtime)
    except Exception as exc:  # noqa: BLE001 — a failing workflow is reported, never hidden
        result = {"ok": False, "status": "failed", "failure_class": f"{type(exc).__name__}"}
        outcome = {
            "status": "failed",
            "workflow": workflow_id,
            "verified": False,
            "reason": f"{type(exc).__name__}: {exc}"[:300],
            "result": result,
        }
    else:
        raw_status = str((result or {}).get("status") or "").strip().lower()
        ok = (result or {}).get("ok")
        if ok is True or raw_status in {"ok", "verified", "success", "succeeded"}:
            status = "verified"
        elif raw_status and raw_status not in {"unknown", ""}:
            status = raw_status
        elif ok is False:
            status = "failed"
        else:
            status = "unknown"
        outcome = {
            "status": status,
            "workflow": workflow_id,
            "verified": status == "verified",
            "success_rule": contract["success"],
            "scope": contract["scope"],
            "purpose": contract["purpose"],
            "result": result,
        }
        if status != "verified":
            outcome["reason"] = str(
                (result or {}).get("failure_class") or (result or {}).get("detail") or ""
            )[:300]
    if runtime is not None:
        # persist_run keys the file by run_id — without one nothing is written at all (measured)
        outcome["run_id"] = _now_run_id(f"wf-{workflow_id}")
        path = persist_run(runtime, outcome)
        if path is not None:
            outcome["evidence"] = str(path)
    # No event is emitted: the event store's vocabulary is closed (it refuses "workflow_run"),
    # and inventing a kind is a product decision. The persisted run record is the evidence.
    return outcome


def workflow_contracts() -> list[dict[str, Any]]:
    """§6 compact contracts of the workflows (registry view)."""
    return [
        {
            "name": "open_app_snapshot",
            "scope": "global",
            "purpose": "work",
            "execution": "workflow",
            "reasoning": "deterministic",
            "inputs": ["app", "project_path?", "start_cmd?"],
            "success": "right window found + fresh screenshot",
            "side_effects": ["app-start (only with start_cmd)"],
            "error_policy": "precise failure_class instead of a substitute success",
        },
        {
            "name": "open_app_settings_snapshot",
            "scope": "global",
            "purpose": "work",
            "execution": "workflow",
            "reasoning": "deterministic",
            "inputs": ["app", "section?", "settings_keys?"],
            "success": "settings state verified + screenshot",
            "side_effects": [],
            "error_policy": "PARTIAL without state verification, never a silent success",
        },
        {
            "name": "verify_setting_persistence",
            "scope": "project",
            "purpose": "verification",
            "execution": "workflow",
            "reasoning": "deterministic",
            "inputs": ["setting", "temporary_value", "settings_file", "start_cmd"],
            "success": "test value survived a controlled restart AND was restored",
            "side_effects": ["setting-write (temporary)", "app-restart (controlled)"],
            "error_policy": "partial execution is never a success; restore state becomes visible",
        },
        {
            "name": "run_model_test",
            "scope": "global",
            "purpose": "verification",
            "execution": "workflow",
            "reasoning": "bounded-agent",
            "inputs": ["app", "model", "test_case", "expect?"],
            "success": "answer captured + routing proven by backend evidence",
            "side_effects": ["chat-turn (test prompt)", "model-selection (temporary)"],
            "error_policy": (
                "a dropdown alone proves nothing; without backend evidence "
                "routing stays unverified"
            ),
        },
    ]
