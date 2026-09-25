"""``skaldir adversarial …`` — the release-blocking adversarial suite (VERIFY layer).

Three verbs over :mod:`skaldir.adversarial`:

======================  ==========================================================
``adversarial plan``    show the cases the planner picked — which surface, which
                        intent class, which operation and which oracle — and who
                        planned them (the Jev façade or the deterministic fallback)
``adversarial run``     run every case in parallel, each in its own browser context,
                        judge each one with its declared oracle and print the verdict
                        ``PASS``/``FAIL``/``UNKNOWN`` plus the defects behind it
``adversarial selftest`` prove the suite *bites*: the same cases must FAIL on a
                        target with known defect classes and PASS on the fixed one
======================  ==========================================================

Targets (at least one is required)::

    --ui-url URL          a running web UI to attack
    --canary broken|fixed start the built-in canary app (the suite's own proof target)
    --api-url URL         a running HTTP API whose served document declares its routes
    --with-api            start the real Skaldir API (its own process, throwaway runtime)

Exit codes — the product CLI's ``cli_contract`` vocabulary:::

    0   ok                     PASS: every case was verified against its oracle
    1   usage                  no target given, or --block-release with --allow-unknown
    2   verification failed    FAIL: at least one case failed its oracle (defects listed)
    3   blocked                UNKNOWN: a case could not be verified, so nothing is certified
    7   internal               an envelope problem or an unexpected crash

**UNKNOWN is never PASS.** A case whose oracle could not be evaluated (a target that could
not be reached, a surface without a document, an assigned oracle that is not deterministic) is
reported ``unknown`` and the verdict is at least ``UNKNOWN`` — the suite has no code path that
turns "could not tell" into "fine".

``--block-release`` makes the exit code the release decision: the default report mode may be
waived for an UNKNOWN suite with ``--allow-unknown`` (recorded as a warning), a release gate
may not — ``--block-release --allow-unknown`` is a usage error, and ``FAIL`` exits 2 either
way. The run's report is written to ``<runtime>/adversarial/<run_id>.json`` (``--state``).

The group is added by the product CLI through one call (the CLI owner wires it; this module
never edits ``cli.py``):::

    from .adversarial_cli import register_cli
    register_cli(sub)

:func:`install_contract_entries` runs inside :func:`register_cli`, so wiring the group cannot
forget the three ``COMMANDS`` entries (``adversarial.plan``, ``adversarial.run``,
``adversarial.selftest``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from . import adversarial as A
from .cli_shared import (
    blocked,
    dump,
    envelope_mode,
    product_contract,
    runtime_dir,
    share_common_flags,
    text_mode,
    verification_failed,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

#: the evidence kinds ``cli_contract.add_evidence`` accepts (anything else is stored as code)
_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")

_PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "planner": {"type": "string"},
        "advisor": {"type": "string"},
        "stop_reason": {"type": "string"},
        "case_count": {"type": "integer"},
        "offered_surfaces": {"type": "array"},
        "cases": {"type": "array"},
        "notes": {"type": "array"},
    },
    "required": ["planner", "case_count", "cases"],
}

_RUN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "suite_version": {"type": "string"},
        "run_id": {"type": "string"},
        "verdict": {"type": "string"},
        "blocks_release": {"type": "boolean"},
        "release_gate": {"type": "boolean"},
        "waived_unknown": {"type": "boolean"},
        "counts": {"type": "object"},
        "defects": {"type": "array"},
        "defect_classes": {"type": "array"},
        "cases": {"type": "array"},
        "record": {"type": "string"},
    },
    "required": ["verdict", "counts", "cases"],
}

_SELFTEST_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "broken_verdict": {"type": "string"},
        "fixed_verdict": {"type": "string"},
        "detected": {"type": "array"},
        "missed": {"type": "array"},
        "false_positives": {"type": "array"},
        "detail": {"type": "string"},
    },
    "required": ["ok", "detail"],
}


# --------------------------------------------------------------------------- #
# the envelope                                                                  #
# --------------------------------------------------------------------------- #


def _publish_result(
    payload: Any,
    *,
    refs: Iterable[tuple[str, str, str]] = (),
    warnings: Iterable[str] = (),
    next_actions: Iterable[tuple[str, str]] = (),
) -> None:
    """Hand the payload, its evidence and the hints to the shared envelope when wired.

    Standalone (no ``cli_contract``) the payload is printed as JSON in ``--json`` mode and the
    human renderer prints itself anyway, so this stays a no-op rather than an exception.
    """
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for ref, kind, note in refs:
            _CONTRACT.add_evidence(
                str(ref),
                str(kind) if str(kind) in _EVIDENCE_KINDS else "code",
                str(note),
            )
        for line in warnings:
            _CONTRACT.warn(str(line))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _human_report(report: A.SuiteReport, *, gate: dict) -> str:
    """The one-screen text view: verdict first, then the defects, then the cases."""
    lines = [
        f"adversarial suite {report.suite_version} — verdict {report.verdict}"
        f" ({report.counts['pass']} pass / {report.counts['fail']} fail / "
        f"{report.counts['unknown']} unknown)"
    ]
    for surface_id, surface in report.target.get("surfaces", {}).items():
        lines.append(f"  surface {surface_id} ({surface['kind']}): {surface['url']}")
    lines.append(
        f"  planner {report.plan.get('planner')} / advisor {report.plan.get('advisor')}"
        f" — {report.plan.get('case_count')} case(s), stop: {report.plan.get('stop_reason')}"
    )
    if report.defects:
        lines.append("  defects:")
        for defect in report.defects:
            lines.append(
                f"    {defect['defect_class']} ({defect['case_id']}): {defect['detail']}"
            )
    for result in report.results:
        lines.append(
            f"  {result.outcome.upper():7s} {result.case_id:24s} "
            f"[{result.observed_by}] {result.reason}"
        )
    lines.append(
        f"  release gate: {gate['release_gate']} · blocks_release {gate['blocks_release']}"
        f" · exit {gate['exit_code']}"
    )
    for note in report.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines) + "\n"


def _emit_standalone(payload: Any) -> None:
    """Print the payload as JSON unless the product CLI's envelope already carries it."""
    if envelope_mode():
        return
    dump(payload)


