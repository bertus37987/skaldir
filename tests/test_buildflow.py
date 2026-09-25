"""§4a/§4b/§4d — persistente Build-Zustandsmaschine, begrenzte Reparatur, Evidenz,
transaktionaler Publikations-Outbox.

Diese Datei fährt den neuen Kern (`godsv.buildflow`) plus die Registry-Seite
(`godsv.tools` Outbox/Revision/Fallback). Die Verdrahtung im ECHTEN Builder
(`ToolBuilder.build`) steht in `tests/test_toolbuilder.py`.
"""

import json

import pytest

from skaldir import buildflow as bf
from skaldir.toolbuilder import STAGES
from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore, resolve_tool


def _flow(tmp_path) -> bf.BuildFlowStore:
    return bf.BuildFlowStore(tmp_path / "buildflow.db")


def _spec(name: str = "demo_tool", version: str = "0.1.0") -> ToolSpec:
    return ToolSpec(
        name=name,
        title="Demo",
        description="demo tool",
        status=STATUS_APPROVED,
        version=version,
    )


# --------------------------------------------------------------------------- #
# §4a Phasen
# --------------------------------------------------------------------------- #


class TestPhases:
    def test_every_builder_stage_maps_to_a_master_prompt_phase(self):
        """The existing STAGES stay as they are — the mapping must be complete."""
        for stage in STAGES:
            assert stage in bf.STAGE_PHASE, f"stage not mapped: {stage}"
            assert bf.STAGE_PHASE[stage] in bf.PHASES
        assert bf.STAGE_PHASE["requested"] == "need"
        assert bf.STAGE_PHASE["researching"] == "search"
        assert bf.STAGE_PHASE["compose_check"] == "decide"
        assert bf.STAGE_PHASE["dependency_inspection"] == "inspect"
        assert bf.STAGE_PHASE["manifest"] == "contract"
        assert bf.STAGE_PHASE["generating"] == "adapter"
        assert bf.STAGE_PHASE["gates"] == "verify"
        assert bf.STAGE_PHASE["register"] == "publish"
        assert bf.STAGE_PHASE["ready"] == "bind"

    def test_phase_order_is_the_master_prompt_order(self):
        assert bf.PHASES == (
            "need", "search", "decide", "inspect", "contract", "adapter",
            "verify", "validate", "publish", "bind", "observe",
        )
        assert set(bf.OUTCOMES) == {"failed", "blocked", "cancelled", "superseded"}

    def test_forward_transitions_are_recorded_with_history_and_timestamps(self, tmp_path):
        flow = _flow(tmp_path)
        record, created = flow.admit(need="convert money", session_id="s1")
        assert created is True and record.phase == "need"
        for phase in ("search", "decide", "inspect", "contract", "adapter", "verify"):
            record = flow.enter(record.build_id, phase)
        assert record.phase == "verify"
        history = flow.history(record.build_id)
        kinds = [entry["kind"] for entry in history]
        assert kinds[0] == "created"
        assert kinds.count("enter") == 6
        assert [entry["phase"] for entry in history][1:] == [
            "search", "decide", "inspect", "contract", "adapter", "verify",
        ]
        assert all(entry["ts"] > 0 for entry in history)

    def test_invalid_transition_is_refused(self, tmp_path):
        flow = _flow(tmp_path)
        record, _created = flow.admit(need="convert money")
        with pytest.raises(bf.InvalidTransition) as exc:
            flow.enter(record.build_id, "publish")  # skips search…adapter
        assert "need → publish is not allowed" in str(exc.value)
        with pytest.raises(bf.InvalidTransition):
            flow.enter(record.build_id, "does_not_exist")
        assert flow.get(record.build_id).phase == "need"  # nothing moved

    def test_terminal_states_are_final(self, tmp_path):
        flow = _flow(tmp_path)
        record, _created = flow.admit(need="convert money")
        flow.finish(
            record.build_id, "failed", reason="gates failed", artifacts=["manifest.yaml"],
            next_step="fix the adapter and retry",
        )
        closed = flow.get(record.build_id)
        assert closed.terminal and closed.phase == "failed"
        assert closed.artifacts == ["manifest.yaml"]
        assert closed.next_step == "fix the adapter and retry"
        for target in ("search", "blocked", "cancelled"):
            with pytest.raises(bf.InvalidTransition):
                flow.transition(record.build_id, target)

    def test_repair_edge_needs_a_recorded_repair_attempt(self, tmp_path):
        flow = _flow(tmp_path)
        record, _created = flow.admit(need="convert money")
        for phase in ("search", "decide", "inspect", "contract", "adapter", "verify"):
            flow.enter(record.build_id, phase)
        with pytest.raises(bf.InvalidTransition) as exc:
            flow.enter(record.build_id, "adapter")  # backwards without a repair
        assert "repair edge" in str(exc.value)
        moved = flow.transition(record.build_id, "adapter", repair_attempt=1)
        assert moved.phase == "adapter"
        assert flow.history(record.build_id)[-1]["detail"]["repair_attempt"] == 1

    def test_binding_pending_and_not_yet_used_are_their_own_states(self, tmp_path):
        flow = _flow(tmp_path)
        record, _created = flow.admit(need="convert money")
        for phase in (
            "search", "decide", "inspect", "contract", "adapter", "verify",
            "validate", "publish", "bind",
        ):
            flow.enter(record.build_id, phase)
        pending = flow.mark_bind(record.build_id, "pending", detail={"surface": "b5"})
        assert pending.status_text == "published, binding pending"
        applied = flow.mark_bind(record.build_id, "applied")
        assert applied.bind_state == "applied"
        observed = flow.observe(record.build_id, detail={"call": "first"})
        assert observed.phase == "observe"
        assert observed.observed_at > 0 and observed.completed_at > 0
        assert flow.get(record.build_id).status_text == "observed"


