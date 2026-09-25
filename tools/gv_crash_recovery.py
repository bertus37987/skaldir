#!/usr/bin/env python3
"""God's Voice — crash recovery: ``kill -9`` → restart → state intact, no double effects.

Destructive ONLY towards the marker-identified God's Voice instance. The process
identification is deliberately paranoid (never ``pkill -f``):

1. ``pgrep -f 'gods[-]voice'`` — the bracket trick keeps the pattern from
   matching this script's own command line or its shell wrapper,
2. this process and its parent are removed from the candidate list,
3. a PID must carry the GV user-data marker (``HERMES_DESKTOP_USER_DATA_DIR``
   pointing under ``$HERMES_HOME/godsv``) in ``/proc/<pid>/environ``,
4. a PID whose cmdline names the *stock* Hermes app
   (``hermes-agent/apps/desktop/release/linux-unpacked/Hermes``) is refused —
   that process is the running session's own UI.

Five state areas are snapshotted before/after the restart:

1. ``$HERMES_HOME/godsv/settings.yaml`` — voice strictness + byte hash,
2. completion store ``completion/<sid>.json`` — ``revision``, ``applied_requests``
   idempotency, duplicate ``.jsonl`` lines,
3. ``$HERMES_HOME/state.db`` — the ``sessions`` row for this id, open
   ``session_turn_leases`` (read-only ``mode=ro``),
4. process/ownership topology — one GV process group, one live backend owner,
5. ``desktop-launch.log`` — exactly one new ``--- launch`` block; the known
   ``Session not found`` retry cadence is counted and reported (F15 post), never
   failed on.

Modes:
  ``--check``/``--dry-run``  identify + report only; kills NOTHING, starts NOTHING.
  default                    full recovery run (kills and restarts the GV instance).

Exit codes:
  0 OK · 2 GV instance not found · 3 kill/restart failed ·
  4 state violation (settings/sessions/store/topology) ·
  5 double effect (revision jump, duplicate request id, second serve/process).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from godsv import desktop
from godsv.completion import CompletionStore
from godsv.pause import load_strictness

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from gv_product_run import _pick_verified_serve, api  # noqa: E402

# --- constants and exit catalogue -------------------------------------------

GV_PATTERN = "gods[-]voice"  # bracket trick: must never contain the literal "gods-voice"
STOCK_APP_SUFFIX = "/hermes-agent/apps/desktop/release/linux-unpacked/Hermes"
LAUNCH_LINE = "--- launch "
F15_MARKER = "Session not found"
REPLAY_TEXT = "crash-recovery replay marker (idempotency probe)"
DEFAULT_PORT = 9333

EXIT_OK = 0
EXIT_NO_INSTANCE = 2
EXIT_KILL_RESTART = 3
EXIT_STATE = 4
EXIT_DOUBLE = 5

EXIT_CODES = {
    EXIT_OK: "state intact, no double effects",
    EXIT_NO_INSTANCE: "no marker-identified God's Voice instance found",
    EXIT_KILL_RESTART: "kill or restart failed (PID survived / binary missing / CDP timeout)",
    EXIT_STATE: "state violation (settings, sessions, store or topology)",
    EXIT_DOUBLE: "double effect (revision jump, duplicate request id, second serve/process)",
}


class CrashError(RuntimeError):
    """Kill/restart path failed — the caller maps this to EXIT_KILL_RESTART."""


# --- paths -------------------------------------------------------------------


def hermes_home() -> Path:
    return desktop.hermes_home()


def runtime_dir() -> Path:
    """$HERMES_HOME/godsv — same root the panel backend uses (plugin_api.runtime_dir)."""
    return hermes_home() / "godsv"


def receipts_root() -> Path:
    return runtime_dir() / "receipts" / "crash-recovery"


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


# --- process identification (B.2) --------------------------------------------


def _pgrep_pids(pattern: str) -> list[int]:
    """PIDs whose full command line matches *pattern* (``pgrep -f``)."""
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, read-only process listing
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(token) for token in proc.stdout.split() if token.isdigit()]


def _proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.decode("utf-8", "replace").replace("\0", " ").strip()


def is_pid_alive(pid: int) -> bool:
    """Signal-0 probe (mirrors update-marker.ts): ESRCH dead, EPERM alive."""
    if not isinstance(pid, int) or pid <= 0:  # pid 0 would signal our whole group
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _pid_env_marker(pid: int, marker: str, *, read_env=None) -> bool:
    """Second, independent marker source: the PID's own environment.

    ``desktop._pid_has_marker`` reads ``/proc/<pid>/environ`` too, but this path
    is deliberately separate so the identification stands on two legs (audit-4
    R1: the instance ran while identification said "no instance found"). It
    accepts both real spellings of the user-data dir — ``…/godsv/`` and
    ``…/godsv/desktop-userdata`` — because the live instance has worn both.

    ``read_env`` defaults to the desktop reader *at call time* (not as a bound
    default) so tests and callers can substitute it.
    """
    reader = read_env or _proc_environ_text
    env_text = reader(pid)
    return bool(env_text) and desktop.user_data_marker_matches(env_text, marker)


def gv_pids(
    *,
    pattern: str = GV_PATTERN,
    pgrep=_pgrep_pids,
    has_marker=desktop._pid_has_marker,
    env_marker=_pid_env_marker,
    cmdline=_proc_cmdline,
    marker: str | None = None,
    self_pids: tuple[int, ...] | None = None,
) -> list[int]:
    """Marker-identified GV PIDs — the ONLY process set this tool may kill.

    Bracket pattern + self/parent exclusion + user-data marker (desktop helper
    *or* the environ probe) + a hard refusal of anything pointing at the stock
    Hermes app binary.
    """
    marker_text = marker if marker is not None else str(desktop.skaldir_user_data())
    exclude = {os.getpid(), os.getppid()} if self_pids is None else set(self_pids)
    found: list[int] = []
    for pid in pgrep(pattern):
        if pid in exclude:
            continue
        if STOCK_APP_SUFFIX in cmdline(pid):
            continue
        if not (has_marker(pid, marker_text) or env_marker(pid, marker_text)):
            continue
        found.append(pid)
    return sorted(found)


def crash(
    pids: list[int],
    *,
    kill=os.kill,
    alive=is_pid_alive,
    timeout: float = 15.0,
    interval: float = 0.5,
    sleep=time.sleep,
) -> dict:
    """``kill -9`` each PID (never ``pkill -f``) and wait until all are gone."""
    if not pids:
        raise CrashError("refusing to kill: empty PID set (would be a silent no-op)")
    for pid in pids:
        try:
            kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    started = time.time()
    remaining = [pid for pid in pids if alive(pid)]
    while remaining and time.time() - started < timeout:
        sleep(interval)
        remaining = [pid for pid in pids if alive(pid)]
    if remaining:
        raise CrashError(f"PIDs survived kill -9 after {timeout:.0f}s: {remaining}")
    return {"killed": list(pids), "seconds": round(time.time() - started, 1)}


# --- restart (same env as production) ----------------------------------------


def cdp_alive(port: int, *, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(  # noqa: S310 — loopback only
            f"http://127.0.0.1:{int(port)}/json", timeout=timeout
        ) as resp:
            resp.read()
        return True
    except (OSError, urllib.error.URLError):
        return False


def wait_cdp(
    port: int, *, timeout: float = 90.0, interval: float = 1.0, probe=cdp_alive, sleep=time.sleep
):
    """Bounded wait for the CDP endpoint (never unbounded)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if probe(port):
            return True
        sleep(interval)
    return bool(probe(port))


