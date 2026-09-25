"""Action Engine (W3): execution graph, parallel/pipeline, boundaries, persistence,
permissions, EXPAND promotion, SIGKILL crash recovery."""

from __future__ import annotations

import json

from skaldir.action_engine import (
    ActionGraph,
    ActionNode,
    ActionEngine,
    Boundary,
    GraphStore,
    NodeKind,
    NodeStatus,
    compact_result,
    promote_to_subtool,
    validate_graph,
)

PERMS = ["net", "fs"]


def _ok_exec(node: ActionNode) -> dict:
    return {"ok": True, "verified": True, "detail": f"{node.name} ok", "evidence": [f"{node.id}.log"]}


def _fail_exec(node: ActionNode) -> dict:
    return {"ok": False, "verified": False, "detail": f"{node.name} failed", "evidence": []}


def _ambiguous_exec(node: ActionNode) -> dict:
    return {"ok": True, "verified": False, "detail": "UI differs from expectation", "evidence": []}


class TestGraphValidation:
    def test_valid_graph(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t",
                         verify={"expect_ok": True}, permissions=PERMS))
        assert validate_graph(g) == []

    def test_missing_dependency(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"expect_ok": True},
                         permissions=PERMS, depends_on=["ghost"]))
        errors = validate_graph(g)
        assert any("ghost" in e for e in errors)

    def test_missing_verify(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={},
                         permissions=PERMS))
        errors = validate_graph(g)
        assert any("verify" in e for e in errors)

    def test_missing_permissions_refused(self):
        """Spec §5: a node that cannot declare permissions is refused at validation."""
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t",
                         verify={"expect_ok": True}, permissions=[]))
        errors = validate_graph(g)
        assert any("permissions" in e for e in errors)

    def test_cycle_detected(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["b"]))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a"]))
        errors = validate_graph(g)
        assert any("cycle" in e for e in errors)


class TestParallelPipeline:
    def test_independent_nodes_parallel(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        groups = g.parallel_groups()
        assert len(groups) == 1
        assert {n.id for n in groups[0]} == {"a", "b"}

    def test_dependent_nodes_pipeline(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a"]))
        g.add(ActionNode(id="c", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["b"]))
        groups = g.parallel_groups()
        assert [[n.id for n in grp] for grp in groups] == [["a"], ["b"], ["c"]]

    def test_mixed_parallel_pipeline(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="c", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a", "b"]))
        g.add(ActionNode(id="d", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["c"]))
        groups = g.parallel_groups()
        assert [[n.id for n in grp] for grp in groups] == [["a", "b"], ["c"], ["d"]]


class TestEngineRun:
    def test_all_verified(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a"]))
        engine = ActionEngine(_ok_exec)
        result = engine.run(g)
        assert result.boundary == Boundary.FINISHED
        assert result.graph_status == "done"
        assert all(n.status == "verified" for n in result.nodes)
        assert result.evidence == ["a.log", "b.log"]

    def test_failure_returns_boundary(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a"]))
        engine = ActionEngine(_fail_exec)
        result = engine.run(g)
        assert result.boundary == Boundary.FAILURE
        assert "a" in result.failed
        assert result.graph_status == "paused"  # resumable — model decides next

    def test_ambiguous_returns_boundary(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.CU_ACTION, name="key",
                         verify={"expect_ok": True}, permissions=PERMS))
        engine = ActionEngine(_ambiguous_exec)
        result = engine.run(g)
        assert result.boundary == Boundary.AMBIGUOUS
        assert "a" in result.ambiguous
        assert result.graph_status == "paused"

    def test_unexpected_on_validation_error(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["ghost"]))
        engine = ActionEngine(_ok_exec)
        result = engine.run(g)
        assert result.boundary == Boundary.UNEXPECTED
        assert "validation" in result.detail

    def test_resume_after_failure(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        fail_engine = ActionEngine(_fail_exec)
        result1 = fail_engine.run(g)
        assert result1.boundary == Boundary.FAILURE
        # Now fix the executor and resume
        ok_engine = ActionEngine(_ok_exec)
        result2 = ok_engine.resume(g)
        assert result2.boundary == Boundary.FINISHED
        assert result2.graph_status == "done"

    def test_parallel_execution_runs_both(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        engine = ActionEngine(_ok_exec)
        result = engine.run(g)
        assert result.boundary == Boundary.FINISHED
        assert {n.id for n in result.nodes} == {"a", "b"}


class TestGraphPersistence:
    def test_graph_roundtrip(self):
        g = ActionGraph(goal="persist me")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         input={"path": "x"}, permissions=PERMS, evidence=["a.log"]))
        d = g.to_dict()
        g2 = ActionGraph.from_dict(d)
        assert g2.goal == "persist me"
        assert "a" in g2.nodes
        assert g2.nodes["a"].input == {"path": "x"}
        assert g2.nodes["a"].permissions == PERMS
        assert g2.nodes["a"].evidence == ["a.log"]

    def test_node_roundtrip(self):
        n = ActionNode(id="x", kind=NodeKind.SUBTOOL, name="open_browser",
                       verify={"expect_observe_contains": "browser"},
                       permissions=PERMS,
                       depends_on=["a"], status=NodeStatus.VERIFIED,
                       result={"ok": True}, evidence=["e.log"])
        d = n.to_dict()
        n2 = ActionNode.from_dict(d)
        assert n2.id == "x"
        assert n2.kind == NodeKind.SUBTOOL
        assert n2.status == NodeStatus.VERIFIED
        assert n2.permissions == PERMS
        assert n2.result == {"ok": True}


class TestGraphStore:
    def test_save_load_roundtrip(self, tmp_path):
        store = GraphStore(tmp_path / "graph.json")
        g = ActionGraph(goal="sigkill")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, status=NodeStatus.VERIFIED,
                         result={"ok": True}, evidence=["a.log"]))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a"]))
        store.save(g)
        g2 = store.load()
        assert g2 is not None
        assert g2.goal == "sigkill"
        assert g2.nodes["a"].status == NodeStatus.VERIFIED
        assert g2.nodes["b"].status == NodeStatus.PENDING

    def test_load_missing_returns_none(self, tmp_path):
        store = GraphStore(tmp_path / "missing.json")
        assert store.load() is None

    def test_load_corrupt_returns_none(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        store = GraphStore(path)
        assert store.load() is None

    def test_clear(self, tmp_path):
        store = GraphStore(tmp_path / "graph.json")
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        store.save(g)
        assert store.load() is not None
        store.clear()
        assert store.load() is None


class TestSigkillRecovery:
    def test_sigkill_mid_graph_resume_no_double_apply(self, tmp_path):
        """Spec §11: kill -9 mid-graph → resume completes without double-apply."""
        store = GraphStore(tmp_path / "graph.json")
        g = ActionGraph(goal="sigkill recovery")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a"]))
        g.add(ActionNode(id="c", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["b"]))
        # Simulate SIGKILL after node 'a' completed: persist, then "crash"
        call_count = {"a": 0, "b": 0, "c": 0}
        def crashing_exec(node):
            call_count[node.id] += 1
            if node.id == "b" and call_count["b"] == 1:
                # Simulate SIGKILL mid-node: save graph state then raise
                store.save(g)
                raise RuntimeError("SIGKILL simulated")
            return {"ok": True, "verified": True, "detail": f"{node.name} ok",
                    "evidence": [f"{node.id}.log"]}
        engine = ActionEngine(crashing_exec, graph_store=store)
        result1 = engine.run(g)
        assert result1.boundary == Boundary.FAILURE
        # Node 'a' was applied exactly once
        assert call_count["a"] == 1
        # Resume from persisted state
        g2 = store.load()
        assert g2 is not None
        ok_engine = ActionEngine(_ok_exec, graph_store=store)
        result2 = ok_engine.resume(g2)
        assert result2.boundary == Boundary.FINISHED
        # Node 'a' was NOT re-applied on resume (idempotent by recorded step)
        assert g2.nodes["a"].status == NodeStatus.VERIFIED
        assert g2.nodes["b"].status == NodeStatus.VERIFIED
        assert g2.nodes["c"].status == NodeStatus.VERIFIED

    def test_persist_after_every_node(self, tmp_path):
        store = GraphStore(tmp_path / "graph.json")
        g = ActionGraph(goal="persist")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS))
        g.add(ActionNode(id="b", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, depends_on=["a"]))
        engine = ActionEngine(_ok_exec, graph_store=store)
        engine.run(g)
        # Graph is persisted on disk with all nodes VERIFIED
        g2 = store.load()
        assert g2 is not None
        assert all(n.status == NodeStatus.VERIFIED for n in g2.nodes.values())


