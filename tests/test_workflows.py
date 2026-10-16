"""§9-Workflows: Verifikationsregeln, präzise Fehler, keine Ersatz-Erfolge."""

import json

from skaldir import workflows as wf


def _fake_window(xid="0x1a", title="Blender 4.2", wm_class="blender.Blender"):
    return {"xid": xid, "title": title, "wm_class": wm_class}


def test_missing_app_without_start_cmd_is_declared_environment_error(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [])
    out = wf.open_app_snapshot("blender", runtime=tmp_path)
    assert out["status"] == "FAILED"
    assert out["failure_class"] == "environment"
    assert out["failed_stage"] == "detect"
    assert "no start_cmd" in out["summary"]
    assert (tmp_path / "workflow_runs" / f"{out['run_id']}.json").exists()


def test_not_installed_app_is_never_auto_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [])
    monkeypatch.setattr(wf.shutil, "which", lambda name: None)
    out = wf.open_app_snapshot("blender", start_cmd="blender --factory-startup", runtime=tmp_path)
    assert out["status"] == "FAILED"
    assert out["failure_class"] == "environment"
    assert "declared precondition" in out["summary"]
    assert "install blender (by the user)" in out["recommended_next_action"]


def test_process_without_window_times_out_with_window_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(wf, "find_window", lambda **k: None)
    monkeypatch.setattr(wf, "x11_windows", lambda: [{"xid": "0x2", "title": "x", "wm_class": "y"}])
    out = wf.open_app_snapshot("blender", ready_timeout=0.05, runtime=tmp_path)
    assert out["status"] == "FAILED"
    assert out["failure_class"] == "timeout"
    assert out["failed_stage"] == "ready"
    assert out["evidence_refs"], "window inventory must be evidence, not a bare failure"


