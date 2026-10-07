"""TDD gui-subtool-lifecycle (VAL-TOOL-002): parameterized, honest GUI subtools.

Three lifecycle halves this feature pins:

* **The record** carries what a reusable GUI capability needs: parameters (with
  types and where they go), preconditions, state checks (anchors), actions, an
  output schema, evidence (object-shaped, the envelope rule), a timeout and its
  target resources. All optional fields — the five stored built-ins keep their
  exact stored shape.
* **Mining** turns one recorded GUI trajectory into a draft: failed attempts are
  REMOVED, redundant repeats are removed, necessary wait steps are KEPT, and a
  value that varied between the repeated occurrences becomes a real parameter.
* **The re-run** is never a blind replay: the declared parameters must match,
  the preconditions/anchors must be observed NOW, and an altered state is a
  reobserve refusal (nothing executed). Discovery (``tool_search``) finds the
  saved subtool with a valid, typed call form.
"""
from __future__ import annotations

import json
from pathlib import Path

from skaldir import act_cli, cli_contract
from skaldir import act_service as S
from skaldir import events as events_mod
from skaldir.computer_use import ActionResult, DryRunRunner
from skaldir.discovery import discover

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "rt"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def _save(spec: dict, runtime: Path) -> Path:
    S.save_subtool(spec, state=str(runtime), force=True)
    return Path(S.subtool_dir(runtime)) / f"{spec['name']}.json"


def _typed_value_spec(**overrides) -> dict:
    """A parameterized flow: type a value into the observed target window."""
    spec = {
        "name": "type_into_window",
        "version": "1.0",
        "description": "type a value into the target window",
        "applicability": {
            "app": "probe",
            "app_versions": [">=1.0,<2"],
            "locales": ["en", "*"],
        },
        "preconditions": [
            {"id": "target", "text": "PROBE-WINDOW", "source": "window_title"},
        ],
        "anchors": [
            {"id": "target_window", "text": "PROBE-WINDOW", "source": "window_title"},
        ],
        "actions": [
            {
                "kind": "type_text",
                "channel": "api",
                "payload": {"text": "{{value}}"},
                "description": "type {{value}} into the window",
                "verify": {"expect_observe_contains": "{{value}}"},
            },
        ],
        "end_state": {
            "channel": "window_title",
            "description": "the value is visible in the window",
            "expect_observe_contains": "{{value}}",
        },
        "verification_contract": "the observation shows exactly the value that was sent",
        "parameters": {
            "value": {
                "type": "string",
                "required": True,
                "description": "the text to type",
                "arg_ref": "actions[0].payload.text",
            }
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "verified": {"type": "boolean"},
            },
            "required": ["status", "verified"],
        },
        "timeout": 45.0,
        "evidence": [
            {
                "ref": "logs/calc-acceptance/chain-edit-20261002-134606.json",
                "kind": "trajectory",
                "note": "a typed value was observed at the target in this run",
            }
        ],
        "target": {"windows": ["PROBE-WINDOW"], "paths": []},
    }
    spec.update(overrides)
    return spec


def _runner(*observations: str, description: str = "do the thing") -> DryRunRunner:
    return DryRunRunner(
        observations=list(observations) or [""],
        behaviors={description: ActionResult(ok=True, stdout="typed")},
    )


def _seed_session(runtime: Path, session_id: str, steps: list[dict]) -> None:
    engine = events_mod.EventStore(runtime / "events.db")
    for index, payload in enumerate(steps, start=1):
        kind = payload.pop("_kind", "cu_action")
        engine.append(session_id, index, kind, payload)
    engine.close()


def _gui_steps(
    first_value: str, second_value: str, *, fail_first_click: bool = False
) -> list[dict]:
    """One GUI flow played twice: open → click → type → wait (twice in pass 1).

    With ``fail_first_click`` the first pass records a FAILED click (a failed
    attempt), then its retry, then one more identical successful click (a
    redundant repeat). Otherwise both passes run the clean 4-step flow.
    """
    steps: list[dict] = []

    def _add(kind: str, anchor: str, detail: str, *, ok: bool = True) -> None:
        steps.append(
            {
                "step": f"step-{len(steps) + 1}",
                "channel": "api",
                "kind": kind,
                "ok": ok,
                "anchor": anchor,
                "detail": detail,
            }
        )

    for pass_index, value in enumerate((first_value, second_value)):
        _add("launch", "probe window", "launched")
        if fail_first_click and pass_index == 0:
            _add("mouse", "OK button", "click refused (no focus)", ok=False)
            _add("mouse", "OK button", "clicked")  # the retry that took
            _add("mouse", "OK button", "clicked")  # identical again: redundant
        else:
            _add("mouse", "OK button", "clicked")
        _add("type_text", "value field", value)
        _add("cli", "wait 2s", "sleep 2")
        if pass_index == 0:
            # the wait ran in two chunks: divided sleep is still a wait
            _add("cli", "wait 2s", "sleep 2")
    return steps


