"""The approval ladder's new rung: a control the profile *requires* forfeits every allow.

The ladder above this rung is unchanged (an allow rule still allows, a grant still expires with
its session). What is new is the rung below it: when the profile requires a control this host
cannot apply, no rule and no grant may run the command — because the alternative is running it
under a policy the kernel is not actually applying.

The verdict also answers *"under what?"* now, and that answer reaches the evidence ledger, so
"why did this run" and "with what enforcement" are one record.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skaldir import approval as A
from skaldir import permission_profile as P
from skaldir import security_linux as S


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


def _engine(runtime: Path, profile: P.PermissionProfile, **kwargs: object) -> A.ApprovalEngine:
    return A.ApprovalEngine(profile=profile, runtime=runtime, session="s1", **kwargs)  # type: ignore[arg-type]


def test_the_ladder_is_unchanged_when_the_profile_requires_nothing(
    workspace: Path, runtime: Path
) -> None:
    """The new rung costs nothing when nothing is required: rules still allow."""
    profile = P.default_profile(workspace)
    assert profile.unenforceable_required() == []
    engine = _engine(runtime, profile)
    engine.store.add_rule(("git", "status"), "allow")
    verdict = engine.judge("git status")
    assert verdict.allowed is True and verdict.reason_kind == "rule", verdict.to_dict()


def test_a_required_control_this_host_cannot_apply_forbids_everything(
    workspace: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(S, "landlock_abi", lambda refresh=False: 0)
    monkeypatch.setattr(S, "_ABI_CACHE", {"abi": 0, "abi_errno": 95})
    profile = P.default_profile(workspace).with_changes(
        linux_security=P.LinuxSecurityPolicy(landlock="require")
    )
    engine = _engine(runtime, profile)
    engine.store.add_rule(("git", "status"), "allow")  # an allow rule must not save it
    verdict = engine.judge("git status")
    assert verdict.forbidden is True
    assert verdict.blocked is True and verdict.exit_code == 3
    assert verdict.reason_kind == "unenforceable_control"
    assert "landlock" in verdict.reason and "cannot apply" in verdict.reason
    assert "fix" in verdict.reason or "auto" in verdict.reason
    assert verdict.allowed is False
    assert verdict.what_would_allow()[0].startswith("no command can be allowed"), (
        "no grant can fix this, so the suggestion list must not offer one"
    )


def test_the_verdict_carries_under_what_the_command_would_run(
    workspace: Path, runtime: Path
) -> None:
    profile = P.default_profile(workspace).with_changes(caps=P.ResourceCaps(max_processes=8))
    verdict = _engine(runtime, profile).judge("git status")
    enforcement = verdict.enforcement
    assert enforcement["profile_sha256"] == profile.identity_sha256
    assert enforcement["mechanism"] == profile.mechanism()[0]
    assert set(enforcement["controls"]) == {"landlock", "seccomp", "no_new_privs", "pids_cgroup"}
    assert enforcement["controls"]["pids_cgroup"] == profile.pids_cgroup_mode()[0]
    assert enforcement["controls"]["pids_cgroup"] == "enforce", (
        "the cap is declared and this host can host a cgroup leaf"
    )
    assert enforcement["unenforceable_required"] == []
    assert verdict.to_dict()["enforcement"] == enforcement, (
        "the JSON payload and the object must agree — a GUI reads one of them"
    )


def test_the_enforcement_answer_reaches_the_evidence_ledger(
    workspace: Path, runtime: Path
) -> None:
    profile = P.default_profile(workspace)
    engine = _engine(runtime, profile)
    engine.judge("git status")
    events = A.EvidenceLedger(runtime).tail(5)
    record = events[-1]
    assert record["profile"]["sha256"] == profile.identity_sha256
    assert record["enforcement"]["mechanism"] == profile.mechanism()[0]
    assert set(record["enforcement"]["controls"]) >= {"landlock", "seccomp", "no_new_privs"}
    assert isinstance(record["enforcement"]["unenforced"], list)
    assert "caps.max_processes" in record["enforcement"]["unenforced"], (
        "the ledger must keep the honest gap list too, not only the wins"
    )


def test_policy_summary_can_answer_under_what_without_being_told_twice(
    workspace: Path, runtime: Path
) -> None:
    profile = P.default_profile(workspace)
    engine = _engine(runtime, profile)
    without = A.policy_summary(engine.store, engine.grants)
    assert "enforcement" not in without, "an existing caller keeps its payload"
    with_profile = A.policy_summary(engine.store, engine.grants, profile)
    assert with_profile["enforcement"]["mechanism"] == profile.mechanism()[0]
    assert with_profile["enforcement"]["profile_sha256"] == profile.identity_sha256


def test_the_snapshot_comes_from_the_profile_not_from_the_plan(workspace: Path) -> None:
    """One derivation site: a verdict cannot describe a mechanism the run will not use."""
    profile = P.default_profile(workspace)
    snapshot = A.enforcement_snapshot(profile)
    plan = profile.security_plan()
    assert snapshot["mechanism"] == plan.mechanism
    assert snapshot["profile_sha256"] == plan.profile_sha256
    assert snapshot["controls"]["seccomp"] == plan.seccomp
    assert snapshot["controls"]["landlock"] == plan.landlock
