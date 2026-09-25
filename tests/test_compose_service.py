"""COMPOSE — the composite model: check grammar, validation, execution, candidates.

Layer under test: ``src/godsv/compose_service.py`` (Learned Composite Tools). The
tests execute **real** steps: generated tools go through ``mcpserver._wrapper_for``
(bwrap sandbox), docker steps through a fake ``docker`` binary — no network and no
container on the shared daemon.
"""

from __future__ import annotations

import json

import pytest

from skaldir.compose_service import (
    DOCKER_OUTPUT_KEYS,
    CheckError,
    CompositeError,
    StepSpec,
    buildable_chains,
    composite_from_registry,
    composite_path,
    document_digest,
    evaluate_check,
    executor_report,
    load_composite,
    missing_kinds,
    parse_check,
    parse_composite,
    run_composite,
    save_composite,
    validate_composite,
)
from skaldir.gates import _sandbox_available
from skaldir.tools import (
    KIND_COMPOSITION,
    KIND_PYTHON,
    STATUS_APPROVED,
    STATUS_CANDIDATE,
    ToolSpec,
    ToolStore,
)

DIGEST = "sha256:" + "a" * 64
SANDBOX = pytest.mark.skipif(
    not _sandbox_available(),
    reason="generated tools only execute through the shared bwrap sandbox (gates._run)",
)


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


def _register_tool(runtime, name, body, *, status=STATUS_APPROVED, sample=None):
    """A real generated tool in the registry (module file + spec)."""
    module = f"{name}_mod"
    directory = runtime / "generated_tools" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{module}.py").write_text(body, encoding="utf-8")
    store = ToolStore(runtime / "tools.db")
    store.upsert(
        ToolSpec(
            name=name,
            title=name,
            description=f"step tool {name}",
            kind=KIND_PYTHON,
            module=module,
            entrypoint="run",
            status=status,
            tags=["text"],
            sample_input={"text": "sample"} if sample is None else sample,
        )
    )
    store.close()


def _two_step_doc(name="text_report"):
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
            {
                "id": "normalize",
                "kind": "tool",
                "target": "step_normalize",
                "inputs": {"text": "$input.text"},
                "outputs": ["upper"],
                "precondition": "always",
                "end_state": "exit_ok and $steps.normalize.upper present",
            },
            {
                "id": "render",
                "kind": "tool",
                "target": "step_render",
                "inputs": {"upper": "$steps.normalize.upper"},
                "outputs": ["report"],
                "precondition": "$steps.normalize.upper present",
                "end_state": "exit_ok and $steps.render.report contains 'REPORT'",
            },
        ],
    }


@pytest.fixture()
def two_tools(tmp_path, monkeypatch):
    """Two approved generated tools + a clean two-step composite document."""
    runtime = _runtime(tmp_path, monkeypatch)
    _register_tool(
        runtime,
        "step_normalize",
        "def run(text=''):\n    return {'upper': str(text).strip().upper()}\n",
    )
    _register_tool(
        runtime,
        "step_render",
        "def run(upper=''):\n    return {'report': 'REPORT:' + str(upper)}\n",
    )
    store = ToolStore(runtime / "tools.db")
    yield runtime, store, parse_composite(_two_step_doc())
    store.close()


# --------------------------------------------------------------------------- #
# the check grammar                                                             #
# --------------------------------------------------------------------------- #


class TestCheckGrammar:
    def test_always_and_never(self):
        assert evaluate_check(parse_check("always"), {})[0] is True
        assert evaluate_check(parse_check("never"), {})[0] is False

    def test_exit_ok_reads_the_verdict(self):
        assert evaluate_check(parse_check("exit_ok"), {"exit_ok": True})[0] is True
        assert evaluate_check(parse_check("exit_ok"), {"exit_ok": False})[0] is False

    @pytest.mark.parametrize(
        "text,context,expected",
        [
            ("$input.text present", {"input": {"text": "x"}}, True),
            ("$input.text present", {"input": {"text": ""}}, False),
            ("$input.text empty", {"input": {"text": ""}}, True),
            ("$steps.a.n == 3", {"steps": {"a": {"n": 3}}}, True),
            ("$steps.a.n != 3", {"steps": {"a": {"n": 4}}}, True),
            ("$steps.a.n > 2", {"steps": {"a": {"n": "3"}}}, True),
            ("$steps.a.n <= 2", {"steps": {"a": {"n": 5}}}, False),
            ("$steps.a.words contains 'x'", {"steps": {"a": {"words": ["x"]}}}, True),
            ("$steps.a.s contains x", {"steps": {"a": {"s": "axb"}}}, True),
            ("$steps.a.flag == true", {"steps": {"a": {"flag": True}}}, True),
        ],
    )
    def test_predicates(self, text, context, expected):
        assert evaluate_check(parse_check(text), context)[0] is expected

    def test_clauses_join_with_and(self):
        check = parse_check("$input.a present and $input.b present")
        assert evaluate_check(check, {"input": {"a": 1, "b": 2}})[0] is True
        assert evaluate_check(check, {"input": {"a": 1}})[0] is False

    def test_unknown_reference_is_not_silently_true(self):
        ok, detail = evaluate_check(parse_check("$steps.nope.x present"), {"steps": {}})
        assert ok is False
        assert "no 'x'" in detail or "no 'nope'" in detail

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "text present", "$input present", "$input.text maybe 3", "== 3",
         "$input.text ==", "$input.text always"],
    )
    def test_unparsable_texts_raise(self, text):
        with pytest.raises(CheckError):
            parse_check(text)

    def test_a_constant_cannot_be_combined(self):
        with pytest.raises(CheckError):
            parse_check("always and $input.a present")

    def test_references_are_reported(self):
        check = parse_check("$input.a present and $steps.b.c == 1")
        assert check.refs == ["$input.a", "$steps.b.c"]

    def test_checks_are_data(self):
        payload = parse_check("always").to_dict()
        assert payload["text"] == "always" and payload["clauses"][0]["op"] == "always"


