"""ACT service: the honest capability matrix, declared runs, GUI sub-tools (no GUI needed).

Evidence class: unit + in-process runners. Everything here runs without a display: the
fixture runner is scripted, and the host-dependent facts are asserted *against* the measured
host facts (``host_facts()``) instead of assuming a machine that has podman or bwrap. The
real container-display proof lives in ``tests/test_act_service_container.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import act_service as S
from skaldir.computer_use import (
    ALL_KINDS,
    ActionResult,
    CUPolicy,
    DryRunRunner,
)

# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #


def _action(description: str, *, kind: str = "cli", expect: str = "ok") -> dict:
    """One valid declared action (kind/argv/verify) with a scripted expectation."""
    action: dict = {
        "kind": kind,
        "channel": "api",
        "description": description,
        "verify": {"expect_stdout_contains": expect},
    }
    if kind in ("cli", "api_call"):
        action["argv"] = ["echo", expect]
    return action


def _fixture_for(
    actions, *observations: str, override: dict | None = None
) -> DryRunRunner:
    """A scripted runner whose stdout answers exactly the declared expectations.

    The DryRunRunner is a fixture: it returns nothing from the display, so the only honest
    way to exercise the verification path is to script its stdout per action description.
    """
    behaviors = {}
    for action in actions:
        verify = action.get("verify") or {}
        text = str(verify.get("expect_stdout_contains", "") or "")
        behaviors[str(action.get("description", ""))] = ActionResult(ok=True, stdout=text)
    behaviors.update(override or {})
    return DryRunRunner(observations=list(observations) or [""], behaviors=behaviors)


def _fixture(*observations: str, behaviors: dict | None = None) -> DryRunRunner:
    return DryRunRunner(observations=list(observations) or [""], behaviors=behaviors or {})


def _subtool_spec(**overrides) -> dict:
    spec = {
        "name": "probe_flow",
        "version": "1.0",
        "description": "a stored flow whose checks are observable on the display",
        "applicability": {
            "app": "probe",
            "app_versions": [">=1.0,<2"],
            "locales": ["en", "*"],
        },
        "preconditions": [
            {"id": "display_ready", "text": "READY", "source": "window_title"},
        ],
        "anchors": [
            {"id": "target_window", "text": "TARGET-WINDOW", "source": "window_title"},
        ],
        "actions": [_action("do the thing")],
        "end_state": {
            "channel": "window_title",
            "description": "the flow left the expected window behind",
            "expect_observe_contains": "AFTER-WINDOW",
        },
        "verification_contract": "the display shows the window the flow is supposed to leave",
    }
    spec.update(overrides)
    return spec


# --------------------------------------------------------------------------- #
# the capability matrix                                                         #
# --------------------------------------------------------------------------- #


def test_the_live_desktop_is_reported_as_forbidden_not_as_missing():
    report = S.capability_report()
    bridges = {record["id"]: record for record in report["bridges"]}
    live = bridges[S.BRIDGE_LIVE_DESKTOP]
    assert live["available"] is False
    assert live["forbidden_by_policy"] is True
    assert "require_isolation" in live["reason"]
    assert report["live_desktop"] is False


def test_every_capability_names_a_bridge_a_driver_and_a_verification_channel():
    report = S.capability_report()
    names = [record["name"] for record in report["capabilities"]]
    assert names == list(report["capability_names"])
    assert {"window.list", "window.focus", "click", "key", "type_text", "scroll"} <= set(names)
    for record in report["capabilities"]:
        assert record["bridge"] in S.BRIDGES
        assert record["driver"], record["name"]
        assert record["verification_channel"], record["name"]
        assert isinstance(record["available"], bool)
        assert record["reason"], record["name"]
        if not record["available"]:
            # an unavailable capability always names the precise missing piece
            assert record["missing_capability"], record["name"]


def test_the_matrix_matches_the_measured_host_facts():
    facts = S.host_facts()
    report = S.capability_report()
    bridges = {record["id"]: record for record in report["bridges"]}
    assert bridges[S.BRIDGE_SANDBOX]["available"] is bool(facts["facts"]["bwrap"])
    assert bridges[S.BRIDGE_CONTAINER]["available"] is bool(facts["isolation_tools"]["podman"])
    assert bridges[S.BRIDGE_CONTAINER_GUI]["available"] is bool(facts["gui_image"]["present"])
    assert report["host"]["gui_image"]["image"] == S.GUI_IMAGE
    assert report["verdict"] == ("isolated-only" if facts["gui_image"]["present"] else "blocked")


def test_a_capability_needing_a_tool_the_image_lacks_is_unavailable_and_named():
    """``observe.screenshot`` against what the image really ships.

    Measured 2026-09-19 in ``localhost/godsv-cu-x11:latest``: ``xwd`` is present and
    ``import``/``scrot``/``gnome-screenshot`` are not — so the capture capability is
    **available** through ``xwd`` (decoded on the host by ``computer_use.xwd_to_png``), and
    the capability's *driver* is what names the tool. The older expectation ("the image has
    no screenshot tool, so this is unavailable") described the defect this wave closed; the
    unavailable branch still has to name its missing piece when the image is absent or a
    monkeypatched baseline says the tool is gone.
    """
    record = S.capability_status("observe.screenshot")
    assert "xwd" in record["driver"]
    if S.host_facts()["gui_image"]["present"]:
        assert record["available"] is True
        assert record["real"] is True
        assert S.IMAGE_TOOL_BASELINE["xwd"] is True
    else:
        assert record["available"] is False
        assert record["missing_capability"]


def test_a_capture_capability_names_the_tool_it_is_missing(monkeypatch):
    """With the tool gone the capability turns unavailable *and* names what is missing."""
    monkeypatch.setitem(S.IMAGE_TOOL_BASELINE, "xwd", False)
    monkeypatch.setattr(S, "_gui_image_present", lambda: (True, "image present"))
    record = S.capability_status("observe.screenshot")
    assert record["available"] is False
    assert "xwd" in str(record["missing_capability"])


def test_unknown_capability_is_reported_with_the_known_names():
    record = S.capability_status("click.very.hard")
    assert record["available"] is False
    assert record["not_found"] is True
    assert "click" in record["known"]


def test_the_capability_report_is_read_only(tmp_path, monkeypatch):
    """``skaldir selftest`` probes with this: it may read, never write."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("GODSV_HOME", str(runtime))
    report = S.capability_report()
    S.capability_status("window.list")
    assert report["policy"]["require_isolation"] is True
    assert sorted(item.name for item in runtime.iterdir()) == []


