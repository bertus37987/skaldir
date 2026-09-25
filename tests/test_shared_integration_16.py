"""Report 16 — the shared seams the integration waves left open, wired and measured.

Five hooks, each with the *defect* it closes named where it was measured:

1. **the attached chat's status band** lost the goal entirely (audit 10 §7 O1: at 80 columns with a
   real model name the band read ``model … · 00:00`` and said nothing about the run this chat was
   the surface of) — :meth:`chatbar.ChatBar.band_text` keeps a *known* goal, giving up an
   unmeasured price first and clipping the goal rather than dropping it;
2. **``skaldir cu check``** hard-coded ``live_desktop: false`` and warned that no live-desktop
   driver was wired (both stale — report 15 §6.1) — it now reports
   ``act_service.capability_report()``'s own measured record;
3. **the SUPERVISE selftest probe** never named the delegation seam (report 13 §4) — a
   constant-free probe now measures that a run's tool surface really offers ``spawn_subagents``
   and refuses it by name without a provider client;
4. **the HTTP act route** must forward the runner's target/opt-in/capture flags (report 15 §6.2)
   *without* widening a permission boundary — measured live against a real ``skaldir serve --http``;
5. **the compaction report** named neither the source nor the decision layer that judged it
   (report 14 §11.3) — the asker's provenance travels in the run report: names only, never a value.

The TypeSafe request shape is repaired in ``tests/test_jev_provider_compat.py`` (the documented
``questions`` **map** plus a verbatim fixture of the reference's own example) and
``tests/test_system_one_adapters.py``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest

from skaldir import chatbar, cli, cli_contract

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / ".venv" / "bin" / "skaldir"


@pytest.fixture
def session_runtime(tmp_path, monkeypatch):
    """An isolated runtime: no test here may touch the owner's ``GODSV_HOME``."""
    home = tmp_path / "home"
    (home / "godsv").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    monkeypatch.delenv("SKALDIR_CU_ALLOW_LIVE_DESKTOP", raising=False)
    monkeypatch.delenv("SKALDIR_CU_LIVE_TARGET", raising=False)
    return home / "godsv"


def _cli(argv: list[str], capsys) -> tuple[int, dict]:
    """One in-process CLI run through the real parser; returns ``(exit_code, envelope)``."""
    args = cli.build_parser().parse_args(list(argv))
    code = cli_contract.run_command(args, args.func)
    return int(code), json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# 1 — the attached chat's band never loses the goal                            #
# --------------------------------------------------------------------------- #


def _band(model: str, goal: Any, *, width: int = 80, unicode: bool = True) -> str:
    def probe() -> chatbar.Fact:
        return cast(chatbar.Fact, goal() if callable(goal) else goal)
    bar = chatbar.ChatBar(
        io.StringIO(), width=width, unicode=unicode, screen=True,
        model=lambda: chatbar.Fact(model, "env", ""), goal=probe,
    )
    # simulate a turn that measured nothing so the band shows its n/a facts
    bar.begin_turn()
    bar.end_turn()
    band = bar.band_text(width)
    assert len(band) == width, f"the band is exactly the width, got {len(band)}"
    assert "\n" not in band, "one row, one line: the frame's arithmetic depends on it"
    return band


def test_the_band_keeps_the_attached_goal_at_eighty_columns():
    """The measured defect: the band gave the goal up first, so an attached chat said nothing.

    The configuration is the audit's ``c1-attached-steer`` capture — a real model name plus a real
    attached goal label, 80 columns. Before the fix this band carried no ``goal`` segment at all.
    """
    band = _band("gv-acceptance-stub", chatbar.Fact("other-se WORKING", "runs_store", ""))
    assert "goal:" in band, band
    assert "other-se" in band, band          # the run is still named (clipped at worst)
    assert "goal: none" not in band, band    # and never replaced by `none`


def test_the_band_gives_up_an_unmeasured_price_before_a_real_goal():
    """``$ n/a`` is a *non-measurement*; the run this chat is the surface of is not."""
    band = _band("gv-acceptance-stub", chatbar.Fact("other-se WORKING", "runs_store", ""))
    assert "$ n/a" not in band, band
    assert "ctx n/a" in band and "tok/s n/a" in band and "00:00" in band


def test_a_session_without_a_goal_still_says_none_and_keeps_its_price():
    """The owner's wording is unchanged for a session that has no durable goal."""
    band = _band("gv-acceptance-stub", chatbar.fact_unknown("no goal is recorded"))
    assert "goal: none" in band, band
    assert "$ n/a" in band, band