# --------------------------------------------------------------------------- #
# targets                                                                       #
# --------------------------------------------------------------------------- #


class _Targets:
    """The targets a call offers, and the ones this call itself started."""

    def __init__(self) -> None:
        self.ui_url = ""
        self.api_url = ""
        self._canary: A.CanaryApp | None = None
        self._api: A.ApiProcess | None = None
        self.started: list[str] = []

    @classmethod
    def resolve(cls, args: argparse.Namespace) -> _Targets:
        targets = cls()
        targets.ui_url = str(getattr(args, "ui_url", "") or "").strip()
        targets.api_url = str(getattr(args, "api_url", "") or "").strip()
        canary = str(getattr(args, "canary", "") or "").strip()
        if canary:
            if canary not in A.CANARY_MODES:
                raise ValueError(
                    f"unknown canary mode {canary!r}; use one of {','.join(A.CANARY_MODES)}"
                )
            targets._canary = A.start_canary(canary)
            targets.ui_url = targets._canary.url
            targets.started.append(f"canary({canary}) at {targets.ui_url}")
        if bool(getattr(args, "with_api", False)):
            targets._api = A.start_skaldir_api()
            targets.api_url = targets._api.url
            targets.started.append(
                f"skaldir api_server at {targets.api_url} (runtime {targets._api.runtime_dir})"
            )
        return targets

    def surfaces(self) -> tuple[A.TargetSurface, ...]:
        return A.offered_surfaces(ui_url=self.ui_url, api_url=self.api_url)

    def cleanup(self) -> None:
        if self._canary is not None:
            self._canary.stop()
        if self._api is not None:
            self._api.stop()

    def as_dict(self) -> dict:
        return {
            "ui_url": self.ui_url,
            "api_url": self.api_url,
            "started": list(self.started),
        }


