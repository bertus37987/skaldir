#!/usr/bin/env python3
"""Independent re-derivation of the System-One (Jev) provider/Computer-Use claims.

This is NOT the authors' suite. It re-derives the statements from the outside so
that an implementation agreeing with a mis-read contract cannot pass: the
provider error taxonomy, the Treasury handle discipline (a secret must never
reach an event or a prompt), the canary ladder (no silent stage skip), the
Computer-Use question coverage, the applicability check and the blind-replay
refusal.

    .venv/bin/python tools/jev_acceptance_probe.py      # rc=0 all green

Read-only; it writes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from godsv import system_one_adapters as ad  # noqa: E402
from godsv import system_one_canary as cn  # noqa: E402
from godsv import system_one_subtools as st  # noqa: E402
from godsv import system_one_treasury as tr  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), str(detail)[:110]))


def discover(module, *needles: str) -> list[str]:
    return sorted(n for n in dir(module) if any(x in n.lower() for x in needles))


def main() -> int:
    # ---- §17 provider error taxonomy -----------------------------------------
    codes = getattr(ad, "PROVIDER_ERROR_CODES", None) or getattr(ad, "ERROR_CODES", None)
    check("§17 the seven error codes exist as a closed set",
          codes is not None and len(codes) == 7,
          f"{sorted(codes) if codes else discover(ad, 'error', 'code')}")
    mapper = None
    for name in ("error_code_for_status", "status_to_error_code", "classify_status"):
        if hasattr(ad, name):
            mapper = getattr(ad, name)
            break
    if mapper is None:
        check("§17 a status maps onto the code set", False,
              f"no mapper found; candidates: {discover(ad, 'status', 'error')}")
    else:
        for status, expected in ((401, "connection_invalid"), (422, "request_contract_error"),
                                 (429, "provider_rate_limited"), (529, "provider_overloaded")):
            got = str(mapper(status))
            check(f"§17 HTTP {status} -> {expected}", expected in got, got)

    # ---- §16 Treasury: handle discipline, no secret in any payload -----------
    fake = "ts_super_secret_value_0123456789"
    try:
        ref = tr.ConnectionRef(provider="typesafe", target="system_one", scope="evaluate",
                               secret_handle="secret://typesafe/systemone", status="active",
                               revision=3)
        check("§16 a valid ConnectionRef is accepted", ref.active is True,
            ref.as_dict().get("status"))
    except Exception as exc:  # noqa: BLE001
        check("§16 a valid ConnectionRef is accepted", False, f"{type(exc).__name__}: {exc}")
    for bad, label in (
        (dict(provider="openai", target="system_one", scope="evaluate",
              secret_handle="secret://typesafe/systemone", status="active", revision=1),
         "a foreign provider"),
        (dict(provider="typesafe", target="system_one", scope="evaluate",
              secret_handle="PLAINTEXT-KEY", status="active", revision=1),
         "a plaintext key instead of a handle"),
        (dict(provider="typesafe", target="system_one", scope="evaluate",
              secret_handle="secret://typesafe/systemone", status="active", revision=0),
         "an active connection without a revision"),
    ):
        try:
            tr.ConnectionRef(**bad)
            check(f"§16 {label} is refused", False, "accepted")
        except Exception as exc:  # noqa: BLE001
            check(f"§16 {label} is refused",
                "connection" in str(exc).lower() or "handle" in str(exc).lower()
                  or "revision" in str(exc).lower(), type(exc).__name__)

    try:
        tr.assert_no_secret({"outer": {"inner": f"Bearer {fake}"}}, fake, where="probe")
        check("§16 a payload carrying the secret is refused", False, "accepted")
    except Exception as exc:  # noqa: BLE001
        check("§16 a payload carrying the secret is refused", True, type(exc).__name__)
    try:
        tr.assert_no_secret({"handle": "secret://typesafe/systemone", "note": "ok"}, fake,
            where="probe")
        check("§16 a clean payload passes", True)
    except Exception as exc:  # noqa: BLE001
        check("§16 a clean payload passes", False, f"{type(exc).__name__}: {exc}")

    collected: list[tuple[str, dict]] = []
    emitter = tr.GuardedEmitter(recorder=lambda kind, payload: collected.append((kind, payload)),
                                secret=fake)
    emitter("system_one.completed", {"ok": True})
    # A leaky payload must never be recorded. The emitter is fail-CLOSED: it counts
    # the refusal AND raises, so a call site cannot swallow a leak attempt by
    # ignoring a return value. Both properties are asserted.
    raised = ""
    try:
        emitter("system_one.failed", {"detail": fake})
    except Exception as exc:  # noqa: BLE001
        raised = type(exc).__name__
    check("§16 the guarded emitter records clean events only",
          len(collected) == 1 and collected[0][0] == "system_one.completed",
          f"recorded={[c[0] for c in collected]}")
    check("§16 a leaky event is refused, counted and raised (fail-closed)",
          raised == "TreasuryLeakError" and getattr(emitter, "refused", 0) == 1,
          f"raised={raised or 'nothing'} refused={getattr(emitter, 'refused', '?')}")

    # ---- §21 canary ladder ---------------------------------------------------
    stages = discover(cn, "stage_")
    check("§21 the six canary stages exist",
          all(hasattr(cn, f"STAGE_{n}") for n in
              ("DISABLED", "REPLAY", "SHADOW", "ADVISORY", "LOW_RISK_ACTIVE", "CALIBRATED_ACTIVE")),
          str(stages)[:90])
    check("§21 hard policy stays outside the ladder",
          getattr(cn, "HARD_POLICY_OUTSIDE", False) is True)
    trans = getattr(cn, "stage_transition", None)
    if trans is None:
        check("§21 a stage transition is decided in code", False, str(discover(cn, "transition")))
    else:
        try:
            skip = trans("J0", "J3", reason="")
            allowed = getattr(skip, "allowed", None)
            check("§21 skipping stages without a reason is refused", allowed is False,
                  f"allowed={allowed} reason={getattr(skip, 'reason', '')[:60]}")
        except Exception as exc:  # noqa: BLE001
            check("§21 skipping stages without a reason is refused", False,
                f"{type(exc).__name__}: {exc}")
        try:
            short = trans("J0", "J3", reason="we want to")
            check("§21 a placeholder-short reason is refused too",
                  getattr(short, "allowed", None) is False, getattr(short, "reason", "")[:70])
        except Exception as exc:  # noqa: BLE001
            check("§21 a placeholder-short reason is refused too", False, type(exc).__name__)

    # ---- §10 computer-use coverage + §11 subtool guards ----------------------
    pack = st.computer_use_pack()
    ids = {q.id for q in pack.questions}
    wanted = {
        "correct_application", "correct_window", "correct_document", "blocking_dialog_present",
        "state_sufficient_for_action", "stored_subtool_anchor_valid",
        "previous_action_succeeded", "expected_transition_occurred",
            "unexpected_transition_occurred",
        "wrong_target_changed", "reobserve_required",
        "retry_safe", "alternate_candidate_needed", "state_recoverable", "system2_needed",
        "user_takeover_detected", "candidate_action",
    }
    missing = sorted(w for w in wanted if not any(w in qid for qid in ids))
    check("§10 the Computer-Use pack covers pre/post/recovery + candidate choice",
          not missing, f"missing={missing} (have {len(ids)})")

    try:
        st.validate_candidate_choice("rm -rf /", allowed_candidates=("candidate_a", "reobserve"))
        check("§10 a candidate outside the allowed set is refused", False, "accepted")
    except Exception as exc:  # noqa: BLE001
        check("§10 a candidate outside the allowed set is refused", True, type(exc).__name__)

    subtool = None
    for name in ("VisualSubtool", "SubtoolSpec", "Subtool"):
        if hasattr(st, name):
            cls = getattr(st, name)
            subtool = cls
            break
    check("§11 a subtool contract type exists", subtool is not None,
          str(discover(st, "subtool", "spec"))[:80])

    print("discovered in subtools:", [n for n in dir(st) if not n.startswith("_")][:24])

    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    print(f"jev acceptance probe: {len(RESULTS) - len(failed)}/{len(RESULTS)} green")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
