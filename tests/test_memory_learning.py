"""Block 7 — memory learning: schema, retrieval rule, staleness, cross-session.

Every test here pins one of the six Block 7 requirements:

1. the incident schema (incl. backward compatibility with pre-Block-7 rows);
2. the enforced retrieval rule ``retrieve → applicability check → use/reject``,
   including **§38/GS-04**: a false incident match must never drive a strong
   intervention;
3. the research schema + search-before-research (only gaps are re-researched);
4. staleness (``valid_until``) and provenance;
5. cross-session proof: session 2 does measurably better on the same store;
6. the audit side finding: duplicate-event detection + retention reporting.
"""

import json
import sqlite3
import time

from skaldir.events import EventStore
from skaldir.incidents import Incident, IncidentStore, make_provenance
from skaldir.llm import ScriptedLLM
from skaldir.memory import (
    INCIDENT_SCHEMA_FIELDS,
    VERDICT_INFORMATIONAL,
    VERDICT_REJECT,
    VERDICT_STRONG,
    RetrievalContext,
    RetrievalLog,
    applicability_report,
    classify_candidate,
    duplicate_event_groups,
    evaluate_retrieval,
    merge_provenance,
    provenance_of,
    retention_report,
    schema_axes,
    strong_basis,
)
from skaldir.nightshift import build_episode_summary, consolidate
from skaldir.notes import NoteStore, ResearchNote
from skaldir.prompts import PromptRegistry
from skaldir.research import (
    RESEARCH_SCHEMA_FIELDS,
    ResearchRunner,
    archive_to_schema,
    find_prior_research,
    plan_research,
)
from skaldir.retrieval import find_evidence

LEGACY_SCHEMA = """
CREATE TABLE incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    signature        TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'error',
    root_cause       TEXT NOT NULL DEFAULT '',
    resolution_steps TEXT NOT NULL DEFAULT '[]',
    verification     TEXT NOT NULL DEFAULT '',
    evidence_refs    TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'candidate',
    confidence       REAL NOT NULL DEFAULT 0.5,
    occurrences      INTEGER NOT NULL DEFAULT 1,
    first_seen       REAL NOT NULL DEFAULT 0,
    last_seen        REAL NOT NULL DEFAULT 0,
    repo_scope       TEXT NOT NULL DEFAULT 'global',
    embedding        TEXT
)
"""


def _legacy_db(path):
    """A database exactly as the previous version shipped it, with two rows."""
    db = sqlite3.connect(path)
    db.execute(LEGACY_SCHEMA)
    db.execute(
        "INSERT INTO incidents(signature, title, kind, root_cause, resolution_steps,"
        " verification, evidence_refs, status, confidence, occurrences, first_seen,"
        " last_seen, repo_scope) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "fp-legacy",
            "Import-Fehler cv_split",
            "error",
            "Modul umbenannt",
            json.dumps(["Import auf split_cv umgestellt"]),
            "pytest grün",
            json.dumps(["session-1: step 7"]),
            "active",
            0.9,
            2,
            100.0,
            200.0,
            "src/trainer/**",
        ),
    )
    db.execute(
        "INSERT INTO incidents(signature, title) VALUES (?,?)", ("fp-legacy-2", "Alt")
    )
    db.commit()
    db.close()


# --------------------------------------------------------------------------- #
# 1. incident schema (mandated fields, backward compatible)
# --------------------------------------------------------------------------- #


