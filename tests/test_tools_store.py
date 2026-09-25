"""Tool specs: store lifecycle, compose-before-create, composition validation, drafts."""

import json

from skaldir.llm import BudgetTracker, ScriptedLLM
from skaldir.prompts import PromptRegistry
from skaldir.tools import (
    KIND_COMPOSITION,
    STATUS_APPROVED,
    STATUS_REVOKED,
    ToolSpec,
    ToolStore,
    compose_candidates,
    compose_candidates_scored,
    draft_tool_spec,
    draft_tool_spec_result,
    propose_tool,
    validate_composition,
)


class BoomLLM:
    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        raise RuntimeError("boom")


def _store(tmp_path) -> ToolStore:
    return ToolStore(tmp_path / "tools.db")


class TestStore:
    def test_upsert_dedupes_by_name(self, tmp_path):
        store = _store(tmp_path)
        first = store.upsert(ToolSpec(name="a", title="A"))
        second = store.upsert(ToolSpec(name="a", title="A2"))
        assert first.id == second.id
        assert store.count() == 1
        assert store.get("a").title == "A2"

    def test_invalid_status_rejected(self, tmp_path):
        import pytest

        store = _store(tmp_path)
        store.upsert(ToolSpec(name="a", title="A"))
        with pytest.raises(ValueError):
            store.set_status("a", "enabled")

    def test_mcp_descriptor(self):
        spec = ToolSpec(name="x", title="Titel", description="Beschreibung", tags=["a"])
        descriptor = spec.to_mcp_descriptor()
        assert descriptor["name"] == "x"
        assert descriptor["description"] == "Beschreibung"


class TestCompose:
    def test_scored_candidates_expose_the_overlap(self, tmp_path):
        """The builder needs the real overlap score (2 = hint, 3 = refuse)."""
        store = _store(tmp_path)
        store.upsert(
            ToolSpec(
                name="dataset_stats",
                title="Dataset statistics",
                description="",
                status=STATUS_APPROVED,
            )
        )
        scored = compose_candidates_scored(
            store, description="compute dataset statistics", min_score=2
        )
        assert [(score, spec.name) for score, spec in scored] == [(2, "dataset_stats")]
        assert compose_candidates_scored(
            store, description="compute dataset statistics", min_score=3
        ) == []
        assert compose_candidates(
            store, description="compute dataset statistics", min_score=3
        ) == []

    def test_default_min_score_keeps_single_term_matches(self, tmp_path):
        store = _store(tmp_path)
        store.upsert(
            ToolSpec(
                name="csv_export",
                title="CSV Export",
                description="writes tables",
                status=STATUS_APPROVED,
            )
        )
        # default min_score=1 — a single shared term still reports the tool
        assert [spec.name for spec in compose_candidates(store, description="export")] == [
            "csv_export"
        ]
        # min_score=2 — one shared term is not worth reporting
        assert compose_candidates(store, description="export", min_score=2) == []

    def test_ranked_by_overlap(self, tmp_path):
        store = _store(tmp_path)
        store.upsert(
            ToolSpec(
                name="dataset_stats",
                title="Dataset Statistik",
                description="berechnet Mittelwert und Varianz über Arrays",
                tags=["numpy"],
                status=STATUS_APPROVED,
            )
        )
        store.upsert(
            ToolSpec(
                name="csv_export",
                title="CSV Export",
                description="schreibt Tabellen",
                status=STATUS_APPROVED,
            )
        )
        candidates = compose_candidates(
            store, description="Statistik über Arrays berechnen", tags=["numpy"]
        )
        assert candidates and candidates[0].name == "dataset_stats"
        assert all(candidate.name != "csv_export" for candidate in candidates)

    def test_candidate_status_is_not_composable(self, tmp_path):
        store = _store(tmp_path)
        store.upsert(
            ToolSpec(name="draft", title="Arrays Statistik", description="arrays statistik")
        )
        assert compose_candidates(store, description="arrays statistik") == []

    def test_propose_reports_composables(self, tmp_path):
        store = _store(tmp_path)
        store.upsert(
            ToolSpec(
                name="base_tool",
                title="Array Statistik",
                description="arrays rechnen",
                status=STATUS_APPROVED,
            )
        )
        stored, composables = propose_tool(
            store,
            ToolSpec(
                name="new_tool",
                title="Array Statistik Plus",
                description="mehr arrays rechnen",
            ),
        )
        assert stored.id is not None
        assert [candidate.name for candidate in composables] == ["base_tool"]