# --------------------------------------------------------------------------- #
# §4a Idempotenz, Sperre, Leases
# --------------------------------------------------------------------------- #


class TestAdmissionLockAndLeases:
    def test_two_identical_requests_admit_to_one_build(self, tmp_path):
        flow = _flow(tmp_path)
        first, created_a = flow.admit(need="Convert money amounts", session_id="s1")
        second, created_b = flow.admit(need="Convert money amounts", session_id="s2")
        assert created_a is True and created_b is False
        assert first.build_id == second.build_id
        assert len(flow.active()) == 1

    def test_a_reformulated_request_is_the_same_need(self, tmp_path):
        flow = _flow(tmp_path)
        first, _ = flow.admit(need="convert money amounts", project="p1")
        second, created = flow.admit(need="amounts, money — convert!", project="p1")
        assert created is False and second.build_id == first.build_id
        # …but another project is a different build (project is part of the key)
        third, created_other = flow.admit(need="convert money amounts", project="p2")
        assert created_other is True and third.build_id != first.build_id

    def test_two_sessions_subscribe_with_their_own_goal_revisions(self, tmp_path):
        flow = _flow(tmp_path)
        record, _ = flow.admit(need="convert money", session_id="s1", goal_revision=1)
        flow.subscribe(record.build_id, "s2", goal_revision=7)
        entries = flow.subscribers(record.build_id)
        assert [(entry["session_id"], entry["goal_revision"]) for entry in entries] == [
            ("s1", 1),
            ("s2", 7),
        ]
        assert all(entry["ts"] > 0 for entry in entries)
        assert flow.subscription_ok(record.build_id, "s1", 1)[0] is True
        assert flow.subscription_ok(record.build_id, "s2", 7)[0] is True
        ok, reason = flow.subscription_ok(record.build_id, "s1", 2)
        assert ok is False and "goal revision changed" in reason

    def test_lease_is_bounded_renewable_and_reclaimable_after_expiry(self, tmp_path):
        flow = _flow(tmp_path)
        record, _ = flow.admit(need="convert money", owner="worker-a", lease_seconds=60)
        assert flow.acquire(record.build_id, "worker-b", lease_seconds=60) is False
        assert flow.lease_ok(record.build_id, "worker-a") is True
        assert flow.renew(record.build_id, "worker-a", lease_seconds=60) is True
        assert flow.renew(record.build_id, "worker-b", lease_seconds=60) is False
        # the lease expires: another worker takes over, recovery names the takeover
        assert flow.acquire(record.build_id, "worker-b", ts=record.lease_until + 1) is True
        taken_over = flow.get(record.build_id)
        report = flow.recover(now=taken_over.lease_until + 1)
        assert any(item["build_id"] == record.build_id for item in report["reclaimed"])
        assert flow.renew(record.build_id, "worker-b", lease_seconds=60,
                          ts=taken_over.lease_until + 1) is False  # lease is gone


# --------------------------------------------------------------------------- #
# §4a Crash-Recovery
# --------------------------------------------------------------------------- #


