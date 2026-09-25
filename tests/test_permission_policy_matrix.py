"""The policy matrix: one table of cases, one judge, zero divergences.

``tests/fixtures/permission_policy_cases.json`` holds one row per policy situation — a command,
the rules and grants in force, the profile's command ceiling, the approval policy — and the
decision, reason kind and exit code that must come out. Every row is judged by the same engine
the CLI uses, and the report at the end must read **0 divergences**.

The table is deliberately redundant in places (a banned prefix with an allow rule present, an
undecomposable command with an allow rule present) because the *precedence between the rungs* is
the part worth pinning, and that is exactly what a single row cannot test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import approval as A
from skaldir import permission_profile as P

CASES_PATH = Path(__file__).resolve().parent / "fixtures" / "permission_policy_cases.json"
DOCUMENT = json.loads(CASES_PATH.read_text(encoding="utf-8"))
CASES = DOCUMENT["cases"]
CASE_IDS = [case["id"] for case in CASES]


def _judge(case: dict, workspace: Path) -> A.Verdict:
    profile = P.default_profile(workspace).with_changes(
        allowed_subprocess_prefixes=tuple(
            tuple(item) for item in case.get("profile", {}).get("allowed_subprocess_prefixes", ())
        )
    )
    store = A.RuleStore(
        rules=[
            A.PrefixRule(
                prefix=tuple(rule["prefix"]), decision=rule["decision"], note=rule.get("note", "")
            )
            for rule in case.get("rules", [])
        ],
        persist=False,
    )
    ledger = A.GrantLedger(banned=store.banned, persist=False)
    for grant in case.get("grants", []):
        ledger.add(
            tuple(grant["prefix"]),
            grant["scope"],
            command=case["command"],
            granted_at=1.0,
        )
    engine = A.ApprovalEngine(
        profile,
        store=store,
        grants=ledger,
        policy=case.get("policy", "on_request"),
        interactive=False,
        session="matrix",
    )
    return engine.judge(case["command"])


def test_the_table_has_at_least_twelve_cases() -> None:
    assert len(CASES) >= 12, f"the matrix is too thin to mean anything: {len(CASES)} case(s)"
    assert len(set(CASE_IDS)) == len(CASES), "case ids must be unique"


def test_every_case_declares_what_it_expects_and_why() -> None:
    for case in CASES:
        assert set(case) >= {"id", "command", "expect", "why"}, case["id"]
        expect = case["expect"]
        assert set(expect) == {"decision", "reason_kind", "exit_code"}, case["id"]
        assert expect["decision"] in {item.value for item in A.Decision}, case["id"]
        assert len(case["why"]) > 20, f"{case['id']} does not explain itself"


def test_every_case_is_exercised_by_at_least_one_rung() -> None:
    """A case with no rules, no grants and no ceiling only tests the default — say so."""
    trivial = [
        case["id"]
        for case in CASES
        if case["expect"]["reason_kind"] == "default"
        and not case.get("rules")
        and not case.get("grants")
        and not case.get("profile", {}).get("allowed_subprocess_prefixes")
        and case.get("policy", "on_request") == "on_request"
    ]
    assert len(trivial) <= 2, (
        f"{trivial} are all 'nothing matched' rows; the matrix would not be testing the ladder"
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_matches(case: dict, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    verdict = _judge(case, workspace)
    got = {
        "decision": verdict.decision,
        "reason_kind": verdict.reason_kind,
        "exit_code": verdict.exit_code,
    }
    assert got == case["expect"], (
        f"{case['id']}: expected {case['expect']}, got {got} — {verdict.reason}"
    )
    assert verdict.blocked == (verdict.decision != A.Decision.ALLOW.value)
    if verdict.decision == A.Decision.ALLOW.value:
        assert verdict.what_would_allow() == []
    else:
        assert verdict.what_would_allow(), f"{case['id']}: a block must say what would allow it"


def test_the_whole_table_judged_in_one_pass_has_zero_divergences(tmp_path: Path) -> None:
    """The headline number: every row, one run, no drift between expectation and ruling."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    divergences: list[str] = []
    for case in CASES:
        verdict = _judge(case, workspace)
        got = {
            "decision": verdict.decision,
            "reason_kind": verdict.reason_kind,
            "exit_code": verdict.exit_code,
        }
        if got != case["expect"]:
            divergences.append(f"{case['id']}: expected {case['expect']} got {got}")
    report = {
        "cases": len(CASES),
        "divergences": len(divergences),
        "decisions": {
            decision: sum(1 for case in CASES if case["expect"]["decision"] == decision)
            for decision in ("allow", "prompt", "forbid")
        },
    }
    print(json.dumps(report, sort_keys=True))
    assert report["decisions"]["allow"] >= 5, "an all-prompt table proves nothing"
    assert report["decisions"]["prompt"] >= 5
    assert report["decisions"]["forbid"] >= 3
    assert divergences == [], "divergences:\n" + "\n".join(divergences)