# --------------------------------------------------------------------------- #
# parsing the document                                                          #
# --------------------------------------------------------------------------- #


class TestParse:
    def test_roundtrip(self):
        doc = _two_step_doc()
        comp = parse_composite(doc)
        again = parse_composite(comp.to_dict())
        assert again.to_dict() == comp.to_dict()
        assert [step.id for step in comp.steps] == ["normalize", "render"]

    def test_unknown_composite_field_is_an_error(self):
        doc = _two_step_doc()
        doc["endstate"] = "always"
        with pytest.raises(CompositeError) as exc:
            parse_composite(doc)
        assert "unknown field" in str(exc.value)

    def test_unknown_step_field_is_an_error(self):
        doc = _two_step_doc()
        doc["steps"][0]["preconditions"] = "always"
        with pytest.raises(CompositeError) as exc:
            parse_composite(doc)
        assert "unknown field" in str(exc.value)

    def test_missing_name_is_an_error(self):
        doc = _two_step_doc()
        doc.pop("name")
        with pytest.raises(CompositeError):
            parse_composite(doc)

    def test_inputs_accept_list_and_mapping(self):
        doc = _two_step_doc()
        doc["inputs"] = ["text"]
        comp = parse_composite(doc)
        assert comp.inputs == {"text": {"type": "string", "required": True}}
        assert comp.required_inputs == ["text"]

    def test_non_string_outputs_are_refused(self):
        doc = _two_step_doc()
        doc["steps"][0]["outputs"] = [1, 2]
        with pytest.raises(CompositeError):
            parse_composite(doc)

    def test_registry_row_mirrors_the_chain(self):
        comp = parse_composite(_two_step_doc())
        spec = comp.to_spec(status=STATUS_APPROVED)
        assert spec.kind == KIND_COMPOSITION
        assert [step["id"] for step in spec.steps] == ["normalize", "render"]
        # the registry spelling tools.validate_composition understands
        assert spec.steps[0]["tool"] == "step_normalize"
        mirrored = composite_from_registry(spec)
        assert [step.id for step in mirrored.steps] == ["normalize", "render"]

    def test_document_digest_is_stable(self):
        comp = parse_composite(_two_step_doc())
        first = document_digest(comp.to_dict())
        assert first == document_digest(parse_composite(comp.to_dict()).to_dict())
        assert len(first) == 64

    def test_document_roundtrips_through_the_runtime_dir(self, tmp_path):
        runtime = tmp_path / "rt"
        comp = parse_composite(_two_step_doc())
        path = save_composite(runtime, comp)
        assert path == composite_path(runtime, "text_report")
        loaded = load_composite(runtime, "text_report")
        assert loaded is not None and loaded.to_dict() == comp.to_dict()
        assert load_composite(runtime, "nope") is None