def test_a_band_with_no_room_keeps_the_other_facts_whole():
    """No room for even a clipped goal: the band stays its width, wraps nothing, stays ASCII."""
    band = _band("gv-acceptance-stub", chatbar.Fact("other-se WORKING", "runs_store", ""),
                 width=34, unicode=False)
    assert "ctx n/a" in band and "00:00" in band
    assert all(ord(char) < 128 for char in band), band


def test_the_band_asks_the_real_shell_probe_and_agrees_with_it(session_runtime):
    """The layer split the audit asked for: the fact was never wrong, the layout dropped it.

    ``probe_goal(shell)`` → ``shell.session.probes.goal()`` is the *same* call the header makes,
    and the band must now agree with it instead of showing nothing.
    """
    from skaldir import repl
    from skaldir import runs_store as rs

    store = rs.RunsStore(rs.run_store_path(session_runtime))
    record = store.start_goal("attach me: the band must say which run", session_id="other-session")
    store.close()

    session = repl.Session(runtime=session_runtime, runner=lambda argv: 0, out=io.StringIO(),
                           err=io.StringIO())
    shell = repl.Shell(repl.ShellOptions(probe=False, attach=record.goal_id), session=session,
                       out=io.StringIO(), err=io.StringIO(), stdin=io.StringIO(""))
    label = shell.session.probes.goal().label
    assert label.startswith(record.goal_id[:8])
    band = _band("gv-acceptance-stub", lambda: chatbar.probe_goal(shell))
    assert record.goal_id[:8] in band, band


# --------------------------------------------------------------------------- #
# 2 — `cu check`: the measured opt-in, not a literal                           #
# --------------------------------------------------------------------------- #


def test_cu_check_reports_the_measured_opt_in_and_never_a_literal(session_runtime, capsys):
    """The two stale facts this hook closes: the hard-coded `false` and the false warning."""
    code, envelope = _cli(["cu", "check", "--json"], capsys)
    assert code == 0
    data = envelope["data"]
    # the legacy payload keeps what older consumers read ...
    assert "dry_run" in data["runners"] and "container_podman" in data["runners"]
    assert data["levels_reported"]
    # ... and gains act check's own measured record
    assert data["live_desktop"] is False, "no opt-in is set in this isolated runtime"
    assert data["live_desktop_optin"]["allowed"] is False
    assert "allow_live_desktop" in data["live_desktop_optin"]["reason"]
    assert data["verdict"] in {"isolated-only", "blocked"}
    assert {record["id"] for record in data["bridges"]} >= {
        "container-gui", "host-session", "live-desktop", "dry-run"
    }
    assert "observe.screenshot" in data["capability_names"]
    assert data["policy"]["allow_live_desktop"] is False
    assert data["policy"]["live_desktop_target"] == ""
    # the stale sentence is gone, and what replaced it names the switch
    warnings = " ".join(envelope["warnings"])
    assert "no live desktop driver is wired" not in warnings
    assert "measured refusal" in warnings and "allow_live_desktop" in warnings


def test_cu_check_flips_when_the_switch_and_a_target_are_really_set(session_runtime, capsys,
                                                                   monkeypatch):
    """`live_desktop` is measured: the switch alone IS a full-desktop opt-in (owner rule)."""
    monkeypatch.setenv("SKALDIR_CU_ALLOW_LIVE_DESKTOP", "1")
    _code, without_target = _cli(["cu", "check", "--json"], capsys)
    assert without_target["data"]["live_desktop"] is True, "switch alone = full desktop (owner rule)"
    assert without_target["data"]["live_desktop_optin"]["opted_in"] is True
    assert without_target["data"]["live_desktop_optin"]["allowed"] is True

    monkeypatch.setenv("SKALDIR_CU_LIVE_TARGET", "a-window-of-mine")
    _code, with_target = _cli(["cu", "check", "--json"], capsys)
    assert with_target["data"]["live_desktop"] is True
    assert with_target["data"]["verdict"] == "live-desktop-opted-in"
    assert with_target["data"]["live_desktop_optin"]["target"] == "a-window-of-mine"