def _targets_or_blocked(args: argparse.Namespace) -> tuple[_Targets | None, int]:
    """Resolve the targets, or answer the structured refusal when a declared driver is missing.

    A requested target that cannot be started is *blocked* (exit ``3``) with the extra and the
    install command — never an internal error, and never a run that quietly continues without
    the target the caller asked for.
    """
    try:
        return _Targets.resolve(args), EXIT_OK
    except A.DriverUnavailable as exc:
        refusal = exc.as_dict()
        sys.stderr.write(f"adversarial: {exc}\n")
        return None, blocked(
            str(exc),
            hint="install the declared extra and run again: "
            + " && ".join(refusal.get("install_commands") or []),
            reason="driver_missing",
            details=refusal,
        )


# --------------------------------------------------------------------------- #
# plan                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_plan(args: argparse.Namespace) -> int:
    targets, failure = _targets_or_blocked(args)
    if targets is None:
        return failure
    try:
        surfaces = targets.surfaces()
        if not surfaces:
            payload = {"planner": "", "case_count": 0, "cases": [], "offered_surfaces": []}
            _publish_result(
                payload,
                refs=((f"{A.SUITE_VERSION}:plan", "code", "no surface was offered"),),
                next_actions=(
                    (
                        "skaldir adversarial plan --canary broken",
                        "run the suite against its own proof target",
                    ),
                ),
            )
            _emit_standalone(payload)
            sys.stderr.write(
                "adversarial plan: no target was offered — pass --ui-url, --api-url, "
                "--canary or --with-api\n"
            )
            return blocked("no target was offered, so no case is reachable")
        plan = A.plan_suite(
            surfaces=surfaces,
            slots=int(getattr(args, "slots", 6)),
            per_case_timeout_s=int(getattr(args, "timeout", 45)),
            planner=str(getattr(args, "planner", "auto")),
            # MVP/87: the verb asks the *configured provider* by default, so `--planner jev`
            # means "Jev decides, through the provider this runtime names" instead of
            # "Skaldir's own declared-space reader answers while the façade is pinned on top".
            # `--offline-planner` restores the deterministic, network-free path and says so
            # in `answer_source`.
            live=not bool(getattr(args, "offline_planner", False)),
            runtime=runtime_dir(args),
        )
        payload = plan.as_dict()
        payload["target"] = targets.as_dict()
        _publish_result(
            payload,
            refs=(
                (
                    f"skaldir.jev:adversarial_test_planning ({plan.planner})",
                    "code",
                    f"{len(plan.cases)} case(s) over {len(plan.surfaces)} surface(s)",
                ),
            ),
            warnings=plan.notes,
        )
        if text_mode(args):
            sys.stdout.write(
                f"planner {plan.planner} / advisor {plan.advisor}: {len(plan.cases)} case(s), "
                f"stop {plan.stop_reason}\n"
            )
            sys.stdout.write(
                f"  answer source: {plan.answer_source}"
                + (f" ({plan.live_jev_reason})" if plan.live_jev_reason else "")
                + "\n"
            )
            for planned in plan.cases:
                sys.stdout.write(
                    f"  {planned.case.case_id:24s} {planned.case.surface_id}/"
                    f"{planned.case.intent_class:18s} {planned.operation:13s} "
                    f"{planned.case.oracle}\n"
                )
        else:
            _emit_standalone(payload)
        if not plan.cases:
            # MVP/87/SHIPPING-GAP: a plan with no case is not a success — it is a blocked
            # planning step, and the reason the façade gave travels with it (no advisory, no
            # provider, no declarable pair). Never a silent empty run.
            return blocked(
                f"the planner assigned no case: {plan.stop_reason}",
                hint="configure the provider this runtime names (skaldir jev status), or plan "
                     "the declared catalogue deterministically with --offline-planner",
                next_command="skaldir jev status --json",
                reason=plan.stop_reason or "no_cases",
                details={
                    "planner": plan.planner,
                    "answer_source": plan.answer_source,
                    "live_jev_reason": plan.live_jev_reason,
                    "decisions": [
                        {
                            "decision_id": item.get("decision_id", ""),
                            "status": item.get("status", ""),
                            "refusal_reason": item.get("refusal_reason", ""),
                        }
                        for item in plan.decisions[:3]
                    ],
                },
            )
        return EXIT_OK
    finally:
        targets.cleanup()


# --------------------------------------------------------------------------- #
# run                                                                           #
# --------------------------------------------------------------------------- #