# --------------------------------------------------------------------------- #
# runner resolution                                                             #
# --------------------------------------------------------------------------- #


def test_auto_never_falls_back_to_the_in_process_fixture(monkeypatch):
    monkeypatch.setattr(S, "_gui_image_present", lambda: (False, f"image missing: {S.GUI_IMAGE}"))
    monkeypatch.setattr(S, "_sandbox_available", lambda: False)
    runner, record = S.resolve_runner("auto")
    assert runner is None
    assert record["resolved"] is False
    assert record["fixture"] is False
    assert "no real bridge is available" in record["reason"]
    assert "image missing" in record["reason"]


def test_the_fixture_stays_reachable_when_it_is_asked_for_explicitly():
    runner, record = S.resolve_runner("dry-run")
    assert isinstance(runner, DryRunRunner)
    assert record["fixture"] is True
    assert record["real"] is False
    assert record["scope"] == "in-process fixture"
    assert "explicitly requested" in record["reason"]


def test_an_unknown_runner_name_lists_the_known_ones():
    runner, record = S.resolve_runner("teleport")
    assert runner is None
    assert "unknown runner" in record["reason"]
    assert "container-gui" in record["reason"] and "dry-run" in record["reason"]


def test_the_container_gui_runner_is_only_resolved_when_its_image_is_really_there():
    facts = S.host_facts()
    runner, record = S.resolve_runner("container-gui")
    if facts["gui_image"]["present"]:
        assert runner is not None
        assert record["bridge"] == S.BRIDGE_CONTAINER_GUI
        assert record["real"] is True
        assert S.CONTAINER_DISPLAY in record["reason"]
    else:
        assert runner is None
        assert "unavailable" in record["reason"]


