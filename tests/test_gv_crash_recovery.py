"""gv_crash_recovery.py — safety, snapshots and verdict logic, WITHOUT real processes.

Everything runs against fakes: pgrep results, marker probes, kill/alive callables,
subprocess.Popen, the panel API and SQLite fixtures. Nothing here kills, starts or
touches the running God's Voice app; ``--check`` is exercised with mocked lookups.
"""

import json
import signal
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import gv_crash_recovery as rec  # noqa: E402

SID = "crashtest-1"


# --- process identification (the safety net) ----------------------------------


def test_pattern_cannot_match_this_script_or_its_shell():
    # the bracket trick: the literal "gods-voice" must never appear in the pattern
    assert "gods-voice" not in rec.GV_PATTERN
    assert "[-]" in rec.GV_PATTERN


def test_gv_pids_keeps_only_marker_carrying_non_self_pids():
    seen_patterns = []

    def fake_pgrep(pattern):
        seen_patterns.append(pattern)
        return [111, 222, 333, 444, 555]

    def fake_has_marker(pid, marker):
        assert marker == "/home/x/.hermes/godsv/desktop-userdata"
        return pid in (111, 333, 444)  # 222: foreign app, 444: fake marker, 555: no marker

    def fake_cmdline(pid):
        if pid == 444:
            return "/home/x/.hermes/hermes-agent/apps/desktop/release/linux-unpacked/Hermes"
        return "/home/x/.hermes/godsv/app/linux-unpacked/gods-voice --class=GodsVoice"

    pids = rec.gv_pids(
        pgrep=fake_pgrep,
        has_marker=fake_has_marker,
        cmdline=fake_cmdline,
        marker="/home/x/.hermes/godsv/desktop-userdata",
        self_pids=(111,),  # simulate "this script's own PID was a candidate"
    )

    assert pids == [333]
    assert seen_patterns == [rec.GV_PATTERN]


def test_gv_pids_empty_when_nothing_carries_the_marker():
    assert (
        rec.gv_pids(
            pgrep=lambda pattern: [9001],
            has_marker=lambda pid, marker: False,
            cmdline=lambda pid: "gods-voice",
        )
        == []
    )


