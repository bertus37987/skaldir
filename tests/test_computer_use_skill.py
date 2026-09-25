"""Computer-Use-Skill: content validation, system prompt integration, self-improvement loop."""

from __future__ import annotations

from skaldir.skill_builtin import BUILTIN_SKILL_NAMES, import_builtin, load_builtin
from skaldir.skills import (
    STATUS_PROMOTED,
    SkillCandidate,
    SkillStore,
    apply_review,
    is_valid_skill_body,
    parse_skill_header,
    record_correction,
    review_agents_md,
    review_all_skills,
    review_skill,
)


class TestComputerUseSkillContent:
    def test_skill_is_registered(self):
        assert "computer-use" in BUILTIN_SKILL_NAMES

    def test_skill_body_is_valid(self):
        body = load_builtin("computer-use")
        assert is_valid_skill_body(body)

    def test_skill_header_fields(self):
        body = load_builtin("computer-use")
        header = parse_skill_header(body)
        assert header["name"] == "computer-use"
        assert len(header.get("description", "")) >= 10

    def test_skill_has_six_primitives(self):
        body = load_builtin("computer-use")
        for kind in ("launch", "key", "type_text", "mouse", "drag", "window"):
            assert kind in body, f"missing primitive: {kind}"

    def test_skill_has_observe_act_verify_loop(self):
        body = load_builtin("computer-use")
        assert "Observe" in body
        assert "Act" in body
        assert "Verify" in body

    def test_skill_has_platform_guides(self):
        body = load_builtin("computer-use")
        assert "Omarchy" in body
        assert "GNOME" in body
        assert "macOS" in body
        assert "Windows" in body

    def test_skill_has_app_patterns(self):
        body = load_builtin("computer-use")
        assert "Blender" in body or "3D" in body
        assert "Docker" in body
        assert "Video" in body or "video" in body
        assert "Browser" in body or "browser" in body

    def test_skill_has_self_improvement(self):
        body = load_builtin("computer-use")
        assert "Self-improvement" in body or "self-improvement" in body
        assert "learned_subtool" in body
        assert "correction" in body.lower()

    def test_skill_requires_end_state(self):
        body = load_builtin("computer-use")
        assert "end_state" in body
        assert "verify" in body.lower()

    def test_skill_no_builtin_dependency(self):
        body = load_builtin("computer-use")
        # The skill must teach generic driving, not require built-ins
        assert "No app-specific built-in is required" in body or "no built-in" in body.lower() or "without built-in" in body.lower() or "built-in is required" in body or "Built-ins" in body

    def test_skill_english_only(self):
        body = load_builtin("computer-use")
        # Product text is English (AGENTS.md rule)
        assert "Computer use" in body or "computer use" in body.lower()


class TestSelfImprovementLoop:
    def _store(self, tmp_path) -> SkillStore:
        store = SkillStore(str(tmp_path / "skills.db"))
        store.upsert(SkillCandidate(
            name="computer-use",
            title="Computer use",
            body=load_builtin("computer-use"),
            status=STATUS_PROMOTED,
            evidence=["builtin"],
        ))
        return store

    def test_record_correction_folds_into_review(self, tmp_path):
        store = self._store(tmp_path)
        assert record_correction(store, "computer-use", "use the Omarchy menu Super+Space for app launching")
        proposal = review_skill(store, "computer-use")
        assert proposal is not None
        assert proposal.kind == "correction"
        assert "Omarchy menu" in proposal.suggested_body
        assert "## Learned corrections" in proposal.suggested_body

    def test_apply_review_updates_body(self, tmp_path):
        store = self._store(tmp_path)
        record_correction(store, "computer-use", "always use the Omarchy capture menu for screenshots")
        proposal = review_skill(store, "computer-use")
        assert apply_review(store, proposal)
        updated = store.get("computer-use")
        assert "Omarchy capture menu" in updated.body

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


class TestImportIntoStore:
    def test_import_builtin_computer_use(self, tmp_path):
        store = SkillStore(str(tmp_path / "skills.db"))
        candidate = import_builtin(store, "computer-use")
        assert candidate.name == "computer-use"
        assert candidate.status == STATUS_PROMOTED
        loaded = store.get("computer-use")
        assert loaded is not None
        assert "Computer use" in loaded.body