# --------------------------------------------------------------------------- #
# declared actions                                                              #
# --------------------------------------------------------------------------- #


def test_an_action_without_a_verification_is_refused_with_its_index():
    problem = S.action_error(3, {"kind": "cli", "argv": ["echo", "x"]})
    assert problem.startswith("action 3:")
    assert "verify" in problem
    empty = S.action_error(0, {"kind": "cli", "argv": ["x"], "verify": {}})
    assert "names no expectation" in empty


def test_declared_action_errors_cover_kind_channel_and_payload():
    assert "unknown kind" in S.action_error(0, {"kind": "hypnosis", "verify": {"x": 1}})
    assert "unknown channel" in S.action_error(
        0, {"kind": "cli", "argv": ["x"], "channel": "telepathy", "verify": {"x": 1}}
    )
    assert "argv" in S.action_error(0, {"kind": "cli", "verify": {"expect_stdout_contains": "x"}})
    assert "payload.key" in S.action_error(
        0, {"kind": "key", "payload": {}, "verify": {"expect_stdout_contains": "x"}}
    )
    assert "payload.text" in S.action_error(
        0, {"kind": "type_text", "payload": {}, "verify": {"expect_stdout_contains": "x"}}
    )
    assert "payload.op" in S.action_error(
        0, {"kind": "window", "payload": {"op": "fly"}, "verify": {"expect_stdout_contains": "x"}}
    )
    assert S.action_error(0, _action("fine")) == ""


def test_scroll_and_window_are_translated_without_widening_the_policy():
    scroll = S.to_core_action(
        {"kind": "scroll", "payload": {"direction": "down", "amount": 2}}
    )
    assert scroll.kind == "mouse"
    assert scroll.argv == ["xdotool", "click", "--repeat", "2", "5"]
    up = S.to_core_action({"kind": "scroll", "payload": {"direction": "up", "amount": 1}})
    assert up.argv[-1] == "4"

    window = S.to_core_action({"kind": "window", "payload": {"op": "list", "title": "T"}})
    assert window.kind == "cli"
    assert window.argv[:4] == ["xdotool", "search", "--name", "T"]
    focus = S.to_core_action({"kind": "window", "payload": {"op": "focus", "title": "T"}})
    assert "windowfocus --sync" in focus.argv[-1]

    policy = S.default_policy()
    assert "scroll" not in policy.allowed_kinds
    assert "drag" in policy.allowed_kinds
    assert "launch" in policy.allowed_kinds
    assert set(policy.allowed_kinds) <= set(ALL_KINDS)
    assert S.policy_record(policy)["require_isolation"] is True


def test_a_mouse_click_settles_the_pointer_before_the_button_event():
    """Measured: `mousemove` without `--sync` lets the click land where the pointer was."""
    mouse = S.to_core_action(
        {"kind": "mouse", "payload": {"x": 12, "y": 34, "click": True, "button": 1}}
    )
    assert mouse.argv == ["xdotool", "mousemove", "--sync", "12", "34", "click", "1"]


def test_the_end_state_is_read_from_the_observation_never_from_the_action_stdout():
    assert S.check_end_state(None, "anything")["declared"] is False
    assert S.check_end_state({}, "anything")["declared"] is False

    ok = S.check_end_state({"expect_observe_contains": "AFTER"}, "the display shows AFTER")
    assert ok["ok"] is True and ok["verified"] is True

    missing = S.check_end_state(
        {"expect_observe_contains": ["AFTER", "ALSO"], "expect_state_contains": "THIRD"},
        "only AFTER",
    )
    assert missing["ok"] is False
    assert missing["missing"] == ["ALSO", "THIRD"]