class TestCrashRecovery:
    def test_a_new_instance_reads_the_persisted_state(self, tmp_path):
        first = _flow(tmp_path)
        record, _ = first.admit(need="convert money", session_id="s", tool_name="money")
        first.enter(record.build_id, "search")
        first.close()
        reopened = _flow(tmp_path)  # a new process reads the same DB
        loaded = reopened.get(record.build_id)
        assert loaded is not None and loaded.phase == "search"
        assert loaded.tool_name == "money"
        assert [entry["phase"] for entry in reopened.history(record.build_id)] == [
            "need", "search",
        ]

    def test_open_publish_without_a_registry_entry_is_blocked_not_green(self, tmp_path):
        first = _flow(tmp_path)
        record, _ = first.admit(need="convert money", tool_name="money_convert")
        for phase in ("search", "decide", "inspect", "contract", "adapter", "verify",
                      "validate", "publish"):
            first.enter(record.build_id, phase)
        first.step_done(record.build_id, "adapter", detail={"files": ["/tmp/x.py"]})
        first.close()

        reopened = _flow(tmp_path)
        report = reopened.recover(registry=lambda name: False)
        assert [step["phase"] for step in report["open_steps"]] == ["publish"]
        closed = reopened.get(record.build_id)
        assert closed.phase == "blocked"
        assert "not in the registry" in closed.reason
        assert closed.next_step  # a concrete next step, not a bare failure
        assert report["blocked"]

    def test_open_publish_that_landed_is_reconciled_to_bind(self, tmp_path):
        first = _flow(tmp_path)
        record, _ = first.admit(need="convert money", tool_name="money_convert")
        for phase in ("search", "decide", "inspect", "contract", "adapter", "verify",
                      "validate", "publish"):
            first.enter(record.build_id, phase)
        first.close()
        reopened = _flow(tmp_path)
        report = reopened.recover(registry=lambda name: name == "money_convert")
        assert report["reconciled"] and not report["blocked"]
        assert reopened.get(record.build_id).phase == "bind"

    def test_a_finished_phase_is_not_an_open_step(self, tmp_path):
        flow = _flow(tmp_path)
        record, _ = flow.admit(need="convert money")
        flow.enter(record.build_id, "search")
        flow.step_done(record.build_id, "search", detail={"decision": "build"})
        assert flow.open_step(record.build_id) is None
        flow.enter(record.build_id, "decide")
        assert flow.open_step(record.build_id) == {
            "build_id": record.build_id, "phase": "decide",
        }


# --------------------------------------------------------------------------- #
# §4a Abbruch / verspätete Insertion
# --------------------------------------------------------------------------- #


class TestCancelAndSupersession:
    def test_cancelled_build_may_not_publish(self, tmp_path):
        flow = _flow(tmp_path)
        record, _ = flow.admit(need="convert money", session_id="s1", goal_revision=1)
        flow.cancel(record.build_id, reason="user stopped the task", by="s1")
        ok, reason = flow.subscription_ok(record.build_id, "s1", 1)
        assert ok is False and "cancelled" in reason
        with pytest.raises(bf.InvalidTransition):
            flow.enter(record.build_id, "search")

    def test_a_late_build_never_slips_into_a_changed_goal(self, tmp_path):
        flow = _flow(tmp_path)
        record, _ = flow.admit(need="convert money", session_id="s1", goal_revision=1)
        for phase in ("search", "decide", "inspect", "contract", "adapter", "verify",
                      "validate", "publish"):
            flow.enter(record.build_id, phase)
        ok, reason = flow.subscription_ok(record.build_id, "s1", 3)  # goal moved on
        assert ok is False and "goal revision changed" in reason
        # the flow decides that as supersession, and the state is durable
        closed = flow.finish(record.build_id, "superseded", reason=reason)
        assert closed.phase == "superseded" and closed.reason == reason

    def test_reuse_decision_supersedes_the_build(self, tmp_path):
        flow = _flow(tmp_path)
        record, _ = flow.admit(need="convert money")
        flow.enter(record.build_id, "search")
        closed = flow.transition(record.build_id, "decide", detail={"decision": "reuse"})
        assert closed.phase == "decide"
        superseded = flow.transition(
            record.build_id, "superseded", reason="reuse: money_convert already covers this"
        )
        assert superseded.outcome == "superseded"


# --------------------------------------------------------------------------- #
# §4b begrenzte Reparatur
# --------------------------------------------------------------------------- #


