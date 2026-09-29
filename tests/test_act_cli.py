"""``skaldir act …`` — the CLI surface: envelope, exit codes, contract entries, wiring.

Evidence class: CLI contract tests through the real ``cli_contract`` envelope. The group is
registered on a parser built here *and* on the product parser (``cli.build_parser()``), which
is exactly the one-line wiring the CLI owner adds — so these tests prove the snippet works
against the real parser instead of claiming it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from skaldir import act_cli, cli, cli_contract
from skaldir import act_service as S

ACT_COMMANDS = (
    "act.check",
    "act.run",
    "act.subtool.save",
    "act.subtool.list",
    "act.subtool.show",
    "act.subtool.run",
    "act.live-setup",
)


@pytest.fixture
def act_home(tmp_path, monkeypatch):
    """An isolated runtime: nothing here may touch the owner's GODSV_HOME."""
    home = tmp_path / "home"
    (home / "godsv").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    # env.py prefers SKALDIR_HOME over the legacy GODSV_HOME: a leaked SKALDIR_HOME in
    # the session would send `--state`-less verbs (save/list/run) to the owner's runtime,
    # where a leftover <name>.json turns the first save into an exit-6 conflict.
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return tmp_path


def _build_parser() -> argparse.ArgumentParser:
    """A stand-in for ``skaldir``: same wiring, minus the 28 other command groups."""
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=False)
    act_cli.register_cli(sub)
    cli_contract.annotate(parser)
    return parser


def _run(argv: list[str], capsys) -> tuple[int, Any, str, str]:
    """Run one ``act`` invocation.

    Returns ``(exit_code, envelope|None, stdout, stderr)`` — in ``--json`` mode stdout holds
    exactly one envelope (parsed here) and the human view moves to stderr.
    """
    parser = _build_parser()
    args = parser.parse_args(["act"] + list(argv))
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    stream = captured.out.strip()
    document = json.loads(stream) if stream.startswith("{") else None
    return int(code), document, captured.out, captured.err


def _subtool(tmp_path: Path, **overrides) -> Path:
    spec = {
        "name": "probe_flow",
        "version": "1.0",
        "description": "a stored flow whose checks are observable on the display",
        "applicability": {"app": "probe", "app_versions": [">=1.0,<2"], "locales": ["en", "*"]},
        "preconditions": [{"id": "display_ready", "text": "READY", "source": "window_title"}],
        "anchors": [{"id": "target", "text": "TARGET-WINDOW", "source": "window_title"}],
        "actions": [
            {
                "kind": "cli",
                "channel": "api",
                "argv": ["echo", "ok"],
                "description": "do the thing",
                "verify": {"expect_stdout_contains": "ok"},
            }
        ],
        "end_state": {
            "channel": "window_title",
            "description": "the expected window is there",
            "expect_observe_contains": "AFTER-WINDOW",
        },
        "verification_contract": "the display shows the window the flow leaves behind",
    }
    spec.update(overrides)
    target = tmp_path / "probe_flow.json"
    target.write_text(json.dumps(spec), encoding="utf-8")
    return target


def _request(tmp_path: Path, name: str, payload) -> Path:
    target = tmp_path / name
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- #
# the wiring contract                                                           #
# --------------------------------------------------------------------------- #


def test_register_cli_is_idempotent_and_returns_the_same_group():
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    first = act_cli.register_cli(sub)
    second = act_cli.register_cli(sub)  # the "conflicting subparser" trap, defused
    assert first is second
    assert "act" in sub.choices


def test_the_group_registers_its_own_contract_entries():
    _build_parser()
    for command in ACT_COMMANDS:
        assert command in cli_contract.COMMANDS
        spec = cli_contract.spec_for(command)
        assert spec.layer == "ACT"
        assert spec.evidence
    assert "act.check" in cli_contract.cli_commands_for_layer("ACT")


def test_the_wiring_snippet_against_the_real_parser_leaves_no_unregistered_command():
    """The one line the CLI owner adds: ``register_cli`` on the real subparsers handle."""
    parser = cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions  # noqa: SLF001 — argparse keeps them here
        if isinstance(action, argparse._SubParsersAction)
    )
    before = set(subparsers.choices)
    act_cli.register_cli(subparsers)
    assert set(subparsers.choices) == before | {"act"}
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == []
    dumped = {item["command"] for item in inventory["commands"]}
    assert set(ACT_COMMANDS) <= dumped
    for item in inventory["commands"]:
        if item["command"].startswith("act."):
            assert item["layer"] == "ACT"
            assert item["has_json_flag"] is True
            assert item["args"]