def test_pgrep_wrapper_swallows_tool_errors(monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("pgrep missing")

    monkeypatch.setattr(rec.subprocess, "run", broken)
    assert rec._pgrep_pids(rec.GV_PATTERN) == []


# --- R1 regression: the live instance MUST be found (mocked process list) -----


def _live_env(*, user_data: str) -> str:
    """A /proc/<pid>/environ blob in the shape the live instance really has."""
    return (
        f"HERMES_DESKTOP_USER_DATA_DIR={user_data}\0"
        "GODSV_MODE=whisper\0"
        "PATH=/usr/bin\0"
    )


def test_marker_accepts_the_root_spelling_the_live_instance_uses():
    """R1: the live env names ``…/.hermes/godsv/`` (trailing slash), no child dir.

    A raw substring comparison against ``…/godsv/desktop-userdata`` missed it and
    made ``--check`` report no instance while God's Voice was running.
    """
    wanted = "/home/willy/.hermes/godsv/desktop-userdata"
    live = _live_env(user_data="/home/willy/.hermes/godsv/")
    assert rec.desktop._pid_has_marker(4242, wanted, read_environ=lambda pid: live) is True
    assert rec._pid_env_marker(4242, wanted, read_env=lambda pid: live) is True


def test_marker_accepts_both_real_spellings_and_refuses_foreign_ones():
    wanted = "/home/willy/.hermes/godsv/desktop-userdata"
    ok_child = _live_env(user_data="/home/willy/.hermes/godsv/desktop-userdata")
    ok_root = _live_env(user_data="/home/willy/.hermes/godsv/")
    foreign = _live_env(user_data="/home/willy/.hermes/other")
    stock = _live_env(user_data="/home/willy/.hermes/desktop-userdata")

    for blob in (ok_child, ok_root):
        assert rec.desktop._pid_has_marker(1, wanted, read_environ=lambda pid, b=blob: b) is True
    for blob in (foreign, stock, ""):
        assert rec.desktop._pid_has_marker(1, wanted, read_environ=lambda pid, b=blob: b) is False


def test_gv_pids_finds_the_live_instance_through_the_real_marker_logic():
    """The exact R1 scenario over a mocked process list — no process is started,
    none is killed: only the process listing and /proc reads are faked, while the
    marker matching itself is the real implementation."""
    wanted = "/home/willy/.hermes/godsv/desktop-userdata"
    envs = {
        330649: _live_env(user_data="/home/willy/.hermes/godsv/"),  # main window
        330653: _live_env(user_data="/home/willy/.hermes/godsv/"),  # zygote
        424242: _live_env(user_data="/home/willy/.hermes/other"),  # foreign
        555555: _live_env(user_data="/home/willy/.hermes/desktop-userdata"),  # stock app
    }
    cmdlines = {
        330649: "/home/willy/.hermes/godsv/app/linux-unpacked/gods-voice --class=GodsVoice",
        330653: "/home/willy/.hermes/godsv/app/linux-unpacked/gods-voice --type=zygote",
        424242: "/opt/other/app --flag",
        555555: ("/home/willy/.hermes/hermes-agent/apps/desktop/release/"
                 "linux-unpacked/Hermes"),
    }

    pids = rec.gv_pids(
        pgrep=lambda pattern: [330649, 330653, 424242, 555555, 999999],
        has_marker=lambda pid, marker: rec.desktop._pid_has_marker(
            pid, marker, read_environ=lambda p: envs.get(p, "")
        ),
        env_marker=lambda pid, marker: rec._pid_env_marker(
            pid, marker, read_env=lambda p: envs.get(p, "")
        ),
        cmdline=lambda pid: cmdlines.get(pid, ""),
        marker=wanted,
        self_pids=(999999,),
    )

    assert pids == [330649, 330653]


def test_run_check_reports_the_live_instance(tmp_path, monkeypatch):
    """R1's acceptance criterion: ``--check`` MUST report the running instance."""
    monkeypatch.setattr(rec, "desktop", _fake_desktop(tmp_path))
    monkeypatch.setattr(rec, "gv_pids", lambda **kw: [330649, 330653])

    code = rec.run_check(tmp_path / "receipts")

    receipt = json.loads((tmp_path / "receipts" / "receipt.json").read_text(encoding="utf-8"))
    assert code == rec.EXIT_OK
    assert receipt["ok"] is True
    assert receipt["gv_pids"] == [330649, 330653]


def test_marker_probe_survives_an_unreadable_environ():
    """A foreign PID (empty/denied /proc read) is never mistaken for ours."""
    wanted = "/home/willy/.hermes/godsv/desktop-userdata"
    assert rec._pid_env_marker(1, wanted, read_env=lambda pid: "") is False
    assert rec.desktop._pid_has_marker(1, wanted, read_environ=lambda pid: "") is False


def test_marker_fallback_keeps_exact_markers_working():
    """Callers passing an exact-path marker keep the raw-containment behaviour."""
    wanted = "/home/x/.hermes/godsv/desktop-userdata"
    blob = f"HERMES_DESKTOP_USER_DATA_DIR={wanted}\0"
    assert rec.desktop._pid_has_marker(1, wanted, read_environ=lambda pid: blob) is True
    assert rec.desktop._pid_has_marker(1, "/nope", read_environ=lambda pid: blob) is False


def test_is_pid_alive_rejects_non_positive_pids():
    assert rec.is_pid_alive(0) is False
    assert rec.is_pid_alive(-5) is False


def test_is_pid_alive_distinguishes_esrch_from_eperm(monkeypatch):
    def gone(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(rec.os, "kill", gone)
    assert rec.is_pid_alive(4242) is False

    def foreign(_pid, _sig):
        raise PermissionError

    monkeypatch.setattr(rec.os, "kill", foreign)
    assert rec.is_pid_alive(4242) is True


# --- kill path -----------------------------------------------------------------


def test_crash_refuses_an_empty_pid_set():
    with pytest.raises(rec.CrashError, match="empty PID set"):
        rec.crash([])


def test_crash_kills_each_pid_with_sigkill_and_verifies_death():
    killed = []

    def fake_kill(pid, sig):
        killed.append((pid, sig))

    result = rec.crash([11, 22], kill=fake_kill, alive=lambda pid: False, sleep=lambda _s: None)

    assert killed == [(11, signal.SIGKILL), (22, signal.SIGKILL)]
    assert result["killed"] == [11, 22]


def test_crash_waits_bounded_and_raises_when_a_pid_survives():
    started = time.time()
    with pytest.raises(rec.CrashError, match="survived kill -9"):
        rec.crash(
            [7],
            kill=lambda pid, sig: None,
            alive=lambda pid: True,
            timeout=0.05,
            interval=0.005,
            sleep=lambda _s: None,
        )
    assert time.time() - started < 5  # bounded, never an infinite loop


# --- restart path --------------------------------------------------------------


def test_restart_app_refuses_without_a_packaged_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(rec.desktop, "hermes_app_binary", lambda: None)
    with pytest.raises(rec.CrashError, match="no packaged God's Voice binary"):
        rec.restart_app(binary="", log_path=tmp_path / "launch.log")


def test_restart_app_spawns_production_env_and_counts_one_launch_block(tmp_path):
    spawned = []
    wait_calls = []

    def fake_popen(argv, **kwargs):
        spawned.append((argv, kwargs))

    def fake_wait(port, timeout=90.0):
        wait_calls.append((port, timeout))
        return True

    log_path = tmp_path / "desktop-launch.log"
    result = rec.restart_app(
        port=9333,
        binary="/tmp/linux-unpacked/gods-voice",
        popen=fake_popen,
        env={"GODSV_MODE": "whisper"},
        log_path=log_path,
        wait=fake_wait,
    )

    argv, kwargs = spawned[0]
    assert argv == [
        "/tmp/linux-unpacked/gods-voice",
        "--class=SkaldirAgent",
        "--remote-debugging-port=9333",
        "hermes://open/gods-voice",
    ]
    assert kwargs["start_new_session"] is True
    assert kwargs["env"] == {"GODSV_MODE": "whisper"}
    assert wait_calls == [(9333, 90.0)]
    assert result["binary"] == "/tmp/linux-unpacked/gods-voice"
    assert rec.count_launch_blocks(log_path.read_text(encoding="utf-8")) == 1


def test_restart_app_cdp_timeout_is_bounded(tmp_path):
    with pytest.raises(rec.CrashError, match="did not answer"):
        rec.restart_app(
            binary="/tmp/gods-voice",
            popen=lambda argv, **kwargs: None,
            log_path=tmp_path / "launch.log",
            wait=lambda port, timeout: False,
        )


# --- serve discovery -----------------------------------------------------------


def test_gv_candidates_skips_the_debug_port_and_foreign_markers():
    ss_out = (
        'LISTEN 0 128 127.0.0.1:44039 0.0.0.0:* users:(("python",pid=111,fd=7))\n'
        'LISTEN 0 128 127.0.0.1:9333 0.0.0.0:* users:(("gods-voice",pid=222,fd=9))\n'
        'LISTEN 0 128 127.0.0.1:50000 0.0.0.0:* users:(("python",pid=333,fd=3))\n'
    )
    env = {
        111: "HERMES_DESKTOP_USER_DATA_DIR=/home/x/.hermes/godsv/desktop-userdata\0"
        "HERMES_DASHBOARD_SESSION_TOKEN=tok-api\0",
        222: "HERMES_DESKTOP_USER_DATA_DIR=/home/x/.hermes/godsv/desktop-userdata\0"
        "HERMES_DASHBOARD_SESSION_TOKEN=tok-debug\0",
        333: "HERMES_DESKTOP_USER_DATA_DIR=/home/x/.hermes/other\0",
    }
    cmdline = {
        111: "python -m hermes_cli.main serve --port 0",
        222: "gods-voice --remote-debugging-port=9333",
        333: "python -m hermes_cli.main serve",
    }

    found = rec.gv_candidates(
        run=lambda cmd, **kw: SimpleNamespace(stdout=ss_out),
        read_env=lambda pid: env.get(pid, ""),
        read_cmdline=lambda pid: cmdline.get(pid, ""),
        marker="/home/x/.hermes/godsv/desktop-userdata",
    )

    assert found == [(44039, "tok-api")]


def test_gv_serve_returns_the_verified_candidate_only():
    def api_fn(port, method, path, payload=None, token=""):
        if port == 9333:
            return {"http_error": 404, "detail": "not the API"}
        return {"state": "WORKING", "revision": 1}

    serve = rec.gv_serve(
        api_fn=api_fn, candidates_fn=lambda: [(9333, "tok-debug"), (44039, "tok-api")]
    )

    assert serve == {"port": 44039, "token": "tok-api", "candidates": 2, "verified": 1}


def test_gv_serve_reports_zero_when_nothing_answers():
    serve = rec.gv_serve(api_fn=lambda *a, **k: {"http_error": 500}, candidates_fn=lambda: [])

    assert serve["port"] == 0
    assert serve["verified"] == 0


# --- snapshots -----------------------------------------------------------------


def test_settings_snapshot_tracks_strictness_and_bytes(tmp_path):
    (tmp_path / "settings.yaml").write_text("strictness: strict\n", encoding="utf-8")
    first = rec.settings_snapshot(state_dir=tmp_path)
    assert first["exists"] is True
    assert first["strictness"] == "strict"

    (tmp_path / "settings.yaml").write_text("strictness: relaxed\n", encoding="utf-8")
    second = rec.settings_snapshot(state_dir=tmp_path)
    assert second["strictness"] == "relaxed"
    assert second["sha256"] != first["sha256"]


def test_store_snapshot_reads_revision_and_flags_duplicate_history_lines(tmp_path):
    base = tmp_path / "completion"
    base.mkdir(parents=True)
    (tmp_path / "completion" / f"{SID}.json").write_text(
        json.dumps({"state": "WORKING", "revision": 7, "applied_requests": ["req-1"]}),
        encoding="utf-8",
    )
    (tmp_path / "completion" / f"{SID}.jsonl").write_text("a\nb\na\n\n", encoding="utf-8")

    store = rec.store_snapshot(SID, state_dir=tmp_path)

    assert store["exists"] is True
    assert store["revision"] == 7
    assert store["applied_requests"] == ["req-1"]
    assert store["jsonl_lines"] == 3
    assert store["jsonl_duplicates"] == 1


def test_store_snapshot_survives_a_missing_store(tmp_path):
    store = rec.store_snapshot(SID, state_dir=tmp_path)
    assert store["exists"] is False
    assert store["revision"] == 0
    assert store["applied_requests"] == []


def _session_db(path: Path, *, with_leases: bool = True) -> Path:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, session_key TEXT, started_at REAL,"
            " message_count INTEGER)"
        )
        con.execute("INSERT INTO sessions VALUES ('db-id', ?, 100.0, 5)", (SID,))
        if with_leases:
            con.execute(
                "CREATE TABLE session_turn_leases (conversation_id TEXT PRIMARY KEY, holder TEXT,"
                " acquired_at REAL, expires_at REAL)"
            )
            con.execute(
                "INSERT INTO session_turn_leases VALUES (?, 'runner', 0.0, 9999999999.0)", (SID,)
            )
        con.commit()
    finally:
        con.close()
    return path


def test_session_snapshot_reads_the_row_and_open_leases(tmp_path):
    db = _session_db(tmp_path / "state.db")

    snap = rec.session_snapshot(SID, db_path=db, now=1000.0)

    assert snap["sessions"]["rows"] == 1
    assert snap["sessions"]["row"]["message_count"] == 5
    assert snap["leases"]["open_for_session"] == 1


def test_session_snapshot_counts_expired_leases_as_closed(tmp_path):
    db = _session_db(tmp_path / "state.db")

    snap = rec.session_snapshot(SID, db_path=db, now=10**12)

    assert snap["leases"]["open_for_session"] == 0
    assert snap["leases"]["all_open"] == 0


def test_session_snapshot_survives_a_db_without_lease_table(tmp_path):
    db = _session_db(tmp_path / "state.db", with_leases=False)

    snap = rec.session_snapshot(SID, db_path=db)

    assert snap["sessions"]["rows"] == 1
    assert snap["leases"]["available"] is False


def test_ownership_snapshot_counts_only_live_owners(tmp_path):
    path = tmp_path / "backend-ownership.json"
    path.write_text(
        json.dumps({"backends": [{"pid": 5, "nonce": "a"}, {"pid": 6, "nonce": "b"}]}),
        encoding="utf-8",
    )

    snap = rec.ownership_snapshot(path=path, alive=lambda pid: pid == 5)

    assert snap == {"available": True, "entries": 2, "live": 1, "path": str(path)}


def test_process_snapshot_counts_main_processes_only():
    def cmdline(pid):
        return "gods-voice --type=renderer" if pid == 2 else "gods-voice"

    snap = rec.process_snapshot(list_pids=lambda: [1, 2], cmdline=cmdline)

    assert snap["gv_pids"] == [1, 2]
    assert snap["main_procs"] == [1]


def test_log_counters(tmp_path):
    log = tmp_path / "desktop-launch.log"
    log.write_text(
        "--- launch 1\nnoise\n--- launch 2\nSession not found\nSession not found\n",
        encoding="utf-8",
    )

    snap = rec.log_snapshot(launch_log=log)

    assert snap["launch_blocks"] == 2
    assert snap["f15_retry_lines"] == 2


# --- the verdict ---------------------------------------------------------------


def _snap(
    *,
    revision=3,
    settings_sha="a" * 64,
    strictness="strict",
    rows=1,
    started_at=100.0,
    message_count=5,
    open_leases=0,
    gv_pids=(10, 11),
    main_procs=(10,),
    ownership_live=1,
    launch_blocks=5,
    applied=(),
    jsonl_duplicates=0,
):
    return {
        "ts": 0.0,
        "session_id": SID,
        "settings": {"path": "s", "exists": True, "sha256": settings_sha, "strictness": strictness},
        "store": {
            "session_id": SID,
            "path": "p",
            "history_path": "h",
            "exists": True,
            "state": "WORKING",
            "revision": revision,
            "applied_requests": list(applied),
            "jsonl_lines": 10,
            "jsonl_duplicates": jsonl_duplicates,
        },
        "sessions": {
            "available": True,
            "rows": rows,
            "row": {
                "id": "db-id",
                "session_key": SID,
                "started_at": started_at,
                "message_count": message_count,
            },
            "error": "",
        },
        "leases": {"available": True, "open_for_session": open_leases, "all_open": 0, "error": ""},
        "processes": {"gv_pids": list(gv_pids), "main_procs": list(main_procs), "cmdlines": {}},
        "ownership": {"available": True, "entries": 1, "live": ownership_live, "path": "o"},
        "log": {"path": "l", "launch_blocks": launch_blocks, "f15_retry_lines": 0, "sha256": "x"},
    }


def _post(**kw):
    """A post-restart snapshot: identical to _snap() except ONE new launch block."""
    kw.setdefault("launch_blocks", 6)
    return _snap(**kw)


def test_verify_clean_pair_yields_no_findings():
    findings = rec.verify(_snap(), _post(), replay=None, serves=1)

    assert findings == []
    assert rec.verdict(findings) == rec.EXIT_OK


def test_verify_settings_change_is_a_state_violation():
    findings = rec.verify(_snap(), _post(settings_sha="b" * 64), serves=1)

    assert [f["kind"] for f in findings] == ["state"]
    assert rec.verdict(findings) == rec.EXIT_STATE


def test_verify_revision_jump_is_a_double_effect():
    findings = rec.verify(_snap(), _post(revision=4), serves=1)

    assert [f["kind"] for f in findings] == ["double_effect"]
    assert rec.verdict(findings) == rec.EXIT_DOUBLE


def test_verify_detects_a_second_session_row_and_reset_counters():
    findings = rec.verify(_snap(), _post(rows=2), serves=1)
    assert rec.verdict(findings) == rec.EXIT_STATE

    findings = rec.verify(_snap(), _post(message_count=9), serves=1)
    assert rec.verdict(findings) == rec.EXIT_STATE


def test_verify_detects_a_double_lease_and_two_serves():
    assert rec.verdict(rec.verify(_snap(), _post(open_leases=2), serves=1)) == rec.EXIT_DOUBLE
    assert rec.verdict(rec.verify(_snap(), _post(), serves=2)) == rec.EXIT_DOUBLE
    assert rec.verdict(rec.verify(_snap(), _post(), serves=0)) == rec.EXIT_STATE


def test_verify_detects_a_second_launch_block_and_a_dead_process_set():
    assert rec.verdict(rec.verify(_snap(), _post(launch_blocks=8), serves=1)) == rec.EXIT_STATE
    assert rec.verdict(rec.verify(_snap(), _post(gv_pids=()), serves=1)) == rec.EXIT_STATE


def test_verify_detects_two_gv_main_processes_as_double_effect():
    assert rec.verdict(rec.verify(_snap(), _post(main_procs=(10, 11)), serves=1)) == rec.EXIT_DOUBLE


def test_verify_replay_must_apply_exactly_once_and_keep_the_revision():
    replay_ok = {"request_id": "r-1", "applied_count": 1, "revision_before": 3, "revision_after": 3}
    assert rec.verify(_snap(), _post(), replay=replay_ok, serves=1) == []

    replay_twice = dict(replay_ok, applied_count=2)
    assert (
        rec.verdict(rec.verify(_snap(), _post(), replay=replay_twice, serves=1)) == rec.EXIT_DOUBLE
    )

    replay_bumped = dict(replay_ok, revision_after=4)
    assert (
        rec.verdict(rec.verify(_snap(), _post(), replay=replay_bumped, serves=1)) == rec.EXIT_DOUBLE
    )


# --- API seeding / replay (fakes only — nothing is sent anywhere) --------------


def test_seed_state_uses_the_panel_routes_the_ui_uses():
    calls = []

    def api_fn(port, method, path, payload=None, token=""):
        calls.append((port, method, path, payload, token))
        return {"ok": True}

    rec.seed_state({"port": 44039, "token": "tok"}, SID, api_fn=api_fn)

    assert [call[2] for call in calls] == ["/completion/goal", "/models/strictness"]
    assert all(call[0] == 44039 and call[4] == "tok" for call in calls)
    assert calls[1][3] == {"level": "strict"}


def test_replay_request_posts_the_same_request_id_twice():
    calls = []

    def api_fn(port, method, path, payload=None, token=""):
        calls.append(payload)
        return {"replayed": len(calls) == 2}

    result = rec.replay_request(
        {"port": 44039, "token": "tok"},
        SID,
        request_id="crashtest-1-replay-1",
        expected_revision=3,
        api_fn=api_fn,
    )

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0]["op"] == "add"
    assert calls[0]["request_id"] == "crashtest-1-replay-1"
    assert calls[0]["expected_revision"] == 3
    assert result["replayed_flag"] is True