def _cmd_run(args: argparse.Namespace) -> int:
    block_release = bool(getattr(args, "block_release", False))
    allow_unknown = bool(getattr(args, "allow_unknown", False))
    if block_release and allow_unknown:
        sys.stderr.write(
            "adversarial run: --block-release and --allow-unknown contradict each other — "
            "a release gate does not accept a waiver\n"
        )
        return EXIT_USAGE
    targets, failure = _targets_or_blocked(args)
    if targets is None:
        return failure
    try:
        surfaces = targets.surfaces()
        if not surfaces:
            _publish_result(
                {"verdict": "UNKNOWN", "counts": A.verdict_for(()), "cases": [], "defects": []},
                refs=((f"{A.SUITE_VERSION}:run", "code", "no surface was offered"),),
            )
            sys.stderr.write(
                "adversarial run: no target was offered — pass --ui-url, --api-url, "
                "--canary or --with-api\n"
            )
            return blocked("no target was offered, so nothing could be verified")
        slots = int(getattr(args, "slots", 6))
        plan = A.plan_suite(
            surfaces=surfaces,
            slots=slots,
            per_case_timeout_s=int(getattr(args, "timeout", 45)),
            planner=str(getattr(args, "planner", "auto")),
            # MVP/87: same switch as `adversarial plan` — the configured provider plans the
            # cases unless --offline-planner says to read the declared catalogue instead
            live=not bool(getattr(args, "offline_planner", False)),
            runtime=runtime_dir(args),
        )
        report = A.run_suite(plan, slots=slots)
        if not plan.cases:
            # MVP/87/SHIPPING-GAP: nothing was planned, so nothing was measured. That is a
            # blocked run with the planner's own reason — never an empty UNKNOWN that reads
            # like "the suite ran and found nothing".
            return blocked(
                f"the planner assigned no case, so no case ran: {plan.stop_reason}",
                hint="configure the provider this runtime names, or use --offline-planner to "
                     "plan the declared catalogue deterministically",
                next_command="skaldir adversarial plan --json",
                reason=plan.stop_reason or "no_cases",
                details={
                    "planner": plan.planner,
                    "answer_source": plan.answer_source,
                    "live_jev_reason": plan.live_jev_reason,
                    "target": targets.as_dict(),
                },
            )
        gate = A.release_gate_payload(
            report, block_release=block_release, allow_unknown=allow_unknown
        )
        state_dir = Path(str(getattr(args, "state", "")) or A.default_state_dir())
        record = A.record_run(report, state_dir, extra=gate)
        payload = report.as_dict()
        payload.update(gate)
        payload["record"] = str(record)
        payload["target"] = targets.as_dict()
        warnings: list[str] = list(report.notes)
        if gate["waived_unknown"]:
            warnings.append(
                "UNKNOWN was waived with --allow-unknown: the suite could not certify the "
                "release, it only reported that it could not"
            )
        refs = [
            (
                f"{A.SUITE_VERSION}:{len(report.results)} case(s) over "
                f"{len(plan.surfaces)} surface(s)",
                "run",
                f"verdict {report.verdict} from executed oracles only",
            ),
            (str(record), "file", "the run's report with the observation of every case"),
            (
                str(Path(record).with_name(Path(record).stem + A.DECISIONS_SUFFIX)),
                "file",
                f"the decision record: {len(payload.get('plan', {}).get('decisions') or [])} "
                f"call(s) by {plan.planner}"
                + (" (live Jev)" if plan.live_jev else " (offline façade)"),
            ),
            (
                f"planner:{plan.planner}",
                "verification",
                "the plan the cases were chosen by",
            ),
        ]
        for defect in report.defects:
            refs.append(
                (
                    f"defect:{defect['defect_class']}",
                    "verification",
                    f"{defect['case_id']}: {defect['detail']}"[:200],
                )
            )
        _publish_result(
            payload,
            refs=refs,
            warnings=warnings,
            next_actions=(
                (
                    "skaldir adversarial run --block-release",
                    "gate the release on this verdict in CI",
                ),
            )
            if not block_release
            else (),
        )
        if text_mode(args):
            sys.stdout.write(_human_report(report, gate=gate))
        else:
            _emit_standalone(payload)
        exit_code = int(gate["exit_code"])
        if exit_code == EXIT_VERIFICATION_FAILED:
            return verification_failed(
                f"{len(report.defects)} defect(s) in "
                f"{gate['defect_count']} finding(s): {', '.join(gate['defect_classes'])}",
                hint="fix the defect classes named in the payload and run again",
                details={"failed_cases": gate["failed_cases"]},
            )
        if exit_code == EXIT_BLOCKED:
            if report.blocked:
                # the run never started: the reason is the missing driver, and the fix is a
                # command, so both travel in the structured refusal instead of a case list
                refusal = A._refusal_line(report.blocked)
                sys.stderr.write(f"adversarial run: blocked — {refusal}\n")
                return blocked(
                    refusal,
                    hint="install the declared extra and run again: "
                    + " && ".join(report.blocked.get("install_commands") or []),
                    reason="driver_missing",
                    details=dict(report.blocked),
                )
            return blocked(
                f"the suite could not verify {len(gate['unknown_cases'])} case(s): "
                + ", ".join(gate["unknown_cases"]),
                hint="a case that cannot be verified is not a pass; make it observable",
                details={"unknown_cases": gate["unknown_cases"]},
            )
        return EXIT_OK
    finally:
        targets.cleanup()