def test_the_cu_check_contract_documents_the_new_facts_without_granting_one():
    """The schema documents the *response*; the request side stays empty, so nothing widens."""
    spec = cli_contract.COMMANDS["cu.check"]
    schema = spec.data_schema
    for field in ("bridges", "verdict", "live_desktop_optin", "policy", "summary"):
        assert field in schema["properties"], field
    assert set(schema["required"]) >= {"runners", "live_desktop", "bridges", "verdict"}
    assert "no container is started" in spec.notes

    parser = cli.build_parser()
    groups = next(item for item in parser._actions  # noqa: SLF001 — argparse's own group
                  if isinstance(item, argparse._SubParsersAction))  # noqa: SLF001
    cu = groups.choices["cu"]
    cu_verbs = next(item for item in cu._actions  # noqa: SLF001
                    if isinstance(item, argparse._SubParsersAction))  # noqa: SLF001
    # the legacy verb takes NO arguments that could opt in to anything: the request side is
    # empty (only the annotated common flags exist), so this schema change grants no privilege
    names = [row["dest"] for row in cli_contract.args_of(cu_verbs.choices["check"])]
    assert not any("live" in name for name in names), names


# --------------------------------------------------------------------------- #
# 3 — selftest: the delegation production check                                #
# --------------------------------------------------------------------------- #


def _supervise_probe(tmp_path) -> dict:
    _ok, _reason, probes = cli_contract._probe_supervise(tmp_path)  # noqa: SLF001 — the seam
    by_name = {probe.name: probe for probe in probes}
    assert "delegation_surface" in by_name, sorted(by_name)
    return by_name["delegation_surface"].as_dict()


def test_the_supervise_probe_measures_the_delegation_surface(tmp_path):
    """Not an import check: the probe reads the real tool list a run offers its model."""
    probe = _supervise_probe(tmp_path)
    assert probe["ok"] is True, probe
    assert "spawn_subagents" in probe["detail"]
    assert "explore, worker, verifier" in probe["detail"]
    assert "refuses it by name without one" in probe["detail"]
    assert probe["evidence_ref"] == "src/skaldir/goal_run.py:291"


def test_the_delegation_probe_fails_when_the_run_stops_offering_the_tool(tmp_path, monkeypatch):
    """The regression bites: a seam that is not wired turns the probe red, not green."""
    from skaldir import goal_run

    monkeypatch.setattr(goal_run, "turn_tools", lambda *args, **kwargs: [])
    probe = _supervise_probe(tmp_path)
    assert probe["ok"] is False, "a run that offers nothing must not pass the probe"
    assert "does not offer" in probe["detail"]


def test_selftest_publishes_the_delegation_probe_for_the_supervise_layer(session_runtime, capsys):
    """Reachable in the product's own self-description — what the layer claim rests on."""
    _code, envelope = _cli(["selftest", "--json"], capsys)
    layers = {row["layer"]: row for row in envelope["data"]["layers"]}
    supervise = layers["SUPERVISE"]
    probe = next(item for item in supervise["probes"] if item["name"] == "delegation_surface")
    assert probe["ok"] is True, probe
    assert any("delegation_surface" in row["note"] for row in supervise["evidence"])


def test_the_act_probe_states_the_capture_capability(session_runtime, capsys):
    """Report 15 §6.4: the ACT probe names pixel evidence instead of leaving it to the bridges."""
    _code, envelope = _cli(["selftest", "--json"], capsys)
    act = next(row for row in envelope["data"]["layers"] if row["layer"] == "ACT")
    desktop = next(item for item in act["probes"] if item["name"] == "live_desktop")
    assert "observe.screenshot=" in desktop["detail"], desktop
    assert "host.session.screenshot=" in desktop["detail"], desktop


# --------------------------------------------------------------------------- #
# 4 — the live HTTP route: target, screenshot and opt-in forward               #
# --------------------------------------------------------------------------- #

def _request_document(root: Path, name: str = "request.json") -> str:
    """One declared action list on disk — the shape the route's ``--actions`` argument names.

    ``--actions`` is a **path**, over HTTP as on the CLI (the route is a thin argv bridge), so a
    request document is written here; the step is one the in-process fixture can verify.
    """
    payload = {
        "goal": "an HTTP-declared goal",
        "actions": [{
            "kind": "cli",
            "channel": "api",
            "argv": ["echo", "ok"],
            "description": "one step the fixture can answer",
            "verify": {"expect_stdout_contains": "dry-run"},
        }],
        "end_state": {"expect_observe_contains": "AFTER"},
    }
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _post(url: str, body: dict, token: str = "") -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=90) as response:
        return json.loads(response.read())