class TestCompositionValidation:
    def test_missing_step_tool_fails(self, tmp_path):
        store = _store(tmp_path)
        spec = ToolSpec(name="comp", title="C", kind=KIND_COMPOSITION, steps=[{"tool": "ghost"}])
        problems = validate_composition(store, spec)
        assert problems and "ghost" in problems[0]

    def test_ok_when_steps_exist(self, tmp_path):
        store = _store(tmp_path)
        store.upsert(ToolSpec(name="a", title="A", status=STATUS_APPROVED))
        store.upsert(ToolSpec(name="b", title="B", status=STATUS_APPROVED))
        spec = ToolSpec(
            name="c",
            title="C",
            kind=KIND_COMPOSITION,
            steps=[{"tool": "a"}, {"tool": "b"}],
        )
        assert validate_composition(store, spec) == []

    def test_revoked_step_fails(self, tmp_path):
        store = _store(tmp_path)
        store.upsert(ToolSpec(name="a", title="A", status=STATUS_APPROVED))
        store.set_status("a", STATUS_REVOKED)
        spec = ToolSpec(name="c", title="C", kind=KIND_COMPOSITION, steps=[{"tool": "a"}])
        problems = validate_composition(store, spec)
        assert problems and "revoked" in problems[0]


class TestDraft:
    def test_compose_decision_parsed(self):
        payload = json.dumps(
            {
                "name": "stats_over_arrays",
                "title": "Statistik aus Arrays",
                "description": "komponiert Kennzahlen",
                "tags": ["numpy"],
                "kind": "composition",
                "compose_decision": "compose",
                "steps": [{"tool": "dataset_stats", "note": "liefert Kennzahlen"}],
                "rationale": "dataset_stats deckt die Rechnung ab",
            }
        )
        client = ScriptedLLM(responses={"tool_spec_draft": payload})
        spec = draft_tool_spec(
            client,
            PromptRegistry(),
            need="Kennzahlen",
            existing_tools=["dataset_stats — Kennzahlen"],
        )
        assert spec is not None
        assert spec.kind == KIND_COMPOSITION
        assert spec.steps[0]["tool"] == "dataset_stats"
        assert spec.provenance.startswith("llm")

    def test_bad_name_is_rejected(self):
        client = ScriptedLLM(responses={"tool_spec_draft": json.dumps({"name": "Bad Name!"})})
        assert draft_tool_spec(client, PromptRegistry(), need="x", existing_tools=[]) is None

    def test_client_failure_returns_none(self):
        assert draft_tool_spec(BoomLLM(), PromptRegistry(), need="x", existing_tools=[]) is None


VALID_DRAFT = json.dumps(
    {
        "name": "csv_stats_report",
        "title": "CSV stats report",
        "description": "komponiert einen Report",
        "tags": ["report"],
        "kind": "composition",
        "compose_decision": "compose",
        "steps": [{"tool": "dataset_stats", "note": "liefert Kennzahlen"}],
        "rationale": "dataset_stats deckt die Rechnung ab",
    }
)


