"""``skaldir compose …`` — the CLI surface, its envelope and its contract entries.

The group is wired by ``register_cli(subparsers)`` (see ``src/godsv/compose_cli.py``);
these tests exercise it through the **real** ``cli.build_parser()`` tree and the real
``cli_contract.run_command`` envelope, so the exit codes and the "no success without
evidence" rule are measured, not asserted from the handler.
"""

from __future__ import annotations

import argparse
import json

import pytest

from skaldir import cli, cli_contract
from skaldir.compose_cli import build_compose_parser, register_cli
from skaldir.compose_service import composite_path
from skaldir.tools import KIND_PYTHON, STATUS_APPROVED, STATUS_CANDIDATE, ToolSpec, ToolStore

#: The exact ``COMMANDS`` entries the CLI owner adds to ``cli_contract.py`` — this
#: dict is the patch that must land there (the dump invariant fails without it).
COMPOSE_COMMANDS = {
    "compose.candidates": cli_contract._C(  # noqa: SLF001 — the module's own constructor
        "COMPOSE",
        (cli_contract._ev("src/skaldir/compose_service.py:1599"),),  # noqa: SLF001
        "buildable chains from the real registry (+ stored composites)",
        data_schema={"type": "object",
                     "properties": {"registry": {"type": "string"},
                                    "steps_considered": {"type": "integer"},
                                    "chains": {"type": "array"},
                                    "composites": {"type": "array"}}},
        notes="a chain candidate is a heuristic proposal, never proof of a join",
    ),
    "compose.create": cli_contract._C(  # noqa: SLF001
        "COMPOSE",
        (cli_contract._ev("src/skaldir/compose_service.py:686"),),  # noqa: SLF001
        "store a composite definition + its registry row",
        mutates=True, exit_codes=(0, 1, 2, 6, 7),
        data_schema={"type": "object",
                     "properties": {"composite": {"type": "string"},
                                    "created": {"type": "boolean"},
                                    "path": {"type": "string"},
                                    "validation": {"type": "object"}}},
        notes="an existing name is exit 6 (conflict) unless --replace; a stored but "
              "invalid composite exits 2",
    ),
    "compose.validate": cli_contract._C(  # noqa: SLF001
        "COMPOSE",
        (cli_contract._ev("src/skaldir/compose_service.py:807"),),  # noqa: SLF001
        "validate a composite; --approve flips the registry row when clean",
        mutates=True, exit_codes=(0, 2, 5, 7),
        data_schema={"type": "object",
                     "properties": {"composite": {"type": "string"},
                                    "validation": {"type": "object"},
                                    "approved": {"type": "boolean"}}},
        notes="--force records the override; it never makes a missing runner exist",
    ),
    "compose.run": cli_contract._C(  # noqa: SLF001
        "COMPOSE",
        (cli_contract._ev("src/skaldir/compose_service.py:1355"),),  # noqa: SLF001
        "execute an approved composite through the runtime's real runners",
        mutates=True, exit_codes=(0, 1, 2, 3, 5, 7),
        data_schema={"type": "object",
                     "properties": {"composite": {"type": "string"},
                                    "status": {"type": "string"},
                                    "verified": {"type": "boolean"},
                                    "steps": {"type": "array"},
                                    "outputs": {"type": "object"},
                                    "missing_kinds": {"type": "array"}}},
        notes="a step kind without a runner is exit 3 and names the kind; a step that "
              "fails is exit 2; only a held end-state exits 0",
    ),
    "compose.show": cli_contract._C(  # noqa: SLF001
        "COMPOSE",
        (cli_contract._ev("src/skaldir/compose_service.py:807"),),  # noqa: SLF001
        "one composite (or all) with per-step executability",
        exit_codes=(0, 5, 7),
        data_schema={"type": "object",
                     "properties": {"composite": {"type": "string"},
                                    "document": {"type": "object"},
                                    "validation": {"type": "object"},
                                    "registry": {"type": "object"}}},
    ),
}


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #


def _runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    runtime = home / "godsv"
    runtime.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(runtime))
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return runtime


def _subparsers(parser):
    return next(
        action for action in parser._actions  # noqa: SLF001 — argparse has no public API
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    )


def _compose_parser():
    """The product parser with the group registered exactly once.

    Two details mirror what ``cli.py`` does: the group is added **before** the
    final ``cli_contract.annotate()`` pass (that pass is what puts the dotted
    ``command_path`` on every leaf, so the envelope names ``compose.run`` instead
    of ``skaldir``), and the guard is the point: once ``cli.py`` calls
    ``register_cli`` itself, this helper must not register a second time
    ("conflicting subparser: compose").
    """
    parser = cli.build_parser()
    sub = _subparsers(parser)
    if "compose" not in sub.choices:
        register_cli(sub)
        cli_contract.annotate(parser)  # cli.py does this at the end of build_parser()
    return parser