@pytest.fixture
def live_server(tmp_path):
    """A real ``skaldir serve --http`` on a free port, its own runtime and a throwaway token.

    The token is generated *here* (a literal, written 0600) so no secret of the owner's is read or
    printed: the server's own generated token would have to be scraped out of its stderr.
    """
    runtime = tmp_path / "rt"
    runtime.mkdir()
    token_path = tmp_path / "api.token"
    token = "test-token-not-a-credential"
    token_path.write_text(token + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    port = _free_port()
    env = dict(os.environ, GODSV_HOME=str(runtime), HERMES_HOME=str(tmp_path))
    proc = subprocess.Popen(
        # the runtime IS `GODSV_HOME` here: `serve` has no `--runtime` switch, which is what
        # keeps this test off the owner's runtime
        [str(ENTRY), "serve", "--http", "--port", str(port), "--token-file", str(token_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT), env=env,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 120
        ready = False
        while time.time() < deadline and proc.poll() is None:
            try:
                _get(f"{base}/health")
                ready = True
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        assert ready, f"the HTTP surface never answered /health (exit {proc.poll()})"
        yield base, token, runtime
    finally:
        proc.terminate()
        try:
            _stdout, stderr = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            _stdout, stderr = proc.communicate(timeout=60)
        # the throwaway token is the one that was used, and it never reached stdout
        assert token not in _stdout


def test_the_act_route_exposes_the_capture_and_opt_in_arguments(live_server):
    """The GUI/API contract of the route: the flags report 15 §6.2 asks for are really there."""
    base, _token, _runtime = live_server
    document = _get(f"{base}/openapi.json")
    assert "/cli/act/run" in document["paths"], sorted(document["paths"])[:8]
    request_schema = json.dumps(document["paths"]["/cli/act/run"]["post"])
    for name in ("allow_live_desktop", "live_target", "screenshot", "runner", "actions"):
        assert name in request_schema, name


def test_a_mutating_act_run_needs_the_token_and_changes_nothing(live_server, tmp_path):
    """This route's permission boundary is the bearer token, and it is enforced."""
    base, token, runtime = live_server
    body = {"actions": _request_document(tmp_path), "runner": "dry-run"}
    status, envelope = _post(f"{base}/cli/act/run", body)
    assert status == 401, envelope
    assert not (runtime / "cu_runs.jsonl").exists(), "an untokened call must not have run anything"

    # with the token the run really happens: verified *actions*, fixture scope, and the end
    # state the fixture cannot observe is reported as unseen (exit 2 / HTTP 422 — not a pass)
    status, envelope = _post(f"{base}/cli/act/run", body, token=token)
    assert status == 422, envelope
    data = envelope["data"]
    assert data["runner"]["bridge"] == "dry-run"
    assert data["scope"] == "in-process fixture"
    assert data["live"] is False
    assert [row["status"] for row in data["actions"]] == ["verified"]
    assert data["end_state"]["ok"] is False
    assert data["verified"] is False


def test_the_route_forwards_the_opt_in_and_one_flag_alone_widens_nothing(live_server, tmp_path):
    """`allow_live_desktop` reaches the CLI (its own words change); a target is still required.

    Two *distinguishable* refusals prove the value travelled, and the second one is the boundary:
    the flag alone never attaches to the owner's session. This test never touches ``:0``.
    """
    base, token, _runtime = live_server
    actions = _request_document(tmp_path)
    body = {"actions": actions, "runner": "live-desktop"}
    _status, off = _post(f"{base}/cli/act/run", body, token=token)
    assert "opt-in is off" in json.dumps(off)

    status, on = _post(f"{base}/cli/act/run", dict(body, allow_live_desktop=True), token=token)
    reason = json.dumps(on)
    assert "no target is named" in reason, reason
    assert "opt-in is off" not in reason
    assert on["data"]["status"] == "blocked" and on["data"]["verified"] is False
    assert status != 200, "a blocked run is not a success"

    _status, unknown = _post(f"{base}/cli/act/run", {"actions": actions, "screenshotzzz": True},
                             token=token)
    assert "unknown argument" in json.dumps(unknown), unknown


def test_the_route_forwards_the_screenshot_flag_into_the_run_record(live_server, tmp_path):
    """`screenshot: true` is observable: the run records the capture attempt and its reason.

    With the in-process fixture there is no display, so the *attempt* is the evidence — and an
    attempt that cannot happen is reported as absent with its reason, never as an empty file.
    """
    base, token, _runtime = live_server
    actions = _request_document(tmp_path)
    status, envelope = _post(f"{base}/cli/act/run",
                             {"actions": actions, "runner": "dry-run", "screenshot": True},
                             token=token)
    assert status == 422, envelope  # exit 2: the fixture cannot observe the end state
    warnings = " ".join(envelope["data"].get("warnings") or [])
    assert "screenshot could not be taken" in warnings, envelope["data"]
    assert "nothing to capture" in warnings, warnings
    # the lifecycle report travels on every run, verified or not
    assert envelope["data"]["cleanup"]["closed"] is True
    assert envelope["data"]["cleanup"]["still_present"] is False

    _status, plain = _post(f"{base}/cli/act/run", {"actions": actions, "runner": "dry-run"},
                           token=token)
    assert "screenshot" not in " ".join(plain["data"].get("warnings") or [])


def test_the_route_serves_the_capability_matrix_the_gui_reads(live_server):
    """The productive read-only route: the same measured matrix, over HTTP, token-free."""
    base, _token, _runtime = live_server
    data = _get(f"{base}/cli/act/check")["data"]
    assert "observe.screenshot" in data["capability_names"]
    assert {row["id"] for row in data["bridges"]} >= {"host-session", "live-desktop"}
    assert data["live_desktop_optin"]["allowed"] is False  # nothing opted in on this host


# --------------------------------------------------------------------------- #
# 5 — the compaction report names the decision layer that judged it            #
# --------------------------------------------------------------------------- #


def _decision_runtime(tmp_path) -> Path:
    runtime = tmp_path / "rt"
    runtime.mkdir()
    (runtime / "config.yaml").write_text(
        "godsv:\n"
        "  jev:\n"
        "    provider: openai-compatible\n"
        "    base_url: http://127.0.0.1:9/v1\n"
        "    model: stub-decision-model\n"
        "    api_key_env: SKALDIR_STUB_DECISION_KEY\n"
        "    system_one: llm\n"
        "    stream: false\n",
        encoding="utf-8",
    )
    return runtime


def test_the_run_report_names_the_decision_layer_that_judged_compaction(tmp_path, monkeypatch,
                                                                       capsys):
    """One line where the asker is built, and the same fact in the report — names only."""
    from skaldir import goal_run_cli as G

    monkeypatch.setenv("SKALDIR_STUB_DECISION_KEY", "stub-local-key")
    # the asker is resolved from the *runtime's own* config (the Jev role's block), so the
    # runtime is the home this test points at — never the owner's
    monkeypatch.setenv("GODSV_HOME", str(_decision_runtime(tmp_path)))
    args = argparse.Namespace(compaction_asker="provider", probability=0.05)
    asker = G._asker_for(args)  # noqa: SLF001 — the seam report 14 named
    printed = capsys.readouterr().err
    assert "compaction asker" in printed and "decision_layer=llm" in printed, printed

    provenance = G._asker_provenance(asker)  # noqa: SLF001
    assert provenance["source"] == "godsv.jev", provenance
    assert provenance["decision_layer"] == "llm"  # the compatibility backend, labelled as one
    assert provenance["model"] == "stub-decision-model"
    assert provenance["api_key_env"] == "SKALDIR_STUB_DECISION_KEY"  # the NAME, never a value
    assert "stub-local-key" not in json.dumps(provenance)

    # the report itself carries it (the writer is `_finish_run`), and nothing else does
    captured: dict = {}
    monkeypatch.setattr(G, "_report", lambda payload, evidence=(): captured.update(payload))
    monkeypatch.setattr(G, "_render", lambda payload: None)
    progress = argparse.Namespace(settle=lambda *a, **k: None, close=lambda: None)
    report = {"goal_id": "g", "verify": {}, "exit_code": 3}
    assert G._finish_run(report, state_dir=tmp_path, status={}, progress=progress,  # noqa: SLF001
                         asker=asker) == G.EXIT_BLOCKED
    assert captured["compaction_asker"] == provenance
    no_asker = {"goal_id": "g", "verify": {}, "exit_code": 3}
    G._finish_run(no_asker, state_dir=tmp_path, status={}, progress=progress)  # noqa: SLF001
    assert "compaction_asker" not in no_asker, "an empty object is not a measurement"


def test_the_fixture_asker_is_labelled_a_simulation():
    """A simulated decision still says so — the id of the asker is not the evidence."""
    from skaldir import goal_run_cli as G

    asker = G._asker_for(argparse.Namespace(compaction_asker="fixture", probability=0.05))  # noqa: SLF001
    provenance = G._asker_provenance(asker)  # noqa: SLF001
    assert provenance["source"] == "fixture" and provenance["simulation"] is True
    assert G._asker_provenance(object()) == {}, "an asker with no provenance reports none"  # noqa: SLF001
    assert G._asker_provenance(None) == {}  # noqa: SLF001


def test_the_run_contract_documents_the_asker_provenance():
    from skaldir import goal_run_cli as G

    schema = G.contract_entries()["run"].data_schema
    asker = schema["properties"]["compaction_asker"]
    assert "api_key_env" in asker["properties"]
    assert "never its value" in asker["description"]