# --------------------------------------------------------------------------- #
# run_action_list                                                               #
# --------------------------------------------------------------------------- #


def test_an_empty_action_list_is_a_usage_error(tmp_path):
    record = S.run_action_list([], runtime=tmp_path)
    assert record["status"] == "usage"
    assert record["exit_code"] == S.EXIT_USAGE
    assert record["verified"] is False


def test_a_declaration_that_cannot_be_verified_stops_before_anything_runs(tmp_path):
    record = S.run_action_list(
        [{"kind": "cli", "argv": ["echo", "x"]}], runtime=tmp_path, runner=_fixture("x")
    )
    assert record["status"] == "usage"
    assert record["exit_code"] == S.EXIT_USAGE
    assert record["executed"] == 0


def test_a_run_without_a_real_bridge_is_blocked_with_the_missing_capability(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "_gui_image_present", lambda: (False, f"image missing: {S.GUI_IMAGE}"))
    monkeypatch.setattr(S, "_sandbox_available", lambda: False)
    record = S.run_action_list(
        [_action("anything")],
        end_state={"expect_observe_contains": "AFTER"},
        runtime=tmp_path,
    )
    assert record["status"] == "blocked"
    assert record["exit_code"] == S.EXIT_BLOCKED
    assert "image missing" in record["reason"]
    missing = record["missing_capability"]
    assert S.GUI_IMAGE in missing or "container" in missing


def test_more_actions_than_the_policy_allows_is_refused(tmp_path):
    actions = [_action(f"step {index}") for index in range(CUPolicy().max_actions + 1)]
    record = S.run_action_list(
        actions, runtime=tmp_path, runner=_fixture_for(actions, "AFTER")
    )
    assert record["status"] == "usage"
    assert "max_actions" in record["reason"]


def test_a_policy_that_forbids_the_kind_blocks_the_run_and_names_the_action(tmp_path):
    actions = [
        {
            "kind": "mouse",
            "channel": "vision",
            "payload": {"x": 1, "y": 2, "click": True},
            "description": "a click the policy does not allow",
            "verify": {"expect_observe_contains": "somewhere"},
        }
    ]
    record = S.run_action_list(
        actions,
        runner=_fixture_for(actions, "somewhere"),
        policy=CUPolicy(allowed_kinds=("cli",)),
        end_state={"expect_observe_contains": "somewhere"},
        runtime=tmp_path,
    )
    assert record["status"] == "blocked"
    assert record["exit_code"] == S.EXIT_BLOCKED
    assert "action 0" in record["reason"]
    assert record["actions"][0]["status"] == "refused"
    assert "policy" in record["actions"][0]["detail"]


def test_the_fixture_runner_is_labelled_as_a_fixture_and_still_verifies_the_end_state(tmp_path):
    actions = [_action("first")]
    record = S.run_action_list(
        actions,
        end_state={"expect_observe_contains": "AFTER"},
        runtime=tmp_path,
        runner=_fixture_for(actions, "AFTER"),
    )
    assert record["status"] == "verified"
    assert record["verified"] is True
    assert record["exit_code"] == S.EXIT_OK
    assert record["scope"] == "in-process fixture"
    assert record["live"] is False
    assert record["runner"]["fixture"] is True
    assert record["end_state"]["ok"] is True
    assert record["actions"][0]["status"] == "verified"


