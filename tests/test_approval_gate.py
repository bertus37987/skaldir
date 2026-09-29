"""The approval gate: the three modes must be *effectively* different, APR-02 must not be.

GOAL §4: "Ask Everything, Approve for me und Approve Everything müssen wirksam verschieden
sein. Geld und irreversible Schäden erfordern weiterhin konkrete Zustimmung."

The first sentence is the point of the gate. The second is the line it may never cross.
"""

from __future__ import annotations

from skaldir.approval import Decision
from skaldir.approval_gate import ApprovalGate, ReadonlyTools, SpendingTools
from skaldir.approval_modes import ALWAYS_NEEDS_APPROVAL, ApprovalMode


def test_the_three_modes_are_effectively_different():
    """APR-01/04 — three labels over one behaviour would fail this."""
    outcomes = {}
    for mode in ApprovalMode:
        gate = ApprovalGate(mode)
        outcomes[mode] = (
            gate.decide(tool="read_file", text="read a file", readonly=True).decision,
            gate.decide(tool="write_file", text="write a file", readonly=False).decision,
        )
    # every mode must differ from at least one other on the same input
    assert len(set(outcomes.values())) == 3, f"the modes are not distinct: {outcomes}"

    ask, approve_me, approve_all = (
        outcomes[ApprovalMode.ASK_EVERYTHING],
        outcomes[ApprovalMode.APPROVE_FOR_ME],
        outcomes[ApprovalMode.APPROVE_EVERYTHING],
    )
    assert ask[0] is Decision.PROMPT, "Ask Everything must ask even for a read"
    assert approve_me[0] is Decision.ALLOW, "Approve for me must let a read through"
    assert approve_me[1] is Decision.PROMPT, "Approve for me must still ask before a write"
    assert approve_all == (Decision.ALLOW, Decision.ALLOW), "Approve Everything must run"


def test_spending_needs_explicit_approval_in_every_mode():
    """APR-02 — the line that no mode may widen."""
    for mode in ApprovalMode:
        gate = ApprovalGate(mode)
        for tool in sorted(SpendingTools):
            verdict = gate.decide(tool=tool, text="", readonly=False)
            assert verdict.decision is Decision.PROMPT, f"{mode} auto-approved spending via {tool}"
            assert verdict.requires_explicit is True
            assert "APR-02" in verdict.reason
            assert "explicit approval" in verdict.reason


def test_an_irreversible_effect_needs_explicit_approval_in_every_mode():
    for mode in ApprovalMode:
        gate = ApprovalGate(mode)
        for text in ("this is irreversible", "rm -rf /var/lib/app", "DROP TABLE users",
                     "publish the draft now"):
            verdict = gate.decide(tool="run", text=text, readonly=False)
            assert verdict.decision is Decision.PROMPT, f"{mode} auto-approved {text!r}"
            assert verdict.requires_explicit is True


def test_the_gate_can_only_tighten_a_decision_never_loosen_it():
    """The safety invariant: a mode is not a way around a refusal."""
    for mode in ApprovalMode:
        gate = ApprovalGate(mode)
        for judge in (Decision.ALLOW, Decision.PROMPT, Decision.FORBID):
            for tool in ("read_file", "write_file", "purchase", "unknown_tool"):
                verdict = gate.decide(tool=tool, text="", judge_decision=judge, readonly=False)
                if judge is Decision.FORBID:
                    assert verdict.decision is Decision.FORBID, f"{mode} overrode a refusal"
                if judge is Decision.PROMPT:
                    assert verdict.decision is not Decision.ALLOW, f"{mode} removed an ask"


def test_an_unknown_tool_is_not_assumed_to_be_readonly():
    """Only a named tool is readonly — an unknown one is asked about."""
    assert "definitely_not_a_known_tool" not in ReadonlyTools
    gate = ApprovalGate(ApprovalMode.APPROVE_FOR_ME)
    verdict = gate.decide(tool="definitely_not_a_known_tool", text="")
    assert verdict.decision is Decision.PROMPT


def test_a_batch_is_released_only_when_every_member_passes():
    """GOAL §4 — batches are covered, and one bad member refuses the batch."""
    gate = ApprovalGate(ApprovalMode.APPROVE_EVERYTHING)

    clean = gate.decide_batch([
        {"tool": "read_file", "args": {"path": "a.txt"}},
        {"tool": "read_file", "args": {"path": "b.txt"}},
    ])
    assert gate.batch_decision(clean).decision is Decision.ALLOW

    mixed = gate.decide_batch([
        {"tool": "read_file", "args": {"path": "a.txt"}},
        {"tool": "purchase", "args": {"item": "stock"}},
    ])
    batch = gate.batch_decision(mixed)
    assert batch.decision is Decision.PROMPT, "a batch containing spending ran without asking"
    assert batch.requires_explicit is True

    forbidden = gate.decide_batch(
        [{"tool": "read_file", "args": {}}, {"tool": "write_file", "args": {}}],
        judge_decisions=[Decision.ALLOW, Decision.FORBID],
    )
    assert gate.batch_decision(forbidden).decision is Decision.FORBID


def test_the_empty_batch_runs_and_says_so():
    gate = ApprovalGate(ApprovalMode.ASK_EVERYTHING)
    assert gate.batch_decision([]).decision is Decision.ALLOW


def test_the_policy_document_is_carried_but_not_trusted_over_apr02():
    """The mode's policy document is reported; APR-02 still wins."""
    gate = ApprovalGate(ApprovalMode.APPROVE_EVERYTHING)
    verdict = gate.decide(tool="purchase", text="")
    assert verdict.policy["mode"] == ApprovalMode.APPROVE_EVERYTHING.value
    # ... and the document claims `default_decision: allow`, yet the call still prompts
    assert verdict.policy["default_decision"] == "allow"
    assert verdict.decision is Decision.PROMPT, "the policy document outranked APR-02"
    assert set(verdict.policy["requires_explicit_approval"]) == set(ALWAYS_NEEDS_APPROVAL)


def test_every_always_needs_approval_entry_is_actually_enforced():
    """If APR-02 names a category, something must enforce it."""
    gate = ApprovalGate(ApprovalMode.APPROVE_EVERYTHING)
    text = " ".join(ALWAYS_NEEDS_APPROVAL)      # spend money, irreversible effect
    verdict = gate.decide(tool="anything", text=text)
    assert verdict.requires_explicit is True
    assert verdict.decision is Decision.PROMPT
