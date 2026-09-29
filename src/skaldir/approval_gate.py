"""The approval gate: what an approval *mode* actually lets through.

GOAL §4:

    "Verbinde `approval_modes.policy_for` mit der vorhandenen Approval-Engine und dem
     tatsächlichen Tool-Dispatch ... Ask Everything, Approve for me und Approve Everything
     müssen wirksam verschieden sein. Geld und irreversible Schäden erfordern weiterhin
     konkrete Zustimmung."

Until now :func:`skaldir.approval_modes.policy_for` produced a policy *document* that
nothing read: the modes were three labels over one behaviour. This module is the part that
turns a mode into a decision at the point where a tool call is about to run.

**The one invariant that shapes everything:**

The gate may only make a decision *stricter* than the deterministic judge in
:mod:`skaldir.toolcall`. It can turn ``allow`` into ``prompt``; it can never turn
``forbid`` into ``allow``, and never ``prompt`` into ``allow``. An approval mode is a
preference about how often to ask, never a way around a refusal. That is why the gate
takes the judge's verdict as input instead of re-deriving it.

**APR-02 is not a mode.** New spending and irreversible effects require explicit
approval in *every* mode, including Approve Everything. ``ALWAYS_NEEDS_APPROVAL`` is
therefore checked first and cannot be widened by any policy document.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .approval import Decision
from .approval_modes import ApprovalMode, policy_for

__all__ = [
    "ApprovalGate",
    "GateVerdict",
    "ReadonlyTools",
    "SpendingTools",
]

#: Tools that only read. Harmless enough for "Approve for me" to let through without
#: asking. Deliberately a small, named list: an unknown tool is not assumed to be readonly.
ReadonlyTools: frozenset[str] = frozenset({
    "read_file", "search_files", "list_dir", "glob", "grep",
    "web_search", "web_extract", "knowledge_read", "memory_read",
    "tools_list", "tools_search", "tool_search", "status", "doctor",
    "contract_dump", "selftest",
})

#: Tools that can spend money or buy something. Never auto-approved (APR-02).
SpendingTools: frozenset[str] = frozenset({
    "purchase", "buy", "order", "pay", "checkout", "subscribe", "book", "transfer",
})

#: Kinds of effect that are irreversible or permanently harmful. Never auto-approved.
IrreversibleMarkers: tuple[str, ...] = (
    "irreversible", "permanently", "destructive", "delete", "rm -rf", "drop table",
    "shred", "overwrite", "format ", "mkfs", "publish", "send email", "post publicly",
)


@dataclass(frozen=True)
class GateVerdict:
    """The gate's answer plus why, so the caller can show it without guessing."""

    decision: Decision
    mode: str
    reason: str
    requires_explicit: bool = False
    was_stricter_than_judge: bool = False
    policy: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def blocked(self) -> bool:
        return self.decision is Decision.FORBID

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "mode": self.mode,
            "reason": self.reason,
            "requires_explicit": self.requires_explicit,
            "was_stricter_than_judge": self.was_stricter_than_judge,
        }


def _needs_explicit(tool: str, text: str) -> tuple[bool, str]:
    """APR-02: does this call spend money or do something irreversible?"""
    name = (tool or "").strip().lower()
    if name in SpendingTools:
        return True, (
            f"{name!r} spends money — new spending needs explicit approval in "
            "every mode (APR-02)"
        )

    lowered = (text or "").lower()
    for marker in IrreversibleMarkers:
        if marker in lowered:
            return True, (
                f"irreversible effect indicated by {marker!r} — needs explicit approval "
                "in every mode (APR-02)"
            )
    return False, ""