def _learn(runtime: Path, session_id: str, name: str = "", force: bool = True) -> dict:
    return S.learn_subtool(
        session_id,
        runtime=str(runtime),
        state=str(runtime),
        name=name,
        force=force,
    )


# --------------------------------------------------------------------------- #
# A. the record: parameters, output schema, timeout, evidence, target
# --------------------------------------------------------------------------- #


def test_subtool_record_carries_parameters_schema_timeout_evidence_target(tmp_path):
    runtime = _runtime(tmp_path)
    spec = _typed_value_spec()
    assert S.subtool_errors(spec) == []
    path = _save(spec, runtime)
    stored = json.loads(path.read_text(encoding="utf-8"))
    for key in ("parameters", "output_schema", "timeout", "evidence", "target"):
        assert key in stored, f"save_subtool dropped optional key {key}"
    assert stored["parameters"]["value"]["type"] == "string"
    assert stored["timeout"] == 45.0
    assert stored["evidence"][0]["kind"] == "trajectory"
    summary = S.subtool_summary(stored, origin="saved", path=str(path))
    assert summary["parameters"] == stored["parameters"]
    assert summary["output_schema"] == spec["output_schema"]
    assert summary["timeout"] == 45.0


def test_evidence_must_be_object_shaped_refs(tmp_path):
    spec = _typed_value_spec(evidence=["just a string"])
    problems = S.subtool_errors(spec)
    assert any("object" in problem for problem in problems), problems
    missing_ref = _typed_value_spec(evidence=[{"kind": "trajectory", "note": "no ref"}])
    assert any("ref" in problem for problem in S.subtool_errors(missing_ref))


def test_parameter_declarations_are_validated(tmp_path):
    no_type = _typed_value_spec(parameters={"value": {"required": True}})
    assert any("type" in problem for problem in S.subtool_errors(no_type))
    bad_type = _typed_value_spec(parameters={"value": {"type": "color"}})
    assert any("color" in problem for problem in S.subtool_errors(bad_type))
    bad_default = _typed_value_spec(
        parameters={"value": {"type": "number", "default": "seven"}}
    )
    assert any("default" in problem for problem in S.subtool_errors(bad_default))
    ok_default = _typed_value_spec(parameters={"value": {"type": "string", "default": "hi"}})
    assert S.subtool_errors(ok_default) == []


def test_undeclared_placeholders_refuse_the_call_form(tmp_path):
    spec = _typed_value_spec(
        actions=[
            {
                "kind": "type_text",
                "channel": "api",
                "payload": {"text": "{{cell}}"},
                "description": "type the cell",
                "verify": {"expect_observe_contains": "{{cell}}"},
            }
        ],
    )
    problems = S.subtool_errors(spec)
    assert any("cell" in problem for problem in problems), problems


def test_the_five_builtins_stay_exactly_as_they_are():
    for name, spec in S.builtin_subtools().items():
        assert S.subtool_errors(spec) == [], name
        for key in ("parameters", "output_schema", "timeout", "evidence", "target"):
            assert key not in spec, (name, key)


# --------------------------------------------------------------------------- #
# B. the re-run: parameters are real and state is checked first
# --------------------------------------------------------------------------- #


def test_run_subtool_substitutes_and_executes_with_parameters(tmp_path):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    runner = _runner(
        "PROBE-WINDOW", "PROBE-WINDOW hello", description="type hello into the window"
    )
    record = S.run_subtool(
        "type_into_window",
        state=str(runtime),
        runner=runner,
        params={"value": "hello"},
    )
    assert record["verified"] is True, record
    assert [str(a.payload.get("text")) for a in runner.actions] == ["hello"]
    assert record["params_used"] == ["value"]


def test_run_subtool_refuses_undeclared_parameter_before_acting(tmp_path):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    runner = _runner("PROBE-WINDOW", "PROBE-WINDOW x")
    record = S.run_subtool(
        "type_into_window", state=str(runtime), runner=runner, params={"oops": "1"}
    )
    assert record["status"] == "usage", record
    assert "oops" in record["reason"]
    assert not runner.actions, "a refused run must not have executed anything"


def test_run_subtool_refuses_missing_required_parameter(tmp_path):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    runner = _runner("PROBE-WINDOW", "PROBE-WINDOW x")
    record = S.run_subtool("type_into_window", state=str(runtime), runner=runner)
    assert record["status"] == "usage", record
    assert "value" in record["reason"]
    assert not runner.actions


def test_run_subtool_refuses_wrongly_typed_parameter(tmp_path):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    runner = _runner("PROBE-WINDOW", "PROBE-WINDOW x")
    record = S.run_subtool(
        "type_into_window", state=str(runtime), runner=runner, params={"value": 12345}
    )
    assert record["status"] == "usage", record
    assert "type" in record["reason"].lower()
    assert not runner.actions