class TestDraftResult:
    """§1.8: the draft reports WHY it failed — a swallowed error is not an answer."""

    def test_budget_exhaustion_is_reported_not_swallowed(self):
        client = ScriptedLLM(responses={"tool_spec_draft": VALID_DRAFT})
        budget = BudgetTracker(limits={"small": 1, "mid": 1})
        outcome = draft_tool_spec_result(
            client, PromptRegistry(), need="report", existing_tools=[], budget=budget
        )
        assert outcome.spec is None
        assert outcome.kind == "budget"
        assert "budget" in outcome.error.lower()
        # the legacy seam keeps its contract (None, no raise)
        assert (
            draft_tool_spec(
                ScriptedLLM(responses={"tool_spec_draft": VALID_DRAFT}),
                PromptRegistry(),
                need="report",
                existing_tools=[],
                budget=BudgetTracker(limits={"small": 1, "mid": 1}),
            )
            is None
        )

    def test_happy_path_reports_the_spec_and_the_usage(self):
        client = ScriptedLLM(responses={"tool_spec_draft": VALID_DRAFT})
        budget = BudgetTracker(limits={"small": 4000, "mid": 4000})
        outcome = draft_tool_spec_result(
            client, PromptRegistry(), need="report", existing_tools=[], budget=budget
        )
        assert outcome.spec is not None and outcome.spec.name == "csv_stats_report"
        assert outcome.kind == ""
        assert outcome.usage and outcome.usage.get("mid", 0) > 0

    def test_invalid_name_is_reported_as_invalid(self):
        client = ScriptedLLM(responses={"tool_spec_draft": json.dumps({"name": "Bad Name!"})})
        outcome = draft_tool_spec_result(
            client, PromptRegistry(), need="x", existing_tools=[]
        )
        assert outcome.spec is None and outcome.kind == "invalid"
        assert "name" in outcome.error.lower()

    def test_client_failure_is_reported_as_llm_error(self):
        outcome = draft_tool_spec_result(
            BoomLLM(), PromptRegistry(), need="x", existing_tools=[]
        )
        assert outcome.spec is None and outcome.kind == "llm"
        assert "boom" in outcome.error.lower()


class TestCli:
    def test_composition_flow(self, tmp_path, capsys):
        from skaldir.cli import main

        db = tmp_path / "tools.db"
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(
            json.dumps(
                {
                    "name": "composed_report",
                    "title": "Report",
                    "description": "Report aus Tools",
                    "kind": "composition",
                    "steps": [{"tool": "csv_export", "note": "schreibt"}],
                }
            ),
            encoding="utf-8",
        )
        assert main(["tools", "list", "--db", str(db)]) == 0
        assert main(["tools", "propose", "--spec", str(spec_file), "--db", str(db)]) == 0
        out = capsys.readouterr().out
        assert "proposed: composed_report" in out

        # approval refuses while the referenced tool is missing
        assert main(["tools", "approve", "composed_report", "--db", str(db)]) == 1
        capsys.readouterr()

        store = ToolStore(db)
        store.upsert(ToolSpec(name="csv_export", title="CSV", status=STATUS_APPROVED))
        store.close()
        assert main(["tools", "approve", "composed_report", "--db", str(db)]) == 0
        assert "approved: composed_report" in capsys.readouterr().out

    def test_python_approve_runs_gates(self, tmp_path, capsys):
        from skaldir.cli import main

        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "gattool.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        db = tmp_path / "tools.db"
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(
            json.dumps(
                {
                    "name": "adder",
                    "title": "Adder",
                    "module": "gattool",
                    "entrypoint": "add",
                    "sample_input": {"a": 1, "b": 2},
                }
            ),
            encoding="utf-8",
        )
        assert main(["tools", "propose", "--spec", str(spec_file), "--db", str(db)]) == 0
        capsys.readouterr()
        assert main(["tools", "approve", "adder", "--db", str(db), "--workdir", str(workdir)]) == 0
        out = capsys.readouterr().out
        assert "✓ scan" in out and "approved: adder" in out

    def test_scan_cli_blocks_evil(self, tmp_path, capsys):
        from skaldir.cli import main

        evil = tmp_path / "evil.py"
        evil.write_text("x = eval('1')\n", encoding="utf-8")
        assert main(["tools", "scan", str(evil)]) == 1
        assert "eval" in capsys.readouterr().out
