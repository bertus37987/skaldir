"""Grant scopes: a turn grant dies with its turn, a session grant never crosses sessions.

This is the rung that makes a "yes" mean something precise. The measurement that matters is the
last one: the same command, the same rules, the same runtime — only the **session id** differs
— must be allowed in the session that granted it and prompted in a new one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skaldir import approval as A
from skaldir import permission_profile as P


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    home = tmp_path / "rt"
    home.mkdir()
    return home


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _engine(runtime: Path, workspace: Path, *, session: str) -> A.ApprovalEngine:
    store = A.RuleStore(runtime=runtime, persist=True)
    ledger = A.GrantLedger(runtime=runtime, session=session, banned=store.banned, persist=True)
    return A.ApprovalEngine(
        P.default_profile(workspace),
        runtime=runtime,
        session=session,
        store=store,
        grants=ledger,
    )


def test_a_turn_grant_allows_once_in_process(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    engine.grants.add(("pytest",), A.GrantScope.TURN.value, command="pytest -q")
    assert engine.judge("pytest -q").decision == A.Decision.ALLOW.value
    assert engine.judge("pytest -q").reason_kind == "grant"
    assert engine.grants.to_dict()["counts"]["turn"] == 1


def test_a_turn_grant_does_not_survive_the_turn(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    engine.grants.add(("pytest",), A.GrantScope.TURN.value, command="pytest -q")
    assert engine.grants.purge_turn() == 1
    verdict = engine.judge("pytest -q")
    assert verdict.decision == A.Decision.PROMPT.value


def test_a_turn_grant_is_never_written_to_disk(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    engine.grants.add(("pytest",), A.GrantScope.TURN.value, command="pytest -q")
    assert A.grants_path(runtime, "s1").is_file() is False, (
        "a turn grant must not be persisted: it would outlive the turn that gave it"
    )


def test_a_session_grant_survives_inside_its_session(runtime: Path, workspace: Path) -> None:
    first = _engine(runtime, workspace, session="s1")
    first.grants.add(("pytest",), A.GrantScope.SESSION.value, command="pytest -q")
    assert A.grants_path(runtime, "s1").is_file()
    again = _engine(runtime, workspace, session="s1")
    assert again.grants.to_dict()["counts"]["session"] == 1, (
        "a session grant must be reloaded by the session that gave it"
    )
    verdict = again.judge("pytest -q")
    assert verdict.decision == A.Decision.ALLOW.value
    assert verdict.scope == A.GrantScope.SESSION.value
    assert "s1" in verdict.reason


def test_a_session_grant_does_not_cross_into_a_new_session(runtime: Path, workspace: Path) -> None:
    granted = _engine(runtime, workspace, session="s1")
    granted.grants.add(("pytest",), A.GrantScope.SESSION.value, command="pytest -q")

    fresh = _engine(runtime, workspace, session="s2")
    assert fresh.grants.to_dict()["counts"] == {"turn": 0, "session": 0}
    verdict = fresh.judge("pytest -q")
    assert verdict.decision == A.Decision.PROMPT.value, (
        "a session grant must never be inherited by another session"
    )
    assert verdict.reason_kind == "default"

    # and the first session still has it: the grant was scoped, not lost
    assert granted.judge("pytest -q").decision == A.Decision.ALLOW.value


def test_a_session_grant_can_be_revoked_by_editing_the_file(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    engine.grants.add(("pytest",), A.GrantScope.SESSION.value, command="pytest -q")
    path = A.grants_path(runtime, "s1")
    path.write_text(
        '{"schema": "skaldir.approval-policy/1", "session": "s1", "session_grants": []}\n',
        encoding="utf-8",
    )
    assert _engine(runtime, workspace, session="s1").judge("pytest -q").decision == (
        A.Decision.PROMPT.value
    )


def test_the_ledger_file_explains_why_only_session_grants_are_written(
    runtime: Path, workspace: Path
) -> None:
    engine = _engine(runtime, workspace, session="s1")
    engine.grants.add(("pytest",), A.GrantScope.SESSION.value, command="pytest -q")
    document = A.grants_path(runtime, "s1").read_text()
    assert "only session-scoped grants are persisted" in document


def test_a_grant_covers_a_longer_command_with_the_same_prefix(
    runtime: Path, workspace: Path
) -> None:
    engine = _engine(runtime, workspace, session="s1")
    engine.grants.add(("pytest",), A.GrantScope.SESSION.value, command="pytest")
    assert engine.judge("pytest -q tests/test_a.py").decision == A.Decision.ALLOW.value
    assert engine.judge("pytestx -q").decision == A.Decision.PROMPT.value


def test_the_most_recent_turn_grant_wins_over_a_session_grant(
    runtime: Path, workspace: Path
) -> None:
    engine = _engine(runtime, workspace, session="s1")
    engine.grants.add(("pytest",), A.GrantScope.SESSION.value, command="pytest")
    engine.grants.add(("pytest", "-q"), A.GrantScope.TURN.value, command="pytest -q")
    verdict = engine.judge("pytest -q")
    assert verdict.decision == A.Decision.ALLOW.value
    assert verdict.scope == A.GrantScope.TURN.value


def test_a_banned_prefix_can_never_be_granted(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    for prefix in (("python3",), ("rm",), ("curl",), ("sudo",), ("sh",)):
        with pytest.raises(A.BannedPrefixRefusal):
            engine.grants.add(prefix, A.GrantScope.SESSION.value)
        with pytest.raises(A.BannedPrefixRefusal):
            engine.grants.add(prefix, A.GrantScope.TURN.value)
    assert engine.grants.to_dict()["counts"] == {"turn": 0, "session": 0}


def test_an_empty_prefix_cannot_be_granted(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    with pytest.raises(A.PolicyError):
        engine.grants.add((), A.GrantScope.TURN.value)


def test_an_unknown_scope_is_refused(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    with pytest.raises(A.PolicyError):
        engine.grants.add(("pytest",), "forever")
    with pytest.raises(A.PolicyError):
        A.grant_scope_from("forever")


def test_a_grant_with_no_session_id_is_not_persisted(runtime: Path, workspace: Path) -> None:
    ledger = A.GrantLedger(runtime=runtime, session="", persist=True)
    ledger.add(("pytest",), A.GrantScope.SESSION.value, command="pytest")
    assert list((runtime / "approval" / "grants").glob("*.json")) == []
    assert ledger.match(("pytest", "anything")) is not None


def test_engine_approve_records_the_review_decision(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    grant, rule, record = engine.approve("pytest -q", scope=A.GrantScope.TURN.value)
    assert grant is not None and rule is None
    assert record["review_decision"] == A.ReviewDecision.APPROVED.value
    assert record["prefix"] == ["pytest", "-q"]

    _, rule, record = engine.approve(
        "pytest -q", scope=A.GrantScope.SESSION.value, persist_rule=True, reason="trusted runner"
    )
    assert rule is not None
    assert record["review_decision"] == A.ReviewDecision.APPROVED_RULE_AMENDMENT.value
    assert rule.note == "trusted runner"


def test_engine_deny_teaches_back_and_can_be_persisted(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace, session="s1")
    rule, record = engine.deny("make deploy", reason="use the release job instead")
    assert rule is None
    assert record["review_decision"] == A.ReviewDecision.DENIED.value
    assert "release job" in record["reason"]

    rule, record = engine.deny("make deploy", reason="no", persist_rule=True)
    assert rule is not None and rule.decision == "forbid"
    assert engine.judge("make deploy").decision == A.Decision.FORBID.value

    _, record = engine.deny("git status", reason="stop", abort=True)
    assert record["review_decision"] == A.ReviewDecision.ABORT.value