class ApprovalGate:
    """Turns an approval mode into a decision for one about-to-run tool call."""

    def __init__(self, mode: ApprovalMode | str = ApprovalMode.ASK_EVERYTHING,
                 *, store: Any | None = None) -> None:
        self.store = store
        self.mode = ApprovalMode(mode) if not isinstance(mode, ApprovalMode) else mode

    def mode_for(self, run_id: str = "") -> ApprovalMode:
        if self.store is not None and run_id:
            try:
                return self.store.mode_for(run_id)
            except Exception:  # noqa: BLE001 — an unreadable store must not widen anything
                return self.mode
        return self.mode

    def decide(
        self,
        *,
        tool: str = "",
        text: str = "",
        judge_decision: Decision | str = Decision.ALLOW,
        readonly: bool | None = None,
        run_id: str = "",
    ) -> GateVerdict:
        """Decide for one call.

        ``judge_decision`` is the deterministic verdict from :mod:`skaldir.toolcall`. The
        gate can only tighten it.
        """
        mode = self.mode_for(run_id)
        policy = policy_for(mode)
        judge = (
            Decision(judge_decision) if not isinstance(judge_decision, Decision) else judge_decision
        )


        # 1. a refusal is final — no mode turns FORBID into anything else
        if judge is Decision.FORBID:
            return GateVerdict(
                decision=Decision.FORBID,
                mode=mode.value,
                reason="the judge refused this call — no approval mode can override a refusal",
                policy=policy,
            )

        # 2. APR-02 outranks every mode
        explicit, why = _needs_explicit(tool, text)
        if explicit:
            return GateVerdict(
                decision=Decision.PROMPT,
                mode=mode.value,
                reason=why,
                requires_explicit=True,
                policy=policy,
            )

        # 3. a call the judge already wanted to ask about stays an ask in every mode
        if judge is Decision.PROMPT:
            return GateVerdict(
                decision=Decision.PROMPT,
                mode=mode.value,
                reason="the judge wants a human decision — an approval mode does not remove it",
                policy=policy,
            )

        # 4. now the modes genuinely differ
        is_readonly = (
            readonly if readonly is not None
            else (tool or "").strip().lower() in ReadonlyTools
        )
        if mode is ApprovalMode.ASK_EVERYTHING:
            return GateVerdict(
                decision=Decision.PROMPT,
                mode=mode.value,
                reason="Ask Everything: every call is shown first",
                was_stricter_than_judge=True,
                policy=policy,
            )
        if mode is ApprovalMode.APPROVE_FOR_ME:
            if is_readonly:
                return GateVerdict(
                    decision=Decision.ALLOW,
                    mode=mode.value,
                    reason="Approve for me: a known read-only call runs without asking",
                    policy=policy,
                )
            return GateVerdict(
                decision=Decision.PROMPT,
                mode=mode.value,
                reason="Approve for me: anything that changes state is still shown first",
                was_stricter_than_judge=True,
                policy=policy,
            )
        # APPROVE_EVERYTHING
        return GateVerdict(
            decision=Decision.ALLOW,
            mode=mode.value,
            reason="Approve Everything: this call runs without asking (APR-02 still applies)",
            policy=policy,
        )

    def decide_batch(
        self,
        calls: Iterable[dict],
        *,
        judge_decisions: Iterable[Decision | str] = (),
        run_id: str = "",
    ) -> list[GateVerdict]:
        """GOAL §4 says batches are covered too.

        A batch is released as a whole only when *every* member passes. One prompt-worthy
        member makes the batch prompt-worthy; one forbidden member refuses the batch.
        """
        items = list(calls)
        verdicts = list(judge_decisions) or [Decision.ALLOW] * len(items)
        results = [
            self.decide(
                tool=str(item.get("tool", "") or item.get("name", "")),
                text=(
                    " ".join(str(v) for v in item.get("args", {}).values())
                    if isinstance(item.get("args"), dict) else str(item)
                ),
                judge_decision=verdicts[index] if index < len(verdicts) else Decision.ALLOW,
                run_id=run_id,
            )
            for index, item in enumerate(items)
        ]
        return results

    def batch_decision(self, verdicts: list[GateVerdict]) -> GateVerdict:
        """The batch as a whole: the strictest member wins."""
        if any(v.blocked for v in verdicts):
            strictest = next(v for v in verdicts if v.blocked)
        elif any(v.decision is Decision.PROMPT for v in verdicts):
            strictest = next(v for v in verdicts if v.decision is Decision.PROMPT)
        elif verdicts:
            strictest = verdicts[0]
        else:
            return GateVerdict(decision=Decision.ALLOW, mode="", reason="an empty batch runs")
        return GateVerdict(
            decision=strictest.decision,
            mode=strictest.mode,
            reason=f"batch: {strictest.reason}",
            requires_explicit=any(v.requires_explicit for v in verdicts),
            was_stricter_than_judge=any(v.was_stricter_than_judge for v in verdicts),
            policy=strictest.policy,
        )
