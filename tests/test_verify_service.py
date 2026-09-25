"""VERIFY layer — the real-world verification service (plan → run → evidence → verdict).

The layer's promise is narrow and testable: *can a user actually use this?* Every check
must really run against the workspace, every claim must carry the hash and the timestamp
it was produced with, and `UNKNOWN` must never become a `PASS`.

Pinned here:

1. PLAN — a criterion gets the real check behind it (derived, declared by the workspace,
   or read from the completion contract); a criterion nothing can certify stays visible
   as *unplannable* / *uncertifiable* instead of being dropped.
2. RUN — the checks really execute: the project suite (a real nested pytest run), a
   declared command through the sandbox, an artifact inspected for existence/size/sha256.
3. VERDICT — `PASS` only when every blocking criterion has an executed passing check;
   `FAIL` on a real failure; `UNKNOWN` for anything that could not be executed.
4. EVIDENCE — timestamps, output sha256, artifact sha256, `evidence_sha256`, `plan_sha256`,
   the workspace digest, persisted under the state dir and readable back.
5. FRESHNESS — a `PASS` whose workspace changed since is marked stale, not quoted as proof.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skaldir import verify_service as V

# -- helpers ---------------------------------------------------------------- #


def _workspace(tmp_path: Path, plan: dict | None = None) -> Path:
    root = tmp_path / "ws"
    (root / ".godsv").mkdir(parents=True, exist_ok=True)
    if plan is not None:
        (root / ".godsv" / "verify.json").write_text(json.dumps(plan), encoding="utf-8")
    return root


def _git_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "gitws"
    root.mkdir()
    (root / "README.md").write_text("fixture\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "fixture")
    return root


def _plan_for(root: Path, criteria: list) -> V.Plan:
    return V.build_plan(criteria, workspace=root)


# -- 1. plan ----------------------------------------------------------------- #


class TestPlan:
    def test_a_test_criterion_is_derived_to_the_project_suite(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan(["the test suite passes"], workspace=root)
        assert [item.check_id for item in plan.criteria] == ["pytest"]
        assert plan.criteria[0].kind == V.KIND_SUITE
        assert plan.criteria[0].certifiable is True
        assert plan.certifiable is True
        assert "plan_verification" in plan.criteria[0].reason

    def test_the_plan_file_supplies_criteria_and_their_checks(self, tmp_path):
        root = _workspace(tmp_path, {
            "goal": "ship the widget",
            "criteria": [
                {"criterion": "the bundle exists", "paths": ["dist/app.asar"], "min_bytes": 4},
                {"criterion": "the CLI answers", "argv": [sys.executable, "-c", "print('PASS')"],
                 "require_pass_line": True},
            ],
        })
        plan = V.build_plan(goal="", workspace=root)
        assert plan.goal == "ship the widget"
        assert plan.source == "plan-file"
        kinds = [item.kind for item in plan.criteria]
        assert kinds == [V.KIND_ARTIFACT, V.KIND_COMMAND]
        assert all(item.certifiable for item in plan.criteria)
        assert plan.plannable and plan.certifiable

    def test_a_criterion_nothing_can_certify_stays_visible(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan(["the app compiles nicely"], workspace=root)
        assert plan.criteria and plan.criteria[0].planned is False
        assert plan.criteria[0].certifiable is False
        assert plan.plannable is False and plan.certifiable is False
        assert plan.unplannable and plan.unplannable[0].text == "the app compiles nicely"
        assert plan.as_dict()["uncertifiable"] == ["the app compiles nicely"]

    def test_a_declared_canonical_check_is_planned_when_the_workspace_declares_it(
        self, tmp_path, monkeypatch
    ):
        root = _workspace(tmp_path)
        monkeypatch.setenv("GODSV_CHECK_BUILD_CMD", f"{sys.executable} -c pass")
        plan = V.build_plan(["the app builds"], workspace=root)
        assert plan.criteria[0].check_id == "build"
        assert plan.criteria[0].certifiable is True
        assert "declares that check" in plan.criteria[0].reason

        monkeypatch.delenv("GODSV_CHECK_BUILD_CMD", raising=False)
        undeclared = V.build_plan(["the app builds"], workspace=root)
        assert undeclared.criteria[0].certifiable is False

    def test_checks_json_declaration_is_honoured_by_the_plan(self, tmp_path):
        root = _workspace(tmp_path)
        (root / ".godsv" / "checks.json").write_text(
            json.dumps({"build": {"argv": [sys.executable, "-c", "print('PASS')"], "timeout": 30}}),
            encoding="utf-8",
        )
        plan = V.build_plan([{"criterion": "the build finishes", "check": "build"}], workspace=root)
        assert plan.criteria[0].check_id == "build"
        assert plan.criteria[0].declared_command.endswith("print('PASS')")
        assert plan.criteria[0].certifiable is True

    def test_a_contract_criterion_keeps_its_id_and_verification(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan(
            [{"id": "c1", "text": "the api answers", "blocking": False, "verification": "api"}],
            workspace=root,
        )
        criterion = plan.criteria[0]
        assert criterion.criterion_id == "c1" and criterion.check_id == "api"
        assert criterion.blocking is False

    def test_an_empty_plan_is_reported_not_invented(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan(workspace=root)
        assert plan.criteria == [] and plan.plannable is False and plan.certifiable is False
        assert any("no criterion is planned" in item for item in plan.warnings)

    def test_a_plan_over_a_missing_workspace_can_never_certify(self, tmp_path):
        absent = tmp_path / "not-there"
        plan = V.build_plan(["the test suite passes"], workspace=absent)
        assert plan.workspace_exists is False
        assert plan.plannable is True  # the criterion maps to a check …
        assert plan.certifiable is False  # … but there is no workspace to check
        assert absent.is_dir() is False
        run = V.run_plan(plan, timeout=30, state_dir=None)
        assert run["verdict"] == V.VERDICT_UNKNOWN
        assert run["results"][0]["status"] == V.STATUS_UNKNOWN
        assert "workspace does not exist" in run["results"][0]["reason"]

    def test_the_plan_file_is_reported_when_it_is_missing_or_broken(self, tmp_path):
        root = _workspace(tmp_path)
        data, reason = V.load_plan_file(root)
        assert data == {} and "no plan file" in reason
        (root / ".godsv" / "verify.json").write_text("{not json", encoding="utf-8")
        data, reason = V.load_plan_file(root)
        assert data == {} and "unreadable" in reason
        assert "unreadable" in V.build_plan(workspace=root).plan_file_reason

    def test_plan_serialises_everything_a_reader_needs(self, tmp_path):
        root = _workspace(tmp_path)
        payload = V.build_plan(["the test suite passes"], goal="g", revision=7,
                               workspace=root).as_dict()
        for key in ("goal", "workspace", "revision", "criteria", "checks", "plannable",
                    "certifiable", "pass_requirement", "created_at"):
            assert key in payload
        assert payload["revision"] == 7
        assert payload["pass_requirement"] == V.PASS_REQUIREMENT
        assert payload["checks"] == ["pytest"]

    def test_the_contract_supplies_goal_and_criteria(self, tmp_path, monkeypatch):
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        from skaldir.completion import CompletionStore, DodItem

        store = CompletionStore(runtime)
        store.begin_goal(
            "s1",
            "ship the widget",
            [DodItem(id="c1", text="the test suite passes", verification="pytest")],
        )
        goal, criteria = V.contract_criteria(runtime, "s1")
        assert goal == "ship the widget"
        assert criteria[0]["criterion"] == "the test suite passes"
        assert criteria[0]["check"] == "pytest"

    def test_no_contract_yields_nothing_instead_of_a_guess(self, tmp_path):
        goal, criteria = V.contract_criteria(tmp_path / "absent", "s1")
        assert goal == "" and criteria == []


# -- 2/3. run: real checks and the verdict ----------------------------------- #


class TestRealChecks:
    def test_the_project_suite_really_runs_and_passes(self, tmp_path):
        root = _workspace(tmp_path)
        (root / "test_green.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        plan = V.build_plan(["the test suite passes"], workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root, timeout=120)
        assert result["status"] == V.STATUS_PASSED, result["reason"]
        assert result["exit_code"] == 0
        assert "pytest" in result["cmd"]
        assert "1 passed" in result["output_tail"]

    def test_the_project_suite_really_runs_and_fails(self, tmp_path):
        root = _workspace(tmp_path)
        (root / "test_red.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
        plan = V.build_plan(["the tests pass"], workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root, timeout=120)
        assert result["status"] == V.STATUS_FAILED
        assert result["exit_code"] != 0

    def test_an_artifact_is_inspected_for_real(self, tmp_path):
        root = _workspace(tmp_path)
        artifact = root / "dist" / "app.bin"
        artifact.parent.mkdir()
        artifact.write_bytes(b"payload-1234")
        plan = V.build_plan([{"criterion": "the bundle exists", "paths": ["dist/app.bin"],
                              "min_bytes": 4}], workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root)
        assert result["status"] == V.STATUS_PASSED
        inspected = result["artifacts"][0]
        assert inspected["exists"] and inspected["size_bytes"] == len(b"payload-1234")
        assert inspected["sha256"] == V.sha256_text("payload-1234")

    def test_a_missing_artifact_is_a_real_failure(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan([{"criterion": "the bundle exists", "paths": ["dist/app.bin"]}],
                            workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root)
        assert result["status"] == V.STATUS_FAILED
        assert "missing" in result["reason"]

    def test_an_artifact_below_the_declared_size_fails(self, tmp_path):
        root = _workspace(tmp_path)
        (root / "small.bin").write_bytes(b"x")
        plan = V.build_plan([{"criterion": "the bundle exists", "paths": ["small.bin"],
                              "min_bytes": 1024}], workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root)
        assert result["status"] == V.STATUS_FAILED
        assert "min_bytes" in result["reason"]

    def test_an_artifact_directory_is_measured_not_just_stated(self, tmp_path):
        root = _workspace(tmp_path)
        (root / "dist").mkdir()
        (root / "dist" / "one.txt").write_text("one", encoding="utf-8")
        plan = V.build_plan([{"criterion": "the build output exists", "paths": ["dist"]}],
                            workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root)
        assert result["status"] == V.STATUS_PASSED
        assert result["artifacts"][0]["kind"] == "dir"
        assert result["artifacts"][0]["entries"] == 1


class TestNotCertifiable:
    """A check that cannot run here is UNKNOWN — never a pass, never a crash."""

    def test_a_declared_check_without_a_declaration_is_unknown(self, tmp_path, monkeypatch):
        root = _workspace(tmp_path)
        for name in V.DECLARED_CHECK_ENV.values():
            monkeypatch.delenv(name, raising=False)
        plan = V.build_plan([{"criterion": "the ui round trip works", "check": "ui_e2e"}],
                            workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root)
        assert result["status"] == V.STATUS_UNKNOWN
        assert result["exit_code"] is None
        assert "no declaration" in result["reason"]

    def test_a_not_certifiable_check_can_never_yield_pass(self, tmp_path, monkeypatch):
        root = _workspace(tmp_path)
        for name in V.DECLARED_CHECK_ENV.values():
            monkeypatch.delenv(name, raising=False)
        plan = V.build_plan([{"criterion": "the ui round trip works", "check": "ui_e2e"}],
                            workspace=root)
        run = V.run_plan(plan, timeout=30, state_dir=tmp_path / "state")
        assert run["verdict"] == V.VERDICT_UNKNOWN
        assert run["verdict"] != V.VERDICT_PASS
        assert run["unknown_reason"]

    def test_an_unplannable_criterion_can_never_yield_pass(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan(["the app compiles nicely"], workspace=root)
        run = V.run_plan(plan, timeout=30)
        assert run["verdict"] == V.VERDICT_UNKNOWN
        assert run["results"][0]["status"] == V.STATUS_UNKNOWN

    def test_a_dry_run_without_a_state_dir_records_nothing(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan(["the test suite passes"], workspace=root)
        run = V.run_plan(plan, timeout=30, state_dir=None)
        assert run["state_dir"] == ""
        assert not (tmp_path / "state").exists()


class TestVerdict:
    def test_only_all_blocking_passed_is_a_pass(self):
        assert V.verdict_for([{"status": V.STATUS_PASSED, "blocking": True}])[0] == V.VERDICT_PASS
        assert V.verdict_for(
            [{"status": V.STATUS_PASSED, "blocking": True}, {"status": V.STATUS_UNKNOWN,
                                                             "blocking": True}]
        )[0] == V.VERDICT_UNKNOWN
        assert V.verdict_for(
            [{"status": V.STATUS_FAILED, "blocking": True}, {"status": V.STATUS_UNKNOWN,
                                                             "blocking": True}]
        )[0] == V.VERDICT_FAIL

    def test_an_empty_result_set_is_unknown(self):
        verdict, reason = V.verdict_for([])
        assert verdict == V.VERDICT_UNKNOWN and "nothing to certify" in reason

    def test_a_non_blocking_unknown_still_passes_but_is_named(self):
        verdict, reason = V.verdict_for(
            [{"status": V.STATUS_PASSED, "blocking": True},
             {"status": V.STATUS_UNKNOWN, "blocking": False}]
        )
        assert verdict == V.VERDICT_PASS and "non-blocking" in reason

    def test_unknown_is_never_reported_as_pass_anywhere_in_the_module(self):
        import inspect

        source = inspect.getsource(V.verdict_for)
        assert "VERDICT_PASS" in source and "STATUS_PASSED" in source
        assert "item.get(\"blocking\", True) and item.get(\"status\") != STATUS_PASSED" in source


# -- 4. evidence ------------------------------------------------------------- #


class TestEvidence:
    def test_a_run_record_carries_timestamps_and_hashes(self, tmp_path):
        root = _workspace(tmp_path)
        (root / "test_green.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        plan = V.build_plan(["the test suite passes"], goal="g", revision=3, workspace=root)
        run = V.run_plan(plan, timeout=120, session="s1", state_dir=tmp_path / "state")
        result = run["results"][0]
        for key in ("started_at", "finished_at", "duration_ms", "output_sha256",
                    "evidence_sha256", "cmd", "exit_code", "workspace", "revision", "session"):
            assert key in result
        assert result["started_at"] <= result["finished_at"]
        assert result["duration_ms"] >= 0
        assert result["revision"] == 3 and result["session"] == "s1"
        assert run["plan_sha256"] == V.sha256_text(V.canonical_json(run["plan"]))
        assert run["pass_requirement"] == V.PASS_REQUIREMENT
        assert "UNKNOWN" in run["fake_pass_guard"]

    def test_every_evidence_hash_is_recomputable_from_the_record(self, tmp_path):
        root = _workspace(tmp_path)
        (root / "artifact.bin").write_bytes(b"abc")
        plan = V.build_plan([{"criterion": "the artifact exists", "paths": ["artifact.bin"]}],
                            workspace=root)
        result = V.check_criterion(plan.criteria[0], workspace=root)
        stored = result.pop("evidence_sha256")
        assert V.sha256_text(V.canonical_json(result)) == stored

    def test_the_run_is_persisted_and_readable_back(self, tmp_path):
        root = _workspace(tmp_path)
        plan = V.build_plan([{"criterion": "the artifact exists", "paths": ["artifact.bin"]}],
                            workspace=root)
        (root / "artifact.bin").write_bytes(b"abc")
        state = tmp_path / "state"
        run = V.run_plan(plan, timeout=30, state_dir=state)
        store = V.VerifyStore(state)
        assert (store.runs_dir / f"{run['run_id']}.json").is_file()
        assert store.evidence_path.is_file()
        assert store.get(run["run_id"])["verdict"] == run["verdict"]
        latest = store.latest()
        assert latest is not None and latest["run_id"] == run["run_id"]
        assert store.runs()[0]["run_id"] == run["run_id"]
        assert store.runs()[0]["counts"]["criteria"] == 1

    def test_the_ledger_holds_one_row_per_criterion_and_filters(self, tmp_path):
        root = _workspace(tmp_path, {
            "criteria": [
                {"criterion": "the artifact exists", "paths": ["artifact.bin"]},
                {"criterion": "another artifact exists", "paths": ["second.bin"]},
            ],
        })
        (root / "artifact.bin").write_bytes(b"abc")
        (root / "second.bin").write_bytes(b"def")
        state = tmp_path / "state"
        plan = V.build_plan(workspace=root)
        run = V.run_plan(plan, timeout=30, state_dir=state)
        store = V.VerifyStore(state)
        rows = store.evidence()
        assert len(rows) == 2
        assert {row["run_id"] for row in rows} == {run["run_id"]}
        assert rows[0]["evidence_sha256"] and rows[0]["output_sha256"]
        assert [row["criterion"] for row in store.evidence(criterion="second")] == [
            "another artifact exists"
        ]
        assert store.evidence(run_id="vr-nope") == []
        assert len(store.evidence(limit=1)) == 1

    def test_a_broken_ledger_line_is_skipped_not_fatal(self, tmp_path):
        state = tmp_path / "state"
        store = V.VerifyStore(state)
        state.mkdir()
        store.evidence_path.write_text('{"run_id": "vr-1", "criterion": "x"}\nnot json\n',
                                       encoding="utf-8")
        rows = store.evidence()
        assert len(rows) == 1 and rows[0]["run_id"] == "vr-1"

    def test_an_empty_store_reports_nothing_instead_of_failing(self, tmp_path):
        store = V.VerifyStore(tmp_path / "empty")
        assert store.runs() == [] and store.latest() is None and store.evidence() == []

    def test_save_run_needs_a_run_id(self, tmp_path):
        with pytest.raises(ValueError):
            V.VerifyStore(tmp_path / "state").save_run({"verdict": V.VERDICT_PASS})


# -- 5. freshness / digests --------------------------------------------------- #


class TestFreshness:
    def test_a_git_workspace_has_a_digest(self, tmp_path):
        root = _git_workspace(tmp_path)
        digest = V.workspace_digest(root)
        assert digest["git"] is True and len(digest["digest"]) == 64
        assert digest["dirty_files"] == 0

    def test_a_non_repository_has_no_digest_and_says_so(self, tmp_path):
        root = _workspace(tmp_path)
        digest = V.workspace_digest(root)
        assert digest["digest"] is None and "not a git repository" in digest["reason"]
        assert V.workspace_digest(tmp_path / "absent")["reason"] == "workspace does not exist"

    def test_a_run_is_fresh_until_the_workspace_changes(self, tmp_path):
        root = _git_workspace(tmp_path)
        (root / "artifact.bin").write_bytes(b"abc")
        plan = V.build_plan([{"criterion": "the artifact exists", "paths": ["artifact.bin"]}],
                            workspace=root)
        run = V.run_plan(plan, timeout=30, state_dir=tmp_path / "state")
        fresh = V.run_freshness(run, root)
        assert fresh["fresh"] is True and fresh["citable"] is True

        (root / "artifact.bin").write_bytes(b"changed")  # the workspace really changes
        stale = V.run_freshness(run, root)
        assert stale["fresh"] is False and stale["citable"] is False
        assert "changed since this run" in stale["reason"]

    def test_a_commit_also_makes_a_run_stale(self, tmp_path):
        root = _git_workspace(tmp_path)
        plan = V.build_plan([{"criterion": "the artifact exists", "paths": ["README.md"]}],
                            workspace=root)
        run = V.run_plan(plan, timeout=30, state_dir=tmp_path / "state")
        assert V.run_freshness(run, root)["fresh"] is True
        (root / "NEW.md").write_text("new\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=T", "-c",
                        "user.email=t@example.invalid", "commit", "-qm", "next"],
                       check=True, capture_output=True)
        assert V.run_freshness(run, root)["fresh"] is False

    def test_a_run_without_a_digest_is_not_citable(self, tmp_path):
        root = _workspace(tmp_path)
        fresh = V.run_freshness({"workspace": str(root), "workspace_digest": {}}, root)
        assert fresh["fresh"] is None and fresh["citable"] is False
        assert "freshness unknown" in fresh["reason"]


# -- hashing helpers ---------------------------------------------------------- #


class TestHashing:
    def test_sha256_file_reports_what_it_hashed(self, tmp_path):
        path = tmp_path / "x.bin"
        path.write_bytes(b"hello")
        digest, scope = V.sha256_file(path)
        assert digest == V.sha256_text("hello") and scope == "whole file"
        limited, scope = V.sha256_file(path, limit=2)
        assert len(limited) == 64 and scope == "first 2 bytes"
        missing, scope = V.sha256_file(tmp_path / "nope")
        assert missing == "" and "unreadable" in scope

    def test_canonical_json_is_stable_across_key_order(self):
        assert V.canonical_json({"b": 1, "a": 2}) == V.canonical_json({"a": 2, "b": 1})
        assert json.loads(V.canonical_json({"a": [1, 2]})) == {"a": [1, 2]}

    def test_now_iso_is_a_local_timestamp(self):
        stamp = V.now_iso()
        assert stamp[:4].isdigit() and "T" in stamp
        assert time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S") is not None


# -- the module's own vocabulary --------------------------------------------- #


def test_declared_check_env_covers_every_canonical_check():
    from skaldir.completion import CANONICAL_CHECKS

    declared = set(V.DECLARED_CHECK_ENV) | set(V.SUITE_CHECKS)
    assert set(CANONICAL_CHECKS) <= declared, (
        "a canonical check id exists that the plan cannot name — "
        f"missing: {sorted(set(CANONICAL_CHECKS) - declared)}"
    )


def test_the_module_advertises_the_hard_rule():
    assert "UNKNOWN" in V.PASS_REQUIREMENT
    assert V.VERDICT_UNKNOWN != V.VERDICT_PASS
    for name in ("VERDICT_PASS", "VERDICT_FAIL", "VERDICT_UNKNOWN", "build_plan", "run_plan",
                 "verdict_for", "VerifyStore", "run_freshness"):
        assert name in V.__all__