def _invoke(argv, capsys, *, json_mode=True):
    """Run one command through the real envelope; return (exit_code, envelope)."""
    args = _compose_parser().parse_args(argv)
    code = cli_contract.run_command(args, args.func, json_mode=json_mode, quiet=True)
    out = capsys.readouterr().out
    return code, json.loads(out)


def _register_tool(runtime, name, body, *, status=STATUS_APPROVED):
    module = f"{name}_mod"
    directory = runtime / "generated_tools" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{module}.py").write_text(body, encoding="utf-8")
    store = ToolStore(runtime / "tools.db")
    store.upsert(
        ToolSpec(name=name, title=name, description=f"step tool {name}", kind=KIND_PYTHON,
                 module=module, entrypoint="run", status=status, tags=["text"],
                 sample_input={"text": "sample"})
    )
    store.close()


def _doc(name="text_report"):
    return {
        "name": name,
        "title": "Text report",
        "description": "normalize then render",
        "tags": ["text"],
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": ["report"],
        "precondition": "$input.text present",
        "end_state": "$steps.render.report present",
        "steps": [
            {"id": "normalize", "kind": "tool", "target": "step_normalize",
             "inputs": {"text": "$input.text"}, "outputs": ["upper"],
             "precondition": "always",
             "end_state": "exit_ok and $steps.normalize.upper present"},
            {"id": "render", "kind": "tool", "target": "step_render",
             "inputs": {"upper": "$steps.normalize.upper"}, "outputs": ["report"],
             "precondition": "$steps.normalize.upper present",
             "end_state": "exit_ok and $steps.render.report contains 'REPORT'"},
        ],
    }


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A throwaway runtime with two approved generated tools and a spec file."""
    runtime = _runtime(tmp_path, monkeypatch)
    _register_tool(runtime, "step_normalize",
                   "def run(text=''):\n    return {'upper': str(text).strip().upper()}\n")
    _register_tool(runtime, "step_render",
                   "def run(upper=''):\n    return {'report': 'REPORT:' + str(upper)}\n")
    spec = tmp_path / "text_report.json"
    spec.write_text(json.dumps(_doc()), encoding="utf-8")
    return runtime, spec


def _approved(workspace, capsys):
    runtime, spec = workspace
    _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
    code, _ = _invoke(["compose", "validate", "--name", "text_report", "--approve", "--json"],
                      capsys)
    return code


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


class TestParserContract:
    def test_register_cli_adds_the_group_and_returns_the_parser(self):
        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        returned = register_cli(sub)
        assert returned is not None
        assert "compose" in sub.choices
        assert sorted(_subparsers(returned).choices) == [
            "candidates", "create", "run", "show", "validate",
        ]

    def test_the_standalone_parser_has_the_same_five_verbs(self):
        from skaldir.compose_cli import build_parser

        parser = build_parser()
        assert sorted(_subparsers(parser).choices) == [
            "candidates", "create", "run", "show", "validate",
        ]

    def test_build_compose_parser_is_the_same_thing(self):
        parser = argparse.ArgumentParser(prog="t")
        sub = parser.add_subparsers(dest="command")
        build_compose_parser(sub)
        assert "compose" in sub.choices

    def test_the_five_leaves_carry_their_dotted_paths(self):
        parser = _compose_parser()
        paths = sorted(
            path for path, _sub in cli_contract.leaf_commands(parser)
            if path.startswith("compose.")
        )
        assert paths == [
            "compose.candidates", "compose.create", "compose.run", "compose.show",
            "compose.validate",
        ]

    def test_wiring_the_group_twice_is_the_documented_trap(self):
        """`register_cli` must run once — the guard every test/CLI must use."""
        parser = _compose_parser()
        _compose_parser()  # the second call must not raise (guard)
        sub = _subparsers(parser)
        assert list(sub.choices).count("compose") == 1
        with pytest.raises(argparse.ArgumentError):
            register_cli(sub)

    def test_every_compose_verb_takes_json(self):
        parser = _compose_parser()
        for path, sub in cli_contract.leaf_commands(parser):
            if not path.startswith("compose."):
                continue
            assert any("--json" in action.option_strings for action in sub._actions), path  # noqa: SLF001


class TestContractEntries:
    """The five ``COMMANDS`` entries the CLI owner adds — measured, not claimed."""

    def test_the_reported_entries_close_the_dump_invariant(self, monkeypatch):
        monkeypatch.setattr(
            cli_contract, "COMMANDS", {**cli_contract.COMMANDS, **COMPOSE_COMMANDS}
        )
        parser = _compose_parser()
        leaves = [path for path, _sub in cli_contract.leaf_commands(parser)]
        inventory = cli_contract.dump(parser)
        dumped = [item["command"] for item in inventory["commands"]]
        assert sorted(dumped) == sorted(leaves)
        assert inventory["unregistered"] == []
        for item in inventory["commands"]:
            if item["command"].startswith("compose."):
                assert item["registered"] is True
                assert item["layer"] == "COMPOSE"
                assert item["data_schema"]["type"] == "object"
                assert item["default_evidence"], item["command"]
                assert item["has_json_flag"] is True
                assert 0 in item["exit_codes"]

    def test_the_recorded_args_match_the_real_parser(self, monkeypatch):
        monkeypatch.setattr(
            cli_contract, "COMMANDS", {**cli_contract.COMMANDS, **COMPOSE_COMMANDS}
        )
        parser = _compose_parser()
        inventory = cli_contract.dump(parser)
        for path, sub in cli_contract.leaf_commands(parser):
            if not path.startswith("compose."):
                continue
            record = next(item for item in inventory["commands"] if item["command"] == path)
            expected = cli_contract.args_of(sub)
            assert [arg["name"] for arg in record["args"]] == [
                arg["name"] for arg in expected
            ], path

    def test_the_shipped_entries_leave_the_dump_with_nothing_unregistered(self):
        """The gap this class was written for is closed — and stays closed.

        The hand-over state was: the five ``COMMANDS`` entries missing, so
        ``compose.run`` (and its four siblings) showed up in ``unregistered``.
        ``compose_cli.register_cli`` installs them itself now, so the same
        measurement must report the opposite.
        """
        parser = _compose_parser()
        inventory = cli_contract.dump(parser)
        assert inventory["unregistered"] == []
        record = next(
            item for item in inventory["commands"] if item["command"] == "compose.run"
        )
        assert record["registered"] is True
        assert record["layer"] == "COMPOSE"


# --------------------------------------------------------------------------- #
# compose candidates                                                            #
# --------------------------------------------------------------------------- #


class TestCandidatesVerb:
    def test_candidates_are_reported_from_the_real_registry(self, workspace, capsys):
        runtime, _spec = workspace
        code, envelope = _invoke(["compose", "candidates", "--json"], capsys)
        assert code == 0 and envelope["ok"] is True
        data = envelope["data"]
        assert data["registry"] == str(runtime / "tools.db")
        assert data["steps_considered"] == 2
        assert data["chain_count"] >= 1
        assert set(data["chains"][0]["chain"]) == {"step_normalize", "step_render"}
        assert envelope["evidence"], "a success must carry evidence"
        assert any(item["kind"] == "file" for item in envelope["evidence"])

    def test_an_empty_registry_says_so_instead_of_inventing_chains(self, tmp_path, monkeypatch,
                                                                   capsys):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        store.close()
        code, envelope = _invoke(["compose", "candidates", "--json"], capsys)
        assert code == 0
        assert envelope["data"]["steps_considered"] == 0
        assert envelope["data"]["chains"] == []
        assert any("no approved, executable tool" in item for item in envelope["warnings"])

    def test_a_missing_registry_is_a_warning_not_a_failure(self, tmp_path, monkeypatch, capsys):
        runtime = _runtime(tmp_path, monkeypatch)
        (runtime / "tools.db").unlink(missing_ok=True)
        code, envelope = _invoke(["compose", "candidates", "--json"], capsys)
        assert code == 0 and envelope["data"]["steps_considered"] == 0
        assert any("no tools db" in item for item in envelope["warnings"])


# --------------------------------------------------------------------------- #
# compose create / validate                                                     #
# --------------------------------------------------------------------------- #


class TestCreateVerb:
    def test_create_stores_both_records_and_validates_clean(self, workspace, capsys):
        runtime, spec = workspace
        code, envelope = _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        assert code == 0 and envelope["ok"] is True
        data = envelope["data"]
        assert data["created"] is True and data["status"] == STATUS_CANDIDATE
        assert data["validation"]["ok"] is True
        assert data["steps"] == ["normalize", "render"]
        assert composite_path(runtime, "text_report").is_file()
        store = ToolStore(runtime / "tools.db")
        row = store.get("text_report")
        store.close()
        assert row is not None and row.kind == "composition" and row.status == STATUS_CANDIDATE
        assert len(envelope["evidence"]) >= 2

    def test_create_reads_stdin(self, workspace, capsys, monkeypatch):
        import io
        import sys

        _runtime_, spec = workspace
        monkeypatch.setattr(sys, "stdin", io.StringIO(spec.read_text(encoding="utf-8")))
        code, envelope = _invoke(["compose", "create", "--spec", "-", "--json"], capsys)
        assert code == 0 and envelope["data"]["created"] is True

    def test_a_second_create_is_a_conflict(self, workspace, capsys):
        runtime, spec = workspace
        _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        code, envelope = _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        assert code == 6
        assert envelope["error"]["code"] == "conflict"
        assert envelope["data"]["created"] is False
        conflict = envelope["next_actions"] or []
        assert "--replace" in envelope["error"]["message"] or any(
            "--replace" in item.get("reason", "") + item.get("command", "") for item in conflict
        )

    def test_replace_overwrites(self, workspace, capsys):
        _runtime_, spec = workspace
        _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        code, envelope = _invoke(
            ["compose", "create", "--spec", str(spec), "--replace", "--json"], capsys
        )
        assert code == 0 and envelope["data"]["replaced"] is True

    def test_a_missing_spec_flag_is_usage(self, workspace, capsys):
        code, envelope = _invoke(["compose", "create", "--json"], capsys)
        assert code == 1 and envelope["error"]["code"] == "usage"

    def test_a_malformed_document_is_usage(self, tmp_path, monkeypatch, capsys):
        _runtime(tmp_path, monkeypatch)
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"name": "x", "steps": [], "mystery": 1}), encoding="utf-8")
        code, envelope = _invoke(["compose", "create", "--spec", str(bad), "--json"], capsys)
        assert code == 1 and envelope["error"]["code"] == "usage"
        assert "unknown field" in envelope["error"]["message"]

    def test_an_invalid_composite_is_stored_and_reported_as_exit_2(self, tmp_path, monkeypatch,
                                                                   capsys):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize",
                       "def run(text=''):\n    return {'upper': text}\n", status=STATUS_CANDIDATE)
        spec = tmp_path / "doc.json"
        spec.write_text(json.dumps(_doc()), encoding="utf-8")
        code, envelope = _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        assert code == 2
        assert envelope["data"]["created"] is True
        assert any("not approved" in item for item in envelope["data"]["validation"]["problems"])
        assert any("does NOT validate" in item for item in envelope["warnings"])


class TestValidateVerb:
    def test_validate_reports_clean_and_approves(self, workspace, capsys):
        runtime, spec = workspace
        _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        code, envelope = _invoke(["compose", "validate", "--name", "text_report", "--json"],
                                 capsys)
        assert code == 0 and envelope["data"]["ok"] is True
        assert envelope["data"]["approved"] is False
        assert envelope["data"]["status_after"] == STATUS_CANDIDATE
        code, envelope = _invoke(
            ["compose", "validate", "--name", "text_report", "--approve", "--json"], capsys
        )
        assert code == 0 and envelope["data"]["approved"] is True
        assert envelope["data"]["status_before"] == STATUS_CANDIDATE
        assert envelope["data"]["status_after"] == STATUS_APPROVED
        store = ToolStore(runtime / "tools.db")
        row = store.get("text_report")
        store.close()
        assert row is not None and row.status == STATUS_APPROVED
        assert row.scan_summary == "compose validation: clean"

    def test_validate_does_not_approve_a_broken_composite(self, workspace, capsys):
        runtime, spec = workspace
        payload = _doc()
        payload["steps"][1]["inputs"] = {"upper": "$steps.normalize.ghost"}
        broken = spec.parent / "broken.json"
        broken.write_text(json.dumps(payload), encoding="utf-8")
        _invoke(["compose", "create", "--spec", str(broken), "--json"], capsys)
        code, envelope = _invoke(
            ["compose", "validate", "--name", "text_report", "--approve", "--json"], capsys
        )
        assert code == 2
        assert envelope["data"]["approved"] is False
        assert envelope["data"]["status_after"] == STATUS_CANDIDATE
        assert any("not a declared output" in item
                   for item in envelope["data"]["validation"]["problems"])

    def test_force_approves_but_records_it(self, workspace, capsys):
        runtime, spec = workspace
        payload = _doc()
        payload["steps"][1]["inputs"] = {"upper": "$steps.normalize.ghost"}
        broken = spec.parent / "broken.json"
        broken.write_text(json.dumps(payload), encoding="utf-8")
        _invoke(["compose", "create", "--spec", str(broken), "--json"], capsys)
        code, envelope = _invoke(
            ["compose", "validate", "--name", "text_report", "--approve", "--force", "--json"],
            capsys,
        )
        assert code == 2, "the validation verdict stays honest"
        assert envelope["data"]["approved"] is True
        store = ToolStore(runtime / "tools.db")
        row = store.get("text_report")
        store.close()
        assert row is not None and "with --force despite" in row.scan_summary

    def test_validate_without_a_name_is_usage(self, workspace, capsys):
        code, envelope = _invoke(["compose", "validate", "--json"], capsys)
        assert code == 1 and envelope["error"]["code"] == "usage"

    def test_an_unknown_composite_is_not_found(self, workspace, capsys):
        code, envelope = _invoke(["compose", "validate", "--name", "ghost", "--json"], capsys)
        assert code == 5 and envelope["error"]["code"] == "not_found"
        assert envelope["data"]["found"] is False

    def test_an_api_step_validates_as_executable_and_is_approved(self, workspace, capsys):
        """The api kind is no longer a gap: the composite validates *and* is executable."""
        runtime, spec = workspace
        payload = _doc()
        payload["steps"] = [
            {"id": "call", "kind": "api", "url": "https://example.invalid/x",
             "outputs": ["text"], "precondition": "always", "end_state": "exit_ok"}
        ]
        payload["outputs"] = ["text"]
        payload["end_state"] = "$steps.call.text present"
        api_spec = spec.parent / "api.json"
        api_spec.write_text(json.dumps(payload), encoding="utf-8")
        code, envelope = _invoke(["compose", "create", "--spec", str(api_spec), "--json"],
                                 capsys)
        assert code == 0, envelope
        assert envelope["data"]["validation"]["ok"] is True
        code, envelope = _invoke(
            ["compose", "validate", "--name", "text_report", "--approve", "--json"], capsys
        )
        assert code == 0
        assert envelope["data"]["missing_kinds"] == []
        assert envelope["data"]["executable"] is True
        call = envelope["data"]["validation"]["steps"][0]
        assert call["executable"] is True and call["reason"] == ""
        assert "run_api_step" in envelope["data"]["validation"]["executors"]["api"]["runner"]
        assert not any("no runner for step kind" in item for item in envelope["warnings"])

    def test_an_api_output_the_runner_never_returns_blocks_the_approval(self, workspace, capsys):
        runtime, spec = workspace
        payload = _doc()
        payload["steps"] = [
            {"id": "call", "kind": "api", "url": "https://example.invalid/x",
             "outputs": ["body"], "precondition": "always", "end_state": "exit_ok"}
        ]
        payload["outputs"] = ["body"]
        payload["end_state"] = "$steps.call.body present"
        api_spec = spec.parent / "api.json"
        api_spec.write_text(json.dumps(payload), encoding="utf-8")
        code, created = _invoke(["compose", "create", "--spec", str(api_spec), "--json"], capsys)
        assert code == 2, "a stored composite that does not validate is a verification failure"
        assert created["data"]["validation"]["ok"] is False
        code, envelope = _invoke(
            ["compose", "validate", "--name", "text_report", "--approve", "--json"], capsys
        )
        assert code == 2 and envelope["data"]["executable"] is False
        assert envelope["data"]["approved"] is False
        assert any("api output 'body' is not one of" in item
                   for item in envelope["data"]["validation"]["problems"])


# --------------------------------------------------------------------------- #
# compose run                                                                   #
# --------------------------------------------------------------------------- #


class TestRunVerb:
    def test_run_refuses_a_composite_that_is_not_approved(self, workspace, capsys):
        runtime, spec = workspace
        _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--input", "text=x", "--json"], capsys
        )
        assert code == 3 and envelope["error"]["code"] == "blocked"
        assert envelope["data"]["verified"] is False
        assert any("--approve" in item["command"] for item in envelope["next_actions"])

    def test_run_executes_the_chain_when_approved(self, workspace, capsys):
        _approved(workspace, capsys)
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--input", "text= hello ", "--json"],
            capsys,
        )
        assert code == 0 and envelope["ok"] is True
        data = envelope["data"]
        assert data["status"] == "verified" and data["verified"] is True
        assert data["outputs"] == {"report": "REPORT:HELLO"}
        assert [step["id"] for step in data["steps"]] == ["normalize", "render"]
        assert all(step["ok"] for step in data["steps"])
        assert any(item["kind"] == "run" for item in envelope["evidence"])

    def test_run_accepts_an_inputs_file(self, workspace, capsys):
        runtime, spec = workspace
        _approved(workspace, capsys)
        inputs = spec.parent / "inputs.json"
        inputs.write_text(json.dumps({"text": "from file"}), encoding="utf-8")
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--inputs", str(inputs), "--json"],
            capsys,
        )
        assert code == 0 and envelope["data"]["outputs"] == {"report": "REPORT:FROM FILE"}

    def test_a_missing_required_input_is_usage(self, workspace, capsys):
        _approved(workspace, capsys)
        code, envelope = _invoke(["compose", "run", "--name", "text_report", "--json"], capsys)
        assert code == 1 and envelope["error"]["code"] == "usage"
        assert envelope["data"]["status"] == "usage"
        assert envelope["data"]["steps"] == []

    def test_a_bad_input_pair_is_usage(self, workspace, capsys):
        _approved(workspace, capsys)
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--input", "text", "--json"], capsys
        )
        assert code == 1 and envelope["error"]["code"] == "usage"

    def test_run_on_an_unknown_composite_is_not_found(self, workspace, capsys):
        code, envelope = _invoke(["compose", "run", "--name", "ghost", "--json"], capsys)
        assert code == 5 and envelope["error"]["code"] == "not_found"

    def test_a_runtime_step_failure_is_exit_2(self, tmp_path, monkeypatch, capsys):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize",
                       "def run(text=''):\n    return {'upper': str(text).upper()}\n")
        _register_tool(runtime, "step_render",
                       "def run(upper=''):\n    raise RuntimeError('render exploded')\n")
        spec = tmp_path / "doc.json"
        spec.write_text(json.dumps(_doc()), encoding="utf-8")
        _approved((runtime, spec), capsys)
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--input", "text=x", "--json"], capsys
        )
        assert code == 2 and envelope["error"]["code"] == "verification_failed"
        assert envelope["data"]["status"] == "failed"
        assert envelope["data"]["steps"][-1]["status"] == "failed"
        assert "render exploded" in envelope["data"]["reason"]

    def test_an_api_step_really_runs_after_the_tool_steps_and_exits_2(self, workspace, capsys):
        """The chain executes: both tool steps run, then the api step really makes the
        request, and the refused connection is its own honest verdict (exit 2) — no
        up-front "blocked", no partial-run fiction."""
        runtime, spec = workspace
        payload = _doc()
        payload["steps"].append(
            {"id": "publish", "kind": "api", "url": "http://127.0.0.1:9/x", "timeout_s": 5,
             "outputs": ["text"], "precondition": "always", "end_state": "exit_ok"}
        )
        api_spec = spec.parent / "api.json"
        api_spec.write_text(json.dumps(payload), encoding="utf-8")
        _invoke(["compose", "create", "--spec", str(api_spec), "--replace", "--json"], capsys)
        _invoke(["compose", "validate", "--name", "text_report", "--approve", "--json"], capsys)
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--input", "text=x", "--json"], capsys
        )
        assert code == 2 and envelope["error"]["code"] == "verification_failed", envelope
        data = envelope["data"]
        assert data["status"] == "failed"
        assert data["missing_kinds"] == []
        assert data["verified"] is False
        assert [step["id"] for step in data["steps"]] == ["normalize", "render", "publish"]
        assert [step["status"] for step in data["steps"]] == ["ok", "ok", "failed"]
        publish = data["steps"][-1]
        assert publish["kind"] == "api"
        assert publish["failure_class"] == "network_error"
        assert "run_api_step" in publish["evidence"]
        assert data["validation"]["steps"][-1]["executable"] is True

    def test_a_gui_step_with_no_bridge_is_exit_3_and_names_the_capability(self, workspace,
                                                                         capsys, monkeypatch):
        """A named refusal, not a crash: blocked, exit 3, the missing capability spelled out."""
        import skaldir.act_service as act_service

        monkeypatch.setattr(
            act_service,
            "resolve_runner",
            lambda kind, **kw: (
                None,
                {"requested": kind, "resolved": False, "real": False, "fixture": False,
                 "reason": "no isolation bridge is available on this stubbed host"},
            ),
        )
        runtime, spec = workspace
        payload = _doc()
        payload["steps"].append(
            {"id": "click", "kind": "gui", "target": "open_browser", "outputs": ["status"],
             "precondition": "always", "end_state": "exit_ok"}
        )
        gui_spec = spec.parent / "gui.json"
        gui_spec.write_text(json.dumps(payload), encoding="utf-8")
        _invoke(["compose", "create", "--spec", str(gui_spec), "--replace", "--json"], capsys)
        _invoke(["compose", "validate", "--name", "text_report", "--approve", "--json"], capsys)
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--input", "text=x", "--json"], capsys
        )
        assert code == 3 and envelope["error"]["code"] == "blocked"
        data = envelope["data"]
        assert data["missing_kinds"] == ["gui"]
        step = data["steps"][-1]
        assert step["status"] == "blocked" and step["missing_kind"] == "gui"
        assert "stubbed host" in step["detail"]
        assert step["missing_capability"]


# --------------------------------------------------------------------------- #
# compose show                                                                  #
# --------------------------------------------------------------------------- #


class TestShowVerb:
    def test_show_one_composite_with_its_executability(self, workspace, capsys):
        runtime, spec = workspace
        _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        code, envelope = _invoke(["compose", "show", "--name", "text_report", "--json"], capsys)
        assert code == 0
        data = envelope["data"]
        assert data["composite"] == "text_report"
        assert [step["id"] for step in data["document"]["steps"]] == ["normalize", "render"]
        assert data["registry"]["status"] == STATUS_CANDIDATE
        assert data["validation"]["executable"] is True
        assert data["mirror"]["drift_detected"] is False
        assert len(data["sha256"]) == 64

    def test_show_without_a_name_lists_the_definitions(self, workspace, capsys):
        runtime, spec = workspace
        _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        code, envelope = _invoke(["compose", "show", "--json"], capsys)
        assert code == 0
        assert [row["name"] for row in envelope["data"]["composites"]] == ["text_report"]

    def test_show_without_a_name_on_an_empty_runtime(self, tmp_path, monkeypatch, capsys):
        _runtime(tmp_path, monkeypatch)
        code, envelope = _invoke(["compose", "show", "--json"], capsys)
        assert code == 0 and envelope["data"]["count"] == 0

    def test_show_an_unknown_composite_is_not_found(self, workspace, capsys):
        code, envelope = _invoke(["compose", "show", "--name", "ghost", "--json"], capsys)
        assert code == 5 and envelope["data"]["found"] is False


# --------------------------------------------------------------------------- #
# the selftest probe the CLI owner must patch (cli_contract.py:1400-1424)       #
# --------------------------------------------------------------------------- #


def probe_compose_replacement(runtime):
    """The proposed replacement for ``cli_contract._probe_compose``.

    Verbatim from the hand-over snippet: same two probes, but
    ``compose_lifecycle`` measures the layer instead of asserting the old,
    no-longer-true "nothing can be created/validated/run" verdict, and the
    layer's ``ok`` is derived from its probes.
    """
    probes = []
    db = runtime / "tools.db"

    def _candidates():
        if not db.is_file():
            return False, f"no tools db at {db} — nothing to compose from", str(db)
        from skaldir.tools import ToolStore, compose_candidates

        store = ToolStore(str(db))
        try:
            found = compose_candidates(store, description="selftest probe", tags=None)
        finally:
            store.close()
        return True, f"compose_candidates returned {len(found)} ranked candidate(s)", \
            "src/skaldir/tools.py:469"

    probes.append(
        cli_contract._probe(  # noqa: SLF001 — the module's own constructor
            "compose_candidates", _candidates, blocked=True, evidence_kind="file"
        )
    )

    def _lifecycle():
        from skaldir.compose_service import executor_report

        table = executor_report()
        ready = sorted(kind for kind, row in table.items() if row["executable"])
        missing = sorted(kind for kind, row in table.items() if not row["executable"])
        runnable = sorted(kind for kind, row in table.items() if row.get("available"))
        gap = f"; no runner yet for {', '.join(missing)}" if missing else ""
        detail = (
            "composite lifecycle wired (create/validate/run/show over "
            f"{len(ready)} executable step kind(s): {', '.join(ready)}; "
            f"{len(runnable)} runnable here){gap}"
        )
        return True, detail, "src/skaldir/compose_service.py:807"

    probes.append(cli_contract._probe("compose_lifecycle", _lifecycle))  # noqa: SLF001
    ok = all(probe.ok for probe in probes)
    return ok, "" if ok else " ".join(p.detail for p in probes if not p.ok), probes


class TestSelftestProbeHandover:
    """The COMPOSE layer is reachable and the *shipped* probe measures that."""

    def test_the_shipped_probe_reports_the_layer_reachable(self, tmp_path, monkeypatch):
        """The old verdict ("nothing can be created, validated or run") is gone.

        It stopped being true when ``compose_cli.register_cli`` landed in
        ``build_parser()``; ``cli_contract._probe_compose`` now counts the CLI
        verbs and the executable step kinds instead of asserting the gap.
        """
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        store.close()
        report = cli_contract.run_selftest(runtime, layers=["COMPOSE"])
        layer = report["layers"][0]
        assert layer["ok"] is True, layer["blocked_reason"]
        assert layer["status"] == "reachable"
        assert layer["exit_code"] == 0
        lifecycle = next(p for p in layer["probes"] if p["name"] == "compose_lifecycle")
        assert lifecycle["status"] == "reachable"
        assert "CLI verb(s)" in lifecycle["detail"]
        assert lifecycle["evidence_ref"] == "src/skaldir/compose_service.py:807"

    def test_the_replacement_probe_reports_the_layer_reachable(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        store.close()
        monkeypatch.setitem(cli_contract._PROBES, "COMPOSE", probe_compose_replacement)  # noqa: SLF001
        report = cli_contract.run_selftest(runtime, layers=["COMPOSE"])
        layer = report["layers"][0]
        assert layer["ok"] is True, layer["blocked_reason"]
        assert layer["status"] == "reachable"
        assert layer["exit_code"] == 0
        lifecycle = next(p for p in layer["probes"] if p["name"] == "compose_lifecycle")
        assert lifecycle["status"] == "reachable"
        assert "4 executable step kind(s): api, docker, gui, tool" in lifecycle["detail"]
        assert "no runner yet for" not in lifecycle["detail"]
        assert "runnable here" in lifecycle["detail"]
        assert lifecycle["evidence_ref"] == "src/skaldir/compose_service.py:807"

    def test_the_layer_lists_the_compose_commands(self):
        """The entries landed: the layer listing carries all five compose verbs."""
        shipped = cli_contract.cli_commands_for_layer("COMPOSE")
        assert [item for item in shipped if item.startswith("compose.")] == [
            "compose.candidates", "compose.create", "compose.run", "compose.show",
            "compose.validate",
        ]
        assert "tools.compose" in shipped, "the old listing command keeps its entry"


# --------------------------------------------------------------------------- #
# the envelope itself                                                           #
# --------------------------------------------------------------------------- #


class TestEnvelope:
    def test_one_json_document_on_stdout(self, workspace, capsys):
        _approved(workspace, capsys)
        args = _compose_parser().parse_args(["compose", "run", "--name", "text_report",
                                             "--input", "text=x", "--json"])
        code = cli_contract.run_command(args, args.func)
        out = capsys.readouterr().out
        assert code == 0
        envelope = json.loads(out.strip())
        assert envelope["command"] == "compose.run"
        assert envelope["schema_version"] == "1.0"
        assert out.count("\n") == 1, "stdout carries exactly one JSON document"

    def test_format_json_also_switches_the_envelope_on(self, workspace, capsys):
        args = _compose_parser().parse_args(["compose", "candidates", "--format", "json"])
        code = cli_contract.run_command(args, args.func)
        out = capsys.readouterr().out
        assert code == 0
        assert json.loads(out.strip())["command"] == "compose.candidates"

    def test_human_mode_prints_lines_instead_of_json(self, workspace, capsys):
        args = _compose_parser().parse_args(["compose", "candidates"])
        code = cli_contract.run_command(args, args.func)
        out = capsys.readouterr().out
        assert code == 0
        assert "Chains from" in out and not out.lstrip().startswith("{")

    def test_the_standalone_main_returns_the_exit_code(self, workspace, capsys):
        from skaldir.compose_cli import main

        _approved(workspace, capsys)
        assert main(["candidates"]) == 0
        assert main(["validate", "--name", "ghost"]) == 5

    def test_a_stored_composite_without_a_registry_row_cannot_run(self, workspace, capsys,
                                                                  monkeypatch):
        """The registry row is the gate: no row, no approval, no run."""
        runtime, spec = workspace
        _invoke(["compose", "create", "--spec", str(spec), "--json"], capsys)
        store = ToolStore(runtime / "tools.db")
        store._db.execute("DELETE FROM tool_specs WHERE name = ?", ("text_report",))  # noqa: SLF001
        store._db.commit()  # noqa: SLF001
        store.close()
        code, envelope = _invoke(
            ["compose", "run", "--name", "text_report", "--input", "text=x", "--json"], capsys
        )
        assert code == 3
        assert envelope["data"]["registry"]["present"] is False
