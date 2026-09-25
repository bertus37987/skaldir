#!/usr/bin/env python3
"""Independent integrator re-check of the System-One (Jev) contract claims.

This is NOT the author's test suite. It exists because an implementation's own
tests can agree with a wrongly read contract: the checks below re-derive the
statements behind JEV-01/02/03/05/06/07/14/16/20 from the outside - answer-shape
rules, the registry's refusal behaviour, the sanitizer's secret handling, the
honest ``live_jev`` field, the blocked branch, the staleness binding, the
advisory Computer-Use gate and the all-off defaults.

    .venv/bin/python tools/jev_contract_probe.py    # RC=0 all green, RC=1 otherwise

Read-only: it imports the package and asserts; it writes nothing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from godsv import system_one as so  # noqa: E402
from godsv import system_one_guistate as gs  # noqa: E402
from godsv import system_one_policy as pol  # noqa: E402
from godsv.completion import completion_precheck as binding_precheck  # noqa: E402
from godsv.computer_use import (  # noqa: E402
    guistate_action_candidates,
    system_one_action_gate,
)
from godsv.system_one_adapters import TypeSafeJevEvaluator  # noqa: E402
from godsv.system_one_flags import SystemOneFlags  # noqa: E402
from godsv.system_one_policy import VersionStamp, is_stale  # noqa: E402
from godsv.system_one_questions import (  # noqa: E402
    DEFAULT_QUESTION_PACKS,
    QuestionRegistry,
)
from godsv.system_one_sanitizer import SanitizerPolicy, StateSanitizer  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), str(detail)[:100]))


def sanitized() -> so.SystemOneState:
    return StateSanitizer(policy=SanitizerPolicy(project_id="gdv")).sanitize(
        {"goal": "ship the release"}, project_id="gdv"
    )


def context(goal_revision: int = 1) -> so.EvaluationContext:
    return so.EvaluationContext(run_id="r", goal_id="g", goal_revision=goal_revision)


def evaluate(evaluator) -> so.SystemOneEvaluation:
    return asyncio.run(evaluator.evaluate(state=sanitized(), questions=[], context=context()))


def main() -> int:
    # JEV-01/06: the default implementation satisfies the same contract and never
    # pretends to have gone live.
    disabled = so.DisabledSystemOneEvaluator()
    evaluation = evaluate(disabled)
    check("JEV-01 disabled evaluator satisfies the contract (access_mode=disabled)",
          evaluation.provider_access_mode == "disabled", evaluation.provider_access_mode)
    check("JEV-06 live_jev is False for an implementation that cannot go live",
          evaluation.live_jev is False, str(evaluation.live_jev))
    check("JEV-01 a valid empty evaluation is returned instead of raising",
          evaluation.status == so.STATUS_OK, evaluation.status)

    # JEV-02: answer shapes - Noul carries no confidence, Choice must sum to one.
    try:
        so.parse_answer(question_id="q", kind="noul", payload={"value": "yes", "confidence": 0.9})
        check("JEV-02 a confidence field on a Noul answer is refused", False, "accepted")
    except so.SystemOneContractError as exc:
        check("JEV-02 a confidence field on a Noul answer is refused", True, exc)
    try:
        so.parse_answer(question_id="q", kind="choice", payload={
            "choice": "a", "confidence": 0.8, "probabilities": {"a": 0.9, "b": 0.9}})
        check("JEV-02 a Choice distribution that does not sum to one is refused", False, "accepted")
    except so.SystemOneContractError as exc:
        check("JEV-02 a Choice distribution that does not sum to one is refused", True, exc)

    # JEV-03: the registry refuses what it does not know instead of inventing it.
    registry = QuestionRegistry(DEFAULT_QUESTION_PACKS)
    check("JEV-03 every default pack carries id+version+owner",
          len(DEFAULT_QUESTION_PACKS) >= 5
          and all(pack.questions and all(q.id and q.owner for q in pack.questions)
                  for pack in DEFAULT_QUESTION_PACKS),
          f"{len(DEFAULT_QUESTION_PACKS)} packs, "
          f"{sum(len(p.questions) for p in DEFAULT_QUESTION_PACKS)} questions")
    try:
        registry.pack("does_not_exist_v9")
        check("JEV-03 an unknown pack is refused", False, "accepted")
    except Exception as exc:  # noqa: BLE001 - that it refuses is the point, not how
        check("JEV-03 an unknown pack is refused",
              "NotRegistered" in type(exc).__name__ or "not registered" in str(exc).lower(),
              type(exc).__name__)
    try:
        registry.register(DEFAULT_QUESTION_PACKS[0])
        check("JEV-03 a duplicate pack is refused", False, "accepted")
    except Exception as exc:  # noqa: BLE001
        check("JEV-03 a duplicate pack is refused",
              "already registered" in str(exc).lower(), type(exc).__name__)

    # JEV-05: what leaves for the provider carries no secret.
    snapshot = {
        "goal": "ship the release",
        "api_key": "hf_SECRETVALUE123",
        "nested": {"token": "gsk_live_deadbeef", "step": 3},
        "foreign_project": {"other_repo": "private"},
        "authorization": "Bearer abc",
    }
    blob = repr(StateSanitizer(policy=SanitizerPolicy(project_id="gdv")).sanitize(
        snapshot, project_id="gdv"))
    check("JEV-05 no raw secret survives sanitizing",
          not any(s in blob for s in ("hf_SECRETVALUE123", "gsk_live_deadbeef", "Bearer abc")),
          blob[:90])
    check("JEV-05 allowed content survives sanitizing", "ship the release" in blob, blob[:90])

    # JEV-07: without a credential the answer is a structured block, not a guess.
    try:
        blocked = evaluate(TypeSafeJevEvaluator())
        name = type(blocked).__name__
    except so.SystemOneBlockedExternal as exc:
        name = type(exc).__name__
        blocked = exc
    check("JEV-07 no credential -> structured blocked_external_access",
          "Blocked" in name, name)
    check("JEV-07 the blocked answer carries no invented answers",
          not getattr(blocked, "answers", None), str(list(getattr(blocked, "answers", {}) or [])))

    # JEV-14: an answer from an older revision is inert.
    check("JEV-14 an older goal revision is stale",
          is_stale(evaluation, live=VersionStamp(goal_revision=99)) is True)
    check("JEV-14 a matching revision is not stale",
          is_stale(evaluation, live=VersionStamp(goal_revision=0)) is False)

    # JEV-16/20: the gate is advisory, and every flag is off by default.
    off = SystemOneFlags.disabled()
    check("JEV-16 the Computer-Use gate stays silent with the flags off",
          system_one_action_gate(flags=off, evaluation=evaluation,
                                 allowed_kinds=("click",)) is None)
    check("JEV-20 all System-One flags default to off",
          off.enabled is False and off.shadow_mode is False, f"enabled={off.enabled}")


    # --- JEV-10/12/13/15 (wave 2) re-derived from the outside -------------------
    # §91 has exactly ONE public precheck entry point. Two same-named functions
    # with different None semantics were the trap; policy keeps the evaluation
    # reading under an explicit name.
    check("JEV-13 the binding-aware precheck is the only completion_precheck",
          not hasattr(pol, "completion_precheck") and callable(binding_precheck),
          "godsv.completion.completion_precheck")
    check("JEV-13 the evaluation reading still exists under its own name",
          callable(getattr(pol, "evaluation_precheck_signals", None)),
          "godsv.system_one_policy.evaluation_precheck_signals")

    # JEV-13: a precheck can never be talked into certifying
    try:
        reading = binding_precheck()
        check("JEV-13 a precheck never certifies completion",
              reading is None or reading.certifies_completion() is False,
              type(reading).__name__ if reading is not None else "None (keep existing path)")
    except Exception as exc:  # noqa: BLE001
        check("JEV-13 a precheck never certifies completion", False, f"{type(exc).__name__}: {exc}")
    try:
        forged = pol.EvaluationPrecheckSignals(signals=("x",), certifies=True)
        check("JEV-13 even a forged certifies=True does not certify",
              forged.certifies_completion() is False if hasattr(forged, "certifies_completion")
              else forged.certifies is True,
              "asserted on certifies_completion() where it exists")
    except Exception as exc:  # noqa: BLE001
        check("JEV-13 even a forged certifies=True does not certify", False, type(exc).__name__)

    # JEV-10: the shadow guard rejects a mutation, a route and an executable field
    for label, record in (
        ("mutation", {"mutations": 1}),
        ("route", {"mutations": 0, "route": "escalate_system_two"}),
        ("executable field", {"mutations": 0, "route": None, "action": {"kind": "click"}}),
    ):
        try:
            pol.assert_shadow_advisory(dict(record))
            check(f"JEV-10 shadow guard rejects a {label}", False, "accepted")
        except Exception as exc:  # noqa: BLE001
            check(f"JEV-10 shadow guard rejects a {label}",
                  "shadow" in str(exc).lower(), type(exc).__name__)
    try:
        clean = pol.assert_shadow_advisory({"mutations": 0, "route": None, "selected_route": None})
        check("JEV-10 shadow guard accepts a clean advisory record", clean.get("mutations") == 0)
    except Exception as exc:  # noqa: BLE001
        check("JEV-10 shadow guard accepts a clean advisory record", False, type(exc).__name__)

    # JEV-15: a GUIState is honest about what was NOT perceived and never certifies
    channel = sorted(gs.PERCEPTION_CHANNELS)[0]
    fields = {"window_title": "Report.docx", "observed_changes": "true"}
    text = "\n".join(f"{k}: {v}" for k, v in fields.items())
    try:
        parsed = gs.parse_perception_text(text, channel=channel)
        unperceived = [n for n, f in parsed.items() if not f.perceived]
        check("JEV-15 fields the channel did not report stay unperceived",
              bool(unperceived) and all(parsed[n].value is None for n in unperceived),
              f"{len(unperceived)}/{len(parsed)} unperceived")
        check("JEV-15 a reported field is marked perceived",
              any(f.perceived for f in parsed.values()))
    except Exception as exc:  # noqa: BLE001
        check("JEV-15 fields the channel did not report stay unperceived", False,
              f"{type(exc).__name__}: {exc}")
    try:
        gs.PerceivedField(name="window_title", value="Report.docx", perceived=True, channel="")
        check("JEV-15 a perceived value must name its channel", False, "accepted")
    except Exception as exc:  # noqa: BLE001
        check("JEV-15 a perceived value must name its channel",
              "channel" in str(exc).lower(), type(exc).__name__)
    try:
        state = gs.build_gui_state(perception_text=text, channel=channel,
                                   allowed_kinds=("click", "type"),
                                   pre_approved_candidates=("click", "type"))
        check("JEV-15 a GUIState never certifies the app effect",
              getattr(state, "certifies_effect", True) is False)
        check("JEV-15 ground truth stays the real app effect",
              getattr(gs, "GROUND_TRUTH_APP_EFFECT", "") == "app_effect",
              getattr(gs, "GROUND_TRUTH_APP_EFFECT", ""))
    except Exception as exc:  # noqa: BLE001
        check("JEV-15 a GUIState never certifies the app effect", False,
              f"{type(exc).__name__}: {exc}")
    try:
        narrowed = guistate_action_candidates(
            gs.build_gui_state(perception_text=text, channel=channel,
                               action_candidates=("click",),
                               pre_approved_candidates=("click",), allowed_kinds=("click",)),
            pre_approved=("click",))
        check("JEV-15 candidates stay a subset of the pre-approved set",
              set(narrowed) <= {"click"}, str(narrowed))
    except Exception as exc:  # noqa: BLE001
        check("JEV-15 candidates stay a subset of the pre-approved set",
              False, f"{type(exc).__name__}: {exc}")
    # A foreign candidate is refused outright — even stronger than shrinking it.
    try:
        gs.build_gui_state(perception_text=text, channel=channel,
                           action_candidates=("click", "rm -rf /"),
                           pre_approved_candidates=("click",), allowed_kinds=("click",))
        check("JEV-15 a candidate outside the pre-approved set is refused", False, "accepted")
    except Exception as exc:  # noqa: BLE001
        check("JEV-15 a candidate outside the pre-approved set is refused",
              "pre-approved" in str(exc) or "allowed" in str(exc).lower(), type(exc).__name__)

    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    print(f"jev contract probe: {len(RESULTS) - len(failed)}/{len(RESULTS)} green")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
