"""gv_update_gate.py — marker semantics, anchor dry run, step mapping, receipts.

No build, no Electron, no node, no `hermes update`: every external runner is
injected. The real overlay module is exercised only against a generated tree in
tmp_path; the live stock checkout and the running app are never touched.
"""

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import gv_update_gate as gate  # noqa: E402

# --- update marker (read-only port of the stock semantics) ---------------------


def _write_marker(home: Path, body: str) -> Path:
    path = Path(home) / ".hermes-update-in-progress"
    path.write_text(body, encoding="utf-8")
    return path


def test_marker_absent_is_none(tmp_path):
    assert gate.read_update_marker(tmp_path) is None


def test_marker_live_owner_is_reported(tmp_path):
    _write_marker(tmp_path, "4242\n1000\n")

    marker = gate.read_update_marker(tmp_path, alive=lambda pid: pid == 4242, now=1060.0)

    assert marker == {
        "path": str(tmp_path / ".hermes-update-in-progress"),
        "pid": 4242,
        "age_seconds": 60.0,
    }


def test_marker_dead_pid_is_ignored(tmp_path):
    _write_marker(tmp_path, "4242\n1000\n")
    assert gate.read_update_marker(tmp_path, alive=lambda pid: False, now=1060.0) is None


def test_marker_past_the_20_minute_ceiling_is_ignored(tmp_path):
    _write_marker(tmp_path, "4242\n1000\n")
    assert gate.read_update_marker(tmp_path, alive=lambda pid: True, now=1000.0 + 1201) is None
    assert gate.read_update_marker(tmp_path, alive=lambda pid: True, now=1000.0 + 1199) is not None


def test_marker_malformed_bodies_are_ignored(tmp_path):
    assert gate.read_update_marker(tmp_path, alive=lambda pid: True) is None  # no file
    _write_marker(tmp_path, "not-a-pid\n")
    assert gate.read_update_marker(tmp_path, alive=lambda pid: True) is None
    _write_marker(tmp_path, "4242\nnot-a-time\n")  # unparseable start ⇒ Infinity age
    assert gate.read_update_marker(tmp_path, alive=lambda pid: True) is None


def test_gate_never_writes_or_deletes_the_marker(tmp_path):
    path = _write_marker(tmp_path, "4242\n1000\n")
    before = path.read_bytes()

    gate.read_update_marker(tmp_path, alive=lambda pid: False, now=999999.0)  # stale
    gate.read_update_marker(tmp_path, alive=lambda pid: True, now=1000.0)  # live

    assert path.read_bytes() == before, "the gate must never touch the stock marker"


# --- step 0: preflight ---------------------------------------------------------