# --------------------------------------------------------------------------- #
# selftest                                                                      #
# --------------------------------------------------------------------------- #


def _cmd_selftest(args: argparse.Namespace) -> int:
    slots = int(getattr(args, "slots", 6))
    timeout_s = int(getattr(args, "timeout", 45))
    check = A.selftest(slots=slots, timeout_s=timeout_s)
    payload = check.as_dict()
    payload["suite_version"] = A.SUITE_VERSION
    payload["catalogue"] = [case.case_id for case in A.ACTION_SPACE]
    if check.blocked:
        # nothing ran: the honest answer is the refusal, not a detector verdict
        _publish_result(
            payload,
            refs=((f"{A.SUITE_VERSION}:selftest", "code", "the canary script, unrun"),),
            warnings=(check.detail,),
        )
        _emit_standalone(payload)
        sys.stderr.write(f"adversarial selftest: blocked — {check.detail}\n")
        return blocked(
            check.detail,
            hint="install the declared extra and run again: "
            + " && ".join(check.blocked.get("install_commands") or []),
            reason="driver_missing",
            details=dict(check.blocked),
        )
    api_report: A.SuiteReport | None = None
    if bool(getattr(args, "with_api", False)):
        try:
            process = A.start_skaldir_api()
        except A.DriverUnavailable as exc:
            refusal = exc.as_dict()
            return blocked(
                str(exc),
                hint="install the declared extra and run again: "
                + " && ".join(refusal.get("install_commands") or []),
                reason="driver_missing",
                details=refusal,
            )
        try:
            api_plan = A.plan_suite(
                surfaces=A.offered_surfaces(api_url=process.url),
                slots=slots,
                per_case_timeout_s=timeout_s,
            )
            api_report = A.run_suite(api_plan, slots=slots)
        finally:
            process.stop()
        payload["api_verdict"] = api_report.verdict
        payload["api_url"] = process.url
        payload["api_cases"] = [
            {"case_id": result.case_id, "outcome": result.outcome, "reason": result.reason}
            for result in api_report.results
        ]
        if api_report.verdict != "PASS":
            check = A.SelfCheck(
                ok=False,
                broken_verdict=check.broken_verdict,
                fixed_verdict=check.fixed_verdict,
                detected=check.detected,
                missed=check.missed,
                false_positives=check.false_positives,
                detail=(
                    f"{check.detail}; the real API answered {api_report.verdict} "
                    f"({sorted({item['defect_class'] for item in api_report.defects})})"
                ),
            )
            payload.update(check.as_dict())
    refs = [
        (
            f"{A.SUITE_VERSION}:detectors {len(check.detected)}/{len(A.CANARY_EXPECTED)}",
            "test",
            "detectors that fired on the target with known defect classes",
        ),
        ("tests/test_adversarial_browser.py", "test", "the suite's own proof"),
    ]
    if api_report is not None:
        refs.append(
            (
                f"{A.SUITE_VERSION}:real-api {api_report.verdict}",
                "verification",
                f"{len(api_report.results)} API case(s) against a started api_server",
            )
        )
    _publish_result(payload, refs=refs, warnings=(check.detail,) if not check.ok else ())
    if text_mode(args):
        state = "ok" if check.ok else "broken"
        sys.stdout.write(f"adversarial selftest: {state} — {check.detail}\n")
        for case_id in check.missed:
            sys.stdout.write(f"  detector did not fire: {case_id}\n")
        for case_id in check.false_positives:
            sys.stdout.write(f"  case did not pass on the fixed target: {case_id}\n")
        _emit_standalone(payload)
    if check.ok:
        return EXIT_OK
    return verification_failed(
        f"the suite does not distinguish the targets: {check.detail}",
        hint="a detector that never fired is decoration — fix the case or the oracle",
        details={"missed": list(check.missed), "false_positives": list(check.false_positives)},
    )