# --------------------------------------------------------------------------- #
# act check                                                                     #
# --------------------------------------------------------------------------- #


def test_act_check_reports_the_matrix_as_one_envelope(act_home, capsys):
    code, document, out, _err = _run(["check", "--json"], capsys)
    assert code == 0
    assert document["ok"] is True
    assert document["command"] == "act.check"
    assert document["exit_code"] == 0
    assert document["evidence"]
    data = document["data"]
    assert data["live_desktop"] is False
    assert data["verdict"] in ("isolated-only", "blocked")
    assert {record["id"] for record in data["bridges"]} == set(S.BRIDGES)
    assert data["capability_names"]


def test_act_check_with_require_exits_3_and_names_the_missing_capability(act_home, capsys):
    record = S.capability_status("observe.screenshot")
    code, document, out, _err = _run(["check", "--require", "observe.screenshot", "--json"], capsys)
    expected = 0 if record["available"] else 3
    assert code == expected
    if expected == 3:
        assert document["error"]["code"] == "blocked"
        assert document["data"]["required"]["unavailable"][0]["name"] == "observe.screenshot"
        assert document["data"]["required"]["unavailable"][0]["missing_capability"]
        assert document["next_actions"]


def test_act_check_with_an_unknown_require_is_blocked_with_the_known_names(act_home, capsys):
    code, document, out, _err = _run(["check", "--require", "click.very.hard", "--json"], capsys)
    assert code == 3
    assert "unknown capability" in document["data"]["required"]["unavailable"][0]["reason"]


def test_act_check_human_table_lists_bridges_and_capabilities(act_home, capsys):
    code, document, out, _err = _run(["check"], capsys)
    assert code == 0
    assert document is None
    assert "bridges:" in out and "capabilities:" in out
    assert "live desktop: never driven" in out
    assert "window.list" in out


# --------------------------------------------------------------------------- #
# act run                                                                       #
# --------------------------------------------------------------------------- #


def _dry_request(tmp_path: Path, **overrides) -> Path:
    """A request whose step the *fixture* can verify (it answers ``dry-run`` as its stdout).

    The fixture's stdout is scripted in-process, so ``dry-run`` is the one expectation it can
    satisfy — everything else about the run stays real, including the end-state check.
    """
    payload = {
        "goal": "a declared goal",
        "actions": [
            {
                "kind": "cli",
                "channel": "api",
                "argv": ["echo", "ok"],
                "description": "one step the fixture can answer",
                "verify": {"expect_stdout_contains": "dry-run"},
            }
        ],
        "end_state": {"expect_observe_contains": "AFTER"},
    }
    payload.update(overrides)
    return _request(tmp_path, "request.json", payload)


def test_the_fixture_runner_can_never_report_a_real_end_state(act_home, capsys):
    """`--runner dry-run` is a fixture: it drives no display, so the end state stays unseen."""
    request = _dry_request(act_home)
    code, document, _out, err = _run(
        ["run", "--actions", str(request), "--runner", "dry-run", "--json"], capsys
    )
    assert code == 2
    assert document["command"] == "act.run"
    data = document["data"]
    assert data["live"] is False
    assert data["runner"]["fixture"] is True
    assert data["runner"]["scope"] == "in-process fixture"
    assert [row["status"] for row in data["actions"]] == ["verified"]
    assert data["end_state"]["ok"] is False
    assert data["end_state"]["observed"] == ""
    assert data["verified"] is False
    assert any("in-process fixture" in warning for warning in document["warnings"])


def test_act_run_refuses_an_action_that_declares_no_verification(act_home, capsys):
    request = _request(
        act_home,
        "bad.json",
        {"actions": [{"kind": "cli", "argv": ["echo", "ok"]}], "end_state": {"x": "y"}},
    )
    code, document, _out, _err = _run(
        ["run", "--actions", str(request), "--runner", "dry-run", "--json"], capsys
    )
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "verify" in document["error"]["message"]