class TestRepairLoop:
    def test_the_cap_is_three_by_default_and_configurable(self):
        loop = bf.RepairLoop()
        assert loop.limit == bf.DEFAULT_REPAIR_LIMIT == 3
        for index in range(3):
            loop.record(
                defect=f"gate test failed on case {index}",
                change=f"adapter edit {index}",
                checks=["test"],
                evidence_new=True,
            )
        ok, reason = loop.may_repair(
            defect="gate test failed on case 4", evidence_new=True
        )
        assert ok is False and "repair limit reached" in reason
        assert loop.state() == {
            "limit": 3, "used": 3, "remaining": 0, "attempts": loop.state()["attempts"],
        }
        # configurable: a smaller cap ends earlier
        tight = bf.RepairLoop(limit=1)
        tight.record(defect="d", change="c", checks=["test"])
        assert tight.may_repair(defect="other", evidence_new=True)[0] is False

    def test_same_defect_without_new_evidence_ends_the_loop(self):
        loop = bf.RepairLoop()
        loop.record(defect="AssertionError: expected 11.0", change="a", checks=["test"])
        ok, reason = loop.may_repair(defect="assertionerror:  expected 11.0")
        assert ok is False and "without new evidence" in reason
        # a genuinely new evidence point reopens the loop
        assert loop.may_repair(
            defect="AssertionError: expected 11.0", evidence_new=True
        )[0] is True

    def test_an_infrastructure_failure_never_rewrites_the_adapter(self):
        loop = bf.RepairLoop()
        assert bf.classify_failure(
            "bwrap: execvp: No such file or directory — sandbox unavailable"
        ) == "infrastructure"
        assert bf.classify_failure("AssertionError: 11.0 != 12.0") == "adapter"
        with pytest.raises(bf.WrongLayerError) as exc:
            loop.record(
                defect="sandbox unavailable (fail-closed)",
                change="rewrite the adapter",
                checks=["import"],
                failure_kind="infrastructure",
                kind="adapter",
            )
        assert "repair the infrastructure, not the adapter" in str(exc.value)
        attempt = loop.record(
            defect="sandbox unavailable (fail-closed)",
            change="install bubblewrap on the host",
            checks=["sandbox"],
            failure_kind="infrastructure",
            kind="infrastructure",
        )
        assert attempt.kind == "infrastructure"

    def test_every_attempt_names_defect_change_and_checks(self):
        loop = bf.RepairLoop()
        attempt = loop.record(
            defect="gates failed: test",
            change="fix the rounding in convert()",
            checks=["test", "determinism"],
        ).to_dict()
        assert attempt["defect"].startswith("gates failed")
        assert attempt["change"].startswith("fix the rounding")
        assert attempt["checks"] == ["test", "determinism"]
        assert attempt["attempt"] == 1 and attempt["ts"] > 0
        assert set(attempt) >= {"fingerprint", "kind", "evidence_new"}


# --------------------------------------------------------------------------- #
# §4b Akzeptanz + Holdout
# --------------------------------------------------------------------------- #


class TestAcceptanceAndHoldout:
    def test_process_criteria_are_refused(self):
        for criteria in (
            [],
            ["the command exits with exit code 0"],
            ["HTTP 200 from the endpoint"],
            ["container running"],
            ["no error"],
        ):
            assert bf.acceptance_problems(criteria), f"accepted: {criteria}"
        assert bf.acceptance_problems(["the report carries every currency rate"]) == []

    def test_criteria_come_from_the_need_before_the_adapter(self):
        criteria = bf.derive_acceptance_criteria(
            "convert money amounts between currencies", declared=["rate table is complete"]
        )
        assert criteria[0] == "rate table is complete"
        assert any("convert money amounts" in item for item in criteria)
        assert bf.acceptance_problems(criteria) == []

    def test_an_opened_holdout_becomes_a_known_regression_test(self):
        acceptance = bf.AcceptanceSet(
            criteria=["produces the report"],
            builder_cases=[{"target": "sample", "expect_pass": True}],
            holdout_cases=[
                {"target": "fresh-a", "expect_pass": True},
                {"target": "fresh-b", "expect_pass": False},
            ],
        )
        assert acceptance.fresh_cases_required is False
        assert len(acceptance.fresh_holdout()) == 2
        acceptance.open_for_diagnosis("fresh-a", reason="debugging the report shape")
        state = acceptance.state()
        assert state["fresh_cases_required"] is True
        assert state["known_regression_tests"] == ["fresh-a"]
        assert state["fresh_holdout_cases"] == 1
        assert [case["id"] for case in acceptance.fresh_holdout()] == ["fresh-b"]
        assert acceptance.burned["fresh-a"] == "debugging the report shape"

    def test_the_repair_view_never_carries_the_holdout(self):
        acceptance = bf.AcceptanceSet(
            criteria=["produces the report"],
            builder_cases=[{"target": "sample", "expect_pass": True}],
            holdout_cases=[{"target": "withheld-secret-case", "expect_pass": False}],
        )
        view = acceptance.repair_view()
        assert view["holdout_visible"] is False
        assert "withheld-secret-case" not in json.dumps(view)
        assert view["builder_cases"] == [{"target": "sample", "expect_pass": True, "id": "sample"}]


