"""Block 3 — Verification Closure (rot-first geschrieben, dann gefixt).

Vier Zusagen des Blocks, jede mit eigenem Regressionstest:

1. RUNNER — die Check-IDs ``run``/``ui_e2e``/``api``/``build``/``persistence_restart``
   haben echte Runner: Exit 0 **und** explizite PASS-Zeile, harter Timeout,
   fail-closed bei allem Unklaren. Eine *deklarierte, aber nicht ausführbare*
   Umgebung wird ehrlich als ``not-certifiable`` gemeldet (nie als PASS).
2. ``gate_tests``-Deadlock — ein Contract ohne pytest-Kriterium, dessen Runner die
   Projekt-Suite wirklich ausgeführt hat, kommt aus dem Gate heraus; ohne echten
   Suite-Lauf bleibt der Blocker stehen (keine Semantik-Aufweichung).
3. EVIDENZ-BINDUNG — eine leere Workspace/Revision/Session passiert den Zaun nicht.
4. TERMINALZUSTAND — ``transition(WORKING)`` kann ein erreichtes
   ``VERIFIED_COMPLETE`` nicht ohne neues Goal auflösen.

Neue Symbole werden bewusst *innerhalb* der Tests importiert: ein noch fehlendes
API erzeugt so einen echten roten Einzeltest statt eines Collection-Fehlers, der
die ganze Datei verdeckt.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skaldir.completion import (
    CompletionRequest,
    DodItem,
    GateRunner,
    RunEvidence,
    execute_check,
    run_requirement_checks,
)

needs_sandbox = pytest.mark.skipif(
    shutil.which("bwrap") is None, reason="declared checks execute sandboxed (bwrap)"
)

PASS_SCRIPT = "print('PASS')\n"
SILENT_SCRIPT = "print('all good, nothing checked')\n"
FAIL_LINE_SCRIPT = "print('FAIL: step did not PASS')\n"
PASS_EXIT3_SCRIPT = "print('PASS')\nraise SystemExit(3)\n"
SLOW_SCRIPT = "import time\ntime.sleep(30)\nprint('PASS')\n"
SUITE_PASS_SCRIPT = "print('3 passed in 0.01s')\nprint('PASS')\n"
SUITE_FAIL_SCRIPT = "print('1 failed, 2 passed')\nprint('FAIL')\nraise SystemExit(1)\n"


# -- helpers ----------------------------------------------------------------


def _script(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> Path:
    """Disposable git repo — gate tests must never read the real repo."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("fixture\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "fixture")
    return root


def _declare(root: Path, check_id: str, **spec) -> Path:
    """Declare one check in the workspace: ``<root>/.godsv/checks.json``."""
    base = root / ".godsv"
    base.mkdir(exist_ok=True)
    path = base / "checks.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data[check_id] = spec
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _pv(root: Path, script: Path) -> str:
    """A declared command string (process-local, absolute paths)."""
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


# -- 1. runner: the exit-0 + explicit-PASS contract -------------------------


class TestPassContract:
    def test_pass_verdict_requires_the_explicit_line(self):
        from skaldir.completion import check_verdict

        assert check_verdict("run", 0, "PASS")[0] == "passed"
        assert check_verdict("run", 0, "PASS run")[0] == "passed"
        ok, why = check_verdict("run", 0, "everything fine")
        assert ok == "failed" and "PASS" in why  # no verdict line => fail-closed

    def test_exit_zero_with_a_fail_line_is_not_a_pass(self):
        from skaldir.completion import check_verdict

        assert check_verdict("run", 0, "FAIL: step did not PASS")[0] == "failed"
        assert check_verdict("run", 0, "ERROR: boom")[0] == "failed"

    def test_nonzero_exit_never_passes(self):
        from skaldir.completion import check_verdict

        assert check_verdict("run", 3, "PASS")[0] == "failed"
        assert check_verdict("run", None, "PASS")[0] == "failed"  # no exit code

    def test_empty_output_is_not_a_pass(self):
        from skaldir.completion import check_verdict

        assert check_verdict("run", 0, "")[0] == "failed"