def restart_app(
    *,
    port: int = DEFAULT_PORT,
    binary: str | None = None,
    popen=subprocess.Popen,
    env: dict | None = None,
    log_path: Path | None = None,
    wait=wait_cdp,
    timeout: float = 90.0,
) -> dict:
    """Start the GV binary with the production env + a debug port; wait for CDP.

    Mirrors ``desktop.launch()`` (same binary resolution, same env, same launch
    log line) with one addition: ``--remote-debugging-port`` for the CDP probes.
    """
    binary = binary or desktop.hermes_app_binary()
    if not binary:
        raise CrashError("no packaged God's Voice binary — run `godsvoice desktop build-app` first")
    log_path = Path(log_path or (runtime_dir() / "desktop-launch.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    launch_note = f"\n{LAUNCH_LINE}{stamp} via {binary} (gv_crash_recovery)\n"
    with log_path.open("a", encoding="utf-8") as log:
        log.write(launch_note)
        log.flush()
        try:
            popen(
                [
                    binary,
                    f"--class={desktop.WM_CLASS}",
                    f"--remote-debugging-port={int(port)}",
                    f"hermes://open/{desktop.PANEL_ROUTE}",
                ],
                start_new_session=True,
                stdout=log,
                stderr=log,
                env=env if env is not None else desktop.gods_voice_env(),
            )
        except OSError as exc:
            raise CrashError(f"spawn failed: {exc}") from exc
    if not wait(port, timeout=timeout):
        raise CrashError(f"CDP port {port} did not answer within {timeout:.0f}s")
    return {"binary": str(binary), "port": int(port), "launch_log": str(log_path)}


# --- the GV backend endpoint (port + token) ----------------------------------


def _proc_environ_text(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def gv_candidates(
    *,
    run=subprocess.run,
    read_env=_proc_environ_text,
    read_cmdline=_proc_cmdline,
    marker: str | None = None,
) -> list[tuple[int, str]]:
    """(port, token) candidates: loopback listeners whose process env carries the
    GV user-data marker. The Electron DevTools port inherits the same marker, so
    candidates declaring ``--remote-debugging-port=<port>`` are filtered out —
    the debug port is never the API (same rule as tools/gv_product_run.py, but
    with the marker derived from ``desktop.skaldir_user_data()`` instead of a
    hardcoded home path).
    """
    marker_text = marker if marker is not None else f"{desktop.skaldir_user_data()}"
    try:
        out = run(["ss", "-tlnp"], capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found: list[tuple[int, str]] = []
    for line in out.splitlines():
        if "127.0.0.1:" not in line or "pid=" not in line:
            continue
        match = re.search(r"127\.0\.0\.1:(\d+)", line)
        if not match:
            continue
        port = int(match.group(1))
        for pid_match in re.finditer(r"pid=(\d+)", line):
            pid = int(pid_match.group(1))
            env_text = read_env(pid)
            if f"HERMES_DESKTOP_USER_DATA_DIR={marker_text}" not in env_text:
                continue
            token = ""
            for chunk in env_text.split("\0"):
                if chunk.startswith("HERMES_DASHBOARD_SESSION_TOKEN="):
                    token = chunk.split("=", 1)[1]
            if f"--remote-debugging-port={port}" in read_cmdline(pid):
                continue
            found.append((port, token))
    return found


def gv_serve(*, api_fn=api, candidates_fn=gv_candidates) -> dict:
    """The real GV serve + how many candidates verified against ``/completion``."""
    candidates = candidates_fn()

    def probe(port: int, token: str) -> dict | None:
        try:
            data = api_fn(port, "GET", "/completion", token=token)
        except Exception:  # noqa: BLE001 — a dead candidate is not an error here
            return None
        return None if data.get("http_error") else data

    port, token = _pick_verified_serve(candidates, probe)
    verified = 0
    for cand_port, cand_token in candidates:
        if isinstance(probe(cand_port, cand_token), dict):
            verified += 1
    return {"port": port, "token": token, "candidates": len(candidates), "verified": verified}


# --- state snapshots (the five areas) ----------------------------------------


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def count_launch_blocks(text: str) -> int:
    return len(re.findall(rf"^{re.escape(LAUNCH_LINE)}", text, re.MULTILINE))


def count_f15_lines(text: str) -> int:
    return text.count(F15_MARKER)


def completion_paths(state_dir: Path | str, sid: str) -> tuple[Path, Path]:
    """(state.json, history.jsonl) — path logic from CompletionStore itself."""
    store = CompletionStore(Path(state_dir), sid)
    return Path(store.state_path), Path(store.history_path)


def settings_snapshot(*, state_dir: Path | None = None) -> dict:
    state_dir = Path(state_dir or runtime_dir())
    path = state_dir / "settings.yaml"
    return {
        "path": str(path),
        "exists": path.exists(),
        "sha256": sha256_file(path),
        "strictness": load_strictness(state_dir),
    }


def store_snapshot(sid: str, *, state_dir: Path | None = None) -> dict:
    state_dir = Path(state_dir or runtime_dir())
    state_path, history_path = completion_paths(state_dir, sid)
    try:
        record = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    lines = [line for line in read_text(history_path).splitlines() if line.strip()]
    return {
        "session_id": sid,
        "path": str(state_path),
        "history_path": str(history_path),
        "exists": state_path.exists(),
        "state": str(record.get("state") or ""),
        "revision": int(record.get("revision") or 0),
        "applied_requests": [str(item) for item in (record.get("applied_requests") or [])],
        "jsonl_lines": len(lines),
        "jsonl_duplicates": len(lines) - len(set(lines)),
    }


def session_snapshot(sid: str, *, db_path: Path | None = None, now: float | None = None) -> dict:
    """Read-only view of the session row + open turn leases for *sid*."""
    db_path = Path(db_path or (hermes_home() / "state.db"))
    now = float(now if now is not None else time.time())
    result: dict = {"available": False, "rows": 0, "row": None, "error": ""}
    leases: dict = {"available": False, "open_for_session": 0, "all_open": 0, "error": ""}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover — missing db
        result["error"] = str(exc)
        leases["error"] = str(exc)
        return {"sessions": result, "leases": leases}
    try:
        try:
            rows = con.execute(
                "SELECT id, session_key, started_at, message_count"
                " FROM sessions WHERE session_key = ?",
                (sid,),
            ).fetchall()
            result.update(
                available=True,
                rows=len(rows),
                row=(
                    None
                    if not rows
                    else {
                        "id": rows[0][0],
                        "session_key": rows[0][1],
                        "started_at": rows[0][2],
                        "message_count": rows[0][3],
                    }
                ),
            )
        except sqlite3.Error as exc:
            result["error"] = str(exc)
        try:
            open_for_session = con.execute(
                "SELECT count(*) FROM session_turn_leases"
                " WHERE conversation_id = ? AND expires_at > ?",
                (sid, now),
            ).fetchone()[0]
            all_open = con.execute(
                "SELECT count(*) FROM session_turn_leases WHERE expires_at > ?", (now,)
            ).fetchone()[0]
            leases.update(
                available=True, open_for_session=int(open_for_session), all_open=int(all_open)
            )
        except sqlite3.Error as exc:
            leases["error"] = str(exc)
    finally:
        con.close()
    return {"sessions": result, "leases": leases}


def ownership_snapshot(*, path: Path | None = None, alive=is_pid_alive) -> dict:
    path = Path(path or (runtime_dir() / "backend-ownership.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "entries": 0, "live": 0, "path": str(path)}
    entries = data.get("backends") if isinstance(data, dict) else None
    entries = entries if isinstance(entries, list) else []
    live = 0
    for entry in entries:
        if isinstance(entry, dict) and alive(int(entry.get("pid") or 0)):
            live += 1
    return {"available": True, "entries": len(entries), "live": live, "path": str(path)}


def process_snapshot(*, list_pids=gv_pids, cmdline=_proc_cmdline) -> dict:
    pids = list(list_pids())
    mains = sorted(pid for pid in pids if "--type=" not in cmdline(pid))
    return {
        "gv_pids": pids,
        "main_procs": mains,
        "cmdlines": {str(pid): cmdline(pid)[:200] for pid in pids},
    }


def log_snapshot(*, launch_log: Path | None = None) -> dict:
    launch_log = Path(launch_log or (runtime_dir() / "desktop-launch.log"))
    text = read_text(launch_log)
    return {
        "path": str(launch_log),
        "launch_blocks": count_launch_blocks(text),
        "f15_retry_lines": count_f15_lines(text),
        "sha256": sha256_file(launch_log),
    }


def snapshot(sid: str, *, state_dir: Path | None = None, launch_log: Path | None = None) -> dict:
    state_dir = Path(state_dir or runtime_dir())
    session = session_snapshot(sid)
    return {
        "ts": time.time(),
        "session_id": sid,
        "settings": settings_snapshot(state_dir=state_dir),
        "store": store_snapshot(sid, state_dir=state_dir),
        "sessions": session["sessions"],
        "leases": session["leases"],
        "processes": process_snapshot(),
        "ownership": ownership_snapshot(),
        "log": log_snapshot(launch_log=launch_log),
    }


# --- the verdict (9 assertions of plan B.3) ----------------------------------


def verify(
    pre: dict, post: dict, *, replay: dict | None = None, serves: int | None = None
) -> list[dict]:
    """Compare pre/post snapshots → findings (kind ``state`` or ``double_effect``)."""
    findings: list[dict] = []

    def report(kind: str, detail: str) -> None:
        findings.append({"kind": kind, "detail": detail})

    # 1) settings.yaml persistence
    if pre["settings"]["sha256"] != post["settings"]["sha256"]:
        report("state", "settings.yaml changed across the restart")
    elif pre["settings"]["strictness"] != post["settings"]["strictness"]:
        report("state", "voice strictness changed across the restart")

    # 2) completion store: revision + idempotency + history hygiene
    if pre["store"]["revision"] != post["store"]["revision"]:
        report(
            "double_effect",
            f"completion revision moved across the restart: "
            f"{pre['store']['revision']} -> {post['store']['revision']}",
        )
    gained = post["store"]["jsonl_duplicates"] - pre["store"]["jsonl_duplicates"]
    if gained > 0:
        report("double_effect", f"completion history gained {gained} duplicate line(s)")
    if replay is not None:
        if replay.get("applied_count") != 1:
            report(
                "double_effect",
                f"replayed request id applied {replay.get('applied_count')}x (expected exactly 1)",
            )
        if replay.get("revision_after") != replay.get("revision_before"):
            report("double_effect", "replaying the same request id bumped the revision")

    # 3) sessions + leases
    sessions = post["sessions"]
    if sessions["available"]:
        if sessions["rows"] != 1:
            report(
                "state",
                f"expected exactly 1 sessions row for the test id, found {sessions['rows']}",
            )
        if sessions["row"] and (pre["sessions"].get("row") or None):
            for field in ("started_at", "message_count"):
                if sessions["row"].get(field) != pre["sessions"]["row"].get(field):
                    report("state", f"sessions.{field} changed across the restart")
    if post["leases"]["available"] and post["leases"]["open_for_session"] > 1:
        report(
            "double_effect",
            f"{post['leases']['open_for_session']} open turn leases for the test id",
        )

    # 4) process / ownership topology
    if not post["processes"]["gv_pids"]:
        report("state", "no marker-identified GV process after the restart")
    if len(post["processes"]["main_procs"]) > 1:
        report(
            "double_effect",
            f"{len(post['processes']['main_procs'])} GV main processes after the restart",
        )
    ownership = post["ownership"]
    if ownership["available"] and ownership["live"] != 1:
        report(
            "double_effect" if ownership["live"] > 1 else "state",
            f"{ownership['live']} live backend owner(s) in backend-ownership.json",
        )

    # 5) launch log: exactly one new start block (F15 cadence is reported, not failed)
    new_blocks = post["log"]["launch_blocks"] - pre["log"]["launch_blocks"]
    if new_blocks != 1:
        report("state", f"expected exactly 1 new launch block, found {new_blocks}")

    if serves is not None and serves != 1:
        report(
            "double_effect" if serves > 1 else "state",
            f"{serves} verified GV serve(s) after the restart",
        )
    return findings


def verdict(findings: list[dict]) -> int:
    if any(finding["kind"] == "double_effect" for finding in findings):
        return EXIT_DOUBLE
    if findings:
        return EXIT_STATE
    return EXIT_OK


# --- seeding / replay over the panel API (same routes the UI uses) -----------


def seed_state(serve: dict, sid: str, *, api_fn=api) -> dict:
    goal = api_fn(
        serve["port"],
        "POST",
        "/completion/goal",
        payload={
            "session_id": sid,
            "goal": "crash-recovery: state survives kill -9",
            "criteria": ["the session store keeps its revision"],
        },
        token=serve.get("token", ""),
    )
    strictness = api_fn(
        serve["port"],
        "POST",
        "/models/strictness",
        payload={"level": "strict"},
        token=serve.get("token", ""),
    )
    return {"goal": goal, "strictness": strictness}


def restore_strictness(serve: dict, level: str, *, api_fn=api) -> dict:
    """Put the pre-test strictness level back (never leaves the voice altered)."""
    if not serve.get("port"):
        return {"restored": False, "reason": "no verified serve"}
    return api_fn(
        serve["port"],
        "POST",
        "/models/strictness",
        payload={"level": level},
        token=serve.get("token", ""),
    )


def replay_request(
    serve: dict,
    sid: str,
    *,
    request_id: str,
    expected_revision: int,
    api_fn=api,
) -> dict:
    """Apply ONE criteria op twice with the same ``request_id`` (idempotency probe).

    The second call must be a no-op: revision unchanged, id in
    ``applied_requests`` exactly once.
    """
    payload = {
        "session_id": sid,
        "op": "add",
        "criteria": [REPLAY_TEXT],
        "request_id": request_id,
        "expected_revision": int(expected_revision),
    }
    first = api_fn(
        serve["port"], "POST", "/completion/criteria", payload=payload, token=serve.get("token", "")
    )
    second = api_fn(
        serve["port"], "POST", "/completion/criteria", payload=payload, token=serve.get("token", "")
    )
    return {
        "request_id": request_id,
        "first": first,
        "second": second,
        "replayed_flag": bool(second.get("replayed")),
    }


# --- receipts ----------------------------------------------------------------


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def write_receipt(receipt_dir: Path, receipt: dict) -> None:
    write_json(Path(receipt_dir) / "receipt.json", receipt)


# --- CLI ---------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    epilog = "\n".join(f"  {code}: {text}" for code, text in EXIT_CODES.items())
    parser = argparse.ArgumentParser(
        prog="gv_crash_recovery.py",
        description=(
            "God's Voice crash-recovery probe: kill -9 the marker-identified GV instance, "
            "restart it, and check the five state areas. --check kills nothing."
        ),
        epilog="Exit codes:\n" + epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--session", default="", help="test session id (default crashtest-<utc>)")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="debug port for the restarted app"
    )
    parser.add_argument(
        "--check",
        "--dry-run",
        action="store_true",
        dest="check",
        help="identify + report only; kills NOTHING and starts NOTHING",
    )
    parser.add_argument(
        "--receipts",
        default="",
        help="receipt directory (default under $HERMES_HOME/godsv/receipts)",
    )
    return parser.parse_args(argv)


def run_check(receipt_dir: Path) -> int:
    """Safe mode: identify the GV process set and report; no kill, no restart."""
    pids = gv_pids()
    binary = desktop.hermes_app_binary()
    state_dir = runtime_dir()
    report = {
        "kind": "gv-crash-recovery",
        "mode": "check",
        "ok": bool(pids),
        "session_would_be": f"crashtest-{utc_stamp()}",
        "gv_pids": pids,
        "app_binary": binary or "",
        "user_data_marker": str(desktop.skaldir_user_data()),
        "settings_path": str(state_dir / "settings.yaml"),
        "completion_dir": str(state_dir / "completion"),
        "state_db": str(hermes_home() / "state.db"),
        "launch_log": str(state_dir / "desktop-launch.log"),
        "self_pids_excluded": [os.getpid(), os.getppid()],
        "note": "check mode never kills or restarts anything",
    }
    write_receipt(receipt_dir, report)
    print(json.dumps(report, indent=2))
    return EXIT_OK if pids else EXIT_NO_INSTANCE


def run_full(args: argparse.Namespace, receipt_dir: Path) -> int:
    sid = args.session or f"crashtest-{utc_stamp()}"
    started = time.time()
    steps: dict = {}

    pids = gv_pids()
    steps["identify"] = {"exit": EXIT_OK if pids else EXIT_NO_INSTANCE, "pids": pids}
    if not pids:
        write_receipt(
            receipt_dir,
            {
                "kind": "gv-crash-recovery",
                "ok": False,
                "session_id": sid,
                "exit_code": EXIT_NO_INSTANCE,
                "message": "no marker-identified God's Voice instance found",
                "steps": steps,
            },
        )
        return EXIT_NO_INSTANCE

    serve = gv_serve()
    steps["serve_pre"] = {"exit": EXIT_OK if serve["port"] else EXIT_STATE, **serve}
    prior_level = load_strictness(runtime_dir())
    if serve["port"]:
        write_json(receipt_dir / "10-seed.json", seed_state(serve, sid))
    pre = snapshot(sid)
    write_json(receipt_dir / "20-pre-snapshot.json", pre)

    try:
        killed = crash(pids)
        restarted = restart_app(port=args.port)
        steps["crash_restart"] = {"exit": EXIT_OK, **killed, **restarted}
    except CrashError as exc:
        write_json(receipt_dir / "30-crash-restart.json", {"error": str(exc)})
        write_receipt(
            receipt_dir,
            {
                "kind": "gv-crash-recovery",
                "ok": False,
                "session_id": sid,
                "exit_code": EXIT_KILL_RESTART,
                "message": str(exc),
                "steps": steps,
            },
        )
        return EXIT_KILL_RESTART

    post = snapshot(sid)
    write_json(receipt_dir / "50-post-snapshot.json", post)

    serve_after = gv_serve()
    steps["serve_post"] = {
        "exit": EXIT_OK if serve_after["verified"] == 1 else EXIT_STATE,
        **serve_after,
    }
    replay: dict | None = None
    if serve_after["port"]:
        replay = replay_request(
            serve_after,
            sid,
            request_id=f"{sid}-replay-1",
            expected_revision=post["store"]["revision"],
        )
        replayed_store = store_snapshot(sid)
        replay["applied_count"] = sum(
            1 for item in replayed_store["applied_requests"] if item == replay["request_id"]
        )
        replay["revision_before"] = post["store"]["revision"]
        replay["revision_after"] = replayed_store["revision"]
        write_json(receipt_dir / "60-replay.json", replay)

    findings = verify(
        pre, post, replay=replay, serves=serve_after["verified"] if serve_after["port"] else None
    )
    code = verdict(findings)
    restored = (
        restore_strictness(serve_after, prior_level) if serve_after["port"] else {"restored": False}
    )
    steps["verify"] = {"exit": code, "findings": findings}

    receipt = {
        "kind": "gv-crash-recovery",
        "ok": code == EXIT_OK,
        "session_id": sid,
        "exit_code": code,
        "message": EXIT_CODES[code],
        "started_at": started,
        "finished_at": time.time(),
        "pids_killed": killed["killed"],
        "pids_after": post["processes"]["gv_pids"],
        "main_procs_after": post["processes"]["main_procs"],
        "settings_same": pre["settings"]["sha256"] == post["settings"]["sha256"],
        "store_revision_before": pre["store"]["revision"],
        "store_revision_after": post["store"]["revision"],
        "replay": replay,
        "sessions_rows": post["sessions"]["rows"],
        "leases_open_for_session": post["leases"]["open_for_session"],
        "serves": serve_after["verified"],
        "launch_blocks": post["log"]["launch_blocks"],
        "f15_retry_lines": post["log"]["f15_retry_lines"],
        "strictness_restored": restored,
        "findings": findings,
        "steps": steps,
        "artifacts": sorted(path.name for path in receipt_dir.glob("*") if path.is_file()),
    }
    write_receipt(receipt_dir, receipt)
    print(
        json.dumps(
            {key: receipt[key] for key in ("ok", "exit_code", "session_id", "message")}, indent=2
        )
    )
    if findings:
        for finding in findings:
            print(f"  {finding['kind']}: {finding['detail']}")
    return code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    receipt_dir = Path(args.receipts) if args.receipts else receipts_root() / utc_stamp()
    receipt_dir.mkdir(parents=True, exist_ok=True)
    if args.check:
        return run_check(receipt_dir)
    return run_full(args, receipt_dir)


if __name__ == "__main__":
    raise SystemExit(main())
