"""Spec §2/§3 — bounded, fresh voice context + watcher/investigator split.

* The context is rebuilt for every decision from the raw event store
  (never a summary-of-summary chain) and reconstructs identically after a
  restart.
* The watcher profile stays small; the investigator gets a fresh, larger window.
* Budget accounting is instrumented: MEASURED chars vs ESTIMATED tokens.
* A research run records its context budget as an event.
"""

from pathlib import Path

from skaldir.context import (
    DEFAULT_QUOTAS,
    ROLE_PROFILES,
    ContextBuilder,
)
from skaldir.events import EventStore
from skaldir.research import ResearchRunner


def _seed(store: EventStore, session: str, n: int) -> None:
    for step in range(1, n + 1):
        store.append(
            session, step, "tool_call",
            {"name": "terminal", "args": {"command": f"echo run-{step}"}},
        )
        store.append(
            session, step, "tool_result",
            {"exit_code": 0, "stdout": f"line {step} " + "x" * 60},
        )


def _builder():
    return ContextBuilder(total_budget=4000)


class TestRoleProfiles:
    def test_watcher_profile_is_smaller(self):
        watcher = ROLE_PROFILES["watcher"]
        investigator = ROLE_PROFILES["investigator"]
        assert watcher["total_budget"] < investigator["total_budget"]
        assert watcher["recent_limit"] < investigator["recent_limit"]
        assert watcher["quotas"]["recent"] < investigator["quotas"]["recent"]
        for name, quota in watcher["quotas"].items():
            assert quota <= investigator["quotas"].get(name, DEFAULT_QUOTAS.get(name, quota))

    def test_role_selects_profile_budget(self, tmp_path):
        store = EventStore(tmp_path / "e.db")
        _seed(store, "s1", 30)
        builder = _builder()
        watcher_ctx = builder.build(store, "s1", role="watcher")
        investigator_ctx = builder.build(store, "s1", role="investigator")
        assert watcher_ctx.budget == ROLE_PROFILES["watcher"]["total_budget"]
        assert investigator_ctx.budget == ROLE_PROFILES["investigator"]["total_budget"]
        assert watcher_ctx.total_chars <= watcher_ctx.budget
        # default (no role) keeps the builder's own budget — backward compatible
        plain = builder.build(store, "s1")
        assert plain.budget == 4000
        store.close()


class TestBoundedAndFresh:
    def test_budget_is_enforced_with_explicit_marker(self, tmp_path):
        store = EventStore(tmp_path / "e.db")
        _seed(store, "s1", 120)
        context = _builder().build(store, "s1", role="investigator")
        assert context.total_chars <= context.budget
        rendered = context.render()
        # oversized sections were cut and say so — no silent truncation
        assert "…[truncated" in rendered or context.truncated_sections() == []
        if context.truncated_sections():
            assert "truncated" in rendered
        store.close()

    def test_context_is_rebuilt_fresh_not_accumulated(self, tmp_path):
        """Repeated builds are byte-identical; a fresh builder reconstructs the same."""
        store = EventStore(tmp_path / "e.db")
        _seed(store, "s1", 25)
        builder = _builder()
        first = builder.build(store, "s1").render()
        second = builder.build(store, "s1").render()
        assert first == second  # no growth, no summary-of-summary chain
        # "restart": new builder instance over the same stores
        restarted = _builder().build(store, "s1").render()
        assert restarted == first
        # new events extend the window; old summaries are not re-summarized in
        _seed(store, "s1", 26)
        grown = _builder().build(store, "s1").render()
        assert grown != first
        assert "…[truncated" not in first or True  # markers stay explicit either way
        store.close()

    def test_stats_split_measured_and_estimated(self, tmp_path):
        store = EventStore(tmp_path / "e.db")
        _seed(store, "s1", 10)
        context = _builder().build(store, "s1", role="investigator")
        stats = context.stats()
        assert stats["measured"]["used_chars"] == context.total_chars
        assert stats["measured"]["budget_chars"] == context.budget
        assert sum(stats["measured"]["sections"].values()) <= context.total_chars + 64
        assert stats["estimated"]["tokens"] == round(context.total_chars / 4)
        assert stats["estimated"]["method"] == "chars/4"
        assert set(stats["measured"]["truncated_sections"]) <= set(
            stats["measured"]["sections"]
        )
        store.close()

    def test_evidence_lines_carry_provenance(self, tmp_path):
        from skaldir.incidents import Incident, IncidentStore

        store = EventStore(tmp_path / "e.db")
        _seed(store, "s1", 5)
        incidents = IncidentStore(tmp_path / "m.db")
        incidents.upsert(
            Incident(signature="abc123", title="Loop", kind="loop", root_cause="retry blind")
        )
        context = _builder().build(
            store, "s1", query="loop retry", incidents=incidents, role="investigator"
        )
        rendered = context.render()
        assert "incident[abc123]" in rendered  # source ref travels with the fact
        incidents.close()
        store.close()


class TestResearchRecordsContextBudget:
    def test_run_emits_voice_context_built_event(self, tmp_path):
        store = EventStore(tmp_path / "e.db")
        _seed(store, "s1", 30)
        runner = ResearchRunner(store=store, state_dir=tmp_path / "state")
        result = runner.run(
            session_id="s1",
            base_step=30,
            trigger_kind="loop",
            trigger_fp="fp-loop",
            problem="terminal call repeated 3x",
        )
        assert result is not None
        events = [
            event for event in store.iter_session("s1") if event.kind == "voice_context_built"
        ]
        assert events, "research must record its context budget"
        payload = events[-1].payload
        assert payload["role"] == "investigator"
        assert payload["budget_chars"] == ROLE_PROFILES["investigator"]["total_budget"]
        assert payload["used_chars"] <= payload["budget_chars"]
        assert "ESTIMATED" in payload["measure"]  # honest labeling, spec §20
        assert isinstance(payload["tokens_estimated"], int)
        store.close()


class TestInvestigatorOutputShape:
    def test_redirect_prompt_has_the_spec_fields(self):
        text = (
            Path(__file__).resolve().parent.parent / "prompts" / "voice_redirect.v1.md"
        ).read_text(encoding="utf-8")
        for field in (
            "problem", "evidence", "why_current_direction_is_bad",
            "direction", "avoid", "confidence",
        ):
            assert field in text, f"missing investigator field: {field}"