def test_preflight_records_commits_and_passes_without_a_marker(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    result = gate.step_preflight(
        tmp_path / "repo",
        tmp_path / "home",
        receipt_dir,
        marker_reader=lambda home: None,
        commit=lambda p: "abc123",
    )

    assert result["exit"] == gate.EXIT_OK
    assert result["stock_commit_before"] == "abc123"
    assert (receipt_dir / "00-stock-commit-before.txt").read_text(encoding="utf-8") == "abc123\n"
    assert (receipt_dir / "00-gv-commit.txt").read_text(encoding="utf-8") == "abc123\n"


def test_preflight_stops_with_exit_20_on_a_live_marker(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    marker = {"path": "m", "pid": 77, "age_seconds": 5.0}

    result = gate.step_preflight(
        tmp_path / "repo",
        tmp_path / "home",
        receipt_dir,
        marker_reader=lambda home: marker,
        commit=lambda p: "abc",
    )

    assert result["exit"] == gate.EXIT_UPDATE_IN_FLIGHT
    assert result["marker"] == marker
    assert "pid 77" in result["detail"]


# --- step 1: stock update artifacts -------------------------------------------


def test_stock_update_adopts_a_green_result_json(tmp_path):
    home = tmp_path / "home"
    (home / "logs").mkdir(parents=True)
    (home / ".hermes-update-result.json").write_text(
        json.dumps({"ok": True, "exit_code": 0, "branch": "main"}), encoding="utf-8"
    )
    (home / "logs" / "desktop-update-handoff.log").write_text(
        "x\n" * 300 + "last line\n", encoding="utf-8"
    )
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    result = gate.step_stock_update(
        tmp_path / "repo", home, receipt_dir, with_update=False, commit=lambda p: "after-sha"
    )

    assert result["exit"] == gate.EXIT_OK
    assert result["ran_update"] is False
    assert result["result"] == {"ok": True, "exit_code": 0, "branch": "main"}
    assert (receipt_dir / "10-update-result.json").exists()
    assert (receipt_dir / "10-handoff.log").read_text(encoding="utf-8").endswith("last line\n")


def test_stock_update_flags_a_red_result_json(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".hermes-update-result.json").write_text(
        json.dumps({"ok": False, "exit_code": 8}), encoding="utf-8"
    )
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    result = gate.step_stock_update(tmp_path / "repo", home, receipt_dir, commit=lambda p: "")

    assert result["exit"] == gate.EXIT_UPDATE_FAILED
    assert "ok:false" in result["detail"]


def test_stock_update_runs_the_cli_only_when_asked(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return {"exit": 0, "stdout": "updated\n", "stderr": ""}

    result = gate.step_stock_update(
        tmp_path / "repo",
        home,
        receipt_dir,
        with_update=True,
        run=fake_run,
        hermes_bin="/usr/bin/hermes",
        commit=lambda p: "sha",
    )

    assert calls[0][0] == ["/usr/bin/hermes", "update", "--yes"]
    assert result["ran_update"] is True
    assert result["exit"] == gate.EXIT_OK
    log = (receipt_dir / "10-hermes-update.log").read_text(encoding="utf-8")
    assert "updated" in log
    assert (receipt_dir / "10-hermes-update.exit").read_text(
        encoding="utf-8"
    ).strip() == "HERMES_UPDATE_EXIT 0"


def test_stock_update_maps_a_nonzero_update_exit_to_21(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    def fake_run(cmd, **kwargs):
        return {"exit": 8, "stdout": "skipped\n", "stderr": ""}

    result = gate.step_stock_update(
        tmp_path / "repo",
        home,
        receipt_dir,
        with_update=True,
        run=fake_run,
        hermes_bin="/usr/bin/hermes",
        commit=lambda p: "",
    )

    assert result["exit"] == gate.EXIT_UPDATE_FAILED
    assert "exited 8" in result["detail"]


# --- step 2: anchor dry run ----------------------------------------------------


class _FakePatchModule:
    """A minimal overlay module with one anchor, mirroring the real contract."""

    PATCHES = [("src/only.ts", "OLD_ANCHOR", "NEW_ANCHOR", 1)]
    FILES: list = []
    REMOVE: list = []

    def apply_patches(self, tree: Path, repo: Path) -> dict:
        text = (tree / "apps" / "desktop" / "src" / "only.ts").read_text(encoding="utf-8")
        count = text.count("OLD_ANCHOR")
        if count != 1:
            raise RuntimeError(
                f"Skaldir overlay failed:\n  src/only.ts: anchor found {count}x, expected 1x"
            )
        return {"patched": {"src/only.ts": 1}, "copied": [], "removed": [], "errors": []}


def _fake_stock_root(tmp_path: Path, body: str | None) -> Path:
    stock = tmp_path / "stock"
    target = stock / "apps" / "desktop" / "src" / "only.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    if body is not None:
        target.write_text(body, encoding="utf-8")
    return stock


def test_anchor_dryrun_green_on_a_healthy_copy(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    stock = _fake_stock_root(tmp_path, "before OLD_ANCHOR after\n")

    result = gate.step_anchor_dryrun(
        REPO, stock, receipt_dir, workdir=tmp_path / "wd", patch_module=_FakePatchModule()
    )

    assert result["exit"] == gate.EXIT_OK
    assert result["anchors"] == 1
    assert result["files"] == 1
    assert (receipt_dir / "20-anchor-dryrun.exit").read_text(
        encoding="utf-8"
    ).strip() == "DRYRUN_EXIT 0"


def test_anchor_dryrun_reports_a_moved_anchor_loudly(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    stock = _fake_stock_root(tmp_path, None)  # upstream lost the anchor line

    result = gate.step_anchor_dryrun(
        REPO, stock, receipt_dir, workdir=tmp_path / "wd", patch_module=_FakePatchModule()
    )

    assert result["exit"] == gate.EXIT_ANCHOR
    assert "anchor found 0x, expected 1x" in result["detail"]
    log = (receipt_dir / "20-anchor-dryrun.log").read_text(encoding="utf-8")
    assert "OVERLAY FAILED" in log
    assert (receipt_dir / "20-anchor-dryrun.exit").read_text(
        encoding="utf-8"
    ).strip() == "DRYRUN_EXIT 22"


def test_anchor_dryrun_runs_the_real_anchors_against_a_generated_tree(tmp_path):
    """The real overlay module + a generated stock tree — no live checkout writes."""
    patch_app = gate._load_patch_app(REPO)
    stock = tmp_path / "stock"
    for rel, old, _new, expected in patch_app.PATCHES:
        path = stock / "apps" / "desktop" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + "\n".join([old] * expected) + "\n", encoding="utf-8")
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    result = gate.step_anchor_dryrun(
        REPO, stock, receipt_dir, workdir=tmp_path / "wd", patch_module=patch_app
    )

    expected_total = sum(item[3] for item in patch_app.PATCHES)
    assert result["exit"] == gate.EXIT_OK
    assert result["anchors"] == expected_total
    assert result["files"] == len({item[0] for item in patch_app.PATCHES})
    assert result["errors"] == []
    assert result["missing_sources"] == []


def test_anchor_dryrun_writes_only_inside_the_workdir(tmp_path):
    patch_app = gate._load_patch_app(REPO)
    stock = tmp_path / "stock"
    for rel, old, _new, expected in patch_app.PATCHES:
        path = stock / "apps" / "desktop" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([old] * expected) + "\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in stock.rglob("*") if path.is_file()}
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    workdir = tmp_path / "wd"

    gate.step_anchor_dryrun(REPO, stock, receipt_dir, workdir=workdir, patch_module=patch_app)

    after = {path: path.read_bytes() for path in stock.rglob("*") if path.is_file()}
    assert after == before, "the stock copy must stay untouched"
    assert (workdir / "tree" / "apps" / "desktop" / "package.json").exists()


# --- step 3: build -------------------------------------------------------------


def test_build_step_records_the_overlay_report(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    overlay = {"patched": {"src/a.ts": 2}, "copied": ["c.js"], "removed": ["r.jpg"], "errors": []}

    def fake_build(*, force=True):
        assert force is True
        return {
            "ok": True,
            "stage": "deployed",
            "binary": "/x/gods-voice",
            "seconds": 101.5,
            "overlay": overlay,
        }

    result = gate.step_build_app(receipt_dir, build=fake_build)

    assert result["exit"] == gate.EXIT_OK
    assert result["overlay"] == overlay
    assert (
        json.loads((receipt_dir / "30-build-app.json").read_text(encoding="utf-8"))["overlay"]
        == overlay
    )


def test_build_step_maps_an_anchor_explosion_to_22(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    def broken_build(*, force=True):
        raise RuntimeError("Skaldir overlay failed:\n  src/a.ts: anchor found 0x, expected 1x")

    result = gate.step_build_app(receipt_dir, build=broken_build)

    assert result["exit"] == gate.EXIT_ANCHOR
    assert "Skaldir overlay failed" in result["detail"]
    assert (receipt_dir / "30-build-app.exit").read_text(
        encoding="utf-8"
    ).strip() == "BUILD_EXIT 22"


# --- step 4: app start ---------------------------------------------------------


def test_start_step_refuses_without_a_binary(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    result = gate.step_start_app(
        receipt_dir, binary_fn=lambda: None, popen=lambda *a, **k: pytest.fail("must not spawn")
    )

    assert result["exit"] == gate.EXIT_APP_START
    assert "build-app first" in result["detail"]


def test_start_step_spawns_with_the_production_env_and_fetches_targets(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append((argv, kwargs))

    result = gate.step_start_app(
        receipt_dir,
        port=9333,
        binary_fn=lambda: "/x/linux-unpacked/gods-voice",
        env_fn=lambda: {"GODSV_MODE": "whisper"},
        popen=fake_popen,
        wait=lambda port, timeout=90.0: True,
        fetch=lambda url: [{"type": "page", "url": "file://app"}],
    )

    argv, kwargs = spawned[0]
    assert argv == [
        "/x/linux-unpacked/gods-voice",
        "--class=SkaldirAgent",
        "--remote-debugging-port=9333",
        "hermes://open/gods-voice",
    ]
    assert kwargs["env"] == {"GODSV_MODE": "whisper"}
    assert kwargs["start_new_session"] is True
    assert result["exit"] == gate.EXIT_OK
    assert result["targets"] == 1
    assert json.loads((receipt_dir / "40-cdp-targets.json").read_text(encoding="utf-8"))["targets"]


def test_start_step_maps_a_cdp_timeout_to_23(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    result = gate.step_start_app(
        receipt_dir,
        binary_fn=lambda: "/x/gods-voice",
        env_fn=dict,
        popen=lambda *a, **k: None,
        wait=lambda port, timeout=90.0: False,
    )

    assert result["exit"] == gate.EXIT_APP_START
    assert "CDP port" in result["detail"]


# --- steps 5/6: node scripts (injected runner, no node needed) -----------------


def test_node_step_maps_a_red_script_to_the_step_exit(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "/fake/node"
        assert cmd[1].endswith("tools/check_desktop_plugin.mjs")
        return {"exit": 1, "stdout": "FAIL something\n", "stderr": ""}

    result = gate.step_node_script(
        REPO,
        "tools/check_desktop_plugin.mjs",
        "60-plugin-contract",
        name="plugin_contract",
        exit_code=gate.EXIT_PLUGIN_CONTRACT,
        receipt_dir=receipt_dir,
        node="/fake/node",
        run=fake_run,
    )

    assert result["exit"] == gate.EXIT_PLUGIN_CONTRACT
    assert (receipt_dir / "60-plugin-contract.log").read_text(encoding="utf-8").startswith("FAIL")
    assert (receipt_dir / "60-plugin-contract.exit").exists()


def test_node_step_passes_the_port_to_the_ui_e2e_and_maps_green(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return {"exit": 0, "stdout": "all checks passed\n", "stderr": ""}

    result = gate.step_node_script(
        REPO,
        "tools/gv_ui_e2e.mjs",
        "50-ui-e2e",
        name="ui_e2e",
        exit_code=gate.EXIT_UI_E2E,
        extra=(9333,),
        receipt_dir=receipt_dir,
        node="/fake/node",
        run=fake_run,
    )

    assert seen["cmd"][2:] == ["9333"]
    assert result["exit"] == gate.EXIT_OK


def test_node_step_skips_cleanly_without_node(tmp_path, monkeypatch):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    monkeypatch.setattr(gate.shutil, "which", lambda _name: None)

    result = gate.step_node_script(
        REPO,
        "tools/check_desktop_plugin.mjs",
        "60-plugin-contract",
        name="plugin_contract",
        exit_code=gate.EXIT_PLUGIN_CONTRACT,
        receipt_dir=receipt_dir,
        run=lambda *a, **k: pytest.fail("must not run without node"),
    )

    assert result == {
        "step": "plugin_contract",
        "exit": None,
        "skipped": True,
        "detail": "node not installed",
    }


# --- step 7: pytest net --------------------------------------------------------


def test_pytest_step_maps_green_and_red(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()

    def green(cmd, **kwargs):
        assert cmd[1:3] == ["-m", "pytest"]
        assert cmd[3] == "tests/test_appbuild.py"
        return {"exit": 0, "stdout": "9 passed\n", "stderr": ""}

    assert gate.step_pytest(repo, receipt_dir, run=green)["exit"] == gate.EXIT_OK

    def red(cmd, **kwargs):
        return {"exit": 1, "stdout": "1 failed\n", "stderr": ""}

    result = gate.step_pytest(repo, receipt_dir, run=red)
    assert result["exit"] == gate.EXIT_PYTEST
    assert (receipt_dir / "70-pytest-appbuild.exit").read_text(
        encoding="utf-8"
    ).strip() == "APPTEST_EXIT 26"


# --- step 8: cleanup -----------------------------------------------------------


def test_cleanup_stops_only_the_debug_port_instance(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    killed = []

    def cmdline(pid):
        return "gods-voice --remote-debugging-port=9333" if pid == 1 else "gods-voice"

    result = gate.step_cleanup(
        receipt_dir,
        port=9333,
        pids_fn=lambda **kw: [1, 2],
        cmdline=cmdline,
        crash_fn=lambda pids, **kw: killed.append(pids),
        relaunch=lambda: 0,
    )

    assert killed == [[1]]
    assert result["exit"] == gate.EXIT_OK
    assert result["relaunch_exit"] == 0


def test_cleanup_records_a_failed_kill_but_stays_green(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()

    def failing_crash(pids, **kw):
        raise gate.CrashError("PIDs survived kill -9")

    result = gate.step_cleanup(
        receipt_dir,
        pids_fn=lambda **kw: [1],
        cmdline=lambda pid: "--remote-debugging-port=9333",
        crash_fn=failing_crash,
        relaunch=lambda: 0,
    )

    assert result["exit"] == gate.EXIT_OK
    assert "cleanup kill failed" in result["detail"]


# --- aggregation + receipt -----------------------------------------------------


def test_aggregate_exit_takes_the_first_failing_step():
    steps = {
        "preflight": {"step": "preflight", "exit": 0},
        "anchor_dryrun": {"step": "anchor_dryrun", "exit": 0},
        "build_app": {"step": "build_app", "exit": gate.EXIT_ANCHOR},
        "ui_e2e": {"step": "ui_e2e", "exit": gate.EXIT_UI_E2E},
    }
    assert gate.aggregate_exit(steps) == gate.EXIT_ANCHOR
    assert (
        gate.aggregate_exit({"preflight": {"exit": 0}, "ui_e2e": {"exit": 24}}) == gate.EXIT_UI_E2E
    )
    assert gate.aggregate_exit({}) == gate.EXIT_OK
    skipped = {"build_app": {"exit": None, "skipped": True}, "ui_e2e": {"exit": 0}}
    assert gate.aggregate_exit(skipped) == gate.EXIT_OK


def test_build_receipt_records_the_documented_schema(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "receipt.json").write_text("{}", encoding="utf-8")
    steps = {
        "preflight": {
            "step": "preflight",
            "exit": 0,
            "stock_commit_before": "aaa",
            "gv_commit": "bbb",
        },
        "stock_update": {"step": "stock_update", "exit": 0, "stock_commit_after": "ccc"},
        "anchor_dryrun": {
            "step": "anchor_dryrun",
            "exit": 0,
            "anchors": 30,
            "files": 12,
            "copied": 3,
            "removed": 1,
            "errors": [],
        },
        "plugin_contract": {"step": "plugin_contract", "exit": 0},
        "pytest_appbuild": {"step": "pytest_appbuild", "exit": 0},
    }

    receipt = gate.build_receipt(
        receipt_dir=receipt_dir,
        steps=steps,
        mode_steps=gate.CHECK_MODE_STEPS,
        dry_run=True,
        started=1.0,
    )

    assert receipt["kind"] == "gv-update-gate"
    assert receipt["ok"] is True
    assert receipt["dry_run"] is True
    assert receipt["exit_code"] == 0
    assert receipt["stock_commit_before"] == "aaa"
    assert receipt["stock_commit_after"] == "ccc"
    assert receipt["gv_commit"] == "bbb"
    assert receipt["steps"] == {
        "preflight": 0,
        "stock_update": 0,
        "anchor_dryrun": 0,
        "plugin_contract": 0,
        "pytest_appbuild": 0,
    }
    assert receipt["skipped_steps"] == ["build_app", "app_start", "ui_e2e", "cleanup"]
    assert receipt["overlay"] == {
        "patched_files": 12,
        "patched_anchors": 30,
        "copied": 3,
        "removed": 1,
        "errors": [],
    }
    assert "receipt.json" in receipt["artifacts"]


def test_build_receipt_prefers_the_build_overlay_when_the_build_ran(tmp_path):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    overlay = {"patched": {"a.ts": 2}, "copied": ["x", "y"], "removed": [], "errors": []}
    steps = {"build_app": {"step": "build_app", "exit": 0, "overlay": overlay}}

    receipt = gate.build_receipt(
        receipt_dir=receipt_dir,
        steps=steps,
        mode_steps=gate.FULL_MODE_STEPS,
        dry_run=False,
        started=1.0,
    )

    assert receipt["overlay"] == {
        "patched_files": 1,
        "patched_anchors": 2,
        "copied": 2,
        "removed": 0,
        "errors": [],
    }


# --- main() in --check mode (fully mocked, safe by construction) ---------------


def _check_mode_monkeypatch(
    monkeypatch, tmp_path, *, anchor_exit=gate.EXIT_OK, preflight_exit=gate.EXIT_OK
):
    calls: dict = {"nodes": [], "stock_update": None}
    monkeypatch.setattr(gate, "hermes_home", lambda: tmp_path / "home")
    monkeypatch.setattr(gate, "default_receipt_dir", lambda: tmp_path / "default-receipts")
    monkeypatch.setattr(
        gate,
        "step_preflight",
        lambda repo, home, receipt_dir: {
            "step": "preflight",
            "exit": preflight_exit,
            "detail": "fake",
            "stock_commit_before": "abc",
            "gv_commit": "def",
            "marker": {"pid": 1} if preflight_exit else None,
        },
    )

    def fake_stock_update(repo, home, receipt_dir, *, with_update=False, **kw):
        calls["stock_update"] = with_update
        return {"step": "stock_update", "exit": 0, "detail": "fake", "stock_commit_after": "ghi"}

    monkeypatch.setattr(gate, "step_stock_update", fake_stock_update)
    monkeypatch.setattr(
        gate,
        "step_anchor_dryrun",
        lambda repo, stock, receipt_dir, **kw: {
            "step": "anchor_dryrun",
            "exit": anchor_exit,
            "anchors": 30,
            "files": 12,
            "copied": 3,
            "removed": 1,
            "errors": [],
            "detail": "fake",
        },
    )

    def fake_node(repo, script, prefix, **kw):
        calls["nodes"].append((script, kw.get("name")))
        return {"step": kw.get("name"), "exit": 0, "detail": "fake", "skipped": False}

    monkeypatch.setattr(gate, "step_node_script", fake_node)
    monkeypatch.setattr(
        gate,
        "step_pytest",
        lambda repo, receipt_dir, **kw: {"step": "pytest_appbuild", "exit": 0, "detail": "fake"},
    )
    monkeypatch.setattr(
        gate, "step_build_app", lambda *a, **k: pytest.fail("build must not run in --check")
    )
    monkeypatch.setattr(
        gate, "step_start_app", lambda *a, **k: pytest.fail("start must not run in --check")
    )
    monkeypatch.setattr(
        gate, "step_cleanup", lambda *a, **k: pytest.fail("cleanup must not run in --check")
    )
    return calls


def test_main_check_mode_runs_the_cheap_steps_only(tmp_path, monkeypatch):
    calls = _check_mode_monkeypatch(monkeypatch, tmp_path)
    receipt_dir = tmp_path / "receipts"

    code = gate.main(["--check", "--with-update", "--receipts", str(receipt_dir)])

    assert code == gate.EXIT_OK
    receipt = json.loads((receipt_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["dry_run"] is True
    assert receipt["ok"] is True
    assert set(receipt["steps"]) == {
        "preflight",
        "stock_update",
        "anchor_dryrun",
        "plugin_contract",
        "pytest_appbuild",
    }
    assert receipt["skipped_steps"] == ["build_app", "app_start", "ui_e2e", "cleanup"]
    assert "build_app" not in receipt["steps"]
    assert calls["nodes"] == [("tools/check_desktop_plugin.mjs", "plugin_contract")]
    # --with-update must never run an update in check mode
    assert calls["stock_update"] is False


def test_main_check_mode_maps_an_anchor_mismatch_to_22(tmp_path, monkeypatch):
    _check_mode_monkeypatch(monkeypatch, tmp_path, anchor_exit=gate.EXIT_ANCHOR)
    receipt_dir = tmp_path / "receipts"

    code = gate.main(["--check", "--receipts", str(receipt_dir)])

    assert code == gate.EXIT_ANCHOR
    receipt = json.loads((receipt_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["steps"]["anchor_dryrun"] == gate.EXIT_ANCHOR


def test_main_refuses_to_run_while_an_update_is_in_flight(tmp_path, monkeypatch):
    calls = _check_mode_monkeypatch(
        monkeypatch, tmp_path, preflight_exit=gate.EXIT_UPDATE_IN_FLIGHT
    )
    receipt_dir = tmp_path / "receipts"

    code = gate.main(["--check", "--receipts", str(receipt_dir)])

    assert code == gate.EXIT_UPDATE_IN_FLIGHT
    receipt = json.loads((receipt_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert set(receipt["steps"]) == {"preflight"}
    assert calls["nodes"] == []