@needs_sandbox
class TestDeclaredRunners:
    """Every canonical check id reaches a real runner now."""

    def test_run_check_executes_the_declared_command(self, tmp_path, monkeypatch):
        script = _script(tmp_path, "smoke.py", PASS_SCRIPT)
        monkeypatch.setenv("GODSV_CHECK_RUN_CMD", _pv(tmp_path, script))
        ev = execute_check("run", tmp_path, timeout=60, revision=4, workspace=str(tmp_path))
        assert ev is not None, "a declared run check must have a runner"
        assert ev.source == "runner" and ev.status == "passed"
        assert "smoke.py" in ev.cmd
        assert ev.revision == 4 and ev.workspace == str(tmp_path)

    def test_silent_success_is_failed_closed(self, tmp_path, monkeypatch):
        script = _script(tmp_path, "quiet.py", SILENT_SCRIPT)
        monkeypatch.setenv("GODSV_CHECK_RUN_CMD", _pv(tmp_path, script))
        ev = execute_check("run", tmp_path, timeout=60)
        assert ev is not None and ev.status == "failed"
        assert ev.ok is False

    def test_fail_verdict_at_exit_zero_is_failed(self, tmp_path, monkeypatch):
        script = _script(tmp_path, "liar.py", FAIL_LINE_SCRIPT)
        monkeypatch.setenv("GODSV_CHECK_RUN_CMD", _pv(tmp_path, script))
        ev = execute_check("run", tmp_path, timeout=60)
        assert ev is not None and ev.status == "failed"

    def test_hard_timeout_fails_instead_of_hanging(self, tmp_path, monkeypatch):
        script = _script(tmp_path, "slow.py", SLOW_SCRIPT)
        monkeypatch.setenv("GODSV_CHECK_RUN_CMD", _pv(tmp_path, script))
        ev = execute_check("run", tmp_path, timeout=2)
        assert ev is not None and ev.status == "failed"
        assert "timeout" in ev.detail.lower() or "timed out" in ev.detail.lower()

    def test_build_check_runs_the_declared_build(self, tmp_path, monkeypatch):
        script = _script(
            tmp_path,
            "build.py",
            "import pathlib\n"
            "out = pathlib.Path('dist')\n"
            "out.mkdir(exist_ok=True)\n"
            "(out / 'artifact.txt').write_text('built')\n"
            "print('PASS build')\n",
        )
        monkeypatch.setenv("GODSV_CHECK_BUILD_CMD", _pv(tmp_path, script))
        ev = execute_check("build", tmp_path, timeout=120)
        assert ev is not None and ev.status == "passed"
        assert (tmp_path / "dist" / "artifact.txt").is_file(), "the build really ran"

    def test_api_check_runs_the_declared_command(self, tmp_path, monkeypatch):
        script = _script(tmp_path, "api.py", "print('PASS api')\n")
        monkeypatch.setenv("GODSV_CHECK_API_CMD", _pv(tmp_path, script))
        ev = execute_check("api", tmp_path, timeout=60)
        assert ev is not None and ev.status == "passed"

    def test_ui_e2e_runs_a_declared_command_and_passes(self, tmp_path, monkeypatch):
        script = _script(tmp_path, "ui.py", "print('PASS ui_e2e')\n")
        monkeypatch.setenv("GODSV_CHECK_UI_CMD", _pv(tmp_path, script))
        ev = execute_check("ui_e2e", tmp_path, timeout=60)
        assert ev is not None and ev.status == "passed"

    def test_checks_json_declaration_is_executed(self, tmp_path):
        script = _script(tmp_path, "from_file.py", PASS_SCRIPT)
        _declare(tmp_path, "run", argv=[sys.executable, str(script)], timeout=60)
        ev = execute_check("run", tmp_path, timeout=60)
        assert ev is not None and ev.status == "passed"
        assert "from_file.py" in ev.cmd


