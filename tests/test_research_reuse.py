"""P0-3 — the research archive is a reusable knowledge base.

Before: research produced temporary files nobody read again. Now: prior
findings are searched by similarity BEFORE a new investigation, carry
provenance (confidence, age, staleness) and are recorded as reused_from.
"""

import json
import time

from skaldir.events import EventStore
from skaldir.research import ResearchRunner, find_prior_research

PROBLEM = "the sandbox cannot see the pytest interpreter path"


def _run(tmp_path, problem, step=1):
    runner = ResearchRunner(
        store=EventStore(tmp_path / "events.db"), state_dir=tmp_path, repo_root=None
    )
    return runner.run(
        session_id="p0-3",
        base_step=step,
        trigger_kind="contradiction",
        trigger_fp=f"fp-{step}",
        problem=problem,
    )


class TestResearchReuse:
    def test_artifacts_carry_provenance_and_freshness(self, tmp_path):
        _run(tmp_path, PROBLEM)
        files = list((tmp_path / "research").glob("research-*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["confidence"] == "unverified"  # findings start unverified
        assert data["stale_after"] > data["created_at"]
        assert data["prior_findings"] == []  # nothing existed before this one

    def test_prior_research_is_found_by_similarity(self, tmp_path):
        _run(tmp_path, PROBLEM)
        hits = find_prior_research(tmp_path, "sandbox cannot resolve the pytest interpreter")
        assert hits and hits[0]["confidence"] == "unverified"
        assert hits[0]["stale"] is False
        assert find_prior_research(tmp_path, "banana unrelated topic") == []

    def test_second_run_reuses_the_first(self, tmp_path):
        first = _run(tmp_path, PROBLEM, step=1)
        time.sleep(0.05)
        _run(tmp_path, PROBLEM, step=5)
        files = sorted(
            (tmp_path / "research").glob("research-*.json"), key=lambda p: p.stat().st_mtime
        )
        assert len(files) == 2
        second = json.loads(files[-1].read_text())
        assert second["reused_from"] == [first.research_id]
        assert any(f"research#{first.research_id}" in line for line in second["memory"])

    def test_stale_findings_are_flagged_never_trusted_blindly(self, tmp_path):
        directory = tmp_path / "research"
        directory.mkdir()
        old = time.time() - 30 * 86400
        (directory / "research-oldoldold0.json").write_text(
            json.dumps(
                {
                    "id": "research-oldoldold0",
                    "problem": "sandbox pytest interpreter path broken",
                    "created_at": old,
                    "confidence": "unverified",
                    "llm": {"finding": "an old workaround"},
                }
            ),
            encoding="utf-8",
        )
        hits = find_prior_research(tmp_path, PROBLEM)
        assert hits and hits[0]["stale"] is True
        assert hits[0]["age_hours"] > 700
