"""Skill mining: pattern detection, drafting, lifecycle, drift."""

from skaldir.events import EventStore
from skaldir.llm import ScriptedLLM
from skaldir.prompts import PromptRegistry
from skaldir.skills import (
    STATUS_DEMOTED,
    STATUS_PROMOTED,
    SkillCandidate,
    SkillStore,
    apply_review,
    demote_on_drift,
    draft_skill,
    is_valid_skill_body,
    mine_tool_patterns,
    parse_skill_header,
    record_correction,
    review_agents_md,
    review_all_skills,
    review_skill,
)

CHAIN = ["read_file", "file_edit", "terminal"]


def _fill(store: EventStore, session: str, chain: list[str], repeats: int = 1) -> None:
    step = 0
    for _ in range(repeats):
        for name in chain:
            step += 1
            store.append(session, step, "tool_call", {"name": name, "args": {"s": step}})


class TestMining:
    def test_finds_chain_across_two_sessions(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        _fill(store, "s1", CHAIN, repeats=2)
        _fill(store, "s2", CHAIN, repeats=1)
        candidates = mine_tool_patterns(store, min_occurrences=3, min_sessions=2)
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.name == "workflow-read_file-file_edit-terminal"
        assert candidate.occurrences == 3
        assert candidate.sessions == ["s1", "s2"]
        assert candidate.evidence

    def test_single_session_does_not_qualify(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        _fill(store, "s1", CHAIN, repeats=4)
        assert mine_tool_patterns(store, min_occurrences=3, min_sessions=2) == []

    def test_below_threshold_is_empty(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        _fill(store, "s1", CHAIN, repeats=1)
        _fill(store, "s2", CHAIN, repeats=1)
        assert mine_tool_patterns(store, min_occurrences=3, min_sessions=2) == []


class TestDrafting:
    def test_skeleton_is_valid_skill_md(self):
        candidate = SkillCandidate(
            name="workflow-a-b",
            title="Workflow: a → b",
            tool_chain=["a", "b"],
            occurrences=3,
            sessions=["s1", "s2"],
        )
        draft_skill(candidate)
        assert candidate.body.startswith("---")
        assert is_valid_skill_body(candidate.body)
        header = parse_skill_header(candidate.body)
        assert header["name"] == "workflow-a-b"

    def test_llm_draft_used_when_valid(self):
        candidate = SkillCandidate(name="x", title="X", tool_chain=["a", "b"])
        body = "---\nname: x\ndescription: \"LLM-Draft\"\nversion: 0.1.0\n---\n\n# X"
        client = ScriptedLLM(responses={"skill_draft": body})
        draft_skill(candidate, client=client, registry=PromptRegistry())
        assert "LLM-Draft" in candidate.body

    def test_invalid_llm_draft_falls_back_to_skeleton(self):
        candidate = SkillCandidate(
            name="x", title="X", tool_chain=["a", "b"], sessions=["s1"]
        )
        client = ScriptedLLM(responses={"skill_draft": "kein markdown"})
        draft_skill(candidate, client=client, registry=PromptRegistry())
        assert candidate.body.startswith("---")
        assert is_valid_skill_body(candidate.body)


class TestStoreLifecycle:
    def _store(self, tmp_path) -> SkillStore:
        return SkillStore(tmp_path / "memory.db")

    def test_upsert_dedupes_and_merges(self, tmp_path):
        store = self._store(tmp_path)
        first = store.upsert(
            SkillCandidate(name="w", title="W", occurrences=3, sessions=["s1"])
        )
        assert first.id is not None
        again = store.upsert(
            SkillCandidate(name="w", title="W", occurrences=2, sessions=["s2"])
        )
        assert again.occurrences == 3
        assert again.sessions == ["s1", "s2"]
        assert store.count() == 1

    def test_status_and_usage_stats(self, tmp_path):
        store = self._store(tmp_path)
        store.upsert(SkillCandidate(name="w", title="W"))
        assert store.set_status("w", STATUS_PROMOTED) is True
        promoted = store.get("w")
        assert promoted is not None and promoted.status == STATUS_PROMOTED
        store.record_use("w", success=True)
        store.record_use("w", success=False)
        skill = store.get("w")
        assert skill is not None
        assert skill.applied == 2
        assert skill.success_rate() == 0.5

    def test_demote_on_drift(self, tmp_path):
        store = self._store(tmp_path)
        store.upsert(SkillCandidate(name="w", title="W"))
        store.set_status("w", STATUS_PROMOTED)
        for index in range(5):
            store.record_use("w", success=index == 0)  # 1/5 success
        assert demote_on_drift(store, threshold=0.5, min_uses=5) == ["w"]
        demoted = store.get("w")
        assert demoted is not None and demoted.status == STATUS_DEMOTED

    def test_no_demote_below_min_uses(self, tmp_path):
        store = self._store(tmp_path)
        store.upsert(SkillCandidate(name="w", title="W"))
        store.set_status("w", STATUS_PROMOTED)
        store.record_use("w", success=False)
        assert demote_on_drift(store, threshold=0.5, min_uses=5) == []
        kept = store.get("w")
        assert kept is not None and kept.status == STATUS_PROMOTED

    def test_invalid_status_rejected(self, tmp_path):
        import pytest

        store = self._store(tmp_path)
        store.upsert(SkillCandidate(name="w", title="W"))
        with pytest.raises(ValueError):
            store.set_status("w", "enabled")


class TestCli:
    def test_mine_dry_run_and_apply(self, tmp_path, capsys):
        from skaldir.cli import main

        events = tmp_path / "events.db"
        store = EventStore(events)
        _fill(store, "s1", CHAIN, repeats=2)
        _fill(store, "s2", CHAIN, repeats=1)
        store.close()
        memory = tmp_path / "memory.db"
        code = main(["skills", "mine", "--events", str(events), "--db", str(memory)])
        out = capsys.readouterr().out
        assert code == 0
        assert "workflow-read_file-file_edit-terminal" in out
        assert "applied: False" in out

        code = main(
            ["skills", "mine", "--events", str(events), "--db", str(memory), "--apply"]
        )
        assert code == 0
        assert "applied: True" in capsys.readouterr().out

        assert main(["skills", "list", "--db", str(memory)]) == 0
        out = capsys.readouterr().out
        assert "[candidate]" in out and "workflow-" in out


from skaldir.skills import validate_candidate  # noqa: E402 — test-only import at use site

NAME = "workflow-read_file-file_edit-terminal"


class TestValidation:
    def _setup(self, tmp_path):
        events = EventStore(tmp_path / "events.db")
        _fill(events, "s1", CHAIN, repeats=2)
        _fill(events, "s2", CHAIN, repeats=1)
        store = SkillStore(tmp_path / "memory.db")
        candidate = mine_tool_patterns(events, min_occurrences=3, min_sessions=2)[0]
        draft_skill(candidate)
        store.upsert(candidate)
        return events, store

    def test_valid_candidate_passes(self, tmp_path):
        events, store = self._setup(tmp_path)
        report = validate_candidate(store.get(NAME), events)
        assert report.ok
        assert any("confirmed" in text for _passed, text in report.checks)

    def test_missing_events_fails(self, tmp_path):
        _events, store = self._setup(tmp_path)
        report = validate_candidate(store.get(NAME), None)
        assert not report.ok

    def test_threshold_not_met_fails(self, tmp_path):
        events, store = self._setup(tmp_path)
        report = validate_candidate(store.get(NAME), events, min_occurrences=10)
        assert not report.ok

    def test_invalid_body_fails(self, tmp_path):
        events, store = self._setup(tmp_path)
        skill = store.get(NAME)
        assert skill is not None
        skill.body = "kein frontmatter"
        assert not validate_candidate(skill, events).ok

    def test_error_context_is_informative(self, tmp_path):
        events, store = self._setup(tmp_path)
        events.append("s1", 99, "tool_result", {"exit_code": 1, "stderr": "boom"}, ts=1.0)
        report = validate_candidate(store.get(NAME), events)
        assert report.ok  # errors in context do not fail validation
        assert any("error events" in text for _passed, text in report.checks)


class TestReviewCli:
    def _mine_and_apply(self, tmp_path):
        events = tmp_path / "events.db"
        store = EventStore(events)
        _fill(store, "s1", CHAIN, repeats=2)
        _fill(store, "s2", CHAIN, repeats=1)
        store.close()
        memory = tmp_path / "memory.db"
        from skaldir.cli import main

        args = ["skills", "mine", "--events", str(events), "--db", str(memory), "--apply"]
        assert main(args) == 0
        return events, memory

    def test_validate_promote_export_flow(self, tmp_path, capsys):
        from skaldir.cli import main

        events, memory = self._mine_and_apply(tmp_path)
        code = main(["skills", "validate", NAME, "--events", str(events), "--db", str(memory)])
        out = capsys.readouterr().out
        assert code == 0 and "✓" in out

        code = main(["skills", "promote", NAME, "--events", str(events), "--db", str(memory)])
        out = capsys.readouterr().out
        assert code == 0 and "promoted" in out

        assert main(["skills", "list", "--db", str(memory)]) == 0
        assert "[promoted" in capsys.readouterr().out

        out_dir = tmp_path / "out"
        code = main(["skills", "export", NAME, "--db", str(memory), "--out", str(out_dir)])
        assert code == 0
        assert (out_dir / f"{NAME}.md").read_text(encoding="utf-8").startswith("---")

    def test_promote_refuses_without_events(self, tmp_path, capsys):
        from skaldir.cli import main

        _events, memory = self._mine_and_apply(tmp_path)
        missing = str(tmp_path / "nope.db")
        code = main(["skills", "promote", NAME, "--events", missing, "--db", str(memory)])
        out = capsys.readouterr().out
        assert code == 1 and "refused" in out

    def test_demote_and_show(self, tmp_path, capsys):
        from skaldir.cli import main

        _events, memory = self._mine_and_apply(tmp_path)
        assert main(["skills", "demote", NAME, "--db", str(memory)]) == 0
        assert "demoted" in capsys.readouterr().out
        assert main(["skills", "show", NAME, "--db", str(memory)]) == 0
        out = capsys.readouterr().out
        assert "## Procedure" in out


class TestSelfReview:
    """Self-improvement review: corrections → proposals → apply."""

    def _store(self, tmp_path) -> SkillStore:
        store = SkillStore(str(tmp_path / "skills.db"))
        store.upsert(SkillCandidate(
            name="computer-use",
            title="Computer use",
            body="# Computer use\n\nDrive any app.\n",
            status=STATUS_PROMOTED,
            evidence=["builtin"],
        ))
        return store

    def test_record_correction_folds_into_review(self, tmp_path):
        store = self._store(tmp_path)
        assert record_correction(store, "computer-use", "use Super+Space not Alt+F2")
        proposal = review_skill(store, "computer-use")
        assert proposal is not None
        assert proposal.kind == "correction"
        assert "Super+Space" in proposal.suggested_body
        assert "## Learned corrections" in proposal.suggested_body

    def test_apply_review_updates_body(self, tmp_path):
        store = self._store(tmp_path)
        record_correction(store, "computer-use", "use Super+Space")
        proposal = review_skill(store, "computer-use")
        assert apply_review(store, proposal)
        updated = store.get("computer-use")
        assert "Super+Space" in updated.body

    def test_drift_proposal_on_low_success_rate(self, tmp_path):
        store = self._store(tmp_path)
        for _ in range(6):
            store.record_use("computer-use", success=False)
        proposal = review_skill(store, "computer-use")
        assert proposal is not None
        assert proposal.kind == "drift"

    def test_no_proposal_when_clean(self, tmp_path):
        store = self._store(tmp_path)
        for _ in range(3):
            store.record_use("computer-use", success=True)
        assert review_skill(store, "computer-use") is None

    def test_review_all_skills(self, tmp_path):
        store = self._store(tmp_path)
        record_correction(store, "computer-use", "fix the thing")
        proposals = review_all_skills(store)
        assert len(proposals) == 1
        assert proposals[0].target == "computer-use"

    def test_review_agents_md(self, tmp_path):
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# Standard\n\nLive desktop may control everything.\n")
        proposal = review_agents_md(agents, ["Live desktop may control everything"])
        assert proposal is None  # already present
        proposal = review_agents_md(agents, ["new rule not yet present"])
        assert proposal is not None
        assert proposal.target == "AGENTS.md"
        assert "new rule not yet present" in proposal.suggested_body
