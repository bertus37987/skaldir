"""``skaldir verify …`` — plan, run, inspect and re-read real-world verification.

Six verbs over :mod:`skaldir.verify_service` (the VERIFY layer's service half) and
:mod:`skaldir.verify_flows` (the user-flow half):

======================  ==========================================================
``verify plan``         derive, for the goal's criteria, the real check that would
                        certify each one — and say plainly which ones cannot be
                        certified here (read-only; exit 3 when a criterion has no
                        certifiable check, so a plan never looks stronger than it is)
``verify run``          execute those checks against the real workspace (the project
                        suite, a declared build/command/end-to-end flow, a declared
                        artifact inspected for real), record the evidence with
                        timestamps and hashes, and print ``PASS``/``FAIL``/``UNKNOWN``
``verify status``       the recorded runs, the latest verdict and whether it is still
                        fresh for the workspace it was produced in
``verify evidence``     the recorded evidence records, filterable by run and criterion
``verify flow``         drive a *declared user flow* (start-state anchors → actions →
                        end-state anchors) against a real browser (Playwright) or a
                        real **isolated** desktop (the Computer Use container runner)
                        and keep every screenshot as the evidence. ``--flow ID``
                        selects, ``--list`` shows the declaration, ``--template``
                        prints the shape to copy
``verify flows``        the recorded flow runs and the evidence each one produced
                        (screenshots with their sha256, assertions, timings)
======================  ==========================================================

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok                     verify plan: every criterion is certifiable here
                               verify run / flow: PASS (everything declared verified)
    1   usage                  nothing to verify / argparse refused the call
    2   verification failed    verify run: a check really failed
                               verify flow: a flow ran and an anchor did not hold
    3   blocked                verify run/plan/flow: UNKNOWN — nothing may be certified
    5   not found              the requested --run-id / --flow is not in the store
    7   internal               an envelope problem or an unexpected crash

``UNKNOWN`` is never reported as ``PASS``: the verdict comes from
:func:`skaldir.verify_service.verdict_for` (checks) or
:func:`skaldir.verify_flows.verdict_for` (flows) over executed results only, and a check or
a flow whose execution could not happen is ``unknown``.

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``)::

    from .verify_cli import register_cli
    register_cli(sub)

``register_cli`` also installs the group's ``cli_contract.COMMANDS`` entries, so
``contract dump --json`` keeps ``unregistered: []``.

``verify run`` and ``verify flow`` write (the run record + the evidence ledger under
``--state``); the other four verbs are read-only. A run whose workspace has changed
since is reported as *stale* by ``verify status`` instead of being quoted as proof.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import verify_flows as VF
from . import verify_service as V
from .cli_shared import (
    blocked,
    dump,
    envelope_mode,
    install_contract_entries,
    not_found,
    product_contract,
    runtime_dir,
    session_id,
    share_common_flags,
    text_mode,
    verification_failed,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

#: the evidence kind per recorded result (the contract's own vocabulary)
_RUN_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")


# --------------------------------------------------------------------------- #
# envelope + shared plumbing                                                    #
# --------------------------------------------------------------------------- #


def _report(
    payload: Any,
    *,
    evidence: Iterable[dict] = (),
    warnings: Iterable[str] = (),
    next_actions: Iterable[tuple[str, str]] = (),
) -> None:
    """Hand the payload and its evidence to the shared envelope (when wired).

    Required when the group runs inside ``skaldir``: the envelope refuses to report
    success it cannot back (``build_envelope`` turns a success without evidence into
    ``EXIT_INTERNAL``). Harmless when the group runs standalone.
    """
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            kind = str(item.get("kind", "code"))
            _CONTRACT.add_evidence(
                str(item.get("ref", "")),
                kind if kind in _RUN_EVIDENCE_KINDS else "code",
                str(item.get("note", "")),
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


# --------------------------------------------------------------------------- #
# arguments → inputs                                                            #
# --------------------------------------------------------------------------- #


def _state_dir(args: argparse.Namespace) -> Path:
    """Where runs and the evidence ledger live: ``--state`` or ``<runtime>/verify``."""
    explicit = str(getattr(args, "state", "") or "")
    if explicit:
        return Path(explicit).expanduser()
    return runtime_dir(args) / "verify"


def _workspace(args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "workspace", "") or "")
    return Path(explicit).expanduser() if explicit else Path.cwd()


def _criteria(args: argparse.Namespace) -> tuple[str, list[Any], str]:
    """The criteria to plan: inline first, then ``--from-contract``, then the plan file.

    Returns ``(goal, criteria, source)``; an empty list with source ``"none"`` means
    nothing was supplied anywhere — the caller reports that instead of inventing work.
    """
    goal = str(getattr(args, "goal", "") or "")
    texts = [str(item) for item in (getattr(args, "criterion", None) or []) if str(item).strip()]
    if texts:
        return goal, texts, "argument"
    runtime = runtime_dir(args)
    if bool(getattr(args, "from_contract", False)):
        contract_goal, dod = V.contract_criteria(runtime, session_id(args))
        return contract_goal or goal, dod, "contract"
    data, _reason = V.load_plan_file(_workspace(args), str(getattr(args, "plan", "") or ""))
    if data.get("criteria"):
        return goal, [], "plan-file"
    contract_goal, dod = V.contract_criteria(runtime, session_id(args))
    if dod:
        return contract_goal or goal, dod, "contract"
    return goal, [], "none"


def _build_plan(args: argparse.Namespace) -> tuple[V.Plan, str]:
    goal, criteria, source = _criteria(args)
    plan = V.build_plan(
        criteria,
        goal=goal,
        workspace=_workspace(args),
        plan_file=str(getattr(args, "plan", "") or ""),
        revision=int(getattr(args, "revision", 0) or 0),
    )
    return plan, source


def _empty_reason(args: argparse.Namespace, source: str) -> tuple[str, str]:
    """``(message, hint)`` for a plan with no criterion at all."""
    if source == "plan-file":
        return (
            "the workspace plan file carries no criteria",
            f"add criteria to {_workspace(args) / V.PLAN_FILENAME} or pass --criterion",
        )
    if source == "contract":
        return (
            "the completion contract records no criteria",
            "record a goal with `skaldir completion begin --goal … --criterion …`",
        )
    return (
        "no criteria to verify",
        "pass --criterion TEXT (repeatable), declare .godsv/verify.json in the workspace, "
        "or use --from-contract",
    )


def _result_evidence(result: dict) -> list[dict]:
    """One evidence item per really executed check (files for inspected artifacts)."""
    items: list[dict] = []
    artifacts = result.get("artifacts") or []
    if artifacts:
        for artifact in artifacts:
            digest = artifact.get("sha256") or ""
            if artifact.get("exists"):
                size = artifact.get("size_bytes")
                facts = f", {size} B" + (f", sha256 {digest}" if digest else "")
                state = "present"
            else:
                state, facts = "missing", ""
            items.append({
                "ref": str(artifact.get("path") or ""),
                "kind": "file",
                "note": f"inspected for '{result.get('criterion')}': {state}{facts}",
            })
        return items
    items.append({
        "ref": str(result.get("cmd") or result.get("check_id") or ""),
        "kind": "run" if result.get("exit_code") is not None else "verification",
        "note": (
            f"{result.get('check_id')} → {result.get('status')} "
            f"(exit {result.get('exit_code')}): {str(result.get('reason'))[:150]}"
        ),
    })
    return items


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_plan(args: argparse.Namespace) -> int:
    """What would certify the goal — and what could not (read-only)."""
    plan, source = _build_plan(args)
    plan_dict = plan.as_dict()
    state = _state_dir(args)
    payload = {
        "plan": plan_dict,
        "source": source,
        "workspace": str(_workspace(args)),
        "workspace_exists": plan.workspace_exists,
        "state_dir": str(state),
        "criteria_count": len(plan.criteria),
        "certifiable": plan.certifiable,
        "verdict": V.VERDICT_UNKNOWN,
        "verdict_reason": "",
        "pass_requirement": V.PASS_REQUIREMENT,
    }
    evidence: list[dict] = [
        {"ref": "src/skaldir/completion.py:334", "kind": "code",
         "note": "plan_verification — the canonical check a criterion text maps to"},
        {"ref": "src/skaldir/verify_service.py", "kind": "code",
         "note": "build_plan — declaration, contract or derivation per criterion"},
    ]
    if plan.plan_file_reason:
        evidence.append({"ref": plan.plan_file, "kind": "file", "note": plan.plan_file_reason})
    if plan.digest.get("digest"):
        evidence.append({"ref": str(plan.digest.get("path")), "kind": "file",
                         "note": f"workspace digest {str(plan.digest['digest'])[:16]} "
                                 f"@ {plan.digest.get('head', '')[:12]}"})
    warnings = list(plan.warnings)
    for criterion in plan.uncertifiable:
        warnings.append(
            f"cannot be certified here: '{criterion.text}' — {criterion.reason}"
        )

    if not plan.criteria:
        message, hint = _empty_reason(args, source)
        payload["verdict_reason"] = message
        _report(payload, evidence=evidence, warnings=tuple(warnings))
        _emit(args, payload, warn=message)
        return blocked(message, hint=hint, next_command="skaldir verify plan --json",
                        reason=message)

    if not plan.workspace_exists:
        message = f"the workspace does not exist: {plan.workspace}"
        payload["verdict_reason"] = message
        _report(payload, evidence=evidence, warnings=tuple(warnings),
                next_actions=(("skaldir verify plan --workspace <dir> --json",
                               "point the plan at the real workspace"),))
        _emit(args, payload, warn=message)
        return blocked(message, hint="pass --workspace DIR that exists (the real workspace)",
                        reason=message)

    if not plan.certifiable:
        message = (
            f"{len(plan.uncertifiable)} of {len(plan.criteria)} criterion/criteria have no "
            f"certifiable check here: "
            + "; ".join(f"'{item.text}' ({item.reason})" for item in plan.uncertifiable[:3])
        )
        payload["verdict_reason"] = message
        _report(payload, evidence=evidence, warnings=tuple(warnings),
                next_actions=(("skaldir verify plan --json",
                               "re-plan after declaring the missing checks"),))
        _emit(args, payload, warn=message)
        return blocked(
            message,
            hint="declare the check (SKALDIR_CHECK_*_CMD or .godsv/checks.json) or point the "
                 "criterion at a path/argv it can really check",
            next_command="skaldir verify plan --json",
            reason="a criterion has no certifiable check",
            details={"uncertifiable": [item.text for item in plan.uncertifiable]},
        )

    _report(payload, evidence=evidence, warnings=tuple(warnings))
    _emit(args, payload)
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Execute every planned check against the real workspace and report the verdict."""
    plan, source = _build_plan(args)
    state = _state_dir(args)
    if not plan.criteria:
        message, hint = _empty_reason(args, source)
        payload = {"plan": plan.as_dict(), "source": source, "verdict": V.VERDICT_UNKNOWN,
                   "verdict_reason": message, "state_dir": str(state), "results": [],
                   "counts": {"criteria": 0, "passed": 0, "failed": 0, "unknown": 0},
                   "workspace": str(_workspace(args))}
        _report(payload, evidence=[{"ref": str(_workspace(args)), "kind": "file",
                                    "note": "no criteria to verify against this workspace"}],
                warnings=(message,))
        _emit(args, payload, warn=message)
        return blocked(message, hint=hint, reason=message,
                        next_command="skaldir verify plan --json")

    run = V.run_plan(
        plan,
        timeout=float(getattr(args, "timeout", V.DEFAULT_TIMEOUT) or V.DEFAULT_TIMEOUT),
        session=session_id(args),
        state_dir=state,
        record=not bool(getattr(args, "dry_run", False)),
    )
    payload = {
        "run_id": run["run_id"],
        "verdict": run["verdict"],
        "verdict_reason": run["verdict_reason"],
        "goal": run["goal"],
        "workspace": run["workspace"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "duration_ms": run["duration_ms"],
        "counts": run["counts"],
        "results": run["results"],
        "plan_sha256": run["plan_sha256"],
        "workspace_digest": run["workspace_digest"],
        "pass_requirement": run["pass_requirement"],
        "state_dir": run["state_dir"] or str(state),
        "recorded": not bool(getattr(args, "dry_run", False)),
    }

    evidence: list[dict] = []
    for result in run["results"]:
        evidence.extend(_result_evidence(result))
    evidence.append({"ref": "src/skaldir/verify_service.py", "kind": "code",
                     "note": "run_plan — every result comes from an executed check"})
    record_path = ""
    if payload["recorded"]:
        record_path = str(V.VerifyStore(state).runs_dir / f"{run['run_id']}.json")
        evidence.append({"ref": record_path, "kind": "file",
                         "note": f"recorded run {run['run_id']} (plan_sha256 "
                                 f"{run['plan_sha256'][:16]})"})

    warnings = list(plan.warnings)
    for criterion in plan.uncertifiable:
        warnings.append(f"cannot be certified here: '{criterion.text}' — {criterion.reason}")
    for result in run["results"]:
        if result["status"] == V.STATUS_UNKNOWN:
            warnings.append(f"UNKNOWN: '{result['criterion']}' — {result['reason'][:200]}")
    next_actions = (
        (f"skaldir verify evidence --run-id {run['run_id']} --json",
         "the recorded evidence of this run"),
        ("skaldir verify status --json", "the latest verdict and its freshness"),
    )

    _report(payload, evidence=tuple(evidence), warnings=tuple(warnings),
            next_actions=next_actions)
    _emit(args, payload)

    if run["verdict"] == V.VERDICT_PASS:
        return EXIT_OK
    if run["verdict"] == V.VERDICT_FAIL:
        return verification_failed(
            run["verdict_reason"],
            hint="fix what failed and run `skaldir verify run --json` again",
            details={"failed": [item["criterion"] for item in run["results"]
                                if item["status"] == V.STATUS_FAILED]},
        )
    return blocked(
        run["verdict_reason"],
        hint="a criterion whose check cannot run here is UNKNOWN — declare the check "
             "(SKALDIR_CHECK_*_CMD or .godsv/checks.json) and re-run",
        next_command="skaldir verify plan --json",
        reason="UNKNOWN is never a PASS",
        details={"unknown": [item["criterion"] for item in run["results"]
                             if item["status"] == V.STATUS_UNKNOWN]},
    )


def cmd_status(args: argparse.Namespace) -> int:
    """The recorded runs, the latest verdict and whether it is still fresh."""
    store = V.VerifyStore(_state_dir(args))
    limit = int(getattr(args, "limit", 20) or 20)
    run_id = str(getattr(args, "run_id", "") or "")
    record = store.get(run_id) if run_id else store.latest()
    if run_id and record is None:
        message = f"no recorded verification run '{run_id}' in {store.dir}"
        payload = {"run_id": run_id, "found": False, "state_dir": str(store.dir),
                   "runs": store.runs(limit=limit), "latest": None, "results": []}
        _report(payload, evidence=[{"ref": str(store.dir), "kind": "file",
                                    "note": "the state directory that was searched"}],
                warnings=(message,))
        _emit(args, payload, warn=message)
        return not_found(message, hint="`skaldir verify status --json` lists the recorded runs")

    runs = store.runs(limit=limit)
    latest = V.VerifyStore.summary(record) if record else None
    freshness = V.run_freshness(record or {}) if record else {}
    payload = {
        "state_dir": str(store.dir),
        "run_id": (record or {}).get("run_id"),
        "found": bool(record),
        "runs": runs,
        "runs_count": len(runs),
        "latest": latest,
        "verdict": (record or {}).get("verdict"),
        "verdict_reason": (record or {}).get("verdict_reason"),
        "freshness": freshness,
        "citable": bool(freshness.get("citable")),
        "results": (record or {}).get("results") or [],
        "pass_requirement": V.PASS_REQUIREMENT,
        "evidence_ledger": str(store.evidence_path),
    }
    evidence: list[dict] = []
    if record:
        evidence.append({
            "ref": str(store.runs_dir / f"{record.get('run_id')}.json"),
            "kind": "file",
            "note": f"verdict {record.get('verdict')} recorded "
                    f"{(record.get('counts') or {}).get('criteria')} criterion/criteria",
        })
        if freshness:
            evidence.append({"ref": str(record.get("workspace")), "kind": "file",
                             "note": freshness.get("reason") or "workspace digest unchanged"})
    else:
        evidence.append({"ref": str(store.dir), "kind": "file",
                         "note": "no verification run recorded yet"})
    warnings: list[str] = []
    if not record:
        warnings.append(
            "no verification run recorded yet — nothing has been verified against a real "
            "workspace"
        )
    elif freshness and not freshness.get("citable"):
        warnings.append(
            f"the recorded verdict is not citable: {freshness.get('reason')}"
        )
    _report(payload, evidence=tuple(evidence), warnings=tuple(warnings),
            next_actions=(("skaldir verify plan --json",
                           "what would certify the current goal"),))
    _emit(args, payload)
    return EXIT_OK


def cmd_evidence(args: argparse.Namespace) -> int:
    """The recorded criterion evidence — with the hashes and timestamps it was stored with."""
    store = V.VerifyStore(_state_dir(args))
    run_id = str(getattr(args, "run_id", "") or "")
    limit = int(getattr(args, "limit", 50) or 50)
    if run_id and store.get(run_id) is None:
        message = f"no recorded verification run '{run_id}' in {store.dir}"
        payload = {"run_id": run_id, "found": False, "evidence": [], "count": 0,
                   "state_dir": str(store.dir), "ledger": str(store.evidence_path)}
        _report(payload, evidence=[{"ref": str(store.dir), "kind": "file",
                                    "note": "the state directory that was searched"}],
                warnings=(message,))
        _emit(args, payload, warn=message)
        return not_found(message,
                          hint="`skaldir verify status --json` lists the recorded runs")

    rows = store.evidence(run_id=run_id, criterion=str(getattr(args, "criterion", "") or ""),
                          limit=limit)
    payload = {
        "evidence": rows,
        "count": len(rows),
        "run_id": run_id,
        "criterion": str(getattr(args, "criterion", "") or ""),
        "state_dir": str(store.dir),
        "ledger": str(store.evidence_path),
        "ledger_exists": store.evidence_path.is_file(),
    }
    if rows:
        evidence = [
            {"ref": str(store.evidence_path), "kind": "file",
             "note": f"{len(rows)} recorded criterion record(s) read back"},
            {"ref": str(rows[-1].get("run_id") or ""), "kind": "verification",
             "note": f"newest record: {rows[-1].get('status')} — "
                     f"{str(rows[-1].get('criterion'))[:120]}"},
        ]
    else:
        evidence = [{"ref": str(store.dir), "kind": "file",
                     "note": "no evidence recorded for this filter yet"}]
    warnings: list[str] = []
    if not rows:
        warnings.append("no recorded evidence matches this filter")
    _report(payload, evidence=tuple(evidence), warnings=tuple(warnings),
            next_actions=(("skaldir verify run --json",
                           "produce evidence by running the checks for real"),))
    _emit(args, payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# real user flows — a declared start state, actions and an end-state check      #
# --------------------------------------------------------------------------- #


def _flows_file(args: argparse.Namespace) -> str:
    return str(getattr(args, "flows", "") or "")


def _screenshot_dir(args: argparse.Namespace, state: Path) -> Path:
    explicit = str(getattr(args, "screenshots", "") or "")
    if explicit:
        return Path(explicit).expanduser()
    return state / "screenshots"


def _selected_flows(declaration: VF.FlowDeclaration, wanted: list[str]) -> tuple[list, list[str]]:
    """``(flows, unknown ids)`` — a ``--flow`` id that is not declared is named, not ignored."""
    if not wanted:
        return list(declaration.flows), []
    by_id = {flow.flow_id: flow for flow in declaration.flows}
    chosen, missing = [], []
    for item in wanted:
        flow = by_id.get(str(item))
        if flow is None:
            missing.append(str(item))
        else:
            chosen.append(flow)
    return chosen, missing


def cmd_flow(args: argparse.Namespace) -> int:
    """Run the declared user flows against the real targets and print PASS/FAIL/UNKNOWN."""
    state = _state_dir(args)
    workspace = _workspace(args)
    if bool(getattr(args, "template", False)):
        payload = {
            "template": VF.template_flows(str(getattr(args, "base_url", "") or "")),
            "flows_filename": str(VF.FLOWS_FILENAME),
            "declared_actions": list(VF.STEP_ACTIONS),
            "anchor_checks": list(VF.ANCHOR_CHECKS),
            "pass_requirement": VF.PASS_REQUIREMENT,
        }
        _report(payload, evidence=[{"ref": "src/skaldir/verify_flows.py", "kind": "code",
                                    "note": "the declaration shape every flow must follow"}])
        _emit(args, payload)
        return EXIT_OK

    declaration = VF.load_flows(workspace, _flows_file(args))
    if bool(getattr(args, "list_only", False)):
        payload = {
            "declaration": declaration.as_dict(),
            "path": declaration.path,
            "source": declaration.source,
            "count": len(declaration.flows),
            "state_dir": str(state),
            "pass_requirement": VF.PASS_REQUIREMENT,
        }
        evidence = [{"ref": declaration.path, "kind": "file",
                     "note": f"{len(declaration.flows)} declared flow(s) read back"}]
        if not declaration.flows:
            _report(payload, evidence=evidence, warnings=declaration.warnings)
            _emit(args, payload, warn="no flow is declared")
            return blocked("no flow declaration was found or it carries no flow",
                           hint=f"write {workspace / VF.FLOWS_FILENAME} "
                                "(see `skaldir verify flow --template`)",
                           reason="nothing to verify")
        _report(payload, evidence=evidence, warnings=declaration.warnings)
        _emit(args, payload)
        return EXIT_OK

    selected, missing = _selected_flows(declaration, list(getattr(args, "flow", None) or []))
    if not selected:
        message = (
            f"no declared flow matches --flow {', '.join(missing)}"
            if missing else "no flow is declared to run"
        )
        payload = {"declaration": declaration.as_dict(), "path": declaration.path,
                   "count": 0, "missing": missing, "verdict": V.VERDICT_UNKNOWN,
                   "verdict_reason": message, "state_dir": str(state),
                   "pass_requirement": VF.PASS_REQUIREMENT}
        _report(payload, evidence=[{"ref": declaration.path, "kind": "file",
                                    "note": "the declaration that was searched"}],
                warnings=tuple(declaration.warnings) + ((message,) if message else ()))
        _emit(args, payload, warn=message)
        if missing:
            return not_found(message,
                             hint="`skaldir verify flow --list --json` names the declared flows")
        return blocked(message, hint=f"write {workspace / VF.FLOWS_FILENAME}",
                       reason="nothing to verify")

    base_url = str(getattr(args, "base_url", "") or "")
    if base_url:
        selected = list(VF.apply_base_url(selected, base_url))
    shot_dir = _screenshot_dir(args, state)
    report = VF.run_flows(
        selected,
        screenshot_dir=shot_dir,
        state_dir=state,
        declaration=declaration,
        headless=not bool(getattr(args, "headed", False)),
        desktop_timeout_s=float(getattr(args, "desktop_timeout", VF.DEFAULT_DESKTOP_TIMEOUT_S)
                                or VF.DEFAULT_DESKTOP_TIMEOUT_S),
    )
    payload = report.as_dict()
    evidence: list[dict] = []
    for result in report.results:
        for shot in result.screenshots:
            evidence.append({"ref": shot.path, "kind": "file",
                             "note": f"{result.flow_id}: {shot.name} "
                                     f"({shot.size_bytes} B, sha256 {shot.sha256[:16]})"})
        evidence.append({
            "ref": result.target or result.flow_id,
            "kind": "verification" if result.target_kind != "browser" else "run",
            "note": f"{result.flow_id} → {result.status} in {result.duration_ms} ms: "
                    f"{result.reason[:150]}",
        })
    if report.state_dir:
        record = VF.FlowStore(state).runs_dir / f"{report.run_id}.json"
        evidence.append({"ref": str(record), "kind": "file",
                         "note": f"recorded flow run {report.run_id} "
                                 f"(evidence_sha256 {report.as_dict()['evidence_sha256'][:16]})"})
    warnings = list(report.warnings)
    for result in report.results:
        if result.status != V.STATUS_PASSED:
            warnings.append(f"{result.status.upper()}: {result.flow_id} — {result.reason}")
    next_actions = (
        (f"skaldir verify flows --run-id {report.run_id} --json",
         "re-read the recorded result and its evidence"),
    )
    _report(payload, evidence=tuple(evidence), warnings=tuple(warnings),
            next_actions=next_actions)
    _emit(args, payload)

    if report.verdict == V.VERDICT_PASS:
        return EXIT_OK
    if report.verdict == V.VERDICT_FAIL:
        return verification_failed(
            report.verdict_reason,
            hint="fix what the flow showed and run `skaldir verify flow` again — the "
                 "screenshots of the failing flows are in the report",
            details={"failed": [item.flow_id for item in report.results
                                if item.status == V.STATUS_FAILED]},
        )
    return blocked(
        report.verdict_reason,
        hint="a flow that could not be driven here is UNKNOWN — install the missing "
             "capability (a browser, an isolated desktop runner), point the flow at a "
             "reachable target, or fix the declaration — and re-run",
        reason="UNKNOWN is never a PASS",
        details={"unknown": [item.flow_id for item in report.results
                             if item.status == V.STATUS_UNKNOWN]},
    )


def cmd_flows(args: argparse.Namespace) -> int:
    """The recorded flow runs and the evidence each one produced (read-only)."""
    state = _state_dir(args)
    store = VF.FlowStore(state)
    run_id = str(getattr(args, "run_id", "") or "")
    limit = int(getattr(args, "limit", 20) or 20)
    record = store.get(run_id) if run_id else store.latest()
    if run_id and record is None:
        message = f"no recorded flow run '{run_id}' in {store.runs_dir}"
        payload = {"run_id": run_id, "found": False, "state_dir": str(store.dir),
                   "runs": store.runs(limit=limit), "evidence": []}
        _report(payload, evidence=[{"ref": str(store.dir), "kind": "file",
                                    "note": "the flow state directory that was searched"}],
                warnings=(message,))
        _emit(args, payload, warn=message)
        return not_found(message,
                         hint="`skaldir verify flows --json` lists the recorded flow runs")
    rows = store.evidence(run_id=str((record or {}).get("run_id") or ""), limit=limit)
    payload = {
        "state_dir": str(store.dir),
        "runs_dir": str(store.runs_dir),
        "evidence_ledger": str(store.evidence_path),
        "found": bool(record),
        "run_id": (record or {}).get("run_id"),
        "verdict": (record or {}).get("verdict"),
        "verdict_reason": (record or {}).get("verdict_reason"),
        "counts": (record or {}).get("counts"),
        "screenshot_dir": (record or {}).get("screenshot_dir"),
        "started_at": (record or {}).get("started_at"),
        "duration_ms": (record or {}).get("duration_ms"),
        "results": (record or {}).get("results") or [],
        "runs": store.runs(limit=limit),
        "runs_count": len(store.runs(limit=limit)),
        "evidence": rows,
        "evidence_count": len(rows),
        "pass_requirement": VF.PASS_REQUIREMENT,
    }
    evidence: list[dict] = []
    if record:
        evidence.append({
            "ref": str(store.runs_dir / f"{record.get('run_id')}.json"),
            "kind": "file",
            "note": f"verdict {record.get('verdict')} over "
                    f"{(record.get('counts') or {}).get('flows')} flow(s)",
        })
    else:
        evidence.append({"ref": str(store.dir), "kind": "file",
                         "note": "no flow run recorded yet"})
    warnings: list[str] = []
    if not record:
        warnings.append("no flow has been run against a real target yet")
    _report(payload, evidence=tuple(evidence), warnings=tuple(warnings),
            next_actions=(("skaldir verify flow --json", "run the declared flows"),))
    _emit(args, payload, warn="; ".join(warnings))
    return EXIT_OK


def _emit(args: argparse.Namespace, payload: dict, *, warn: str = "") -> None:
    """Print: human text in ``--text``; the payload only when no envelope is active."""
    if warn:
        print(warn, file=sys.stderr)
    if text_mode(args):
        _print_text(args, payload)
        return
    if not envelope_mode():
        dump(payload)


def _print_text(args: argparse.Namespace, payload: dict) -> None:
    """The human view of each verb (machine-first: text only on explicit request)."""
    command = str(getattr(args, "command_path", "") or "")
    if command.endswith("plan") or "plan" in payload:
        plan = payload.get("plan") or {}
        print(f"goal: {plan.get('goal') or '(none)'}   workspace: {plan.get('workspace')}")
        print(f"{'kind':<9} {'check':<20} {'certifiable':<12} criterion")
        for item in plan.get("criteria") or []:
            print(f"{item['kind']:<9} {item['check_id']:<20} "
                  f"{str(item['certifiable']):<12} {item['criterion'][:60]}")
        return
    if "results" in payload and (
        command.endswith(("flow", "flows"))
        or (payload.get("results") and "flow_id" in (payload["results"][0] or {}))
    ):
        verdict = payload.get("verdict")
        reason = payload.get("verdict_reason")
        if verdict is None and not payload.get("results"):
            print("no flow runs recorded yet")
            return
        print(f"{verdict}: {reason}")
        for item in payload.get("results") or []:
            shots = item.get("screenshots") or []
            print(f"  {str(item.get('status')):<8} {str(item.get('flow_id')):<34} "
                  f"{item.get('duration_ms')}ms  {len(shots)} shot(s)  "
                  f"{str(item.get('reason'))[:70]}")
        return
    if "verdict" in payload and "results" in payload:
        print(f"{payload['verdict']}: {payload.get('verdict_reason')}")
        for item in payload.get("results") or []:
            print(f"  {item['status']:<8} {str(item['check_id']):<20} "
                  f"exit={item['exit_code']} {str(item['reason'])[:60]}")
        return
    if "evidence" in payload:
        for row in payload.get("evidence") or []:
            print(f"{row.get('run_id')}  {str(row.get('status')):<8} "
                  f"{str(row.get('check_id')):<18} {str(row.get('criterion'))[:50]}")
        return
    for row in payload.get("runs") or []:
        counts = row.get("counts") or {}
        print(f"{row.get('run_id')}  {str(row.get('verdict')):<8} "
              f"{counts.get('passed', 0)}/{counts.get('criteria', 0)} passed  "
              f"{row.get('started_at')}")


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


def _plan_args(parser: Any) -> None:
    """The flags ``verify plan`` and ``verify run`` share."""
    parser.add_argument("--workspace", default="", help="the real workspace (default: cwd)")
    parser.add_argument("--goal", default="", help="the goal the criteria belong to")
    parser.add_argument("--criterion", action="append", default=[],
                        help="one criterion text (repeatable)")
    parser.add_argument(
        "--plan", default="",
        help=f"plan file (default: <workspace>/{V.PLAN_FILENAME})",
    )
    parser.add_argument("--from-contract", action="store_true",
                        help="plan the goal recorded in the completion contract")
    parser.add_argument("--runtime", default="", help="runtime dir (default: $SKALDIR_HOME)")
    parser.add_argument("--session", default="", help="completion session id")
    parser.add_argument("--revision", type=int, default=0,
                        help="goal revision the evidence binds to")
    parser.add_argument("--state", default="", help="state dir (default: <runtime>/verify)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")


def _add_verify_verbs(sub: Any) -> Any:
    """Add the four ``verify`` verbs to a subparsers handle (one definition)."""
    p_plan = sub.add_parser("plan", help="what would certify the goal (read-only)")
    _plan_args(p_plan)
    p_plan.set_defaults(func=cmd_plan, command_path="verify.plan")

    p_run = sub.add_parser("run", help="run the checks against the real workspace")
    _plan_args(p_run)
    p_run.add_argument("--timeout", type=float, default=float(V.DEFAULT_TIMEOUT),
                       help="per-check timeout in seconds")
    p_run.add_argument("--dry-run", action="store_true",
                       help="run the checks but record nothing")
    p_run.set_defaults(func=cmd_run, command_path="verify.run")

    p_status = sub.add_parser("status", help="recorded runs, latest verdict, freshness")
    p_status.add_argument("--state", default="")
    p_status.add_argument("--runtime", default="")
    p_status.add_argument("--run-id", default="", help="a specific run (default: the latest)")
    p_status.add_argument("--limit", type=int, default=20)
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--text", action="store_true")
    p_status.set_defaults(func=cmd_status, command_path="verify.status")

    p_evidence = sub.add_parser("evidence", help="the recorded criterion evidence")
    p_evidence.add_argument("--state", default="")
    p_evidence.add_argument("--runtime", default="")
    p_evidence.add_argument("--run-id", default="")
    p_evidence.add_argument("--criterion", default="", help="filter by criterion text or id")
    p_evidence.add_argument("--limit", type=int, default=50)
    p_evidence.add_argument("--json", action="store_true")
    p_evidence.add_argument("--text", action="store_true")
    p_evidence.set_defaults(func=cmd_evidence, command_path="verify.evidence")

    p_flow = sub.add_parser(
        "flow",
        help="run the declared user flows against a real browser or a real desktop",
        description=(
            "A user flow is declared (start state anchors -> actions -> end-state anchors) "
            "and then really driven: a real browser through Playwright, or a real isolated "
            "desktop through the Computer Use container runner. The verdict is PASS only "
            "when every declared flow ran and every end-state anchor held; a flow that could "
            "not be driven here is UNKNOWN and never a PASS. The screenshots each flow "
            "produced are the evidence."
        ),
    )
    p_flow.add_argument("--workspace", default="", help="the workspace (default: cwd)")
    p_flow.add_argument(
        "--flows", default="",
        help=f"the declaration file (default: <workspace>/{VF.FLOWS_FILENAME})",
    )
    p_flow.add_argument("--flow", action="append", default=[],
                        help="run only this declared flow id (repeatable)")
    p_flow.add_argument("--base-url", default="",
                        help="rewrite loopback target URLs onto this base URL")
    p_flow.add_argument("--screenshots", default="",
                        help="where captures are written (default: <state>/screenshots)")
    p_flow.add_argument("--headed", action="store_true",
                        help="show the browser window instead of running headless")
    p_flow.add_argument("--desktop-timeout", type=float,
                        default=float(VF.DEFAULT_DESKTOP_TIMEOUT_S),
                        help="per-action timeout for a desktop flow, in seconds")
    p_flow.add_argument("--state", default="", help="state dir (default: <runtime>/verify)")
    p_flow.add_argument("--runtime", default="", help="runtime dir (default: $SKALDIR_HOME)")
    p_flow.add_argument("--list", dest="list_only", action="store_true",
                        help="show what is declared, run nothing")
    p_flow.add_argument("--template", action="store_true",
                        help="print the declaration shape a workspace can copy")
    p_flow.add_argument("--json", action="store_true")
    p_flow.add_argument("--text", action="store_true")
    p_flow.set_defaults(func=cmd_flow, command_path="verify.flow")

    p_flows = sub.add_parser(
        "flows",
        help="the recorded flow runs and the evidence each one produced",
    )
    p_flows.add_argument("--state", default="")
    p_flows.add_argument("--runtime", default="")
    p_flows.add_argument("--run-id", default="", help="a specific flow run (default: the latest)")
    p_flows.add_argument("--limit", type=int, default=20)
    p_flows.add_argument("--json", action="store_true")
    p_flows.add_argument("--text", action="store_true")
    p_flows.set_defaults(func=cmd_flows, command_path="verify.flows")

    for parser in (p_plan, p_run, p_status, p_evidence, p_flow, p_flows):
        share_common_flags(parser)
    return sub


def build_verify_parser(subparsers: Any) -> Any:
    """Add the ``verify`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "verify",
        help="real-world verification (plan / run / status / evidence / flow / flows)",
        description=(
            "Can a user actually use this? `verify` plans the real check behind every goal "
            "criterion, runs it against the workspace (the project suite, a declared "
            "build/command/end-to-end flow, an inspected artifact), records the evidence "
            "with timestamps and hashes and answers PASS/FAIL/UNKNOWN. `verify flow` goes "
            "one layer further and drives a *declared user flow* against a real browser or a "
            "real isolated desktop — start state, actions, end-state check — and keeps the "
            "screenshots as the evidence. UNKNOWN is never a PASS, and only a real executed "
            "check or a really driven flow may certify anything."
        ),
    )
    sub = parser.add_subparsers(dest="verify_command", required=True)
    _add_verify_verbs(sub)
    return parser


def contract_entries() -> dict[str, Any]:
    """The ``COMMANDS`` records this group installs when it is wired (idempotent)."""
    from . import cli_contract

    def ev(ref: str, kind: str = "code", note: str = "") -> dict:
        return {"ref": ref, "kind": kind, "note": note}

    return {
        "verify.flow": cli_contract.CommandSpec(
            "VERIFY",
            (
                ev("src/skaldir/verify_flows.py",
                   "code", "the declared flow, the real browser and the isolated desktop"),
                ev("src/skaldir/computer_use.py",
                   "code", "the isolated container runner a desktop flow is driven by"),
            ),
            "drive a declared user flow (start state, actions, end-state check) against a "
            "real browser or a real isolated desktop and answer PASS/FAIL/UNKNOWN, keeping "
            "the screenshots as evidence",
            exit_codes=(0, 1, 2, 3, 5, 7),
            mutates=True,
            data_schema={"type": "object", "additionalProperties": True},
            notes="UNKNOWN is never PASS: a flow that could not be driven here (no browser, "
                  "no isolated desktop runner, an unreachable target, an action the verifier "
                  "does not implement) exits 3; a flow whose anchors did not hold exits 2",
        ),
        "verify.flows": cli_contract.CommandSpec(
            "VERIFY",
            (ev("src/skaldir/verify_flows.py", "code", "the recorded flow runs"),),
            "the recorded user-flow runs and the evidence each one produced (screenshots, "
            "hashes, timings)",
            exit_codes=(0, 1, 5, 7),
            data_schema={"type": "object", "additionalProperties": True},
        ),
    }


def register_contract_entries() -> list[str]:
    """Install this group's ``COMMANDS`` entries (idempotent; returns the paths added)."""
    return install_contract_entries(contract_entries(), _CONTRACT)


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    parser = build_verify_parser(subparsers)
    register_contract_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir verify",
                                     description="real-world verification")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_verify_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir verify …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_parser",
    "build_verify_parser",
    "cmd_evidence",
    "cmd_flow",
    "cmd_flows",
    "cmd_plan",
    "cmd_run",
    "cmd_status",
    "contract_entries",
    "main",
    "register_cli",
    "register_contract_entries",
]