def test_act_run_exits_2_when_the_declared_end_state_is_not_there(act_home, capsys):
    # no end_state in the request, so --expect-end-state is the declaration under test
    request = _dry_request(act_home, end_state=None)
    code, document, _out, _err = _run(
        [
            "run",
            "--actions",
            str(request),
            "--runner",
            "dry-run",
            "--expect-end-state",
            "NEVER-ON-THE-DISPLAY",
            "--json",
        ],
        capsys,
    )
    assert code == 2
    assert document["error"]["code"] == "verification_failed"
    assert document["data"]["end_state"]["ok"] is False
    assert document["data"]["end_state"]["missing"] == ["NEVER-ON-THE-DISPLAY"]


def test_act_run_exits_3_when_no_real_bridge_can_carry_it(act_home, capsys, monkeypatch):
    monkeypatch.setattr(S, "_gui_image_present", lambda: (False, f"image missing: {S.GUI_IMAGE}"))
    monkeypatch.setattr(S, "_sandbox_available", lambda: False)
    request = _dry_request(act_home)
    code, document, _out, _err = _run(["run", "--actions", str(request), "--json"], capsys)
    assert code == 3
    assert document["error"]["code"] == "blocked"
    assert "no real bridge" in document["error"]["message"]
    assert document["next_actions"]


def test_act_run_reads_the_request_from_stdin(act_home, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_payload())))
    code, document, _out, _err = _run(["run", "--runner", "dry-run", "--json"], capsys)
    assert code == 2  # read fine; the fixture cannot show the end state
    assert document["data"]["declared"] == 1
    assert document["data"]["live"] is False


def _payload() -> dict:
    return {
        "actions": [
            {
                "kind": "cli",
                "channel": "api",
                "argv": ["echo", "ok"],
                "verify": {"expect_stdout_contains": "dry-run"},
            }
        ],
        "end_state": {"expect_observe_contains": "AFTER"},
    }