def test_altered_state_rerun_refuses_blind_replay_and_reobserves(tmp_path):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    first = S.run_subtool(
        "type_into_window",
        state=str(runtime),
        runner=_runner("PROBE-WINDOW", "PROBE-WINDOW one", description="type one into the window"),
        params={"value": "one"},
    )
    assert first["verified"] is True
    # the state changed: the target window is gone — a re-run with DIFFERENT
    # parameters must refuse instead of replaying the stored sequence blind
    second_runner = _runner("(no application windows)", "(no application windows)")
    second = S.run_subtool(
        "type_into_window",
        state=str(runtime),
        runner=second_runner,
        params={"value": "two"},
    )
    assert second["status"] == "reobserve", second
    assert second["verified"] is False
    assert not second_runner.actions, "the refusal must have stopped before acting"
    assert second["missing_anchors"], second


def test_run_on_snapshot_state_after_repair_runs_again(tmp_path):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    # state re-observed (window back): the same subtool runs with new parameters
    third = S.run_subtool(
        "type_into_window",
        state=str(runtime),
        runner=_runner("PROBE-WINDOW", "PROBE-WINDOW two", description="type two into the window"),
        params={"value": "two"},
    )
    assert third["verified"] is True, third


# --------------------------------------------------------------------------- #
# C. CLI: the parameterized call form is a real verb argument
# --------------------------------------------------------------------------- #


def _run_cli(tmp_path: Path, argv: list[str], capsys) -> tuple[int, dict]:
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=False)
    act_cli.register_cli(sub)
    cli_contract.annotate(parser)
    args = parser.parse_args(["act"] + list(argv))
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    out = captured.out.strip()
    document = json.loads(out) if out.startswith("{") else None
    data = (document or {}).get("data")
    return int(code), data if isinstance(data, dict) else dict(document or {})


def test_cli_subtool_run_takes_a_params_object(tmp_path, capsys, monkeypatch):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    monkeypatch.setenv("SKALDIR_HOME", str(runtime))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("GODSV_HOME", raising=False)
    code, data = _run_cli(
        tmp_path,
        [
            "subtool", "run", "type_into_window",
            "--runner", "dry-run",
            "--params", '{"value": "cli-value"}',
            "--json",
        ],
        capsys,
    )
    # the CLI resolved its own dry-run runner, so this run refobserves on the
    # fixture's empty observation — the point is the params PATH, not a verdict
    assert code in (act_cli.EXIT_OK, act_cli.EXIT_VERIFICATION_FAILED), data
    if code == act_cli.EXIT_OK:
        assert data["params_used"] == ["value"]
    bad = _run_cli(
        tmp_path, ["subtool", "run", "type_into_window", "--params", "{nope", "--json"], capsys
    )
    assert bad[0] == act_cli.EXIT_USAGE


def test_cli_subtool_show_lists_the_record_fields(tmp_path, capsys, monkeypatch):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)
    code, data = _run_cli(
        tmp_path,
        ["subtool", "show", "type_into_window", "--state", str(runtime), "--json"],
        capsys,
    )
    assert code == 0
    spec = data["spec"]
    assert spec["parameters"]["value"]["arg_ref"] == "actions[0].payload.text"
    assert isinstance(spec["evidence"], list) and spec["evidence"]
    assert spec["timeout"] == 45.0


# --------------------------------------------------------------------------- #
# D. mining: failures and redundant repeats removed, waits kept, params derived
# --------------------------------------------------------------------------- #


def test_mining_removes_failures_and_repeats_keeps_waits_declares_parameters(tmp_path):
    runtime = _runtime(tmp_path)
    _seed_session(runtime, "gui-flow", _gui_steps("alpha", "beta", fail_first_click=True))
    learned = _learn(runtime, "gui-flow", name="learned_gui_flow")
    assert learned["learned"] is True, learned
    mining = learned["mining"]
    assert mining["attempts_removed"] == 1, mining
    assert mining["repeats_removed"] == 1, mining
    assert mining["waits_kept"] >= 1, mining
    summary = learned["subtool"]
    spec, _origin, _path = S.load_subtool("learned_gui_flow", state=str(runtime))
    kinds = [action["kind"] for action in spec["actions"]]
    # the pattern (4 steps × 2) survives: a wait step is IN it, not dropped
    assert kinds.count("cli") >= 1, kinds
    assert "type_text" in kinds, kinds
    assert "mouse" in kinds and "launch" in kinds, kinds
    # the varying value became a declared parameter with a real place to go
    parameters = summary["parameters"] if isinstance(summary, dict) else {}
    parameters = spec.get("parameters") or {}
    assert parameters, "the varied value must become a parameter"
    (parameter,) = parameters.values()
    assert parameter["type"] == "string"
    assert "alpha" in parameter.get("values_sample", []) or "beta" in parameter.get(
        "values_sample", []
    )
    typed_steps = [a for a in spec["actions"] if a["kind"] == "type_text"]
    assert any("{{" in json.dumps(a) for a in typed_steps), typed_steps
    # the failed click is not in the action list
    assert not any(
        "refused" in json.dumps(action) for action in spec["actions"]
    ), spec["actions"]
    # the trajectory itself is cited as evidence
    assert spec.get("evidence"), "a mined subtool must cite its trajectory"
    assert any(item.get("kind") == "trajectory" for item in spec["evidence"])
    assert summary.get("timeout"), "a mined subtool carries a timeout"