def test_success_requires_window_and_screenshot(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(wf, "find_window", lambda **k: _fake_window())
    monkeypatch.setattr(wf, "focus_window", lambda xid: True)

    def fake_shot(path, xid=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return True, "import"

    monkeypatch.setattr(wf, "screenshot", fake_shot)
    out = wf.open_app_snapshot("blender", runtime=tmp_path)
    assert out["status"] == "SUCCEEDED"
    assert out["result"]["window"]["xid"] == "0x1a"
    assert out["artifact_refs"] and out["artifact_refs"][0].endswith(".png")


def test_screenshot_without_focus_is_partial_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(wf, "find_window", lambda **k: _fake_window())
    monkeypatch.setattr(wf, "focus_window", lambda xid: False)

    def fake_shot(path, xid=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return True, "import"

    monkeypatch.setattr(wf, "screenshot", fake_shot)
    out = wf.open_app_snapshot("blender", runtime=tmp_path)
    assert out["status"] == "PARTIAL"
    assert any("focus" in w for w in out["warnings"])


def test_missing_screenshot_backend_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(wf, "find_window", lambda **k: _fake_window())
    monkeypatch.setattr(wf, "focus_window", lambda xid: True)
    monkeypatch.setattr(wf, "screenshot", lambda path, xid="": (False, "no screenshot backend"))
    out = wf.open_app_snapshot("blender", runtime=tmp_path)
    assert out["status"] == "FAILED"
    assert out["failure_class"] == "environment"
    assert out["failed_stage"] == "screenshot"


def test_settings_unverified_state_is_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(
        wf, "find_window", lambda **k: _fake_window(title="Blender 4.2 (no settings)")
    )
    monkeypatch.setattr(wf, "focus_window", lambda xid: True)

    def fake_shot(path, xid=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return True, "import"

    monkeypatch.setattr(wf, "screenshot", fake_shot)
    monkeypatch.setattr(wf.shutil, "which", lambda name: "/usr/bin/xdotool")
    out = wf.open_app_settings_snapshot("blender", runtime=tmp_path)
    assert out["status"] == "PARTIAL"
    assert out["result"]["state_changed"] is False
    assert any("not verified as a separate state" in w for w in out["warnings"])


def test_settings_state_change_is_success(tmp_path, monkeypatch):
    state = {"first": True}

    def fake_find(**k):
        if "settings" in (k.get("title_contains") or "").lower():
            return _fake_window(xid="0x9", title="Blender Preferences")
        if state["first"]:
            state["first"] = False
            return _fake_window(xid="0x1a", title="Blender 4.2")
        return _fake_window(xid="0x9", title="Blender Preferences")

    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(wf, "find_window", fake_find)
    monkeypatch.setattr(wf, "focus_window", lambda xid: True)
    monkeypatch.setattr(wf, "wait_for", lambda pred, timeout, interval=0.4: pred())

    def fake_shot(path, xid=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return True, "import"

    monkeypatch.setattr(wf, "screenshot", fake_shot)
    monkeypatch.setattr(wf.shutil, "which", lambda name: "/usr/bin/xdotool")
    out = wf.open_app_settings_snapshot("blender", runtime=tmp_path)
    assert out["status"] == "SUCCEEDED"
    assert out["result"]["state_changed"] is True


def test_x11_window_inventory_parses_xprop(monkeypatch):
    def fake_run(cmd, timeout=10.0):
        if cmd[:3] == ["xprop", "-root", "_NET_CLIENT_LIST"]:
            return 0, "_NET_CLIENT_LIST(WINDOW): window id # 0x1a, 0x2b", ""
        if cmd[:2] == ["xprop", "-id"]:
            wid = cmd[2]
            return 0, (
                f'_NET_WM_NAME(UTF8_STRING) = "App {wid}"\n'
                f'WM_CLASS(STRING) = "gods-voice", "Gods-voice"\n'
            ), ""
        return 1, "", "unexpected"

    monkeypatch.setattr(wf, "_run", fake_run)
    monkeypatch.setattr(wf.shutil, "which", lambda name: "/usr/bin/xprop")
    wins = wf.x11_windows()
    assert [w["xid"] for w in wins] == ["0x1a", "0x2b"]
    assert "gods-voice" in wins[0]["wm_class"]


def test_contracts_are_declared():
    names = {c["name"] for c in wf.workflow_contracts()}
    assert names == {
        "open_app_snapshot",
        "open_app_settings_snapshot",
        "verify_setting_persistence",
        "run_model_test",
    }
    for contract in wf.workflow_contracts():
        assert contract["scope"] in ("global", "project")
        assert contract["success"] and contract["error_policy"]


def test_run_record_is_json_serializable(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [])
    out = wf.open_app_snapshot("blender", runtime=tmp_path)
    record = json.loads((tmp_path / "workflow_runs" / f"{out['run_id']}.json").read_text())
    assert record["run_id"] == out["run_id"]


def test_process_running_excludes_own_ancestry(monkeypatch):
    import os as _os

    monkeypatch.setattr(wf, "_run", lambda cmd, timeout=10.0: (0, f"{_os.getpid()}\n99999\n", ""))
    assert wf.process_running("blender") == [99999]


def test_cdp_backend_window_and_screenshot(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(
        wf, "cdp_targets",
        lambda port, timeout=5.0: [
            {"type": "page", "title": "God's Voice", "url": "file://index.html"}
        ],
    )

    def fake_cdp(port, path, timeout=20.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return True, "cdp"

    monkeypatch.setattr(wf, "cdp_screenshot", fake_cdp)
    out = wf.open_app_snapshot("gods-voice", cdp_port=9333, runtime=tmp_path)
    assert out["status"] == "SUCCEEDED"
    assert out["result"]["window"]["backend"] == "cdp"
    assert out["result"]["focused"] is True
    assert out["artifact_refs"][0].endswith(".png")


# ---------------- §9-D: verify_setting_persistence ----------------

def _d_rig(tmp_path, monkeypatch, *, persist_ok=True, restore_raises=False):
    state = {"value": "standard"}
    calls = {"n": 0}

    def reader(key):
        return state["value"]

    def writer(key, value):
        calls["n"] += 1
        if restore_raises and calls["n"] >= 2 and value == "standard":
            raise OSError("disk full")
        state["value"] = value

    def fake_restart(stop, start, cdp_port=0, ready_timeout=30.0):
        if not persist_ok:
            state["value"] = "standard"  # app "forgot" the setting
        return True, "restarted"

    monkeypatch.setattr(wf, "_restart_app", fake_restart)
    return state, reader, writer


def test_setting_persistence_success_and_restore(tmp_path, monkeypatch):
    state, reader, writer = _d_rig(tmp_path, monkeypatch)
    out = wf.verify_setting_persistence(
        "strictness", "strict", settings_file=str(tmp_path / "settings.yaml"),
        reader=reader, writer=writer, runtime=tmp_path,
    )
    assert out["status"] == "SUCCEEDED"
    assert out["result"]["persisted"] is True
    assert out["side_effect_state"] == "restored"
    assert state["value"] == "standard", "original value must be restored"


def test_setting_not_persisted_fails_verify_stage(tmp_path, monkeypatch):
    _state, reader, writer = _d_rig(tmp_path, monkeypatch, persist_ok=False)
    out = wf.verify_setting_persistence(
        "strictness", "strict", settings_file=str(tmp_path / "settings.yaml"),
        reader=reader, writer=writer, runtime=tmp_path,
    )
    assert out["status"] == "FAILED"
    assert out["failed_stage"] == "verify"
    assert out["side_effect_state"] == "restored"


def test_failed_restore_stays_visible(tmp_path, monkeypatch):
    state, reader, writer = _d_rig(tmp_path, monkeypatch, restore_raises=True)
    out = wf.verify_setting_persistence(
        "strictness", "strict", settings_file=str(tmp_path / "settings.yaml"),
        reader=reader, writer=writer, runtime=tmp_path,
    )
    assert out["status"] == "PARTIAL", "a partial run must never look complete"
    assert out["side_effect_state"] == "setting-left-changed:strictness"
    assert any("restore NOT confirmed" in w for w in out["warnings"])
    assert state["value"] == "strict", "changed state is what remains"


def test_restart_failure_is_reported_with_side_effect(tmp_path, monkeypatch):
    _state, reader, writer = _d_rig(tmp_path, monkeypatch)
    monkeypatch.setattr(wf, "_restart_app", lambda *a, **k: (False, "not ready"))
    out = wf.verify_setting_persistence(
        "strictness", "strict", settings_file=str(tmp_path / "settings.yaml"),
        reader=reader, writer=writer, runtime=tmp_path,
    )
    assert out["status"] == "FAILED"
    assert out["failed_stage"] == "restart"
    assert out["side_effect_state"] == "restored"


# ---------------- T5/T6: workflow fixes ----------------

def test_settings_navigation_sends_full_key_combo(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, timeout=10.0):
        seen.append(cmd)
        return (0, "", "")

    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(
        wf, "find_window",
        lambda **k: {"xid": "0x1a", "title": "App", "wm_class": "app"},
    )
    monkeypatch.setattr(wf, "focus_window", lambda x: True)

    def fake_shot(path, xid=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return True, "import"

    monkeypatch.setattr(wf, "screenshot", fake_shot)
    monkeypatch.setattr(wf.shutil, "which", lambda n: "/usr/bin/xdotool")
    monkeypatch.setattr(wf, "_run", fake_run)
    wf.open_app_settings_snapshot("app", settings_keys="ctrl+comma", runtime=tmp_path)
    assert any(
        cmd[:2] == ["xdotool", "key"] and "ctrl+comma" in cmd for cmd in seen
    ), f"full combo must be sent, saw: {[c for c in seen if 'xdotool' in c]}"


def test_run_model_test_restores_original_model(tmp_path, monkeypatch):
    calls = {"evals": [], "reader": 0}
    model_state = {"current": "Old Model"}

    def fake_eval(port, expression, timeout=10.0):
        calls["evals"].append(expression)
        if "model-select restore" in expression:
            model_state["current"] = "Old Model"
            return "restored"
        if "no-trigger" in expression:  # the dropdown/setter step selects
            model_state["current"] = "New Model"
            return "opened"
        if "composer-rich-input" in expression:
            return "sent"
        if expression == wf.MODEL_READER_JS:
            calls["reader"] += 1
            return model_state["current"]
        return "PONG"

    monkeypatch.setattr(wf, "cdp_targets", lambda p, timeout=5.0: [{"type": "page"}])
    monkeypatch.setattr(wf, "_cdp_eval", fake_eval)
    monkeypatch.setattr(wf, "wait_for", lambda pred, timeout, interval=2.0: pred())
    out = wf.run_model_test("app", "New Model", "ping", cdp_port=9333, runtime=tmp_path)
    assert any("model-select restore" in e for e in calls["evals"])
    assert out["side_effect_state"] == "restored"


def test_cli_workflows_list_prints_contracts(capsys):
    """T12: workflow_contracts() had no caller — now reachable via CLI."""
    import argparse

    from skaldir import cli

    assert cli._cmd_workflows_list(argparse.Namespace(json=False)) == 0
    out = capsys.readouterr().out
    assert "open_app_snapshot" in out and "run_model_test" in out
    assert cli._cmd_workflows_list(argparse.Namespace(json=True)) == 0
    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert {c["name"] for c in payload} == {
        "open_app_snapshot", "open_app_settings_snapshot",
        "verify_setting_persistence", "run_model_test",
    }


def test_screenshot_routes_through_xlib_capture_first(tmp_path, monkeypatch):
    """VAL-CU-002 / cu-actions-vision: workflows.screenshot uses xlib_capture first."""
    from skaldir.computer_use import ScreenImage

    seen = {}

    def fake_capture(display, window_id=""):
        seen["display"] = display
        seen["window_id"] = window_id
        return ScreenImage(width=4, height=4, rows=(b"\xff\x00\x00" * 4,) * 4)

    monkeypatch.setattr("skaldir.xlib_capture.capture_window_image", fake_capture)
    out = tmp_path / "shot.png"
    ok, backend = wf.screenshot(out, xid="0x2a", display=":0")
    assert ok is True
    assert backend == "xlib"
    assert seen == {"display": ":0", "window_id": "0x2a"}
    assert out.exists() and out.stat().st_size > 0