class TestHonestNotCertifiable:
    """A DECLARED but unavailable environment is reported, never faked."""

    @pytest.mark.parametrize("check_id", ["run", "ui_e2e", "api", "persistence_restart"])
    def test_undeclared_check_is_not_certifiable(self, check_id, tmp_path, monkeypatch):
        monkeypatch.delenv("GODSV_CHECK_RUN_CMD", raising=False)
        monkeypatch.delenv("GODSV_CHECK_UI_CMD", raising=False)
        monkeypatch.delenv("GODSV_CHECK_API_CMD", raising=False)
        monkeypatch.delenv("GODSV_CHECK_RESTART_CMD", raising=False)
        ev = execute_check(check_id, tmp_path, timeout=30)
        assert ev is not None, "a canonical check id must never be missing entirely"
        assert ev.status == "not-certifiable"
        assert ev.ok is False and "declared" in ev.detail.lower()

    def test_build_without_any_build_definition_is_not_certifiable(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("GODSV_CHECK_BUILD_CMD", raising=False)
        ev = execute_check("build", tmp_path, timeout=30)
        assert ev is not None and ev.status == "not-certifiable"
        assert "build" in ev.detail.lower()

    def test_declared_but_missing_executable_is_not_certifiable(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("GODSV_CHECK_RUN_CMD", "/nonexistent/binary --go")
        ev = execute_check("run", tmp_path, timeout=30)
        assert ev is not None and ev.status == "not-certifiable"
        assert "not executable" in ev.detail.lower() or "missing" in ev.detail.lower()

    def test_not_certifiable_evidence_never_certifies(self, tmp_path):
        dod = [DodItem(id="c1", text="ui works", blocking=True, verification="ui_e2e")]
        evidence = [
            RunEvidence(
                name="ui_e2e",
                check_id="ui_e2e",
                source="runner",
                status="not-certifiable",
                ok=False,
                cmd="<check ui_e2e>",
                detail="ui_e2e is declared but not available in this workspace",
            )
        ]
        findings = GateRunner(repo_root=tmp_path, dod=dod).gate_dod(
            CompletionRequest(user_goal="g"), evidence
        )
        assert findings and findings[0].requirement == "c1"

    def test_unknown_check_id_still_has_no_runner(self, tmp_path):
        assert execute_check("ui_e2e_that_does_not_exist", tmp_path) is None


class TestPersistenceRestartRunner:
    """`persistence_restart` runs a REAL restart cycle, not a claim."""

    def _declare_cycle(self, tmp_path, monkeypatch, *, restart_body: str) -> Path:
        marker = tmp_path / "tmp" / "state.txt"
        mark = _script(
            tmp_path,
            "mark.py",
            "import pathlib\n"
            f"p = pathlib.Path({str(marker)!r})\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('before restart')\n"
            "print('PASS mark')\n",
        )
        restart = _script(tmp_path, "restart.py", restart_body.replace("__MARKER__", str(marker)))
        monkeypatch.setenv("GODSV_CHECK_RESTART_MARK_CMD", _pv(tmp_path, mark))
        monkeypatch.setenv("GODSV_CHECK_RESTART_CMD", _pv(tmp_path, restart))
        monkeypatch.setenv("GODSV_CHECK_RESTART_ASSERT", "tmp/state.txt")
        return marker

    def test_restart_cycle_passes_when_state_really_survives(self, tmp_path, monkeypatch):
        self._declare_cycle(
            tmp_path,
            monkeypatch,
            restart_body=(
                "import pathlib\n"
                "p = pathlib.Path('__MARKER__')\n"
                "data = p.read_text()\n"
                "p.write_text(data + '|after restart')\n"
                "print('PASS restart')\n"
            ),
        )
        ev = execute_check("persistence_restart", tmp_path, timeout=60)
        assert ev is not None and ev.status == "passed", ev.detail if ev else None
        assert "restart" in ev.cmd.lower()

    def test_restart_that_does_not_persist_fails(self, tmp_path, monkeypatch):
        """The restart command runs but writes nothing — state did not survive."""
        self._declare_cycle(
            tmp_path, monkeypatch, restart_body="print('PASS restart')\n"
        )
        ev = execute_check("persistence_restart", tmp_path, timeout=60)
        assert ev is not None and ev.status == "failed"
        assert "restart" in ev.detail.lower()

    def test_restart_cycle_requires_a_declaration(self, tmp_path, monkeypatch):
        for var in (
            "GODSV_CHECK_RESTART_CMD",
            "GODSV_CHECK_RESTART_MARK_CMD",
            "GODSV_CHECK_RESTART_ASSERT",
        ):
            monkeypatch.delenv(var, raising=False)
        ev = execute_check("persistence_restart", tmp_path, timeout=30)
        assert ev is not None and ev.status == "not-certifiable"


class TestRequirementChecksUseTheRunners:
    def test_declared_run_criterion_is_certified_by_the_runner(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path)
        # the declared command lives INSIDE the workspace — the sandbox binds
        # the workdir only, so a script outside it is not visible (and rightly so)
        script = _script(repo, "smoke.py", PASS_SCRIPT)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
             "commit", "-qm", "smoke"],
            check=True,
            capture_output=True,
        )
        monkeypatch.setenv("GODSV_CHECK_RUN_CMD", _pv(repo, script))
        dod = [DodItem(id="c1", text="the smoke script reports PASS", verification="run")]
        evidence = run_requirement_checks(
            dod, repo, timeout=60, revision=1, workspace=str(repo)
        )
        assert [ev.check_id for ev in evidence] == ["run"]
        assert evidence[0].status == "passed"
        findings = GateRunner(
            repo_root=repo, dod=dod, revision=1, workspace=str(repo)
        ).gate_dod(CompletionRequest(user_goal="g"), evidence)
        assert findings == [], [f.detail for f in findings]

    def test_ui_e2e_criterion_is_reported_honestly_when_unavailable(self, tmp_path, monkeypatch):
        repo = _repo(tmp_path)
        monkeypatch.delenv("GODSV_CHECK_UI_CMD", raising=False)
        dod = [DodItem(id="c1", text="the UI shows the feature", verification="ui_e2e")]
        evidence = run_requirement_checks(
            dod, repo, timeout=30, revision=1, workspace=str(repo)
        )
        assert evidence and evidence[0].status == "not-certifiable"
        findings = GateRunner(
            repo_root=repo, dod=dod, revision=1, workspace=str(repo)
        ).gate_dod(CompletionRequest(user_goal="g"), evidence)
        assert findings, "not-certifiable must not certify"
        assert "not-certifiable" in findings[0].detail.lower() or "ui" in findings[0].detail.lower()