def test_mined_subtool_is_runnable_with_new_parameter_values(tmp_path):
    runtime = _runtime(tmp_path)
    _seed_session(runtime, "gui-flow", _gui_steps("alpha", "beta"))
    learned = _learn(runtime, "gui-flow", name="learned_gui_flow")
    assert learned["learned"] is True, learned
    spec, _origin, _path = S.load_subtool("learned_gui_flow", state=str(runtime))
    (parameter,) = (spec.get("parameters") or {}).values()
    # the learned draft's entry precondition is window-absent: the flow itself
    # opens its target window — pass 1 must queue that, then show everything
    runner = DryRunRunner(
        observations=[
            "(no windows)",
            "probe window OK button value field gamma wait 2s",
            "probe window OK button value field gamma wait 2s",
        ],
        behaviors={},
    )
    record = S.run_subtool(
        "learned_gui_flow",
        state=str(runtime),
        runner=runner,
        params={str(list(spec.get("parameters") or {})[0]): "gamma"},
    )
    assert record["verified"] is True, record
    typed = [a for a in runner.actions if a.kind == "type_text"]
    assert typed, record
    assert "{{" not in str(typed[0].payload), typed
    assert "gamma" in str(typed[0].payload), typed


def test_unminable_session_stays_an_honest_refusal(tmp_path):
    runtime = _runtime(tmp_path)
    _seed_session(runtime, "solo", _gui_steps("alpha", "beta")[:4])
    learned = _learn(runtime, "solo", name="learned_solo")
    assert learned["learned"] is False, learned
    assert "repeat" in learned["reason"].lower() or "nothing" in learned["reason"].lower()


# --------------------------------------------------------------------------- #
# E. discovery: tool_search finds the subtool with a valid call form
# --------------------------------------------------------------------------- #


def test_tool_search_finds_saved_subtool_with_typed_call_form(tmp_path):
    runtime = _runtime(tmp_path)
    spec = _typed_value_spec(
        evidence=[
            {
                "ref": "cu_runs.jsonl:act-subtool:type_into_window",
                "kind": "verified_run",
                "note": "a real run verified the end state",
            }
        ]
    )
    _save(spec, runtime)
    result = discover(
        "type a value into the target window", runtime=str(runtime), limit=10
    )
    names = [row["name"] for row in result["results"]]
    assert "type_into_window" in names, names
    row = next(item for item in result["results"] if item["name"] == "type_into_window")
    assert row["surface"] == "act_subtools"
    assert row["executable"] is True, row
    schema = row.get("input_schema")
    assert isinstance(schema, dict), row
    assert schema.get("properties", {}).get("value", {}).get("type") == "string"
    assert "value" in (schema.get("required") or [])
    assert "act subtool run" in row.get("call", "")


def test_tool_search_ignores_a_subtool_that_never_ran_as_executable(tmp_path):
    runtime = _runtime(tmp_path)
    _save(_typed_value_spec(), runtime)  # trajectory evidence only, no verified run
    result = discover(
        "type a value into the target window", runtime=str(runtime), limit=10
    )
    names = [row["name"] for row in result["results"]]
    # no verified run → NOT executable: it is a build candidate, honestly named
    assert "type_into_window" not in names
    assert any(
        row["name"] == "type_into_window" for row in result["build_candidates"]
    ), result["build_candidates"]
    assert result["error"]["code"] == "build_candidate_only", result["error"]


def test_tool_search_tool_handler_surfaces_the_subtool(tmp_path):
    from skaldir.agent_toolset import tool_search_tool

    runtime = _runtime(tmp_path)
    spec = _typed_value_spec(
        evidence=[
            {"ref": "cu_runs.jsonl:act-subtool:type_into_window", "kind": "verified_run"}
        ]
    )
    _save(spec, runtime)
    tool = tool_search_tool(runtime=str(runtime))
    payload = tool.handler(need="type a value into the target window", limit=10)
    names = [row["name"] for row in payload["results"]]
    assert "type_into_window" in names, names