# --------------------------------------------------------------------------- #
# validation                                                                    #
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_a_clean_composite_validates(self, two_tools):
        runtime, store, comp = two_tools
        report = validate_composite(store, comp)
        assert report.problems == [], report.problems
        assert report.ok is True and report.executable is True
        assert report.missing_kinds == []
        assert [item.id for item in report.steps] == ["normalize", "render"]
        assert all(item.executable for item in report.steps)
        assert report.checks_parsed == 6  # composite pre + end, and 2x(step pre, end)
        assert report.to_dict()["executors"]["tool"]["executable"] is True

    def test_unknown_tool_step_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[1].target = "does_not_exist"
        report = validate_composite(store, comp)
        assert any("unknown tool 'does_not_exist'" in item for item in report.problems)

    def test_unapproved_tool_step_is_a_problem(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize", "def run(text=''):\n    return {'upper': text}\n")
        _register_tool(
            runtime, "step_render", "def run(upper=''):\n    return {'report': upper}\n",
            status=STATUS_CANDIDATE,
        )
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(_two_step_doc())
        report = validate_composite(store, comp)
        assert any("not approved" in item for item in report.problems)
        store.close()

    def test_revoked_tool_step_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        store.set_status("step_render", "revoked")
        report = validate_composite(store, comp)
        assert any("revoked" in item for item in report.problems)

    def test_a_composite_without_steps_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps = []
        report = validate_composite(store, comp)
        assert any("no steps" in item for item in report.problems)

    def test_a_step_without_outputs_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[0].outputs = []
        report = validate_composite(store, comp)
        assert any("declares no outputs" in item for item in report.problems)

    @pytest.mark.parametrize("field", ["precondition", "end_state"])
    def test_a_step_without_its_checks_is_a_problem(self, two_tools, field):
        runtime, store, comp = two_tools
        setattr(comp.steps[0], field, "")
        report = validate_composite(store, comp)
        assert any(f"declares no {field} check" in item for item in report.problems)

    def test_an_unparsable_check_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[1].end_state = "$input.text ist da"
        report = validate_composite(store, comp)
        assert any("expected one of" in item for item in report.problems)

    def test_a_forward_reference_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[0].inputs = {"text": "$steps.render.report"}
        report = validate_composite(store, comp)
        assert any("does not run before it" in item for item in report.problems)

    def test_a_duplicate_step_id_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[1].id = "normalize"
        report = validate_composite(store, comp)
        assert any("duplicate step id" in item for item in report.problems)

    def test_reading_an_undeclared_output_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[1].inputs = {"upper": "$steps.normalize.not_declared"}
        report = validate_composite(store, comp)
        assert any("not a declared output" in item for item in report.problems)

    def test_an_unproduced_composite_output_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.outputs = ["report", "ghost"]
        report = validate_composite(store, comp)
        assert any("'ghost' is produced by no step" in item for item in report.problems)

    def test_a_bad_name_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.name = "Text Report"
        report = validate_composite(store, comp)
        assert any("not a valid tool name" in item for item in report.problems)

    def test_an_undeclared_composite_input_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[0].inputs = {"text": "$input.whatever"}
        report = validate_composite(store, comp)
        assert any("not a declared composite input" in item for item in report.problems)

    def test_an_unused_input_is_only_a_warning(self, two_tools):
        runtime, store, comp = two_tools
        comp.inputs["extra"] = {"type": "string", "required": False}
        report = validate_composite(store, comp)
        assert report.problems == []
        assert any("'extra' is read by nothing" in item for item in report.warnings)

    def test_an_unconsumed_step_output_is_only_a_warning(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[0].outputs = ["upper", "unused"]
        report = validate_composite(store, comp)
        assert report.problems == []
        assert any("'unused' is consumed by nothing" in item for item in report.warnings)

    def test_an_unknown_kind_is_a_problem(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[0].kind = "telepathy"
        report = validate_composite(store, comp)
        assert any("unknown kind 'telepathy'" in item for item in report.problems)


class TestValidationOfKindsWithRealRunners:
    """``api`` and ``gui`` are not gaps any more: they have real runners, and the probe
    says whether *this host* can run them."""

    def _doc(self, step, *, outputs=("text",)):
        return {
            "name": "kind_chain",
            "inputs": {"text": {"type": "string", "required": True}},
            "outputs": list(outputs),
            "end_state": "always",
            "steps": [step],
        }

    def test_an_api_step_is_executable_through_the_real_runner(self, two_tools):
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {
                    "id": "call", "kind": "api", "url": "https://example.invalid/x",
                    "method": "POST", "outputs": ["text"], "precondition": "always",
                    "end_state": "exit_ok",
                }
            )
        )
        report = validate_composite(store, comp)
        assert report.problems == []
        assert report.ok is True
        assert report.missing_kinds == []
        assert report.executable is True
        assert report.steps[0].executable is True
        assert "run_api_step" in executor_report()["api"]["runner"]
        assert isinstance(report.steps[0].available, bool)

    def test_an_api_output_the_runner_never_returns_is_a_problem(self, two_tools):
        """A declared output must be something the runner really carries."""
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "call", "kind": "api", "url": "https://example.invalid/x",
                 "outputs": ["body"], "precondition": "always", "end_state": "exit_ok"},
                outputs=("body",),
            )
        )
        assert any("api output 'body' is not one of" in item
                   for item in validate_composite(store, comp).problems)

    def test_an_api_field_the_runner_does_not_declare_is_a_problem(self, two_tools):
        """A typo like ``expct_status`` must never survive validation."""
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "call", "kind": "api", "url": "https://example.invalid/x",
                 "inputs": {"expct_status": [200]}, "outputs": ["text"],
                 "precondition": "always", "end_state": "exit_ok"},
            )
        )
        problems = validate_composite(store, comp).problems
        assert any("expct_status" in item and "not declared by the api runner" in item
                   for item in problems)

    def test_an_api_step_accepts_a_parameterised_url(self, two_tools):
        """``url: "$input.url"`` is the reusable form — legal, and the ref is dataflow."""
        runtime, store, _ = two_tools
        comp = parse_composite(
            {
                "name": "kind_chain",
                "inputs": {"url": {"type": "string", "required": True}},
                "outputs": ["text"],
                "end_state": "always",
                "steps": [
                    {"id": "call", "kind": "api", "url": "$input.url", "outputs": ["text"],
                     "precondition": "always", "end_state": "exit_ok"}
                ],
            }
        )
        report = validate_composite(store, comp)
        assert report.problems == [], report.problems
        assert report.executable is True

    def test_an_api_step_with_an_undeclared_url_input_is_a_problem(self, two_tools):
        runtime, store, _ = two_tools
        comp = parse_composite(
            {
                "name": "kind_chain",
                "inputs": {"text": {"type": "string", "required": True}},
                "outputs": ["text"],
                "end_state": "always",
                "steps": [
                    {"id": "call", "kind": "api", "url": "$input.endpoint",
                     "outputs": ["text"], "precondition": "always", "end_state": "exit_ok"}
                ],
            }
        )
        problems = validate_composite(store, comp).problems
        assert any("url uses $input.endpoint, which is not a declared composite input" in item
                   for item in problems)

    def test_an_api_step_without_url_is_a_problem(self, two_tools):
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "call", "kind": "api", "outputs": ["body"], "precondition": "always",
                 "end_state": "exit_ok"}
            )
        )
        assert any("needs a url" in item for item in validate_composite(store, comp).problems)

    def test_a_gui_subtool_step_is_executable_and_needs_the_registry_to_resolve(self, two_tools):
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "click", "kind": "gui", "target": "open_browser", "outputs": ["ok"],
                 "precondition": "always", "end_state": "exit_ok"},
                outputs=("ok",),
            )
        )
        report = validate_composite(store, comp)
        assert report.problems == []
        assert report.missing_kinds == []
        assert report.executable is True
        assert "run_gui_step" in executor_report()["gui"]["runner"]
        # without a sub-tool registry the name cannot be checked here — said out loud,
        # never silently assumed to exist
        assert any("cannot be resolved without a sub-tool registry" in item
                   for item in report.warnings)

    def test_a_gui_subtool_that_really_exists_resolves_against_the_registry(self, two_tools):
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "click", "kind": "gui", "target": "open_browser", "outputs": ["ok"],
                 "precondition": "always", "end_state": "exit_ok"},
                outputs=("ok",),
            )
        )

        class _Registry:
            def resolve(self, name):
                assert name == "open_browser"
                return {"name": name, "origin": "builtin"}

        report = validate_composite(store, comp, subtools=_Registry())
        assert report.problems == []
        assert not any("cannot be resolved" in item for item in report.warnings)

    def test_a_gui_subtool_the_registry_does_not_know_is_a_problem(self, two_tools):
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "click", "kind": "gui", "target": "ghost_flow", "outputs": ["ok"],
                 "precondition": "always", "end_state": "exit_ok"},
                outputs=("ok",),
            )
        )

        class _Registry:
            def resolve(self, name):
                raise KeyError(f"unknown sub-tool {name!r}")

        problems = validate_composite(store, comp, subtools=_Registry()).problems
        assert any("sub-tool 'ghost_flow' is not registered" in item for item in problems)

    def test_an_inline_gui_flow_needs_actions_and_an_end_state(self, two_tools):
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "flow", "kind": "gui", "target": "inline", "outputs": ["observation"],
                 "precondition": "always", "end_state": "exit_ok"},
                outputs=("observation",),
            )
        )
        problems = validate_composite(store, comp).problems
        assert any("declares no inputs.actions" in item for item in problems)
        assert any("declares no inputs.end_state" in item for item in problems)

    def test_a_ref_nested_inside_a_gui_flow_is_dataflow_too(self, two_tools):
        """A binding buried in ``inputs.actions`` is checked like a top-level one."""
        runtime, store, _ = two_tools
        comp = parse_composite(
            self._doc(
                {"id": "flow", "kind": "gui", "target": "inline", "outputs": ["observation"],
                 "inputs": {
                     "actions": [
                         {"kind": "cli", "channel": "api",
                          "argv": ["sh", "-c", "echo $steps.ghost.stdout"],
                          "verify": {"expect_stdout_contains": "x"}}
                     ],
                     "end_state": {"expect_observe_contains": "$steps.relay.text"},
                 },
                 "precondition": "always", "end_state": "exit_ok"},
                outputs=("observation",),
            )
        )
        problems = validate_composite(store, comp).problems
        assert any("reads unknown step 'ghost'" in item for item in problems)
        assert any("reads unknown step 'relay'" in item for item in problems)

    def test_missing_kinds_is_now_empty_for_every_wired_kind(self):
        assert missing_kinds(["tool", "api", "api", "gui", "docker"]) == []
        assert missing_kinds(["tool", "docker"]) == []
        assert missing_kinds(["tool", "telepathy"]) == ["telepathy"]