# --------------------------------------------------------------------------- #
# §4b Evidenzpaket
# --------------------------------------------------------------------------- #


class TestEvidencePackage:
    def test_a_complete_package_carries_every_field_and_no_secret_value(self, tmp_path):
        source = tmp_path / "tool.py"
        source.write_text("def main():\n    return {'ok': True}\n", encoding="utf-8")
        package = bf.evidence_package(
            need="convert money amounts",
            search_decision={"decision": "build", "searched": ["tools.db"]},
            sources=[{"tool": "none", "why": "no existing match"}],
            manifest=str(tmp_path / "manifest.yaml"),
            code_hashes=bf.hashes_for([source]),
            test_commands=[["python", "-m", "pytest", "tests/"]],
            results=[{"name": "test", "ok": True, "detail": "1 passed"}],
            holdout_provenance={"status": "validated", "fresh": True},
            limits=["network: rates.example.com only"],
            runtime_probe={"wrapper": "exit 0", "probe": "ok"},
            secrets=["secret://rates/example"],
        )
        assert bf.evidence_missing(package) == []
        assert package["secrets"] == ["secret://rates/example"]
        raw = json.dumps(package)
        assert package["redactions"] == 0 and "secret://rates/example" in raw

        dirty = bf.evidence_package(
            need="convert money amounts",
            search_decision={"decision": "build"},
            sources=[],
            manifest="m",
            code_hashes={},
            test_commands=["pytest"],
            results=[{"detail": "api_key: sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAA"}],
            holdout_provenance={"status": "validated"},
            limits=["none"],
            runtime_probe={"probe": "ok"},
            secrets=["secret://rates/example"],
        )
        blob = json.dumps(dirty)
        assert dirty["redactions"] >= 1
        assert "sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in blob
        assert "[REDACTED" in blob
        assert "secret://rates/example" in blob  # handles are the sanctioned reference
        # empty sources/code_hashes stay REPORTED as missing — redaction must not
        # paper over a gap (§4b: the package names what it owes)
        assert bf.evidence_missing(dirty) == ["sources", "code_hashes"]

    def test_missing_fields_are_reported(self):
        package = bf.evidence_package(need="x", search_decision={"decision": "build"})
        missing = bf.evidence_missing(package)
        assert "manifest" in missing and "code_hashes" in missing
        assert "results" in missing and "runtime_probe" in missing
        # a build without a credential owes no secrets field
        assert "secrets" not in missing


# --------------------------------------------------------------------------- #
# §4d Outbox (Registry-Seite)
# --------------------------------------------------------------------------- #