def test_the_run_stops_at_the_first_unverified_action(tmp_path):
    actions = [_action("first", expect="ok"), _action("second", expect="never")]
    record = S.run_action_list(
        actions,
        end_state={"expect_observe_contains": "ok"},
        runtime=tmp_path,
        runner=_fixture_for(
            actions,
            "placeholder",
            override={"second": ActionResult(ok=False, detail="the second step refused")},
        ),
    )
    assert record["status"] == "verification_failed"
    assert record["exit_code"] == S.EXIT_VERIFICATION_FAILED
    assert [row["status"] for row in record["actions"]] == ["verified", "aborted"]
    assert record["executed"] == 2
    # report 18 F2: the reason names what really happened — the action did NOT run — instead
    # of the misleading "stdout without 'never'" the expectation comparison used to produce
    # for a refused action. Nothing after the refused action runs either (executed == 2).
    assert "did not run" in record["reason"]
    assert "the second step refused" in record["reason"]
    assert record["verified"] is False


def test_a_verified_action_without_the_end_state_is_a_failure_not_a_pass(tmp_path):
    actions = [_action("first")]
    record = S.run_action_list(
        actions,
        end_state={"expect_observe_contains": "NOT-ON-THE-DISPLAY"},
        runtime=tmp_path,
        runner=_fixture_for(actions, "something else"),
    )
    assert record["status"] == "verification_failed"
    assert record["verified"] is False
    assert record["end_state"]["missing"] == ["NOT-ON-THE-DISPLAY"]


def test_a_missing_end_state_declaration_is_a_usage_error(tmp_path):
    actions = [_action("first")]
    record = S.run_action_list(
        actions, runtime=tmp_path, runner=_fixture_for(actions, "placeholder", "AFTER")
    )
    assert record["status"] == "usage"
    assert "end_state" in record["reason"]