class TestValidationOfDockerSteps:
    def _doc(self, step):
        return {
            "name": "docker_chain",
            "inputs": {"text": {"type": "string", "required": True}},
            "outputs": ["stdout"],
            "end_state": "$steps.box.stdout present",
            "steps": [step],
        }

    def _step(self, **overrides):
        step = {
            "id": "box",
            "kind": "docker",
            "image_digest": DIGEST,
            "argv": ["sh", "-c", "echo $input.text"],
            "outputs": ["stdout"],
            "precondition": "always",
            "end_state": "exit_ok and $steps.box.stdout present",
        }
        step.update(overrides)
        return step

    def test_a_pinned_docker_step_validates(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        report = validate_composite(store, parse_composite(self._doc(self._step())))
        assert report.problems == []
        assert report.executable is True
        store.close()

    def test_an_unpinned_image_is_refused_by_policy(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(self._doc(self._step(image_digest="", image="alpine:3.20")))
        report = validate_composite(store, comp)
        assert any("pin-required" in item for item in report.problems)
        store.close()

    def test_a_docker_step_may_not_use_inputs(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(self._doc(self._step(inputs={"text": "$input.text"})))
        report = validate_composite(store, comp)
        assert any("inputs must stay empty" in item for item in report.problems)
        store.close()

    def test_an_argv_ref_to_an_unknown_input_is_a_problem(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(self._doc(self._step(argv=["sh", "-c", "echo $input.nope"])))
        report = validate_composite(store, comp)
        assert any("not a declared composite input" in item for item in report.problems)
        store.close()

    def test_a_docker_output_outside_the_runner_keys_is_a_problem(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(self._doc(self._step(outputs=["rainbow"])))
        report = validate_composite(store, comp)
        assert any("is not one of" in item for item in report.problems)
        assert set(DOCKER_OUTPUT_KEYS) == {"ok", "exit_code", "stdout", "stderr"}
        store.close()


# --------------------------------------------------------------------------- #
# execution                                                                     #
# --------------------------------------------------------------------------- #


class TestRun:
    @SANDBOX
    def test_a_two_step_composite_really_runs(self, two_tools):
        runtime, store, comp = two_tools
        run = run_composite(store, comp, inputs={"text": "  hello compose  "}, runtime=runtime)
        assert run.status == "verified", run.reason
        assert run.ok is True and run.verified is True
        assert run.exit_code == 0
        assert run.outputs == {"report": "REPORT:HELLO COMPOSE"}
        assert [step.status for step in run.steps] == ["ok", "ok"]
        assert run.steps[0].outputs == {"upper": "HELLO COMPOSE"}
        assert run.steps[0].evidence and "step_normalize" in run.steps[0].evidence
        assert run.to_dict()["step_count"] == 2

    @SANDBOX
    def test_the_context_really_carries_the_previous_output(self, two_tools):
        """The second step must see the first step's value, not a placeholder."""
        runtime, store, comp = two_tools
        comp.steps[1].target = "step_render"
        run = run_composite(store, comp, inputs={"text": "abc"}, runtime=runtime)
        assert run.outputs["report"] == "REPORT:ABC"

    def test_a_missing_required_input_is_usage(self, two_tools):
        runtime, store, comp = two_tools
        run = run_composite(store, comp, inputs={}, runtime=runtime)
        assert run.status == "usage" and run.exit_code == 1
        assert "text" in run.reason
        assert run.steps == [], "nothing may run without its input"

    @SANDBOX
    def test_a_step_failure_fails_the_composite(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize", "def run(text=''):\n    return {'upper': text}\n")
        _register_tool(
            runtime,
            "step_render",
            "def run(upper=''):\n    raise RuntimeError('render exploded')\n",
        )
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(_two_step_doc())
        run = run_composite(store, comp, inputs={"text": "x"}, runtime=runtime)
        assert run.status == "failed" and run.exit_code == 2
        assert run.steps[-1].status == "failed"
        assert "render exploded" in run.reason
        store.close()

    @SANDBOX
    def test_a_declared_output_absent_from_the_result_fails_the_step(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize", "def run(text=''):\n    return {'other': text}\n")
        _register_tool(runtime, "step_render", "def run(upper=''):\n    return {'report': upper}\n")
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(_two_step_doc())
        run = run_composite(store, comp, inputs={"text": "x"}, runtime=runtime)
        assert run.status == "failed" and run.exit_code == 2
        assert "declared output(s) ['upper'] are absent" in run.reason
        store.close()

    @SANDBOX
    def test_a_false_step_end_state_fails_the_composite(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize", "def run(text=''):\n    return {'upper': text}\n")
        _register_tool(runtime, "step_render", "def run(upper=''):\n    return {'report': upper}\n")
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(_two_step_doc())
        comp.steps[1].end_state = "$steps.render.report contains 'REPORT'"
        run = run_composite(store, comp, inputs={"text": "x"}, runtime=runtime)
        assert run.status == "failed" and run.exit_code == 2
        assert run.steps[1].status == "end_state_failed"
        store.close()

    @SANDBOX
    def test_a_false_step_precondition_stops_before_the_step(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "step_normalize", "def run(text=''):\n    return {'upper': 'OK'}\n")
        _register_tool(runtime, "step_render", "def run(upper=''):\n    return {'report': upper}\n")
        store = ToolStore(runtime / "tools.db")
        comp = parse_composite(_two_step_doc())
        comp.steps[1].precondition = "$steps.normalize.upper contains 'NEVER'"
        run = run_composite(store, comp, inputs={"text": "x"}, runtime=runtime)
        assert run.status == "failed" and run.exit_code == 2
        assert run.steps[0].status == "ok"
        assert run.steps[1].status == "precondition_failed"
        assert len(run.steps) == 2, "the second step must not have run"
        store.close()

    @SANDBOX
    def test_a_false_composite_end_state_fails_after_all_steps(self, two_tools):
        runtime, store, comp = two_tools
        comp.end_state = "$steps.render.report contains 'ABSENT'"
        run = run_composite(store, comp, inputs={"text": "x"}, runtime=runtime)
        assert run.status == "failed" and run.exit_code == 2
        assert [step.status for step in run.steps] == ["ok", "ok"]
        assert "composite end_state is false" in run.reason

    @SANDBOX
    def test_a_false_composite_precondition_runs_nothing(self, two_tools):
        runtime, store, comp = two_tools
        comp.precondition = "$input.text contains 'nope'"
        run = run_composite(store, comp, inputs={"text": "x"}, runtime=runtime)
        assert run.status == "failed" and run.exit_code == 2
        assert run.steps == []

    def test_an_api_step_really_goes_out_and_a_dead_endpoint_is_a_real_failure(self, two_tools):
        """Not blocked any more: the request is made, and the refused connection is the
        step's own verdict (exit 2), with the failure class named."""
        runtime, store, _ = two_tools
        comp = parse_composite(
            {
                "name": "api_chain",
                "inputs": {"text": {"type": "string", "required": True}},
                "outputs": ["text"],
                "end_state": "always",
                "steps": [
                    {"id": "call", "kind": "api", "url": "http://127.0.0.1:9/x",
                     "timeout_s": 5, "outputs": ["text"], "precondition": "always",
                     "end_state": "exit_ok"}
                ],
            }
        )
        run = run_composite(store, comp, inputs={"text": "x"})
        assert run.missing_kinds == []
        assert run.status == "failed" and run.exit_code == 2
        step = run.steps[0]
        assert step.kind == "api" and step.ok is False
        assert step.failure_class == "network_error"
        assert "run_api_step" in step.evidence
        assert step.outputs["attempts"] == 1, "one attempt unless the step declares a retry"

    def test_a_parameterised_url_is_bound_from_the_input_and_really_used(self, two_tools):
        """A reusable api step: the url comes from the invocation, and the request goes
        to *that* address (proved by the port it really failed to reach)."""
        runtime, store, _ = two_tools
        comp = parse_composite(
            {
                "name": "api_chain",
                "inputs": {"url": {"type": "string", "required": True}},
                "outputs": ["text"],
                "precondition": "always",
                "end_state": "always",
                "steps": [
                    {"id": "call", "kind": "api", "url": "$input.url", "timeout_s": 5,
                     "outputs": ["text"], "precondition": "always", "end_state": "exit_ok"}
                ],
            }
        )
        run = run_composite(store, comp, inputs={"url": "http://127.0.0.1:9/v1"})
        step = run.steps[0]
        assert step.kind == "api" and step.failure_class == "network_error"
        assert step.outputs["url"] == "http://127.0.0.1:9/v1", (
            "the url the runner used must be the value supplied to the invocation"
        )

    def test_a_gui_step_without_a_bridge_is_refused_by_name_not_by_crash(self, two_tools,
                                                                        monkeypatch):
        """The refusal is a first-class outcome: blocked, exit 3, the bridge named."""
        import skaldir.act_service as act_service

        runtime, store, _ = two_tools
        monkeypatch.setattr(
            act_service,
            "resolve_runner",
            lambda kind, **kw: (
                None,
                {"requested": kind, "resolved": False, "real": False, "fixture": False,
                 "reason": "no isolation bridge is available on this stubbed host"},
            ),
        )
        comp = parse_composite(
            {
                "name": "gui_chain",
                "outputs": ["ok"],
                "end_state": "always",
                "steps": [
                    {"id": "click", "kind": "gui", "target": "open_browser", "outputs": ["ok"],
                     "precondition": "always", "end_state": "exit_ok"}
                ],
            }
        )
        run = run_composite(store, comp)
        assert run.status == "blocked" and run.exit_code == 3
        assert run.missing_kinds == ["gui"]
        step = run.steps[0]
        assert step.status == "blocked" and step.missing_kind == "gui"
        assert step.missing_capability
        assert "stubbed host" in step.detail
        assert step.outputs["missing_capability"] == step.missing_capability

    def test_a_composite_gui_step_can_never_fall_back_to_the_fixture(self, two_tools,
                                                                   monkeypatch):
        """``allow_fixture`` is not reachable from a composite document: a dry-run runner
        resolution is refused (exit 3) instead of silently "verifying" a GUI step."""
        import skaldir.act_service as act_service

        runtime, store, _ = two_tools
        monkeypatch.setattr(
            act_service,
            "resolve_runner",
            lambda kind, **kw: (
                act_service.DryRunRunner(),
                {"requested": kind, "bridge": act_service.BRIDGE_DRY_RUN, "resolved": True,
                 "isolation": "dry-run", "real": False, "fixture": True,
                 "scope": "in-process fixture", "reason": "a fixture is what you asked for"},
            ),
        )
        comp = parse_composite(
            {
                "name": "gui_chain",
                "outputs": ["ok"],
                "end_state": "always",
                "steps": [
                    {"id": "click", "kind": "gui", "target": "open_browser", "outputs": ["ok"],
                     "precondition": "always", "end_state": "exit_ok"}
                ],
            }
        )
        run = run_composite(store, comp)
        assert run.status == "blocked" and run.exit_code == 3
        assert run.steps[0].missing_kind == "gui"
        assert "fixture" in run.steps[0].detail

    def test_an_invalid_composite_is_blocked_before_any_step(self, two_tools):
        runtime, store, comp = two_tools
        comp.steps[0].target = "ghost"
        run = run_composite(store, comp, inputs={"text": "x"}, runtime=runtime)
        assert run.status == "blocked" and run.exit_code == 3
        assert "validation:" in run.reason and run.steps == []


# --------------------------------------------------------------------------- #
# compose candidates                                                            #
# --------------------------------------------------------------------------- #


class TestBuildableChains:
    def test_an_empty_registry_proposes_nothing(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        store = ToolStore(runtime / "tools.db")
        payload = buildable_chains(store)
        assert payload["steps_considered"] == 0
        assert payload["chains"] == [] and payload["chain_count"] == 0
        assert payload["rule"].startswith("a chain candidate is a heuristic proposal")
        assert payload["executors"]["tool"]["executable"] is True
        store.close()

    def test_only_approved_executable_tools_are_steps(self, tmp_path, monkeypatch):
        runtime = _runtime(tmp_path, monkeypatch)
        _register_tool(runtime, "approved_one", "def run(text=''):\n    return {'a': text}\n")
        _register_tool(
            runtime, "candidate_one", "def run(text=''):\n    return {'a': text}\n",
            status=STATUS_CANDIDATE,
        )
        store = ToolStore(runtime / "tools.db")
        payload = buildable_chains(store)
        assert payload["steps_considered"] == 1
        assert payload["chains"] == [], "one step is no chain"
        store.close()

    def test_two_joined_tools_form_one_chain(self, two_tools):
        runtime, store, _ = two_tools
        payload = buildable_chains(store)
        assert payload["steps_considered"] == 2
        assert payload["chain_count"] >= 1
        top = payload["chains"][0]
        assert top["length"] == 2
        assert set(top["chain"]) == {"step_normalize", "step_render"}
        assert top["join_terms"] == ["text"]
        assert top["joins"][0]["terms"] == ["text"]
        assert top["joins"][0]["from"] and top["joins"][0]["to"]
        assert top["join_score"] >= 1
        assert isinstance(top["join_terms"][0], str), "join terms are terms, not nested lists"

    def test_stored_composites_are_listed_next_to_the_chains(self, two_tools):
        runtime, store, comp = two_tools
        save_composite(runtime, comp)
        store.upsert(comp.to_spec(status=STATUS_APPROVED))
        payload = buildable_chains(store)
        assert [row["name"] for row in payload["composites"]] == ["text_report"]
        assert payload["composites"][0]["status"] == STATUS_APPROVED
        assert payload["composites"][0]["kinds"] == ["tool", "tool"]

    def test_the_need_ranks_the_seeds(self, two_tools):
        runtime, store, _ = two_tools
        payload = buildable_chains(store, need="normalize text")
        assert payload["need"] == "normalize text"
        assert payload["chains"], "a matching need must still propose a chain"

    def test_the_proposal_is_labelled_as_a_proposal(self, two_tools):
        runtime, store, _ = two_tools
        top = buildable_chains(store)["chains"][0]
        assert "declare inputs/outputs" in top["proposal"]
        assert "registry step vocabulary" in top["evidence"]


class TestChainedComparisons:
    """A check may compare against a *previous step's* output — the chain's own dataflow.

    The api runner returns rich structured values (the parsed response body), so a later
    step must be able to assert against them without hard-coding a literal.
    """

    def test_a_right_hand_ref_is_parsed_and_resolved(self):
        check = parse_check("$steps.relay.text contains $steps.probe.stdout")
        assert check.refs == ["$steps.relay.text", "$steps.probe.stdout"]
        context = {"input": {},
                   "steps": {"relay": {"text": "token CW-1"}, "probe": {"stdout": "CW-1"}},
                   "exit_ok": True}
        ok, detail = evaluate_check(check, context)
        assert ok is True and "$steps.probe.stdout" in detail

    def test_a_right_hand_ref_that_does_not_resolve_is_not_a_pass(self):
        check = parse_check("$steps.relay.text contains $steps.ghost.stdout")
        ok, detail = evaluate_check(check, {"input": {}, "steps": {"relay": {"text": "x"}},
                                            "exit_ok": True})
        assert ok is False and "ghost" in detail

    def test_a_quoted_ref_stays_a_literal(self):
        check = parse_check("$steps.relay.text contains '$steps.probe.stdout'")
        assert check.refs == ["$steps.relay.text"]
        ok, _ = evaluate_check(
            check, {"input": {}, "steps": {"relay": {"text": "a $steps.probe.stdout"}},
                                       "exit_ok": True})
        assert ok is True

    def test_a_right_hand_ref_is_dataflow_too(self, two_tools):
        """A ref on the right side is validated exactly like the left side."""
        runtime, store, _ = two_tools
        comp = parse_composite(
            {
                "name": "chain_chain",
                "inputs": {"text": {"type": "string", "required": True}},
                "outputs": ["upper"],
                "precondition": "always",
                "end_state": "$steps.second.upper contains $steps.first.upper",
                "steps": [
                    {"id": "first", "kind": "api", "url": "http://127.0.0.1:9/x",
                     "outputs": ["text"],
                     "precondition": "always", "end_state": "exit_ok"},
                    {"id": "second", "kind": "api", "url": "http://127.0.0.1:9/y",
                     "outputs": ["upper"],
                     "precondition": "always", "end_state": "exit_ok"},
                ],
            }
        )
        problems = validate_composite(store, comp).problems
        assert any("$steps.first.upper is not a declared output of step 'first'" in item
                   for item in problems)


def test_a_step_output_may_be_read_through_a_deeper_path():
    """``$steps.relay.json.token`` — a path into a declared output is legal and resolves."""
    check = parse_check("$steps.gui1.observation contains $steps.relay.json.token")
    ok, detail = evaluate_check(
        check,
        {"input": {}, "exit_ok": True,
         "steps": {"gui1": {"observation": "CW-3-20-10"},
                   "relay": {"json": {"token": "CW-3-20-10"}}}},
    )
    assert ok is True


def test_shipped_executor_table_names_a_real_runner_for_every_kind():
    """All four kinds are wired; each row names its runner and its host-level availability.

    The table no longer documents a gap: it says which runner serves each kind and whether
    this host's own probe can run it — the two different things a caller needs to know.
    """
    report = executor_report()
    assert sorted(report) == ["api", "docker", "gui", "tool"]
    for kind, row in report.items():
        assert row["executable"] is True, kind
        assert row["runner"], kind
        assert row["reason"] == "", kind
        assert isinstance(row["available"], bool), kind
    assert "run_api_step" in report["api"]["runner"]
    assert "run_gui_step" in report["gui"]["runner"]
    assert "DockerRuntimeManager" in report["docker"]["runner"]
    assert "mcpserver._wrapper_for" in report["tool"]["runner"]


def test_a_step_spec_is_json_serialisable():
    step = StepSpec(id="s1", kind="tool", target="t", outputs=["o"], precondition="always",
                    end_state="exit_ok")
    assert json.loads(json.dumps(step.to_dict()))["id"] == "s1"