# --------------------------------------------------------------------------- #
# the parser and the contract                                                   #
# --------------------------------------------------------------------------- #


def _add_target_flags(parser: argparse.ArgumentParser) -> None:
    """Every verb takes the same targets, so one plan can be re-run and one run re-planned."""
    parser.add_argument("--ui-url", default="", help="a running web UI to attack")
    parser.add_argument(
        "--canary",
        default="",
        choices=("",) + A.CANARY_MODES,
        help="start the built-in canary target ('broken' has known defects, 'fixed' has none)",
    )
    parser.add_argument("--api-url", default="", help="a running HTTP API to attack")
    parser.add_argument(
        "--with-api",
        action="store_true",
        help="start the real Skaldir API in its own process (throwaway runtime) and attack it",
    )
    parser.add_argument("--slots", type=int, default=6, help="parallel cases (isolated contexts)")
    parser.add_argument("--timeout", type=int, default=45, help="per-case timeout in seconds")
    parser.add_argument(
        "--runtime",
        default="",
        help="runtime whose settings/config name the planning provider and whose event store "
             "receives the decision rows (default: the installation's runtime)",
    )
    parser.add_argument(
        "--planner",
        default="auto",
        choices=("auto", "jev", "deterministic"),
        help="who plans the cases: the Jev façade, the fallback, or either",
    )
    parser.add_argument(
        "--offline-planner",
        dest="offline_planner",
        action="store_true",
        help="answer the planning questions from the declared catalogue instead of asking "
             "the configured provider (deterministic, no network; the plan says "
             "answer_source=declared_space)",
    )


def _add_adversarial_verbs(sub: Any) -> None:
    """The three leaves, created on a group's own ``add_subparsers`` handle."""
    plan = sub.add_parser("plan", help="show the planned cases and who planned them")
    _add_target_flags(plan)
    share_common_flags(plan)
    # the product CLI dispatches on ``func`` (measured: a group that sets ``handler`` is
    # silently treated as "no command" and the root envelope is printed instead)
    plan.set_defaults(func=_cmd_plan)

    run = sub.add_parser("run", help="run the cases and print the verdict")
    _add_target_flags(run)
    run.add_argument(
        "--block-release",
        action="store_true",
        help="the exit code is the release decision: 0 only for PASS, no UNKNOWN waiver",
    )
    run.add_argument(
        "--allow-unknown",
        action="store_true",
        help="report mode only: exit 0 for an UNKNOWN suite (never allowed with --block-release)",
    )
    run.add_argument(
        "--state",
        default="",
        help="where the run report is written (default: <runtime>/adversarial)",
    )
    share_common_flags(run)
    run.set_defaults(func=_cmd_run)

    selftest = sub.add_parser(
        "selftest", help="prove the suite bites: FAIL on the broken canary, PASS on the fixed one"
    )
    selftest.add_argument("--slots", type=int, default=6)
    selftest.add_argument("--timeout", type=int, default=45)
    selftest.add_argument(
        "--with-api", action="store_true", help="also run the API cases against a real api_server"
    )
    share_common_flags(selftest)
    selftest.set_defaults(func=_cmd_selftest)