class TestPublishOutbox:
    def test_publish_is_one_transaction_and_idempotent_per_version(self, tmp_path):
        store = ToolStore(tmp_path / "tools.db")
        spec = _spec()
        receipt = store.publish(spec, payload={"build_id": "b1"})
        assert receipt["created"] is True and receipt["duplicate"] is False
        assert store.get("demo_tool") is not None
        assert store.registry_revision() == 1

        again = store.publish(_spec(), payload={"build_id": "b1"})
        assert again["duplicate"] is True and again["created"] is False
        assert again["outbox_id"] == receipt["outbox_id"]
        assert len(store.outbox_entries()) == 1  # no second row
        assert store.registry_revision() == 2  # the registry did move (re-publish)

        other_version = store.publish(_spec(version="0.2.0"), payload={})
        assert other_version["created"] is True
        assert len(store.outbox_entries()) == 2

    def test_a_failing_outbox_write_rolls_the_registry_write_back(self, tmp_path, monkeypatch):
        store = ToolStore(tmp_path / "tools.db")

        def _boom(_self):
            raise RuntimeError("revision bookkeeping failed")

        monkeypatch.setattr(ToolStore, "_bump_revision_locked", _boom)
        with pytest.raises(RuntimeError):
            store.publish(_spec(), payload={"build_id": "b1"})
        # SAME transaction: neither the registry row nor the outbox entry exists
        assert store.get("demo_tool") is None
        assert store.outbox_entries() == []
        monkeypatch.undo()
        assert store.registry_revision() == 0

    def test_commit_without_drain_survives_a_crash_and_recovery_drains_it(self, tmp_path):
        first = ToolStore(tmp_path / "tools.db")
        receipt = first.publish(_spec(), payload={"build_id": "b1", "kind": "cli"})
        first.close()  # crash right after the commit, before any event was sent

        reopened = ToolStore(tmp_path / "tools.db")
        pending = reopened.pending_outbox("builder")
        assert [entry["id"] for entry in pending] == [receipt["outbox_id"]]
        drained = reopened.drain_outbox("builder")
        assert [entry["name"] for entry in drained] == ["demo_tool"]
        assert drained[0]["payload"]["build_id"] == "b1"
        assert reopened.pending_outbox("builder") == []  # nothing left to do

    def test_double_drain_and_double_ack_are_idempotent(self, tmp_path):
        store = ToolStore(tmp_path / "tools.db")
        store.publish(_spec(), payload={})
        first = store.drain_outbox("b9-discovery")
        second = store.drain_outbox("b9-discovery")
        assert len(first) == 1 and second == []
        assert store.acked_outbox("b9-discovery") == [first[0]["id"]]
        # a second consumer sees the very same fact once (per-consumer acks)
        assert len(store.drain_outbox("recovery")) == 1
        # re-acking the same ids changes nothing
        assert store.ack_outbox("b9-discovery", [first[0]["id"]]) == 0

    def test_status_change_moves_the_registry_revision(self, tmp_path):
        store = ToolStore(tmp_path / "tools.db")
        store.upsert(_spec())
        assert store.registry_revision() == 0  # a proposal is not a publication
        assert store.set_status("demo_tool", "revoked") is True
        assert store.registry_revision() == 1


class TestRegistryFallbackWithoutIndex:
    def test_a_broken_index_never_costs_availability(self, tmp_path):
        store = ToolStore(tmp_path / "tools.db")
        store.upsert(_spec())

        def _broken(_name):
            raise RuntimeError("search index offline")

        resolved = resolve_tool(store, "demo_tool", index=_broken)
        assert resolved["found"] is True and resolved["name"] == "demo_tool"
        assert resolved["via"] == "registry-fallback"
        assert "search index offline" in resolved["index_error"]

    def test_a_stale_index_proposal_falls_back_to_the_registry(self, tmp_path):
        store = ToolStore(tmp_path / "tools.db")
        store.upsert(_spec())
        stale = resolve_tool(store, "demo_tool", index=lambda _name: {"name": "ghost"})
        assert stale["found"] is True and stale["via"] == "registry-fallback"
        # a working index is used, but the registry still answers
        good = resolve_tool(store, "convert money", index=lambda _name: {"name": "demo_tool"})
        assert good["found"] is True and good["via"] == "index"
        unknown = resolve_tool(store, "nope")
        assert unknown["found"] is False and unknown["via"] == "registry"


# --------------------------------------------------------------------------- #
# §4b/§4d Zusammenspiel: Evidenzpaket trägt die Registry-Provenienz
# --------------------------------------------------------------------------- #


class TestEvidenceAndOutboxTogether:
    def test_the_outbox_payload_carries_the_publish_provenance(self, tmp_path):
        store = ToolStore(tmp_path / "tools.db")
        source = tmp_path / "demo_tool.py"
        source.write_text("def main():\n    return {}\n", encoding="utf-8")
        evidence = bf.evidence_package(
            need="convert money",
            search_decision={"decision": "build"},
            sources=["tools.db"],
            manifest="manifest.yaml",
            code_hashes=bf.hashes_for([source]),
            test_commands=["pytest"],
            results=[{"name": "test", "ok": True}],
            holdout_provenance={"status": "validated"},
            limits=["none"],
            runtime_probe={"probe": "ok"},
        )
        receipt = store.publish(
            _spec(),
            payload={
                "build_id": "b1",
                "code_hashes": evidence["code_hashes"],
                "evidence_redactions": evidence["redactions"],
            },
        )
        entry = store.drain_outbox("builder")[0]
        assert entry["id"] == receipt["outbox_id"]
        assert entry["payload"]["code_hashes"][str(source)] != "missing"
        assert entry["payload"]["evidence_redactions"] == 0
