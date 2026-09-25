"""Evidence: every allow, prompt and denial is on the record, with the rule that decided it.

The question this file answers is "why did this run?" — after the fact, from the ledger, not
from anyone's memory. Each event carries the command, the verdict, the reason kind, the rule id
and text, the scope, the profile hash and the session, in an append-only JSONL file.
"""

from __future__ import annotations

import json
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


def _engine(runtime: Path, workspace: Path, *, session: str = "s1") -> A.ApprovalEngine:
    store = A.RuleStore(runtime=runtime, persist=True)
    ledger = A.GrantLedger(runtime=runtime, session=session, banned=store.banned, persist=True)
    return A.ApprovalEngine(
        P.default_profile(workspace),
        runtime=runtime,
        session=session,
        store=store,
        grants=ledger,
    )


def test_a_prompt_is_recorded_with_the_rule_that_decided_it(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace)
    engine.store.add_rule(("npm",), "prompt", note="ask the user")
    verdict = engine.judge("npm install left-pad")
    ledger = A.EvidenceLedger(runtime)
    assert ledger.count() == 1
    event = ledger.tail(1)[0]
    assert event["kind"] == "judgement"
    assert event["decision"] == "prompt"
    assert event["reason_kind"] == "rule"
    assert event["rule_id"] == verdict.rule_id
    assert event["rule_text"] == 'prompt "npm"'
    assert event["command"] == "npm install left-pad"
    assert event["session"] == "s1"
    assert event["profile"]["sha256"] == engine.profile.identity_sha256
    assert event["exit_code"] == 3
    assert event["evidence_ref"] == "approval-events.jsonl:1"
    assert verdict.evidence_ref == "approval-events.jsonl:1"


def test_an_allow_is_recorded_with_its_authority(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace)
    engine.grants.add(("pytest",), A.GrantScope.SESSION.value, command="pytest", granted_at=1.0)
    engine.judge("pytest -q")
    event = A.EvidenceLedger(runtime).tail(1)[0]
    assert event["decision"] == "allow"
    assert event["reason_kind"] == "grant"
    assert event["scope"] == "session"


def test_a_forbid_is_recorded_with_the_forbidding_rule(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace)
    rule = engine.store.add_rule(("make", "deploy"), "forbid", note="production")
    engine.judge("make deploy")
    event = A.EvidenceLedger(runtime).tail(1)[0]
    assert event["decision"] == "forbid"
    assert event["rule_id"] == rule.rule_id
    assert "production" not in event["reason"], "the reason quotes the rule, not the note"
    assert 'forbid "make deploy"' in event["rule_text"]


def test_a_denial_and_an_approval_are_both_recorded(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace)
    engine.approve("pytest -q", scope=A.GrantScope.TURN.value)
    engine.deny("make deploy", reason="use the release job")
    events = A.EvidenceLedger(runtime).read()
    kinds = [event["kind"] for event in events]
    assert kinds == ["approval", "denial"]
    assert events[0]["review_decision"] == "approved"
    assert events[1]["review_decision"] == "denied"
    assert events[1]["reason"] == "use the release job"
    assert events[1]["command"] == "make deploy"


def test_an_undecomposable_prompt_names_the_reason(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace)
    engine.judge("git status > /tmp/out.txt")
    event = A.EvidenceLedger(runtime).tail(1)[0]
    assert event["reason_kind"] == "undecomposable"
    assert event["rule_eligible"] is False
    assert any("redirection" in item for item in event["ineligible_reasons"]), (
        "the record must say *why* the command was not rule-eligible"
    )
    assert "redirection" in json.dumps(event), (
        "the serialized record must carry the reason, not only the verdict"
    )


def test_a_banned_prompt_is_recorded_as_banned(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace)
    engine.judge("python3 -c 'print(1)'")
    event = A.EvidenceLedger(runtime).tail(1)[0]
    assert event["banned_prefix"] == "python3"
    assert event["reason_kind"] == "banned_prefix"


def test_the_sequence_numbers_are_monotonic_and_the_file_appends(
    runtime: Path, workspace: Path
) -> None:
    engine = _engine(runtime, workspace)
    for command in ("git status", "git log", "ls"):
        engine.judge(command)
    events = A.EvidenceLedger(runtime).read()
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert [event["command"] for event in events] == ["git status", "git log", "ls"]
    assert len(list(A.events_path(runtime).read_text().splitlines())) == 3


def test_the_ledger_is_append_only_and_one_json_object_per_line(
    runtime: Path, workspace: Path
) -> None:
    engine = _engine(runtime, workspace)
    engine.judge("git status")
    first = A.events_path(runtime).read_text()
    engine.judge("git log")
    second = A.events_path(runtime).read_text()
    assert second.startswith(first), "an append must not rewrite what was already recorded"
    for line in second.splitlines():
        assert isinstance(json.loads(line), dict)


def test_a_corrupt_line_does_not_hide_the_good_ones(runtime: Path, workspace: Path) -> None:
    engine = _engine(runtime, workspace)
    engine.judge("git status")
    with A.events_path(runtime).open("a", encoding="utf-8") as handle:
        handle.write("{truncated\n")
    engine.judge("git log")
    assert [event["command"] for event in A.EvidenceLedger(runtime).read()] == [
        "git status",
        "git log",
    ]


def test_no_runtime_dir_means_no_file_but_still_a_reference() -> None:
    ledger = A.EvidenceLedger(None)
    record = ledger.record({"kind": "judgement", "command": "x"})
    assert ledger.count() == 0
    assert record["evidence_ref"].startswith("approval-events.jsonl:")


def test_an_engine_without_a_runtime_still_records_in_memory() -> None:
    engine = A.ApprovalEngine(None)
    verdict = engine.judge("git status")
    assert verdict.evidence_ref
    assert verdict.decision == A.Decision.PROMPT.value