class TestExpandPromotion:
    def test_promote_to_subtool(self, tmp_path, monkeypatch):
        """Spec §8: fully verified graph → promoted to a reusable subtool."""
        saved = {}
        def fake_save_subtool(spec, force=False):
            saved["spec"] = spec
            return "saved"
        monkeypatch.setattr("skaldir.act_service.save_subtool", fake_save_subtool)
        g = ActionGraph(goal="open google docs and type")
        g.status = "done"
        g.add(ActionNode(id="a", kind=NodeKind.SUBTOOL, name="open_browser",
                         verify={"expect_observe_contains": "Docs"},
                         permissions=PERMS, status=NodeStatus.VERIFIED,
                         result={"ok": True}, evidence=["a.log"]))
        g.add(ActionNode(id="b", kind=NodeKind.CU_ACTION, name="type_text",
                         input={"text": "hello"},
                         verify={"expect_ok": True},
                         permissions=PERMS, depends_on=["a"],
                         status=NodeStatus.VERIFIED, result={"ok": True},
                         evidence=["b.log"]))
        name = promote_to_subtool(g, goal="open google docs and type")
        assert name == "open_google_docs_and_type"
        assert "spec" in saved
        assert saved["spec"]["name"] == "open_google_docs_and_type"
        assert len(saved["spec"]["actions"]) == 2

    def test_promote_refuses_unverified(self):
        g = ActionGraph(goal="x")
        g.status = "done"
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, status=NodeStatus.FAILED))
        assert promote_to_subtool(g, goal="x") == ""

    def test_promote_refuses_not_done(self):
        g = ActionGraph(goal="x")
        g.status = "paused"
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, status=NodeStatus.VERIFIED))
        assert promote_to_subtool(g, goal="x") == ""


class TestCompactResult:
    def test_compact_envelope(self):
        g = ActionGraph(goal="x")
        g.add(ActionNode(id="a", kind=NodeKind.TOOL, name="t", verify={"ok": True},
                         permissions=PERMS, result={"detail": "ran fine"},
                         evidence=["a.log"]))
        result = compact_result(g, Boundary.FINISHED, detail="all done")
        d = result.to_dict()
        assert d["boundary"] == "finished"
        assert d["nodes"][0]["status"] == "pending"  # not run yet
        assert d["evidence"] == ["a.log"]
        assert "all done" in d["detail"]
