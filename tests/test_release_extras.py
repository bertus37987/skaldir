"""Release extras: context budget, nightshift consolidation, metrics, allowance."""

from skaldir.context import ContextBuilder
from skaldir.events import EventStore
from skaldir.incidents import IncidentStore
from skaldir.metrics import compute_metrics
from skaldir.monitor import error_fingerprint
from skaldir.nightshift import consolidate_research_notes, propose_solved_incidents
from skaldir.notes import NoteStore, ResearchNote
from skaldir.pact import AcceptanceCriterion, Pact
from skaldir.treasury import PRESETS, SpendIntent, SpendLedger, TreasuryState


def _store(tmp_path):
    return EventStore(tmp_path / "events.db")


class TestContextBudget:
    def test_budget_enforced_with_markers(self, tmp_path):
        store = _store(tmp_path)
        try:
            for step in range(1, 61):
                store.append(
                    "c1",
                    step,
                    "tool_result",
                    {"tool": "terminal", "stdout": "x" * 200, "exit_code": 0},
                    ts=float(step),
                )
            context = ContextBuilder(total_budget=800).build(store, "c1", step=60)
            assert context.total_chars <= 800 + 200
            assert context.truncated_sections()
            assert "truncated" in context.render()
        finally:
            store.close()

    def test_pact_is_priority(self, tmp_path):
        store = _store(tmp_path)
        try:
            for step in range(1, 31):
                store.append(
                    "c1", step, "tool_result",
                    {"tool": "terminal", "stdout": "y" * 200, "exit_code": 0},
                    ts=float(step),
                )
            pact = Pact(
                goal="Ziel X",
                acceptance=[AcceptanceCriterion("ac1", "Tests grün")],
                constraints=["kein Cloud"],
            )
            context = ContextBuilder(total_budget=400).build(store, "c1", pact=pact)
            rendered = context.render()
            assert "Ziel X" in rendered
            assert "kein Cloud" in rendered
        finally:
            store.close()

    def test_reconstructable_after_restart(self, tmp_path):
        store = _store(tmp_path)
        for step in range(1, 11):
            store.append(
                "c1", step, "tool_call", {"name": "terminal", "args": {"command": f"c{step}"}},
                ts=float(step),
            )
        first = ContextBuilder().build(store, "c1").render()
        store.close()
        reopened = EventStore(tmp_path / "events.db")
        second = ContextBuilder().build(reopened, "c1").render()
        reopened.close()
        assert first == second


class TestNightshiftExtensions:
    def test_research_notes_proposed_then_applied(self, tmp_path):
        store = _store(tmp_path)
        notes = NoteStore(tmp_path / "memory.db")
        store.append(
            "n1", 1, "research_done",
            {"problem": "Loop bei pytest", "direction": "Ansatz wechseln", "archive": "/x"},
        )
        proposals = consolidate_research_notes(store, notes, "n1")
        assert len(proposals) == 1 and proposals[0].source_type == "research"
        assert notes.count() == 0  # propose-first: dry-run writes nothing
        consolidate_research_notes(store, notes, "n1", apply=True)
        assert notes.count() == 1
        assert consolidate_research_notes(store, notes, "n1") == []  # dedupe by claim
        notes.close()
        store.close()

    def test_contradiction_linked_on_apply(self, tmp_path):
        store = _store(tmp_path)
        notes = NoteStore(tmp_path / "memory.db")
        notes.add(
            ResearchNote(
                topic="Loop bei pytest", claim="Erst Fehlermeldung lesen", source_type="docs"
            )
        )
        store.append(
            "n1", 1, "research_done",
            {"problem": "Loop bei pytest", "direction": "Komplett anderen Ansatz bauen"},
        )
        proposals = consolidate_research_notes(store, notes, "n1", apply=True)
        assert proposals and proposals[0].contradicts == ["note#1"]
        prior = notes.get(1)
        assert prior is not None and prior.status == "contradicted"
        notes.close()
        store.close()

    def test_solved_incident_proposed(self, tmp_path):
        store = _store(tmp_path)
        store.append(
            "s1", 1, "tool_result",
            {"tool": "terminal", "error": "ImportError: cannot import name 'cv_split'"},
        )
        store.append("s1", 2, "tool_result", {"tool": "terminal", "exit_code": 0, "stdout": "ok"})
        incidents = IncidentStore(tmp_path / "memory.db")
        proposals = propose_solved_incidents(store, "s1", incidents)
        assert len(proposals) == 1
        assert proposals[0].status == "candidate"
        # unknown to memory → proposed; once known → not proposed again
        incidents.upsert(proposals[0])
        assert propose_solved_incidents(store, "s1", incidents) == []
        incidents.close()
        store.close()


class TestMetricsRelease:
    def test_counters(self, tmp_path):
        store = _store(tmp_path)
        store.append("m1", 1, "pause_requested", {"pause": "p1"})
        store.append(
            "m1", 2, "pause_delivered",
            {"pause": "p1", "fingerprint": "fp", "recovery_ms": 1200},
        )
        store.append("m1", 3, "research_done", {"duration_ms": 800, "tokens_est": 400})
        store.append("m1", 4, "guard_check", {"status": "ok"})
        store.append("m1", 5, "guard_nudge", {})
        store.append("m1", 6, "claim", {"text": "x"})
        store.append("m1", 7, "contradiction", {})
        store.append("m1", 8, "skill_consumed", {"skill": "s"})
        store.append("m1", 9, "tool_result", {"tool": "terminal", "error": "HTTP 429"})
        metrics = compute_metrics(store)
        assert metrics.pauses_requested == 1 and metrics.pauses_delivered == 1
        assert metrics.research_ms_avg == 800 and metrics.research_tokens_est == 400
        assert metrics.recovery_ms_avg == 1200
        assert metrics.guard_checks == 1 and metrics.guard_nudges == 1
        assert metrics.claims_recorded == 1 and metrics.contradictions == 1
        assert metrics.skills_consumed == 1
        assert metrics.tool_error_classes.get("rate_limit") == 1
        assert metrics.interventions_held == 1
        store.close()

    def test_recurred_after_delivery(self, tmp_path):
        store = _store(tmp_path)
        fingerprint = error_fingerprint("AssertionError: 1 != 2")
        store.append("m1", 1, "pause_delivered", {"fingerprint": fingerprint})
        store.append(
            "m1", 3, "tool_result", {"tool": "terminal", "error": "AssertionError: 1 != 2"}
        )
        metrics = compute_metrics(store)
        assert metrics.interventions_recurred == 1
        assert metrics.interventions_held == 0
        store.close()


class TestAllowance:
    def test_allowance_is_testgeld(self, tmp_path):
        state = TreasuryState(tmp_path / "t.db")
        ledger = SpendLedger(state, PRESETS["stufe2"])
        intent = SpendIntent(amount=4.0, currency="EUR", merchant="infra", purpose="x")
        ledger.request(amount=4.0, merchant="infra", purpose="x", intent=intent)
        allowance = ledger.allowance()
        assert allowance["mode"] == "testgeld"
        assert allowance["tier"] == 2
        assert allowance["spent_week"] == 4.0
        assert allowance["remaining_week"] == 46.0
        state.close()
