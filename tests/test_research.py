"""Research runner — bounded gathering, condensed redirect, archived raw."""

import json

from skaldir.events import EventStore
from skaldir.incidents import Incident, IncidentStore
from skaldir.llm import ScriptedLLM
from skaldir.prompts import PromptRegistry
from skaldir.research import ResearchRunner


def _runner(tmp_path, **kwargs):
    store = EventStore(tmp_path / "events.db")
    runner = ResearchRunner(
        store=store,
        state_dir=tmp_path / "state",
        **kwargs,
    )
    return runner, store


def _seed(store, steps=3):
    for step in range(1, steps + 1):
        store.append(
            "r1", step, "tool_result", {"tool": "terminal", "error": "AssertionError: 1 != 2"},
            ts=float(step),
        )


class TestTemplateFallback:
    def test_runs_without_llm(self, tmp_path):
        runner, store = _runner(tmp_path)
        _seed(store)
        result = runner.run(
            session_id="r1",
            base_step=3,
            trigger_kind="loop",
            trigger_fp="fp1",
            problem="loop: pytest -q 3× in 8 Steps; AssertionError: 1 != 2",
        )
        assert result.status == "ok"
        assert result.used_llm is False
        assert "approach" in result.direction  # loop template
        assert result.duration_ms >= 0
        redirect = result.redirect_md()
        assert "voice·pause" in redirect
        assert "Problem:" in redirect and "Empfohlene Richtung:" in redirect
        store.close()

    def test_archive_written_and_redacted(self, tmp_path):
        runner, store = _runner(tmp_path)
        _seed(store)
        result = runner.run(
            session_id="r1",
            base_step=3,
            trigger_kind="error_burst",
            trigger_fp="fp2",
            problem="token sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2 leaked in output",
        )
        assert result.archive_path
        content = open(result.archive_path, encoding="utf-8").read()
        assert "sk-A1b2C3d4E5f6G7h8I9j0K1l2" not in content  # redacted on disk
        assert "sk-[REDACTED" in content or "REDACTED" in content
        store.close()


class TestLlmPath:
    def test_llm_redirect_used(self, tmp_path):
        payload = json.dumps(
            {
                "problem": "pytest schlägt weiter fehl",
                "evidence": ["step 3: AssertionError"],
                "direction": "Minimalen Repro bauen und den Wertevergleich prüfen",
                "avoid": "Blindes Neuschreiben der Suite",
                "uncertainty": "Ursache unbekannt",
            }
        )
        client = ScriptedLLM(responses={"voice_redirect": payload})
        runner, store = _runner(tmp_path, client=client, registry=PromptRegistry())
        _seed(store)
        result = runner.run(
            session_id="r1", base_step=3, trigger_kind="error_burst", trigger_fp="fp", problem="x"
        )
        assert result.used_llm is True
        assert "Repro" in result.direction
        assert result.tokens_est > 0
        store.close()

    def test_llm_failure_keeps_template(self, tmp_path):
        class Boom(ScriptedLLM):
            def __init__(self):
                super().__init__(responses={})

            def complete(self, *, prompt_id, prompt, tier):
                raise RuntimeError("host denied")

        runner, store = _runner(tmp_path, client=Boom(), registry=PromptRegistry())
        _seed(store)
        result = runner.run(
            session_id="r1", base_step=3, trigger_kind="loop", trigger_fp="fp", problem="x"
        )
        assert result.status == "ok"  # template redirect still delivered
        assert result.used_llm is False
        store.close()


class TestRepoScan:
    def test_snippets_trace_to_file_and_line(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "train.py").write_text(
            "import numpy\nvalue = compute_cv_split(data)\n", encoding="utf-8"
        )
        runner, store = _runner(tmp_path, repo_root=repo)
        _seed(store)
        result = runner.run(
            session_id="r1",
            base_step=3,
            trigger_kind="error_burst",
            trigger_fp="fp",
            problem="cannot import name compute_cv_split from train",
        )
        snippets = [line for line in result.evidence if "train.py:" in line]
        assert snippets, result.evidence
        assert "src/train.py:2" in snippets[0]
        store.close()


def test_memory_hits_are_included(tmp_path):
    incidents = IncidentStore(tmp_path / "memory.db")
    incidents.upsert(
        Incident(
            signature="abc123",
            title="cv_split Import-Fix",
            root_cause="Funktion umbenannt",
            resolution_steps=["Import auf split_cv umgestellt"],
            status="active",
        )
    )
    runner, store = _runner(tmp_path, incidents=incidents)
    _seed(store)
    result = runner.run(
        session_id="r1",
        base_step=3,
        trigger_kind="error_burst",
        trigger_fp="fp",
        problem="cv_split Import schlägt fehl",
    )
    assert any("cv_split Import-Fix" in item for item in result.evidence)
    store.close()
    incidents.close()