# -- 2. gate_tests deadlock -------------------------------------------------


class TestGateTestsDeadlock:
    """The deadlock: code changed + a contract without a pytest criterion.

    The old gate demanded runner-source pytest evidence while the runner only
    executed DoD-declared checks — a contract without a `pytest` criterion could
    never clear it (replay-verified in the skill, P0 #8).
    """

    def _dirty_repo(self, tmp_path) -> Path:
        repo = _repo(tmp_path)
        src = repo / "src"
        src.mkdir()
        (src / "calc.py").write_text("def double(n):\n    return n + n\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        return repo

    def test_dod_declared_pytest_criterion_clears_gate_tests(self, tmp_path):
        """The DoD-declared pytest check really runs and certifies (the wired path)."""
        from skaldir.completion import run_requirement_checks as run_checks

        repo = self._dirty_repo(tmp_path)
        (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        dod = [DodItem(id="c1", text="the pytest suite is green", verification="pytest")]
        evidence = run_checks(dod, repo, timeout=300, revision=1, workspace=str(repo))
        assert evidence and evidence[0].check_id == "pytest"
        assert evidence[0].status == "passed", evidence[0].detail
        verdict = GateRunner(
            repo_root=repo, dod=dod, revision=1, workspace=str(repo)
        ).verify(
            CompletionRequest(user_goal="g", claimed_completed=["the suite is green"]),
            evidence,
        )
        assert not any("no test run is evidenced" in b for b in verdict.blocking_failures)
        assert not any("test run failed" in b for b in verdict.blocking_failures)

    def test_project_suite_run_clears_a_contract_without_a_pytest_criterion(self, tmp_path):
        """No pytest criterion in the DoD, but the suite really ran → no deadlock.

        The DoD criteria carry no check id at all (the state after the missing-plan
        fix): the gate must judge the *executed* suite, not an id inside the contract.
        """
        repo = self._dirty_repo(tmp_path)
        (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        dod = [DodItem(id="c1", text="the smoke script reports PASS", verification="run")]
        script = _script(tmp_path, "smoke.py", PASS_SCRIPT)
        evidence = [
            RunEvidence(
                name="run",
                check_id="run",
                source="runner",
                status="passed",
                ok=True,
                cmd=_pv(tmp_path, script),
                ts=1.0,
                revision=1,
                workspace=str(repo),
            ),
            execute_check("pytest", repo, timeout=300, revision=1, workspace=str(repo)),
        ]
        verdict = GateRunner(
            repo_root=repo, dod=dod, revision=1, workspace=str(repo)
        ).verify(
            CompletionRequest(user_goal="g", claimed_completed=["the smoke script passes"]),
            [e for e in evidence if e is not None],
        )
        assert not any("no test run is evidenced" in b for b in verdict.blocking_failures), (
            "a really executed suite run must clear gate_tests"
        )

    def test_executed_command_identity_clears_gate_tests(self, tmp_path):
        """The gate judges what ran, not the label the contract gave it.

        Option 1 of the deadlock fix: a declared check (`run`) whose executed
        command IS the project's suite clears `gate_tests` — the check id in the
        contract is irrelevant to whether a suite run happened. The identity is
        read from the executed command (``suite=False`` here on purpose), so the
        detection cannot be fooled by a flag.
        """
        repo = self._dirty_repo(tmp_path)
        (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        run_ev = RunEvidence(
            name="run",
            check_id="run",
            source="runner",
            status="passed",
            ok=True,
            cmd="python -m pytest -q",
            ts=9e12,
            revision=1,
            workspace=str(repo),
        )
        verdict = GateRunner(
            repo_root=repo,
            dod=[DodItem(id="c1", text="the smoke script reports PASS", verification="run")],
            revision=1,
            workspace=str(repo),
        ).verify(
            CompletionRequest(user_goal="g", claimed_completed=["the smoke script passes"]),
            [run_ev],
        )
        assert not any(
            "no test run is evidenced" in b for b in verdict.blocking_failures
        ), "the executed suite command must clear gate_tests"

    def test_failed_suite_run_still_blocks(self, tmp_path):
        repo = self._dirty_repo(tmp_path)
        (repo / "test_broken.py").write_text(
            "def test_broken():\n    assert False\n", encoding="utf-8"
        )
        verdict = GateRunner(repo_root=repo).verify(
            CompletionRequest(user_goal="g", claimed_completed=["done"]),
            [execute_check("pytest", repo, timeout=300)],
        )
        assert any("test run failed" in b for b in verdict.blocking_failures)

    def test_code_change_without_any_suite_run_still_blocks(self, tmp_path):
        repo = self._dirty_repo(tmp_path)
        verdict = GateRunner(repo_root=repo).verify(
            CompletionRequest(user_goal="g", claimed_completed=["done"]),
            [RunEvidence(name="run", ok=True, cmd="pwd", status="passed")],
        )
        assert any("no test run is evidenced" in b for b in verdict.blocking_failures)

    def test_a_non_pytest_command_cannot_pose_as_the_suite(self, tmp_path):
        """Identity stays binding: a run check that never invokes pytest is not a suite run."""
        repo = self._dirty_repo(tmp_path)
        script = _script(tmp_path, "smoke.py", "print('PASS')\n")
        verdict = GateRunner(repo_root=repo).verify(
            CompletionRequest(user_goal="g", claimed_completed=["done"]),
            [
                RunEvidence(
                    name="run",
                    check_id="run",
                    source="runner",
                    status="passed",
                    ok=True,
                    cmd=_pv(tmp_path, script),
                    revision=1,
                    workspace=str(repo),
                )
            ],
        )
        assert any("no test run is evidenced" in b for b in verdict.blocking_failures)

    def test_events_only_green_pytest_run_does_not_clear_gate_tests(self, tmp_path):
        repo = self._dirty_repo(tmp_path)
        verdict = GateRunner(repo_root=repo).verify(
            CompletionRequest(user_goal="g", claimed_completed=["done"]),
            [
                RunEvidence(
                    name="pytest",
                    check_id="pytest",
                    source="events",
                    status="passed",
                    ok=True,
                    cmd="pytest -q",
                )
            ],
        )
        assert any("no test run is evidenced" in b for b in verdict.blocking_failures)

    def test_runner_supplies_the_suite_run_the_gate_requires(self, tmp_path):
        """The deadlock's root cause: no producer for what gate_tests demanded.

        `run_requirement_checks` must add and execute the project's suite on its
        own once the workspace carries changed code — otherwise a contract
        without a pytest criterion can never satisfy the gate.
        """
        from skaldir.completion import run_requirement_checks as run_checks

        repo = self._dirty_repo(tmp_path)
        (repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        dod = [DodItem(id="c1", text="the smoke script reports PASS", verification="run")]
        evidence = run_checks(dod, repo, timeout=300, revision=1, workspace=str(repo))
        ids = [ev.check_id for ev in evidence]
        assert ids == ["run", "pytest"], ids
        suite = next(ev for ev in evidence if ev.check_id == "pytest")
        assert suite.suite is True
        assert suite.status == "passed", suite.detail
        verdict = GateRunner(repo_root=repo, dod=dod, revision=1, workspace=str(repo)).verify(
            CompletionRequest(user_goal="g", claimed_completed=["the smoke script passes"]),
            evidence,
        )
        assert not any("no test run is evidenced" in b for b in verdict.blocking_failures)
        assert not any("test run failed" in b for b in verdict.blocking_failures)

    def test_a_failed_auto_suite_run_blocks_instead_of_vanishing(self, tmp_path):
        from skaldir.completion import run_requirement_checks as run_checks

        repo = self._dirty_repo(tmp_path)
        (repo / "test_broken.py").write_text(
            "def test_broken():\n    assert False\n", encoding="utf-8"
        )
        dod = [DodItem(id="c1", text="the smoke script reports PASS", verification="run")]
        evidence = run_checks(dod, repo, timeout=300, revision=1, workspace=str(repo))
        suite = next(ev for ev in evidence if ev.check_id == "pytest")
        assert suite.status == "failed"
        verdict = GateRunner(repo_root=repo, dod=dod, revision=1, workspace=str(repo)).verify(
            CompletionRequest(user_goal="g", claimed_completed=["the smoke script passes"]),
            evidence,
        )
        assert any("test run failed" in b for b in verdict.blocking_failures)


# -- 3. evidence binding (workspace bypass) ---------------------------------


class TestEvidenceBinding:
    def _bound(self, **kw):
        dod = [DodItem(id="c1", text="tests green", blocking=True, verification="pytest")]
        return GateRunner(
            repo_root="/nonexistent-gv-block3-repo", dod=dod, **kw
        )

    def _ev(self, *, revision=1, workspace="/a", session="s1", status="passed"):
        return RunEvidence(
            name="pytest",
            check_id="pytest",
            source="runner",
            status=status,
            ok=(status == "passed"),
            cmd="python -m pytest -q",
            revision=revision,
            workspace=workspace,
            session=session,
        )

    def test_bound_evidence_certifies(self):
        assert self._bound(revision=1, workspace="/a", session="s1").gate_dod(
            CompletionRequest(user_goal="g"), [self._ev()]
        ) == []

    def test_empty_workspace_evidence_does_not_certify(self):
        """The audit bypass: evidence with a blank workspace used to pass the fence."""
        findings = self._bound(revision=1, workspace="/a", session="s1").gate_dod(
            CompletionRequest(user_goal="g"), [self._ev(workspace="")]
        )
        assert findings and findings[0].requirement == "c1"

    def test_none_workspace_evidence_does_not_certify(self):
        findings = self._bound(revision=1, workspace="/a", session="s1").gate_dod(
            CompletionRequest(user_goal="g"), [self._ev(workspace=None)]
        )
        assert findings

    def test_foreign_workspace_evidence_does_not_certify(self):
        findings = self._bound(revision=1, workspace="/a", session="s1").gate_dod(
            CompletionRequest(user_goal="g"), [self._ev(workspace="/b")]
        )
        assert findings

    def test_foreign_session_evidence_does_not_certify(self):
        findings = self._bound(revision=1, workspace="/a", session="s1").gate_dod(
            CompletionRequest(user_goal="g"), [self._ev(session="s2")]
        )
        assert findings

    def test_missing_session_on_bound_evidence_does_not_certify(self):
        findings = self._bound(revision=1, workspace="/a", session="s1").gate_dod(
            CompletionRequest(user_goal="g"), [self._ev(session="")]
        )
        assert findings

    def test_stale_revision_evidence_does_not_certify(self):
        findings = self._bound(revision=2, workspace="/a", session="s1").gate_dod(
            CompletionRequest(user_goal="g"), [self._ev(revision=1)]
        )
        assert findings

    def test_unbound_evidence_still_certifies_legacy_callers(self):
        """No binding context on the runner (0/""/None) keeps the legacy behaviour."""
        assert self._bound().gate_dod(
            CompletionRequest(user_goal="g"),
            [RunEvidence(name="pytest", check_id="pytest", source="runner", status="passed")],
        ) == []

    def test_execute_check_binds_session_revision_and_workspace(self, tmp_path):
        ev = execute_check(
            "pytest",
            tmp_path,
            timeout=120,
            revision=7,
            workspace=str(tmp_path),
            session="sess-7",
        )
        assert ev is not None
        assert ev.revision == 7 and ev.workspace == str(tmp_path) and ev.session == "sess-7"

    def test_foreign_session_evidence_in_a_live_store_is_rejected(self, tmp_path):
        """End to end: the store's own session is what the fence compares against."""
        from skaldir.completion import CompletionStore

        store = CompletionStore(tmp_path / "rt", session_id="mine")
        store.begin_goal(
            "mine",
            "goal",
            [DodItem(id="c1", text="tests green", verification="pytest")],
        )
        revision = int(store.state()["revision"])
        runner = GateRunner(
            repo_root="/nonexistent-gv-block3-repo",
            dod=[DodItem(id="c1", text="tests green", verification="pytest")],
            revision=revision,
            workspace="/a",
            session="mine",
        )
        findings = runner.gate_dod(
            CompletionRequest(user_goal="goal"),
            [self._ev(revision=revision, workspace="/a", session="someone-else")],
        )
        assert findings, "evidence from another session must not certify"


# -- 4. terminal state ------------------------------------------------------


class TestTerminalInvariant:
    def test_working_transition_cannot_reopen_a_verified_contract(self, tmp_path):
        from skaldir.completion import VERIFIED_COMPLETE, WORKING, CompletionStore

        store = CompletionStore(tmp_path / "rt", session_id="s1")
        store.begin_goal("s1", "ship it", [DodItem(id="c1", text="t", verification="pytest")])
        store.transition(VERIFIED_COMPLETE)
        with pytest.raises(ValueError):
            store.transition(WORKING)
        assert store.state()["state"] == VERIFIED_COMPLETE
        assert store.state()["goal"] == "ship it"

    def test_reopen_needs_a_new_goal_identity(self, tmp_path):
        from skaldir.completion import VERIFIED_COMPLETE, WORKING, CompletionStore

        store = CompletionStore(tmp_path / "rt", session_id="s1")
        store.begin_goal("s1", "ship it", [])
        store.transition(VERIFIED_COMPLETE)
        with pytest.raises(ValueError):
            store.transition(WORKING, goal="next idea")  # no fresh revision
        assert store.state()["state"] == VERIFIED_COMPLETE

    def test_a_new_goal_reopens_the_contract(self, tmp_path):
        from skaldir.completion import VERIFIED_COMPLETE, WORKING, CompletionStore

        store = CompletionStore(tmp_path / "rt", session_id="s1")
        store.begin_goal("s1", "ship it", [])
        store.transition(VERIFIED_COMPLETE)
        record = store.begin_goal("s1", "next idea", [])
        assert record["state"] == WORKING and record["goal"] == "next idea"
        assert record["revision"] == 1

    def test_new_goal_with_revision_one_explicitly_reopens(self, tmp_path):
        from skaldir.completion import VERIFIED_COMPLETE, WORKING, CompletionStore

        store = CompletionStore(tmp_path / "rt", session_id="s1")
        store.begin_goal("s1", "ship it", [])
        store.transition(VERIFIED_COMPLETE)
        record = store.transition(WORKING, goal="reopened", revision=1)
        assert record["state"] == WORKING
        assert record["goal"] == "reopened"

    def test_other_terminal_states_keep_their_own_rules(self, tmp_path):
        """The invariant is scoped to VERIFIED_COMPLETE — nothing else changes."""
        from skaldir.completion import BLOCKED_USER, WORKING, CompletionStore

        store = CompletionStore(tmp_path / "rt", session_id="s1")
        store.begin_goal("s1", "ship it", [])
        store.transition(BLOCKED_USER)
        record = store.transition(WORKING, goal="retry after unblock")
        assert record["state"] == WORKING