class TestIncidentSchema:
    def test_incident_exposes_every_mandated_field(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        stored = store.upsert(
            Incident(
                signature="fp1",
                title="ImportError cv_split",
                failure_class="import_error",
                root_cause="Modul umbenannt",
                component="trainer",
                evidence_refs=["session-1: step 7"],
                failed_strategies=["alias import", "reinstall"],
                successful_recovery="Import auf split_cv umgestellt",
                confidence=0.8,
                scope="src/trainer/**",
                provenance=make_provenance(
                    "nightshift", session_id="session-1", step=7, event_refs=["s1:7"]
                ),
            ),
            ts=1000.0,
        )
        schema = stored.to_schema_dict()
        assert set(INCIDENT_SCHEMA_FIELDS) <= set(schema)
        assert schema["failure_class"] == "import_error"
        assert schema["component"] == "trainer"
        assert schema["evidence"] == ["session-1: step 7"]
        assert schema["failed_strategies"] == ["alias import", "reinstall"]
        assert schema["successful_recovery"] == "Import auf split_cv umgestellt"
        assert schema["scope"] == "src/trainer/**"
        assert schema["first_seen"] == 1000.0 and schema["last_seen"] == 1000.0
        assert schema["provenance"]["origin"] == "nightshift"
        # round-trip through SQLite keeps every axis
        loaded = store.get("fp1")
        assert loaded.component == "trainer"
        assert loaded.failed_strategies == ["alias import", "reinstall"]
        assert loaded.successful_recovery.startswith("Import auf split_cv")
        assert loaded.effective_scope == "src/trainer/**"
        store.close()

    def test_legacy_database_and_rows_still_open_and_read(self, tmp_path):
        path = tmp_path / "memory.db"
        _legacy_db(path)
        store = IncidentStore(path)  # migration must not require a rewrite
        legacy = store.get("fp-legacy")
        assert legacy is not None
        assert legacy.title == "Import-Fehler cv_split"
        assert legacy.occurrences == 2
        assert legacy.resolution_steps == ["Import auf split_cv umgestellt"]
        assert legacy.failure_class == ""  # not invented, explicitly empty
        assert legacy.effective_scope == "src/trainer/**"  # repo_scope fallback
        assert legacy.provenance == {}
        assert store.count() == 2
        assert [incident.signature for incident, _s in store.search_text("cv_split")] == [
            "fp-legacy"
        ]

    def test_legacy_row_survives_a_new_sighting(self, tmp_path):
        path = tmp_path / "memory.db"
        _legacy_db(path)
        store = IncidentStore(path)
        again = store.upsert(
            Incident(signature="fp-legacy", title="Import-Fehler cv_split v2"), ts=500.0
        )
        assert again.occurrences == 3 and again.last_seen == 500.0
        assert again.resolution_steps  # old data not wiped by the new sighting
        assert store.count() == 2

    def test_migration_is_idempotent_and_additive(self, tmp_path):
        path = tmp_path / "memory.db"
        _legacy_db(path)
        IncidentStore(path).close()
        first = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(incidents)")}
        IncidentStore(path).close()  # second open must not fail or duplicate columns
        second = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(incidents)")}
        assert first == second
        for column in ("failure_class", "component", "failed_strategies", "successful_recovery",
                       "scope", "valid_until", "provenance"):
            assert column in second

    def test_new_axes_do_not_erase_existing_values_on_repeat(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(
            Incident(
                signature="fp1",
                title="ImportError",
                component="trainer",
                failure_class="import_error",
                provenance=make_provenance("nightshift", session_id="s1", step=1),
            )
        )
        again = store.upsert(
            Incident(
                signature="fp1",
                title="ImportError",
                provenance=make_provenance(
                    "sighting", session_id="s2", step=9, event_refs=["s2:9"]
                ),
            )
        )
        assert again.component == "trainer" and again.failure_class == "import_error"
        assert again.provenance["event_refs"] == ["s2:9"]
        assert again.provenance["origin"] == "sighting"  # new origin, old session kept
        assert again.provenance["session_id"] == "s2"
        store.close()

    def test_embedding_write_path_exists_and_is_used_by_similarity_search(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(Incident(signature="fp1", title="ImportError cv_split", status="active"))
        assert store.set_embedding("fp1", [1.0] + [0.0] * 255) is True
        hits = store.search_similar([1.0] + [0.0] * 255)
        assert [incident.signature for incident, _score in hits] == ["fp1"]
        assert store.set_embedding("nope", [1.0] + [0.0] * 255) is False
        store.close()


# --------------------------------------------------------------------------- #
# 2. the enforced retrieval rule: retrieve → applicability check → use/reject
# --------------------------------------------------------------------------- #


def _active_incident(**kwargs):
    defaults = dict(
        signature="fp-cuda",
        title="RuntimeError CUDA out of memory while loading the trainer dataset",
        kind="error",
        failure_class="cuda_oom",
        component="trainer",
        scope="src/trainer/**",
        status="active",
        confidence=0.85,
    )
    defaults.update(kwargs)
    return Incident(**defaults)


class TestRetrievalRule:
    def test_retrieve_check_use_or_reject_shape(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident())
        context = RetrievalContext(
            problem="CUDA out of memory while loading the trainer dataset",
            component="trainer",
            failure_class="cuda_oom",
            goal_scope="src/trainer/**",
            now=1000.0,
        )
        log = RetrievalLog()
        decision = evaluate_retrieval(store, context=context, log=log)
        assert [item.ref for item in decision.use] == ["incident#1"]
        assert decision.strong_intervention_allowed is True
        assert decision.candidates == ["incident#1"]  # step 1 happened
        assert decision.verdicts[0].matched_fields == (
            "component",
            "failure_class",
            "goal_scope",
        )
        assert log.used == 1 and log.warm is True
        store.close()

    def test_false_incident_match_never_triggers_a_strong_intervention(self, tmp_path):
        """§38/GS-04 — the mandatory regression case.

        The incident is textually plausible (shared vocabulary), so ``search_text``
        retrieves it — but it belongs to another component with another failure
        class. The applicability check must refuse to let it drive an intervention.
        """
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident())
        context = RetrievalContext(
            problem="pytest cannot resolve the trainer import during the test run",
            component="tests",
            failure_class="import_error",
            goal_scope="tests/**",
            now=1000.0,
        )
        decision = evaluate_retrieval(store, context=context)
        assert decision.strong_intervention_allowed is False
        assert decision.use == []
        rejected = {item.ref: item.reasons for item in decision.rejected}
        assert rejected, decision.verdicts
        reasons = " ".join(reason for item in decision.rejected for reason in item.reasons)
        assert "component_mismatch" in reasons and "failure_class_mismatch" in reasons
        assert all(
            item.verdict != VERDICT_STRONG for item in decision.verdicts
        )
        store.close()

    def test_weak_text_overlap_is_informational_never_strong(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(
            _active_incident(
                signature="fp-other",
                title="Timeout waiting for the websocket handshake in the loader",
                failure_class="timeout",
                component="loader",
                scope="src/loader/**",
            )
        )
        context = RetrievalContext(
            problem="websocket handshake timeouts during the loader run",
            component="tools",
            failure_class="timeout",
            now=1000.0,
        )
        decision = evaluate_retrieval(store, context=context)
        assert decision.strong_intervention_allowed is False
        assert all(item.verdict != VERDICT_STRONG for item in decision.verdicts)
        store.close()

    def test_exact_signature_hit_is_strong_without_text_similarity(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(signature="fp-exact", title="völlig anderer Text"))
        context = RetrievalContext(signature="fp-exact", now=1000.0)
        decision = evaluate_retrieval(store, context=context)
        assert decision.strong_intervention_allowed is True
        assert decision.verdicts[0].reasons == ("strong_match",)
        store.close()

    def test_unreviewed_candidate_is_advisory_only(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(status="candidate"))
        context = RetrievalContext(
            problem="CUDA out of memory while loading the trainer dataset",
            component="trainer",
            failure_class="cuda_oom",
            now=1000.0,
        )
        decision = evaluate_retrieval(store, context=context)
        assert decision.strong_intervention_allowed is False
        assert [item.verdict for item in decision.verdicts] == [VERDICT_INFORMATIONAL]
        assert "unreviewed_candidate" in decision.verdicts[0].reasons
        # proposal-first: promoting it (review) is what unlocks strong use
        store.promote("fp-cuda", "active")
        assert evaluate_retrieval(store, context=context).strong_intervention_allowed is True
        store.close()

    def test_low_confidence_high_similarity_is_informational(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(confidence=0.1))
        context = RetrievalContext(
            problem="CUDA out of memory while loading the trainer dataset",
            component="trainer",
            failure_class="cuda_oom",
            now=1000.0,
        )
        decision = evaluate_retrieval(store, context=context)
        assert decision.strong_intervention_allowed is False
        assert decision.verdicts[0].verdict == VERDICT_INFORMATIONAL
        assert "low_confidence" in decision.verdicts[0].reasons
        store.close()

    def test_archived_entry_is_rejected_not_used(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(status="archived"))
        # text search filters archived rows out entirely (step 1)
        assert store.search_text("CUDA out of memory trainer dataset") == []
        # an exact signature hit still reaches the check — and is rejected there
        decision = evaluate_retrieval(
            store,
            context=RetrievalContext(
                signature="fp-cuda", component="trainer", now=1000.0
            ),
        )
        assert decision.strong_intervention_allowed is False
        assert "archived" in decision.rejected[0].reasons
        store.close()

    def test_rejected_entries_are_not_rendered_as_evidence(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident())
        context = RetrievalContext(
            problem="unrelated banana packaging checklist", now=1000.0
        )
        decision = evaluate_retrieval(store, context=context)
        assert decision.evidence_lines() == []
        store.close()

    def test_prose_goal_scope_does_not_create_false_conflicts(self):
        verdict = classify_candidate(
            "incident#9",
            "ImportError cv_split in the trainer",
            context=RetrievalContext(
                problem="ImportError cv_split in the trainer",
                component="trainer",
                goal_scope="make the training pipeline reliable",
                now=1000.0,
            ),
            kind="incident",
            component="trainer",
            scope="global",
            confidence=0.9,
        )
        assert verdict.verdict == VERDICT_STRONG
        assert "goal_scope_mismatch" not in verdict.reasons

    def test_path_scope_mismatch_is_a_conflict(self):
        verdict = classify_candidate(
            "incident#9",
            "ImportError cv_split inside the trainer",
            context=RetrievalContext(
                problem="ImportError cv_split inside the trainer",
                component="trainer",
                goal_scope="src/trainer/**",
                now=1000.0,
            ),
            kind="incident",
            component="trainer",
            scope="src/loader/**",
            confidence=0.9,
        )
        assert verdict.verdict == VERDICT_REJECT
        assert "goal_scope_mismatch" in verdict.reasons

    def test_no_candidates_is_not_a_strong_signal(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        decision = evaluate_retrieval(
            store, context=RetrievalContext(problem="nothing here", now=1000.0)
        )
        assert decision.strong_intervention_allowed is False
        assert decision.verdicts == []
        store.close()

    def test_schema_axes_report_the_mandated_fields(self):
        axes = schema_axes()
        assert set(INCIDENT_SCHEMA_FIELDS) == set(axes["incident_fields"])
        assert set(RESEARCH_SCHEMA_FIELDS) == set(axes["research_fields"])
        assert axes["verdicts"] == [VERDICT_STRONG, VERDICT_INFORMATIONAL, VERDICT_REJECT]


# --------------------------------------------------------------------------- #
# 3. research schema + search-before-research
# --------------------------------------------------------------------------- #


def _runner(tmp_path, **kwargs):
    store = EventStore(tmp_path / "events.db")
    runner = ResearchRunner(store=store, state_dir=tmp_path, **kwargs)
    return runner, store


def _run(runner, problem, step=1):
    return runner.run(
        session_id="s1",
        base_step=step,
        trigger_kind="error_burst",
        trigger_fp=f"fp-{step}",
        problem=problem,
    )


PROBLEM = "the sandbox cannot resolve the pytest interpreter path"


class TestResearchSchema:
    def test_archive_carries_every_mandated_field(self, tmp_path):
        runner, store = _runner(tmp_path, repo_root=None)
        result = _run(runner, PROBLEM)
        data = json.loads(open(result.archive_path, encoding="utf-8").read())
        assert set(RESEARCH_SCHEMA_FIELDS) <= set(data)
        assert data["question"] == PROBLEM
        assert data["finding"]  # the condensed redirect
        assert data["created_at"] > 0 and data["valid_until"] > data["created_at"]
        assert data["confidence"] == "unverified"  # never presented as verified
        assert data["confidence_score"] == 0.0
        assert data["reused_from"] == []
        assert data["contradictions"] == []
        assert data["provenance"]["origin"] == "voice_research"
        assert data["provenance"]["session_id"] == "s1"
        assert data["provenance"]["event_refs"] == ["s1:1"]
        store.close()

    def test_legacy_archive_maps_onto_the_new_schema(self, tmp_path):
        directory = tmp_path / "research"
        directory.mkdir()
        (directory / "research-legacy0001.json").write_text(
            json.dumps(
                {
                    "id": "research-legacy0001",
                    "problem": PROBLEM,
                    "created_at": 100.0,
                    "confidence": "unverified",
                    "llm": {"finding": "an old workaround was to pin the interpreter"},
                }
            ),
            encoding="utf-8",
        )
        entry = archive_to_schema(
            json.loads((directory / "research-legacy0001.json").read_text(encoding="utf-8")),
            path=directory / "research-legacy0001.json",
        )
        assert set(RESEARCH_SCHEMA_FIELDS) <= set(entry)
        assert entry["question"] == PROBLEM
        assert entry["finding"].startswith("an old workaround")
        assert entry["confidence_score"] == 0.0
        assert entry["contradictions"] == [] and entry["sources"] == []

    def test_search_before_research_reports_reusable_and_open_parts(self, tmp_path):
        runner, store = _runner(tmp_path, repo_root=None)
        first = _run(runner, PROBLEM, step=1)
        plan = plan_research(tmp_path, PROBLEM)
        assert [entry["id"] for entry in plan["reusable"]] == [first.research_id]
        assert plan["needed"] is False and plan["questions"] == []
        assert plan["checked"] == 1
        store.close()

    def test_second_session_reuses_and_does_not_research_again(self, tmp_path):
        client = ScriptedLLM(responses={})  # any call would raise LLMError
        runner, store = _runner(
            tmp_path, repo_root=None, client=client, registry=PromptRegistry()
        )
        first = _run(runner, PROBLEM, step=1)
        client.calls.clear()  # session 1 researched (scripted client raises, that is fine)
        second = _run(runner, PROBLEM, step=5)
        assert second.used_llm is False
        assert client.calls == []  # session 2 re-researched nothing
        assert second.used_prior_finding is True
        assert second.research_needed is False
        assert second.reused_research == [first.research_id]
        assert second.direction == first.direction
        assert first.research_id in second.uncertainty
        data = json.loads(open(second.archive_path, encoding="utf-8").read())
        assert data["reused_from"] == [first.research_id]
        assert data["search_plan"]["research_needed"] is False
        store.close()

    def test_unrelated_problem_is_researched_fresh(self, tmp_path):
        runner, store = _runner(tmp_path, repo_root=None)
        _run(runner, PROBLEM, step=1)
        other = _run(runner, "the release wheel cannot find its bundled prompts", step=2)
        assert other.used_prior_finding is False
        assert other.research_needed is True
        data = json.loads(open(other.archive_path, encoding="utf-8").read())
        assert data["reused_from"] == []
        assert data["search_plan"]["reused"] == 0
        store.close()


# --------------------------------------------------------------------------- #
# 4. staleness + provenance
# --------------------------------------------------------------------------- #


class TestStalenessAndProvenance:
    def test_expired_entry_is_rejected_never_used(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(
            _active_incident(valid_until=500.0)  # valid_until already passed
        )
        context = RetrievalContext(
            problem="CUDA out of memory while loading the trainer dataset",
            component="trainer",
            failure_class="cuda_oom",
            now=1000.0,
        )
        decision = evaluate_retrieval(store, context=context)
        assert decision.strong_intervention_allowed is False
        assert "expired" in decision.rejected[0].reasons
        assert decision.rejected[0].fresh is False
        store.close()

    def test_not_yet_expired_entry_stays_usable(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(valid_until=5000.0))
        context = RetrievalContext(
            problem="CUDA out of memory while loading the trainer dataset",
            component="trainer",
            failure_class="cuda_oom",
            now=1000.0,
        )
        decision = evaluate_retrieval(store, context=context)
        assert decision.strong_intervention_allowed is True
        assert decision.use[0].fresh is True
        store.close()

    def test_expired_research_becomes_an_open_question(self, tmp_path):
        runner, store = _runner(tmp_path, repo_root=None)
        _run(runner, PROBLEM, step=1)
        directory = tmp_path / "research"
        for path in directory.glob("research-*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["valid_until"] = time.time() - 60  # expired
            path.write_text(json.dumps(payload), encoding="utf-8")
        plan = plan_research(tmp_path, PROBLEM)
        assert plan["reusable"] == []
        assert plan["needed"] is True
        assert any("expired" in question for question in plan["questions"])
        store.close()

    def test_expired_research_is_not_reused_by_the_runner(self, tmp_path):
        runner, store = _runner(tmp_path, repo_root=None)
        _run(runner, PROBLEM, step=1)
        for path in (tmp_path / "research").glob("research-*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["valid_until"] = time.time() - 60
            path.write_text(json.dumps(payload), encoding="utf-8")
        second = _run(runner, PROBLEM, step=2)
        assert second.used_prior_finding is False
        assert second.research_needed is True
        store.close()

    def test_stale_by_age_is_flagged_and_not_reused(self, tmp_path):
        directory = tmp_path / "research"
        directory.mkdir()
        old = time.time() - 30 * 86400
        (directory / "research-oldoldold0.json").write_text(
            json.dumps(
                {
                    "id": "research-oldoldold0",
                    "question": PROBLEM,
                    "problem": PROBLEM,
                    "finding": "old workaround",
                    "created_at": old,
                    "valid_until": old + 14 * 86400,
                    "confidence": "unverified",
                }
            ),
            encoding="utf-8",
        )
        hits = find_prior_research(tmp_path, PROBLEM)
        assert hits and hits[0]["stale"] is True and hits[0]["age_hours"] > 700
        plan = plan_research(tmp_path, PROBLEM)
        assert plan["reusable"] == [] and plan["needed"] is True

    def test_component_mismatch_in_research_is_not_applicable(self, tmp_path):
        runner, store = _runner(tmp_path, repo_root=None)
        _run(runner, PROBLEM, step=1)
        for path in (tmp_path / "research").glob("research-*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["component"] = "trainer"
            path.write_text(json.dumps(payload), encoding="utf-8")
        plan = plan_research(tmp_path, PROBLEM, component="tools")
        assert plan["reusable"] == [] and plan["needed"] is True
        same = plan_research(tmp_path, PROBLEM, component="trainer")
        assert len(same["reusable"]) == 1
        store.close()

    def test_provenance_is_traceable_for_incidents(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(
            Incident(
                signature="fp1",
                title="ImportError cv_split",
                provenance=make_provenance(
                    "nightshift",
                    session_id="session-7",
                    step=12,
                    event_refs=["session-7:12"],
                    source="consolidate",
                    recorded_at=1000.0,
                ),
            ),
            ts=1000.0,
        )
        view = provenance_of(store.get("fp1"))
        assert view["origin"] == "nightshift"
        assert view["recorded_at"] == 1000.0
        assert view["session_id"] == "session-7" and view["step"] == 12
        assert view["event_refs"] == ["session-7:12"]
        assert view["complete"] is True
        store.close()

    def test_provenance_is_filled_even_when_the_writer_forgets(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        stored = store.upsert(Incident(signature="fp1", title="x"), ts=1000.0)
        assert stored.provenance["origin"] == "incident_proposal"
        assert stored.provenance["recorded_at"] == 1000.0
        assert provenance_of(stored)["complete"] is True
        store.close()

    def test_provenance_merge_keeps_the_origin_and_unions_refs(self):
        merged = merge_provenance(
            {"origin": "nightshift", "session_id": "s1", "event_refs": ["s1:1"]},
            {"origin": "sighting", "event_refs": ["s2:9"], "step": 9},
        )
        assert merged["origin"] == "sighting"
        assert merged["session_id"] == "s1"
        assert merged["step"] == 9
        assert merged["event_refs"] == ["s1:1", "s2:9"]
        # no recorded_at = not traceable, and that is reported as such
        assert provenance_of({"provenance": merged})["complete"] is False
        with_time = merge_provenance(merged, {"recorded_at": 42.0})
        assert provenance_of({"provenance": with_time})["complete"] is True

    def test_corrupt_provenance_degrades_instead_of_crashing(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(Incident(signature="fp1", title="x"))
        store._db.execute("UPDATE incidents SET provenance = ? WHERE signature = 'fp1'", ("{",))
        store._db.commit()
        loaded = store.get("fp1")
        assert loaded.provenance == {}
        assert provenance_of(loaded)["complete"] is False
        store.close()

    def test_applicability_report_is_json_ready(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident())
        context = RetrievalContext(
            problem="CUDA out of memory while loading the trainer dataset",
            component="trainer",
            failure_class="cuda_oom",
            now=1000.0,
        )
        report = applicability_report(
            "incident#1", context=context, text="CUDA out of memory in the trainer dataset",
            component="trainer", failure_class="cuda_oom", confidence=0.9,
        )
        assert json.loads(json.dumps(report.to_dict()))["verdict"] == VERDICT_STRONG
        store.close()


# --------------------------------------------------------------------------- #
# 5. cross-session proof: memory makes the next session measurably cheaper
# --------------------------------------------------------------------------- #


class TestCrossSessionLearning:
    def test_second_session_reuses_the_recovery_without_researching_again(self, tmp_path):
        """Session 1 learns; session 2 resolves the recurrence from memory."""
        client = ScriptedLLM(responses={})  # any LLM call would raise
        store_events = EventStore(tmp_path / "events.db")
        incidents = IncidentStore(tmp_path / "memory.db")
        runner = ResearchRunner(
            store=store_events,
            state_dir=tmp_path,
            repo_root=None,
            incidents=incidents,
            client=client,
            registry=PromptRegistry(),
        )
        problem = "ImportError cannot import name split_cv from trainer"

        # -- session 1: no memory yet → full research, then the fix is learned
        cold = _run(runner, problem, step=1)
        assert cold.used_prior_finding is False
        assert cold.research_needed is True
        incidents.upsert(
            Incident(
                signature="fp-split",
                title="ImportError cannot import name split_cv from trainer",
                kind="error",
                failure_class="import_error",
                component="trainer",
                root_cause="Funktion umbenannt",
                failed_strategies=["pip install -e .", "alias import"],
                successful_recovery="Import auf split_cv umgestellt",
                scope="src/trainer/**",
                status="active",
                confidence=0.9,
                provenance=make_provenance(
                    "session-1", session_id="s1", step=1, event_refs=["s1:1"]
                ),
            ),
            ts=1000.0,
        )

        # -- session 2: same problem, the learned recovery is available
        warm = _run(runner, problem, step=9)

        # measurable: no second full research, and the known recovery is the base
        assert warm.used_prior_finding is True and warm.research_needed is False
        assert warm.reused_research == [cold.research_id]
        context = RetrievalContext(
            problem=problem,
            component="trainer",
            failure_class="import_error",
            goal_scope="src/trainer/**",
            now=2000.0,
        )
        decision = evaluate_retrieval(incidents, context=context)
        assert decision.strong_intervention_allowed is True
        used = decision.use[0]
        assert used.ref == "incident#1"
        assert "split_cv" in used.text  # the recovery path is what gets applied
        assert used.confidence == 0.9

        # control: the same situation without memory costs a full research run
        empty_store = tmp_path / "cold"
        empty_store.mkdir()
        empty_runner = ResearchRunner(
            store=EventStore(empty_store / "events.db"), state_dir=empty_store
        )
        control = _run(empty_runner, problem, step=9)
        assert control.research_needed is True
        assert control.used_prior_finding is False

        store_events.close()
        incidents.close()

    def test_cross_session_log_shows_warm_use(self, tmp_path):
        incidents = IncidentStore(tmp_path / "memory.db")
        incidents.upsert(_active_incident())
        log = RetrievalLog()
        cold = evaluate_retrieval(
            incidents,
            context=RetrievalContext(problem="banana unrelated checklist", now=1.0),
            log=log,
        )
        warm = evaluate_retrieval(
            incidents,
            context=RetrievalContext(
                problem="CUDA out of memory while loading the trainer dataset",
                component="trainer",
                failure_class="cuda_oom",
                now=2.0,
            ),
            log=log,
        )
        assert cold.strong_intervention_allowed is False
        assert warm.strong_intervention_allowed is True
        assert log.warm is True and log.retrievals == 2 and log.used == 1
        incidents.close()


# --------------------------------------------------------------------------- #
# 5b. enforcement at the live boundaries (evidence gate, intervention eligibility)
# --------------------------------------------------------------------------- #


class TestEvidenceBoundaryGate:
    def test_weak_match_is_dropped_at_the_evidence_boundary(self, tmp_path):
        """The live evidence path cannot hand a false match to the voice."""
        store = IncidentStore(tmp_path / "memory.db")
        notes = NoteStore(tmp_path / "memory.db")
        store.upsert(_active_incident())
        notes.add(ResearchNote(topic="CUDA loader", claim="halve the batch size"))
        evidence = find_evidence(store, notes, signature="fp-cuda", query="CUDA loader")
        assert len(evidence) >= 2  # ungated retrieval still returns everything
        gated = find_evidence(
            store,
            notes,
            signature="fp-cuda",
            query="CUDA loader",
            context=RetrievalContext(
                problem="CUDA loader",
                component="tests",
                failure_class="import_error",
                now=1000.0,
            ),
        )
        # the mismatched incident is rejected and gone; the untrusted note stays
        # as context, but is not a strong basis
        assert "incident#1" not in [item.ref for item in gated]
        assert strong_basis(gated) == []
        assert all(
            item.applicability.verdict == VERDICT_INFORMATIONAL for item in gated
        )
        store.close()
        notes.close()

    def test_exact_match_passes_the_gate_and_is_marked_strong(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident())
        gated = find_evidence(
            store,
            None,
            signature="fp-cuda",
            context=RetrievalContext(signature="fp-cuda", component="trainer", now=1000.0),
        )
        assert [item.ref for item in gated] == ["incident#1"]
        assert gated[0].applicability.can_trigger_strong_intervention() is True
        assert strong_basis(gated) == gated
        store.close()

    def test_untrusted_note_never_counts_as_a_strong_basis(self, tmp_path):
        notes = NoteStore(tmp_path / "memory.db")
        notes.add(ResearchNote(topic="CUDA loader", claim="halve the batch size", trust=0.95))
        gated = find_evidence(
            notes=notes,
            query="CUDA loader",
            context=RetrievalContext(problem="CUDA loader", component="trainer", now=1000.0),
        )
        assert gated, "an untrusted note stays visible as context"
        assert strong_basis(gated) == []  # but never as a strong basis
        assert gated[0].applicability.verdict == VERDICT_INFORMATIONAL
        assert "untrusted_source" in gated[0].applicability.reasons
        notes.close()

    def test_archived_incident_is_not_even_returned(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(status="archived"))
        assert find_evidence(store, None, query="CUDA out of memory trainer dataset") == []
        store.close()


class TestInterventionEligibility:
    def test_signatures_lists_everything_but_escalation_is_gated(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(signature="fp-ok"))
        store.upsert(_active_incident(signature="fp-candidate", status="candidate"))
        store.upsert(_active_incident(signature="fp-archived", status="archived"))
        store.upsert(_active_incident(signature="fp-expired", valid_until=500.0))
        store.upsert(_active_incident(signature="fp-lowconf", confidence=0.1))
        assert store.signatures() == {
            "fp-ok",
            "fp-candidate",
            "fp-archived",
            "fp-expired",
            "fp-lowconf",
        }
        assert store.intervention_signatures(now=1000.0) == {"fp-ok"}
        assert store.intervention_signatures(now=1000.0, include_candidates=True) == {
            "fp-ok",
            "fp-candidate",
        }
        store.close()

    def test_an_over_broad_signature_set_is_not_the_same_as_the_gated_one(self, tmp_path):
        """A never-reviewed candidate must not raise a strong escalation."""
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_active_incident(signature="fp-new", status="candidate", confidence=0.3))
        assert "fp-new" in store.signatures()
        assert "fp-new" not in store.intervention_signatures(now=1000.0)
        store.close()


class TestNightshiftProvenance:
    def test_consolidation_proposal_carries_axes_and_provenance(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        store.append(
            "s-night",
            4,
            "tool_result",
            {"tool": "terminal", "error": "ImportError: cannot import name split_cv"},
            ts=1000.0,
        )
        summary = build_episode_summary(store, "s-night")
        result = consolidate(summary)
        assert len(result.proposals) == 1
        proposal = result.proposals[0]
        assert proposal.failure_class  # classified, comparable across sessions
        assert proposal.evidence_refs and "s-night" in proposal.evidence_refs[0]
        view = provenance_of(proposal)
        assert view["origin"] == "nightshift_consolidate"
        assert view["session_id"] == "s-night"
        assert view["step"] == 4
        assert view["complete"] is True
        store.close()

    def test_applied_consolidation_writes_the_provenance_through(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        incidents = IncidentStore(tmp_path / "memory.db")
        store.append(
            "s-night",
            4,
            "tool_result",
            {"tool": "terminal", "error": "TypeError: x is not iterable"},
            ts=1000.0,
        )
        summary = build_episode_summary(store, "s-night")
        consolidate(summary, incidents=incidents, apply=True)
        stored = incidents.all()[0]
        assert provenance_of(stored)["session_id"] == "s-night"
        assert stored.is_expired(now=1000.0) is False
        store.close()
        incidents.close()


# --------------------------------------------------------------------------- #
# 6. audit side finding: duplicate events + retention (report-only)
# --------------------------------------------------------------------------- #


class TestDedupeAndRetention:
    def test_duplicate_event_groups_are_detected(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        for _ in range(3):  # the live shape: one (session, step, kind) repeated
            store.append("s1", 1, "tool_call", {"name": "terminal"}, ts=1000.0)
        store.append("s1", 2, "tool_result", {"tool": "terminal"}, ts=1001.0)
        groups, duplicate_rows, scanned = duplicate_event_groups(store)
        assert scanned == 4
        assert duplicate_rows == 2
        assert groups == [
            {"session_id": "s1", "step": 1, "kind": "tool_call", "count": 3}
        ]
        store.close()

    def test_duplicate_check_does_not_modify_the_store(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        store.append("s1", 1, "thought", {"text": "x"}, ts=1000.0)
        store.append("s1", 1, "thought", {"text": "x"}, ts=1000.0)
        before = store.count()
        duplicate_event_groups(store)
        retention_report(store, now=1000.0)
        assert store.count() == before
        store.close()

    def test_retention_report_states_the_rule_and_the_gaps(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        now = time.time()
        store.append("s1", 1, "thought", {"text": "old"}, ts=now - 40 * 86400)
        store.append("s1", 2, "thought", {"text": "new"}, ts=now - 60)
        store.append("s2", 1, "thought", {"text": "new"}, ts=now - 30)
        report = retention_report(store, max_age_days=30.0, now=now)
        assert report.total == 3 and report.sessions == 2
        assert report.over_age == 1
        assert report.duplicate_groups == [] and report.duplicate_rows == 0
        assert report.bytes_on_disk > 0
        assert report.policy["mode"].startswith("report-only")
        # enforcement lives in an owned file → recorded as an explicit gap
        assert any("events.py" in gap for gap in report.enforcement_gaps)
        data = report.to_dict()
        assert data["oldest_ts"] <= data["newest_ts"]
        store.close()

    def test_retention_report_handles_an_empty_store(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        report = retention_report(store)
        assert report.total == 0 and report.duplicate_groups == []
        assert report.oldest_ts == 0.0 and report.newest_ts == 0.0
        store.close()
