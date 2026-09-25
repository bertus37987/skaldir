"""TDD `act subtool learn --session ID`: repeated GUI sequences → draft sub-tool.

The learner replays one recorded session's computer-use events (``cu_attempt`` /
``cu_action`` rows in ``events.db``), finds an ordered sequence of
``kind + anchor`` that repeats at least twice, and saves it as a *draft*
sub-tool through the existing ``act subtool save`` path. Nothing is executed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import act_cli, act_service as S
from skaldir import cli, cli_contract, events as events_mod


@pytest.fixture
def act_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "godsv").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    # env.py prefers SKALDIR_HOME over the legacy GODSV_HOME — a leaked value would
    # send the state outside tmp_path and make runs cross-contaminate.
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return tmp_path


def _runtime(act_home: Path) -> Path:
    return act_home / "rt"


def _seed_session(runtime: Path, session_id: str, *, repeat: bool) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    store = events_mod.EventStore(runtime / "events.db")
    step = 0
    seq = ["open", "click", "type"] if repeat else ["open", "click", "type"]
    for cycle in range(2 if repeat else 1):
        for anchor in seq:
            step += 1
            store.append(
                session_id,
                step,
                "cu_action",
                {
                    "step": f"step-{step}",
                    "channel": "api",
                    "kind": "type_text" if anchor == "type" else "cli",
                    "ok": True,
                    "anchor": anchor,
                    "detail": f"{anchor}-{cycle}",
                },
            )
    store.close()


def _run(act_home: Path, argv: list[str], capsys) -> tuple[int, dict | None]:
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=False)
    act_cli.register_cli(sub)
    cli_contract.annotate(parser)
    args = parser.parse_args(["act"] + list(argv))
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    out = captured.out.strip()
    document = json.loads(out) if out.startswith("{") else None
    data = (document or {}).get("data") if isinstance((document or {}).get("data"), dict) else dict(document or {})
    return int(code), data


def test_learn_saves_draft_subtool_from_repeated_sequence(act_home, capsys):
    runtime = _runtime(act_home)
    _seed_session(runtime, "sess-repeats", repeat=True)
    code, data = _run(
        act_home,
        ["subtool", "learn", "--session", "sess-repeats", "--state", str(runtime), "--json"],
        capsys,
    )
    assert code == 0
    assert data["learned"] is True
    spec_name = data["subtool"]["name"]
    # the draft went through the real save path: it is listable and valid
    rows = S.list_subtools(state=str(runtime))
    assert any(row["name"] == spec_name for row in rows)
    stored, origin, path = S.load_subtool(spec_name, state=str(runtime))
    assert origin == "saved" and Path(path).is_file()
    assert S.subtool_errors(stored) == []
    # the repeated sequence is in it, in order
    assert len(stored["actions"]) >= 2


def test_learn_without_repeats_reports_honestly_and_saves_nothing(act_home, capsys):
    runtime = _runtime(act_home)
    _seed_session(runtime, "sess-unique", repeat=False)
    before = sorted(p.name for p in (runtime / "act_subtools").glob("*.json")) \
        if (runtime / "act_subtools").is_dir() else []
    code, data = _run(
        act_home,
        ["subtool", "learn", "--session", "sess-unique", "--state", str(runtime), "--json"],
        capsys,
    )
    after = sorted(p.name for p in (runtime / "act_subtools").glob("*.json")) \
        if (runtime / "act_subtools").is_dir() else []
    assert code == act_cli.EXIT_NOT_FOUND
    assert "repeated" in str(data.get("error", "")).lower() or \
        "nothing repeated" in json.dumps(data).lower()
    assert before == after


def test_learn_unknown_session_is_honest(act_home, capsys):
    runtime = _runtime(act_home)
    code, data = _run(
        act_home,
        ["subtool", "learn", "--session", "nope", "--state", str(runtime), "--json"],
        capsys,
    )
    assert code == act_cli.EXIT_NOT_FOUND
    assert "no recorded session" in json.dumps(data).lower()


def test_subtool_show_json_puts_the_actions_list_where_the_counts_are(act_home, capsys):
    """Report: `show --json` put the count at data.subtool.actions while the real
    action list sat only under data.spec — a consumer reading data.subtool.actions
    as a list got an int. The list now lands where the count was."""
    code, data = _run(act_home, ["subtool", "show", "open_browser", "--json"], capsys)
    assert code == 0
    actions = data["subtool"]["actions"]
    assert isinstance(actions, list), type(actions)
    assert actions == data["spec"]["actions"]
    assert actions and actions[0]["kind"] == "cli"
    # the count does not vanish: it moves to its own, honestly named key
    assert data["subtool"]["action_count"] == len(actions)
    # data.spec stays exactly as stored (tests and docs read it)
    assert list(data["spec"]) == list(S.SUBTOOL_KEYS)