def test_the_run_is_recorded_where_the_panel_view_reads_it(tmp_path):
    actions = [_action("first")]
    record = S.run_action_list(
        actions,
        goal="a declared goal",
        end_state={"expect_observe_contains": "AFTER"},
        runtime=tmp_path,
        runner=_fixture_for(actions, "AFTER"),
    )
    written = Path(record["record"])
    assert written == tmp_path / "cu_runs.jsonl"
    row = json.loads(written.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["status"] == "verified"
    assert row["goal"] == "a declared goal"
    assert row["source"] == "act"
    assert row["planned"] == 1


# --------------------------------------------------------------------------- #
# GUI sub-tools: preconditions + anchors + actions + end state                  #
# --------------------------------------------------------------------------- #


def test_the_named_subtools_exist_with_all_four_parts():
    builtins = S.builtin_subtools()
    assert set(builtins) == {
        "open_browser",
        "open_youtube",
        "focus_search_bar",
        "open_website",
        "open_project",
        "open_settings",
        "host_window_state",
    }
    for name, spec in builtins.items():
        assert S.subtool_errors(spec) == [], (name, S.subtool_errors(spec))
        assert spec["preconditions"] and spec["anchors"] and spec["actions"]
        assert spec["end_state"]["expect_observe_contains"]
        assert spec["applicability"]["app_versions"] and spec["applicability"]["locales"]
        assert spec["verification_contract"]
        visual = S.to_visual_subtool(spec)
        assert visual.ref == f"{name}@{spec['version']}"
        assert len(visual.anchors) == len(spec["anchors"])
        assert visual.allowed_actions


def test_the_browser_title_env_names_the_open_browser_focus_target(monkeypatch):
    """``SKALDIR_CU_BROWSER_TITLE`` overrides the window the builtin focuses."""
    from skaldir import act_service as S

    default = S.builtin_subtools()["open_browser"]
    default_action = [a for a in default["actions"] if a["kind"] == "window"][0]
    assert "Firefox" in default_action["payload"]["title"] and "Zen" in default_action["payload"]["title"]

    monkeypatch.setenv("SKALDIR_CU_BROWSER_TITLE", "Chromium \u2014 docs")
    overridden = S.builtin_subtools()["open_browser"]
    overridden_action = [a for a in overridden["actions"] if a["kind"] == "window"][0]
    assert overridden_action["payload"]["title"] == "Chromium \u2014 docs"


def test_the_subtool_schema_reports_every_problem_at_once():
    problems = S.subtool_errors({"name": "x"})
    assert any("missing required key" in problem for problem in problems)
    assert len(problems) >= len(S.SUBTOOL_KEYS) - 1

    broken = _subtool_spec(
        anchors=[{"id": "a", "text": "T", "source": "vibes"}],
        actions=[{"kind": "cli", "argv": ["echo"]}],
        end_state={},
    )
    problems = S.subtool_errors(broken)
    assert any("source 'vibes' unknown" in problem for problem in problems)
    assert any("verify" in problem for problem in problems)
    assert any("expect_observe_contains" in problem for problem in problems)

    host_without_fact = _subtool_spec(
        preconditions=[{"id": "p", "text": "T", "source": "host"}]
    )
    assert any("needs 'fact'" in problem for problem in S.subtool_errors(host_without_fact))


def test_store_round_trip_lists_shadows_and_refuses_a_silent_overwrite(tmp_path):
    summary = S.save_subtool(_subtool_spec(), state=tmp_path)
    assert summary["origin"] == "saved"
    assert Path(summary["path"]).is_file()
    assert S.subtool_dir(tmp_path) == tmp_path / S.DEFAULT_STATE_SUBDIR

    with pytest.raises(FileExistsError):
        S.save_subtool(_subtool_spec(), state=tmp_path)
    replaced = S.save_subtool(
        _subtool_spec(description="replaced"), state=tmp_path, force=True
    )
    assert replaced["description"] == "replaced"

    spec, origin, path = S.load_subtool("probe_flow", state=tmp_path)
    assert origin == "saved" and path.endswith("probe_flow.json")
    assert spec["description"] == "replaced"

    with pytest.raises(FileNotFoundError):
        S.load_subtool("no_such_flow", state=tmp_path)

    rows = {row["name"]: row for row in S.list_subtools(state=tmp_path)}
    assert rows["probe_flow"]["origin"] == "saved"
    assert rows["open_browser"]["origin"] == "builtin"
    assert rows["open_browser"]["end_state"]["expect_observe_contains"]


def test_a_saved_subtool_with_the_same_name_shadows_the_builtin(tmp_path):
    spec = _subtool_spec(name="open_browser")
    S.save_subtool(spec, state=tmp_path)
    rows = {row["name"]: row for row in S.list_subtools(state=tmp_path)}
    assert rows["open_browser"]["origin"] == "saved"
    assert rows["open_browser"]["shadows_builtin"] is True
    loaded, origin, _path = S.load_subtool("open_browser", state=tmp_path)
    assert origin == "saved"
    assert loaded["name"] == "open_browser"


def test_a_save_that_does_not_validate_never_reaches_the_disk(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        S.save_subtool(_subtool_spec(anchors=[]), state=tmp_path)
    assert "anchors" in str(excinfo.value)
    assert not S.subtool_dir(tmp_path).exists()


def test_anchor_observation_separates_missing_from_unobservable():
    spec = _subtool_spec(
        preconditions=[
            {"id": "ready", "text": "READY", "source": "window_title"},
            {"id": "gui", "text": "GUI", "source": "host", "fact": "gui_image"},
        ],
        anchors=[
            {"id": "target", "text": "TARGET-WINDOW", "source": "window_title"},
            {"id": "search", "text": "Search", "source": "app_state"},
        ],
    )
    facts = S.host_facts()["facts"]
    observed = S.observe_anchors(spec, "READY and TARGET-WINDOW are on the display")
    expected_conditions = ["READY"] + (["GUI"] if facts["gui_image"] else [])
    assert list(observed["observed_start_conditions"]) == expected_conditions
    if not facts["gui_image"]:
        assert any("'gui'" in item for item in observed["missing"])
    assert observed["observed_anchors"] == ("TARGET-WINDOW",)
    # app_state is a wired channel (window metadata at least): a needle that is not
    # in the state is MISSING (reobserve), not unobservable.
    assert observed["unobservable"] == ()
    assert any("Search" in item for item in observed["missing"])
    assert any("app state" in item for item in observed["missing"])
    assert not any("TARGET-WINDOW" in item for item in observed["missing"])

    absent = S.observe_anchors(spec, "nothing matches here")
    assert any("'target'" in item for item in absent["missing"])
    assert any("'ready'" in item for item in absent["missing"])


def test_a_declared_probe_that_cannot_run_is_unobservable():
    spec = _subtool_spec(
        anchors=[{"id": "s", "text": "Search", "source": "app_state"}],
        app_state_probe={"argv": ["/nonexistent/never-runs"]},
    )
    observed = S.observe_anchors(spec, "x")
    assert observed["unobservable"]
    assert any("Search" in item for item in observed["unobservable"])


def test_running_a_subtool_whose_probe_cannot_run_is_blocked_with_the_missing_bridge(tmp_path):
    spec = _subtool_spec(
        name="needs_probe",
        anchors=[{"id": "s", "text": "Search", "source": "app_state"}],
        app_state_probe={"argv": ["/nonexistent/never-runs"]},
    )
    S.save_subtool(spec, state=tmp_path)
    record = S.run_subtool(
        "needs_probe", state=tmp_path, runner=_fixture("anything"), write_record=False
    )
    assert record["status"] == "blocked"
    assert record["exit_code"] == S.EXIT_BLOCKED
    assert "app_state_probe" in record["missing_capability"] or "app's own state" in str(record)
    assert "actions" not in record


def test_running_a_subtool_while_its_anchor_is_absent_reobserves_and_executes_nothing(tmp_path):
    S.save_subtool(_subtool_spec(), state=tmp_path)
    record = S.run_subtool(
        "probe_flow", state=tmp_path, runner=_fixture("no anchor here"), write_record=False
    )
    assert record["status"] == "reobserve"
    assert record["exit_code"] == S.EXIT_VERIFICATION_FAILED
    assert record["verified"] is False
    assert any("TARGET-WINDOW" in item for item in record["missing_anchors"])
    assert "actions" not in record


def test_running_a_subtool_with_observed_checks_runs_the_actions_and_the_end_state(tmp_path):
    spec = _subtool_spec()
    S.save_subtool(spec, state=tmp_path)
    record = S.run_subtool(
        "probe_flow",
        state=tmp_path,
        runner=_fixture_for(spec["actions"], "READY TARGET-WINDOW", "AFTER-WINDOW"),
        write_record=False,
    )
    assert record["status"] == "verified"
    assert record["verified"] is True
    assert record["exit_code"] == S.EXIT_OK
    assert record["subtool"] == "probe_flow"
    assert record["origin"] == "saved"
    assert record["precondition_check"]["observed_anchors"] == ("TARGET-WINDOW",)
    assert record["applicability"]["applicable"] is True
    assert record["end_state"]["ok"] is True


def test_a_subtool_for_another_app_version_is_refused_before_it_acts(tmp_path):
    spec = _subtool_spec()
    S.save_subtool(spec, state=tmp_path)
    record = S.run_subtool(
        "probe_flow",
        state=tmp_path,
        runner=_fixture_for(spec["actions"], "READY TARGET-WINDOW", "AFTER-WINDOW"),
        app_version="3.0",
        write_record=False,
    )
    assert record["status"] == "blocked"
    assert "outside the applicability" in record["reason"]
    assert record["applicability"]["applicable"] is False
    assert "actions" not in record


def test_an_unknown_subtool_is_not_found_and_the_known_names_are_listed():
    record = S.run_subtool("no_such_flow", runner=_fixture("x"), write_record=False)
    assert record["status"] == "not_found"
    assert record["exit_code"] == S.EXIT_NOT_FOUND
    assert "open_browser" in record["reason"]
