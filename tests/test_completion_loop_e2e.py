"""Mandatory E2E: the completion loop (Goal §40/§41/§44/§46).

Demonstrates the core promise: a Voice rejection actually causes the main agent
to keep working — automatically, without the user ever typing "continue" —
until the product passes independent verification.

The loop is modelled end-to-end over a real git repo: the fake main agent makes
real changes, the supervisor verifies against real facts, and the loop only
ends on VERIFIED_COMPLETE (or an explicit terminal state).
"""

import subprocess

import pytest

from skaldir.completion import (
    BLOCKED_EXTERNAL,
    BLOCKED_USER,
    COMPLETION_REQUESTED,
    INCOMPLETE,
    SHIPPED_WITH_USER_OVERRIDE,
    USER_CANCELLED,
    VERIFIED_COMPLETE,
    CompletionRequest,
    CompletionStore,
    DodItem,
    GateRunner,
    RunEvidence,
)


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )


def _product_repo(tmp_path):
    """A tiny working product: a module + a test, committed."""
    root = tmp_path / "product"
    (root / "src" / "skaldir").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "skaldir" / "calc.py").write_text("def double(n):\n    return n * 2\n")
    (root / "tests" / "test_calc.py").write_text(
        "from godsv.calc import double\n\n\ndef test_double():\n    assert double(2) == 4\n"
    )
    (root / "README.md").write_text("product\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def _claim(goal="add what the user asked", **overrides):
    base = {
        "user_goal": goal,
        "claimed_completed": ["the requested feature works"],
        "test_results": "suite green",
    }
    base.update(overrides)
    return CompletionRequest(**base)


def _ev(name="pytest", ok=True, ts=9e12, revision=1):
    # evidence produced by the runner of the CURRENT contract revision
    # (audit P1: unbound/old evidence must not certify)
    return RunEvidence(
        name=name, cmd=f"{name} -q", ok=ok, detail="ok", ts=ts, revision=revision
    )


def test_full_loop_rejects_twice_then_verifies(tmp_path):
    """§40/§46: reject → keep working → reject again → keep working → VERIFIED."""
    repo = _product_repo(tmp_path)
    store = CompletionStore(tmp_path / "rt")
    dod = [
        DodItem(id="d1", text="feature works", blocking=True, verification="pytest"),
        DodItem(id="d2", text="UI exposes the feature", blocking=True, verification="ui_e2e"),
    ]
    store.begin_goal("s1", "add doubling to the product", dod)
    runner = GateRunner(repo_root=repo, dod=dod)

    # -- the agent makes a real change and claims completion #1 (no tests run yet)
    target = repo / "src" / "skaldir" / "calc.py"
    target.write_text("def double(n):\n    return n * 2\n\n\ndef triple(n):\n    return n * 3\n")
    request_1 = _claim()
    result_1 = runner.verify(request_1, [], store=store)
    assert result_1.status == INCOMPLETE, "voice must say NO"
    assert not result_1.terminal, "INCOMPLETE never ends the run"
    assert "Continue working now" in result_1.redirect_md()
    assert result_1.next_actions

    # -- the agent continues automatically (no user 'continue'): runs the suite
    run = subprocess.run(  # noqa: S603,S607
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    )
    assert run.returncode == 0  # repo is real; the agent is working in it
    evidence = [_ev("pytest", ok=True)]

    # -- completion #2: tests are green now, but the UI requirement has no evidence
    result_2 = runner.verify(_claim(), evidence, store=store)
    assert result_2.status == INCOMPLETE
    assert any("definition-of-done" in f for f in result_2.blocking_failures)
    assert result_2.fingerprint != result_1.fingerprint, "new blocker set, new fingerprint"

    # -- the agent keeps working and produces the UI evidence
    evidence.append(_ev("ui_e2e", ok=True))

    # -- completion #3: everything required is independently verified
    result_3 = runner.verify(_claim(), evidence, store=store)
    assert result_3.status == VERIFIED_COMPLETE
    assert result_3.terminal
    assert result_3.verified_requirements == ["the requested feature works"]
    assert store.state()["state"] == VERIFIED_COMPLETE

    metrics = store.metrics()
    assert metrics["completion_requests"] == 3
    assert metrics["completion_rejections"] == 2
    assert metrics["verified_completions"] == 1
    assert metrics["false_completion_prevented"] == 2


@pytest.mark.parametrize(
    "case",
    [
        "disconnected_ui",
        "fake_data",
        "tests_not_executed",
        "persistence_failure",
        "runtime_failure",
        "regression",
        "actual_completion",
    ],
)
def test_mandatory_e2e_cases(tmp_path, case):
    """§41: each failure mode → REJECT → FIX → VERIFY AGAIN → outcome."""
    repo = _product_repo(tmp_path)
    store = CompletionStore(tmp_path / "rt")
    dod: list[DodItem] = []
    runner_kwargs = {}
    evidence: list[RunEvidence] = []

    if case == "disconnected_ui":
        # a UI component exists but nothing imports it (§16)
        (repo / "src" / "skaldir" / "ui_panel.py").write_text("def render():\n    return 'ui'\n")
        _git(repo, "add", "-A")
        dod = []
        runner_kwargs = {"dod": dod}
        evidence = [_ev("pytest", ok=True)]
    elif case == "fake_data":
        (repo / "src" / "skaldir" / "calc.py").write_text(
            "def double(n):\n    return {'placeholder': n * 2}\n"
        )
        _git(repo, "add", "-A")
        evidence = [_ev("pytest", ok=True)]
    elif case == "tests_not_executed":
        (repo / "src" / "skaldir" / "calc.py").write_text("def double(n):\n    return 2 * n\n")
        _git(repo, "add", "-A")
        evidence = []  # tests written, never run
    elif case == "persistence_failure":
        dod = [
            DodItem(
                id="p1", text="setting survives restart",
                blocking=True, verification="persistence_restart",
            )
        ]
        runner_kwargs = {"dod": dod}
        evidence = [_ev("pytest", ok=True)]
    elif case == "runtime_failure":
        dod = [DodItem(id="r1", text="app launches", blocking=True, verification="app_launch")]
        runner_kwargs = {"dod": dod}
        evidence = [_ev("pytest", ok=True)]
    elif case == "regression":
        dod = [
            DodItem(
                id="g1", text="old features still work",
                blocking=True, verification="regression",
            )
        ]
        runner_kwargs = {"dod": dod}
        evidence = [_ev("pytest", ok=True)]
    elif case == "actual_completion":
        (repo / "src" / "skaldir" / "calc.py").write_text("def double(n):\n    return n + n\n")
        _git(repo, "add", "-A")
        evidence = [_ev("pytest", ok=True)]
    store.begin_goal("s1", "goal", runner_kwargs.get("dod", []))
    runner = GateRunner(repo_root=repo, **runner_kwargs)

    first = runner.verify(_claim(), evidence, store=store)
    if case == "actual_completion":
        assert first.status == VERIFIED_COMPLETE
        return
    assert first.status == INCOMPLETE, f"{case} must be rejected"
    assert not first.terminal

    # -- fix the reported problem for real, then verify again
    if case == "disconnected_ui":
        (repo / "src" / "skaldir" / "calc.py").write_text(
            "from godsv.ui_panel import render\n\n\ndef double(n):\n"
            "    return n * 2\n\n\nUI = render()\n"
        )
        _git(repo, "add", "-A")
    elif case == "fake_data":
        (repo / "src" / "skaldir" / "calc.py").write_text("def double(n):\n    return n * 2\n")
        _git(repo, "add", "-A")
    elif case == "tests_not_executed":
        evidence = [_ev("pytest", ok=True)]
    elif case == "persistence_failure":
        evidence = [_ev("pytest", ok=True), _ev("persistence_restart", ok=True)]
    elif case == "runtime_failure":
        evidence = [_ev("pytest", ok=True), _ev("app_launch", ok=True)]
    elif case == "regression":
        evidence = [_ev("pytest", ok=True), _ev("regression", ok=True)]

    second = runner.verify(_claim(), evidence, store=store)
    assert second.status == VERIFIED_COMPLETE, (
        f"{case}: after the fix the supervisor must verify "
        f"(blockers: {second.blocking_failures})"
    )


def test_voice_failure_never_implies_completion():
    """§43/§44: when facts cannot be gathered, the supervisor must NOT verify."""
    runner = GateRunner(repo_root="/nonexistent-repo")
    result = runner.verify(_claim(), [], store=None)
    assert result.status == INCOMPLETE
    assert not result.terminal


def test_crash_during_verification_never_marks_complete(tmp_path):
    """§43: crash mid-verification → state stays unverified; verification restarts."""
    repo = _product_repo(tmp_path)
    store = CompletionStore(tmp_path / "rt")
    store.begin_goal("s1", "goal", [])
    # simulate: request arrived, verification was interrupted (crash)
    store.transition(COMPLETION_REQUESTED)
    state = store.state()["state"]
    assert state == COMPLETION_REQUESTED
    assert state not in (VERIFIED_COMPLETE,)
    # recovery: verification restarts from scratch and still rejects
    result = GateRunner(repo_root=repo).verify(_claim(), [], store=store)
    assert result.status == INCOMPLETE


def test_terminal_states_are_the_only_exits(tmp_path):
    """§3: INCOMPLETE is not an exit; every listed terminal state is."""
    assert INCOMPLETE not in (VERIFIED_COMPLETE, BLOCKED_USER, BLOCKED_EXTERNAL, USER_CANCELLED)
    store = CompletionStore(tmp_path / "rt")
    for terminal in (BLOCKED_USER, BLOCKED_EXTERNAL, USER_CANCELLED, SHIPPED_WITH_USER_OVERRIDE):
        store.begin_goal("s", "g", [])
        store.transition(terminal)
        assert store.state()["state"] == terminal
        assert store.state()["state"] != VERIFIED_COMPLETE


def test_user_override_is_not_verified_complete(tmp_path):
    """§35: explicit user override is recorded, never upgraded to VERIFIED_COMPLETE."""
    store = CompletionStore(tmp_path / "rt")
    store.begin_goal("s", "g", [])
    store.transition(SHIPPED_WITH_USER_OVERRIDE, reason="user said ship it")
    state = store.state()
    assert state["state"] == SHIPPED_WITH_USER_OVERRIDE
    assert state["state"] != VERIFIED_COMPLETE


def test_main_agent_cannot_self_certify(tmp_path):
    """§4/§44: there is no API on the request side that sets VERIFIED_COMPLETE."""
    import re as _re

    from skaldir import completion as module

    source = open(module.__file__, encoding="utf-8").read()  # noqa: SIM115
    # exactly one place *assigns* the verified status — the gate outcome
    pattern = r"\s*status\s*=\s*VERIFIED_COMPLETE"
    writes = [line for line in source.splitlines() if _re.match(pattern, line)]
    assert len(writes) == 1, writes
    store = CompletionStore(tmp_path / "rt")
    store.begin_goal("s", "g", [])
    with pytest.raises(ValueError):
        store.transition("DONE")