# --- --check mode and the full orchestration (fully mocked) --------------------


def _fake_desktop(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        hermes_app_binary=lambda: "/tmp/linux-unpacked/gods-voice",
        skaldir_user_data=lambda: tmp_path / "godsv" / "desktop-userdata",
        hermes_home=lambda: tmp_path,
    )


def test_run_check_reports_and_kills_nothing(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("check mode must never kill or restart")

    monkeypatch.setattr(rec, "desktop", _fake_desktop(tmp_path))
    monkeypatch.setattr(rec, "gv_pids", lambda **kw: [4321])
    monkeypatch.setattr(rec, "crash", boom)
    monkeypatch.setattr(rec, "restart_app", boom)

    code = rec.run_check(tmp_path / "receipts")

    assert code == rec.EXIT_OK
    receipt = json.loads((tmp_path / "receipts" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["mode"] == "check"
    assert receipt["gv_pids"] == [4321]
    assert receipt["ok"] is True


def test_run_check_without_an_instance_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "desktop", _fake_desktop(tmp_path))
    monkeypatch.setattr(rec, "gv_pids", lambda **kw: [])

    assert rec.run_check(tmp_path / "receipts") == rec.EXIT_NO_INSTANCE


def test_main_check_mode_is_safe_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "desktop", _fake_desktop(tmp_path))
    monkeypatch.setattr(rec, "gv_pids", lambda **kw: [555])
    monkeypatch.setattr(rec, "crash", _boom)
    monkeypatch.setattr(rec, "restart_app", _boom)
    monkeypatch.setattr(rec, "run_full", _boom)

    code = rec.main(["--check", "--receipts", str(tmp_path / "r")])

    assert code == rec.EXIT_OK
    assert (tmp_path / "r" / "receipt.json").exists()


def _boom(*args, **kwargs):
    raise AssertionError("this path must not run in the given mode")


def test_main_dry_run_alias_behaves_like_check(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "desktop", _fake_desktop(tmp_path))
    monkeypatch.setattr(rec, "gv_pids", lambda **kw: [555])
    monkeypatch.setattr(rec, "run_full", _boom)

    assert rec.main(["--dry-run", "--receipts", str(tmp_path / "r")]) == rec.EXIT_OK


def _full_run_monkeypatch(monkeypatch, tmp_path, *, post_revision=3, crash_error=None):
    pre = _snap()
    post = _post(revision=post_revision, applied=(f"{SID}-replay-1",))
    snapshots = iter([pre, post])
    monkeypatch.setattr(rec, "gv_pids", lambda **kw: [4321])
    monkeypatch.setattr(
        rec,
        "gv_serve",
        lambda **kw: {"port": 44039, "token": "tok", "candidates": 1, "verified": 1},
    )
    monkeypatch.setattr(rec, "load_strictness", lambda _dir: "standard")
    monkeypatch.setattr(rec, "seed_state", lambda *a, **kw: {"goal": {"state": "WORKING"}})
    monkeypatch.setattr(rec, "snapshot", lambda *a, **kw: next(snapshots))
    monkeypatch.setattr(
        rec,
        "store_snapshot",
        lambda *a, **kw: {"revision": post_revision, "applied_requests": [f"{SID}-replay-1"]},
    )
    if crash_error is not None:
        monkeypatch.setattr(rec, "crash", lambda pids, **kw: (_ for _ in ()).throw(crash_error))
    else:
        monkeypatch.setattr(rec, "crash", lambda pids, **kw: {"killed": list(pids), "seconds": 0.1})
    monkeypatch.setattr(
        rec,
        "restart_app",
        lambda **kw: {"binary": "/tmp/linux-unpacked/gods-voice", "port": 9333, "launch_log": "x"},
    )
    monkeypatch.setattr(
        rec,
        "replay_request",
        lambda serve, sid, **kw: {"request_id": kw["request_id"], "first": {}, "second": {}},
    )
    monkeypatch.setattr(rec, "restore_strictness", lambda serve, level, **kw: {"restored": True})
    monkeypatch.setattr(rec, "runtime_dir", lambda: tmp_path / "godsv")
    return tmp_path / "receipts"


def test_main_full_run_orchestrates_and_writes_a_receipt(tmp_path, monkeypatch):
    receipts = _full_run_monkeypatch(monkeypatch, tmp_path)

    code = rec.main(["--session", SID, "--receipts", str(receipts)])

    assert code == rec.EXIT_OK
    receipt = json.loads((receipts / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is True
    assert receipt["pids_killed"] == [4321]
    assert receipt["pids_after"] == [10, 11]
    assert receipt["settings_same"] is True
    assert receipt["store_revision_before"] == 3
    assert receipt["replay"]["applied_count"] == 1
    assert receipt["serves"] == 1
    assert "20-pre-snapshot.json" in receipt["artifacts"]
    assert receipt["strictness_restored"] == {"restored": True}


def test_main_full_run_reports_a_revision_jump_as_double_effect(tmp_path, monkeypatch):
    receipts = _full_run_monkeypatch(monkeypatch, tmp_path, post_revision=4)

    code = rec.main(["--session", SID, "--receipts", str(receipts)])

    assert code == rec.EXIT_DOUBLE
    receipt = json.loads((receipts / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert any(f["kind"] == "double_effect" for f in receipt["findings"])


def test_main_full_run_maps_a_kill_restart_failure_to_exit_3(tmp_path, monkeypatch):
    receipts = _full_run_monkeypatch(
        monkeypatch, tmp_path, crash_error=rec.CrashError("CDP port 9333 did not answer")
    )

    code = rec.main(["--session", SID, "--receipts", str(receipts)])

    assert code == rec.EXIT_KILL_RESTART
    receipt = json.loads((receipts / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["exit_code"] == rec.EXIT_KILL_RESTART
    assert "did not answer" in receipt["message"]


def test_main_full_run_without_an_instance_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "gv_pids", lambda **kw: [])

    code = rec.main(["--receipts", str(tmp_path / "receipts")])

    assert code == rec.EXIT_NO_INSTANCE
    receipt = json.loads((tmp_path / "receipts" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["exit_code"] == rec.EXIT_NO_INSTANCE