def contract_entries() -> dict[str, Any]:
    """The three ``COMMANDS`` records this group installs (idempotently) when it is wired."""
    from . import cli_contract

    def ev(ref: str, kind: str = "code", note: str = "") -> dict:
        return {"ref": ref, "kind": kind, "note": note}

    return {
        "adversarial.plan": cli_contract.CommandSpec(
            "VERIFY",
            (
                ev("src/skaldir/adversarial.py", "code", "the declared action space + planner"),
                ev("src/skaldir/jev/decide.py", "code", "adversarial_test_planning"),
            ),
            "plan the adversarial cases for the offered surfaces (Jev or deterministic) "
            "without running them",
            exit_codes=(0, 1, 3, 7),
            data_schema=_PLAN_SCHEMA,
            notes="no case runs here; a plan without a target exits 3 instead of pretending "
            "there is nothing to test",
        ),
        "adversarial.run": cli_contract.CommandSpec(
            "VERIFY",
            (
                ev(
                    "src/skaldir/adversarial.py",
                    "code",
                    "cases, isolated contexts, oracles, verdict",
                ),
                ev("src/skaldir/adversarial.py", "code", "the run report record"),
            ),
            "run every planned case in parallel, judge each with its declared oracle and "
            "report PASS/FAIL/UNKNOWN with the defects",
            exit_codes=(0, 1, 2, 3, 7),
            mutates=True,
            data_schema=_RUN_SCHEMA,
            notes="UNKNOWN is never PASS (a case that cannot be verified is not certified); "
            "--block-release refuses the --allow-unknown waiver and exits 2 on FAIL, 3 on "
            "UNKNOWN",
        ),
        "adversarial.selftest": cli_contract.CommandSpec(
            "VERIFY",
            (
                ev("src/skaldir/adversarial.py", "code", "the canary target and the detectors"),
                ev("tests/test_adversarial_browser.py", "test", "the same proof in the suite"),
            ),
            "prove the suite bites: the cases must FAIL on the canary with known defects and "
            "PASS on the fixed one",
            exit_codes=(0, 3, 7, EXIT_VERIFICATION_FAILED),
            data_schema=_SELFTEST_SCHEMA,
            notes="a detector that never fired is decoration: this verb exits 2 when a case "
            "cannot tell a broken target from a fixed one, and 3 when the browser driver is "
            "not installed (nothing is attempted, so nothing is blamed on the detectors)",
        ),
    }


def register_contract_entries() -> list[str]:
    """Install this group's ``COMMANDS`` records (idempotent) and return the paths added."""
    from . import cli_contract

    added: list[str] = []
    for path, entry in contract_entries().items():
        if path not in cli_contract.COMMANDS:
            cli_contract.COMMANDS[path] = entry
            added.append(path)
    return added


def build_adversarial_parser(subparsers: Any) -> Any:
    """Add the ``adversarial`` group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "adversarial",
        help="adversarial release cases: isolated runs, deterministic verdicts",
        description=(
            "Many isolated cases in parallel, each in its own browser context, deliberately "
            "breaking a real user flow (empty input, hostile markup, oversized input, double "
            "submit, a slow response, a reload mid-action, an expired session, history replay). "
            "The plan comes from Jev's adversarial_test_planning decision; every case is judged "
            "by a declared deterministic oracle over the DOM and the target's own state, and a "
            "case that cannot be verified is UNKNOWN, never PASS. --block-release makes the "
            "verdict the release decision."
        ),
    )
    sub = parser.add_subparsers(dest="adversarial_command", required=True)
    _add_adversarial_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    parser = build_adversarial_parser(subparsers)
    register_contract_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(
        prog="skaldir adversarial",
        description="adversarial release cases: many isolated runs, one deterministic verdict",
    )
    sub = parser.add_subparsers(dest="adversarial_command", required=True)
    _add_adversarial_verbs(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m skaldir.adversarial_cli run --canary broken`` — returns the exit code.

    Standalone: the verb is required by the parser, so a missing ``func`` can only mean the
    parser changed; that is a usage problem, not a run.
    """
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    chosen = getattr(args, "func", None)
    if chosen is None:
        raise SystemExit(EXIT_USAGE)
    return int(chosen(args))


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_adversarial_parser",
    "build_parser",
    "contract_entries",
    "main",
    "register_cli",
    "register_contract_entries",
]


if __name__ == "__main__":  # pragma: no cover — convenience only
    raise SystemExit(main())