def test_act_run_without_a_request_is_a_usage_error(act_home, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, document, _out, _err = _run(["run", "--runner", "dry-run", "--json"], capsys)
    assert code == 1
    assert document["error"]["code"] == "usage"


# --------------------------------------------------------------------------- #
# act subtool                                                                   #
# --------------------------------------------------------------------------- #


def test_act_subtool_list_shows_the_builtins_and_the_store(act_home, capsys):
    code, document, _out, _err = _run(["subtool", "list", "--json"], capsys)
    assert code == 0
    data = document["data"]
    assert data["count"] == 7
    assert set(data["builtin"]) == {
        "open_browser",
        "open_youtube",
        "focus_search_bar",
        "open_website",
        "open_project",
        "open_settings",
        "host_window_state",
    }
    assert Path(data["directory"]).name == S.DEFAULT_STATE_SUBDIR
    assert all(row["origin"] == "builtin" for row in data["subtools"])


def test_act_subtool_show_returns_the_schema_the_owner_specified(act_home, capsys):
    code, document, _out, _err = _run(["subtool", "show", "open_browser", "--json"], capsys)
    assert code == 0
    data = document["data"]
    assert list(data["spec"]) == list(S.SUBTOOL_KEYS)
    assert data["origin"] == "builtin"
    assert data["valid"] is True and data["problems"] == []
    assert data["spec"]["end_state"]["expect_observe_contains"]
    assert data["spec"]["preconditions"][0]["source"] in S.ANCHOR_SOURCES


def test_act_subtool_show_unknown_is_not_found_and_names_the_known_ones(act_home, capsys):
    code, document, _out, _err = _run(["subtool", "show", "no_such_flow", "--json"], capsys)
    assert code == 5
    assert document["error"]["code"] == "not_found"
    assert "open_browser" in document["data"]["known"]


def test_act_subtool_save_stores_refuses_a_conflict_and_replaces_with_force(act_home, capsys):
    source = _subtool(act_home)
    code, document, _out, _err = _run(["subtool", "save", "--file", str(source), "--json"], capsys)
    assert code == 0
    stored = Path(document["data"]["stored"])
    assert stored.is_file() and stored.name == "probe_flow.json"

    code, document, _out, _err = _run(["subtool", "save", "--file", str(source), "--json"], capsys)
    assert code == 6
    assert document["error"]["code"] == "conflict"

    code, document, _out, _err = _run(
        ["subtool", "save", "--file", str(source), "--force", "--json"], capsys
    )
    assert code == 0
    assert document["data"]["replaced"] is True


def test_act_subtool_save_refuses_an_invalid_declaration_with_every_reason(act_home, capsys):
    broken = _subtool(act_home, name="broken_flow", anchors=[], end_state={})
    code, document, _out, _err = _run(["subtool", "save", "--file", str(broken), "--json"], capsys)
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "anchors" in document["error"]["message"]
    assert "end_state" in document["error"]["message"]


def test_act_subtool_run_reobserves_when_the_anchor_is_not_observed(act_home, capsys):
    source = _subtool(act_home)
    assert _run(["subtool", "save", "--file", str(source), "--json"], capsys)[0] == 0
    code, document, _out, _err = _run(
        ["subtool", "run", "probe_flow", "--runner", "dry-run", "--json"], capsys
    )
    assert code == 2
    assert document["error"]["code"] == "verification_failed"
    assert document["data"]["missing_anchors"]
    assert "actions" not in document["data"]


def test_act_subtool_run_reobserves_when_an_app_state_needle_is_absent(act_home, capsys):
    """``focus_search_bar`` needs 'Search' in the app state — the channel works,
    the needle does not."""
    code, document, _out, _err = _run(
        ["subtool", "run", "focus_search_bar", "--runner", "dry-run", "--json"], capsys
    )
    assert code == 2
    assert document["error"]["code"] == "verification_failed"
    assert document["data"]["missing_anchors"]


def test_act_subtool_run_with_an_inapplicable_app_version_is_blocked(act_home, capsys):
    source = _subtool(act_home)
    assert _run(["subtool", "save", "--file", str(source), "--json"], capsys)[0] == 0
    code, document, _out, _err = _run(
        [
            "subtool",
            "run",
            "probe_flow",
            "--runner",
            "dry-run",
            "--app-version",
            "9.9",
            "--json",
        ],
        capsys,
    )
    assert code == 3
    assert document["data"]["applicability"]["applicable"] is False
    assert "outside the applicability" in document["error"]["message"]


def test_act_subtool_run_unknown_is_not_found(act_home, capsys):
    code, document, _out, _err = _run(
        ["subtool", "run", "no_such_flow", "--runner", "dry-run", "--json"], capsys
    )
    assert code == 5
    assert document["error"]["code"] == "not_found"


def test_act_subtool_list_human_output_names_origin(act_home, capsys):
    code, document, out, _err = _run(["subtool", "list"], capsys)
    assert code == 0
    assert "builtin" in out
    assert "open_browser" in out


def test_act_subtool_run_accepts_the_live_desktop_opt_in_flags(act_home, capsys):
    """`subtool run --runner live-desktop` must carry the same opt-in as `act run`."""
    parser = _build_parser()
    args = parser.parse_args(
        [
            "act",
            "subtool",
            "run",
            "open_browser",
            "--runner",
            "live-desktop",
            "--allow-live-desktop",
            "--live-target",
            "Firefox",
            "--json",
        ]
    )
    assert args.allow_live_desktop is True
    assert args.live_target == "Firefox"
    assert args.runner == "live-desktop"


def test_act_live_setup_reports_without_writing(act_home, capsys):
    code, document, _out, _err = _run(["live-setup", "--json"], capsys)
    assert code == 0
    data = document["data"]
    assert data["allowed_now"] is False
    assert data["write"] is None
    assert data["next_steps"]
    assert "optin" in data
    settings = act_home / "home" / "godsv" / "settings.yaml"
    assert not settings.exists()


def test_act_live_setup_writes_one_target_atomically(act_home, capsys):
    code, document, _out, _err = _run(
        ["live-setup", "--target", "Firefox", "--write", "--json"], capsys
    )
    assert code == 0
    data = document["data"]
    assert data["write"]["wrote"] is True
    assert data["write"]["target"] == "Firefox"
    settings = act_home / "home" / "godsv" / "settings.yaml"
    assert settings.is_file()
    body = settings.read_text(encoding="utf-8")
    assert "allow_live_desktop: true" in body
    assert "live_desktop_target: Firefox" in body
    backups = list(settings.parent.glob("settings.yaml.bak-*"))
    assert data["write"]["backup"] == "" or backups


def test_act_live_setup_empty_target_means_full_desktop(act_home, capsys):
    """Owner's rule: live-desktop may drive *everything*; empty target = full desktop."""
    code, document, _out, _err = _run(["live-setup", "--write", "--json"], capsys)
    assert code == 0
    data = document["data"]
    assert (data.get("write") or {}).get("wrote") is True
    assert (data.get("write") or {}).get("target") in ("*", "all", "full", "")
    assert data.get("allowed_now") is True
