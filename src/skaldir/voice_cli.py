"""``skaldir voice …`` — the SUPERVISE layer's CLI surface.

Four verbs over :mod:`skaldir.voice_service`, which replays the supervisor this repo
already runs (:mod:`skaldir.monitor`, :mod:`skaldir.patterns`, :mod:`skaldir.guard`,
:mod:`skaldir.voice`, :mod:`skaldir.pause`) over the events a session really stored:

======================  ==========================================================
``voice status``        what the supervisor is watching right now: the sessions and
                        their event counts, the pause leases and their statuses, the
                        run-supervisor invariant, the detected categories and the
                        policy thresholds it would apply (read-only)
``voice check``         one pass over a trajectory: findings in the six SUPERVISE
                        categories — loops, goal drift, missed errors, false
                        completion claims, bad tool use, missing verification — each
                        with the signal kind and the event refs it came from
``voice triage``        the Jev pre-filter: should the Voice be woken at all? The
                        deterministic reading decides; the substrate's
                        ``voice_triage_v1`` pack is consulted through the Jev façade
                        (:func:`skaldir.jev.decide`) and recorded as an advisory that
                        never replaces it
``voice explain``       why the supervisor would — or would not — intervene: which
                        finding carries a pause trigger, whether it reaches the
                        threshold under the current strictness, whether the cost gate
                        is worth it, and whether the agent already sees the problem
======================  ==========================================================

Exit codes — the product CLI's :mod:`skaldir.cli_contract` vocabulary::

    0   ok                     status: read                                      \
check: the pass ran, no high-severity finding
                               triage: stay silent (or defer)                    \
explain: the reasoning is in ``data``
    1   usage                  argparse refused the call, or ``--pact`` is not a pact
    2   verification failed    check: a high-severity finding (the trajectory fails
                               supervision); triage: ``wake_voice`` — the Voice would
                               be woken
    3   blocked                there is no trajectory to read (no store, no such
                               session, no events); the reason is in ``data`` and
                               ``error`` — never a silent success
    5   not found              status: the runtime directory carries no store at all

``explain`` never writes and never opens a pause: it answers a question. ``triage``
does not open one either — deciding "wake the Voice" is a reading, and only the live
plugin's :class:`skaldir.pause.VoicePause` may act on it.

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``):::

    # src/skaldir/cli.py, inside build_parser(), right after the line
    #     sub = parser.add_subparsers(dest="command", required=False)
    from .voice_cli import register_cli as register_voice_cli

    register_voice_cli(sub)

and ``src/skaldir/cli_contract.py`` needs one ``COMMANDS`` entry per leaf —
``voice.status``, ``voice.check``, ``voice.triage``, ``voice.explain`` — each with
``layer="SUPERVISE"``; the exact entries are in the hand-over note at the bottom of
this file. ``tests/test_cli_contract_dump.py`` fails while they are missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import voice_service as V
from .cli_shared import (
    blocked,
    dump,
    envelope_mode,
    not_found,
    product_contract,
    session_id,
    share_common_flags,
    text_mode,
    verification_failed,
)

DEFAULT_LIMIT = 200


_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))

_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")


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
    """Hand the payload and its evidence to the shared envelope (when wired)."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            kind = str(item.get("kind", "code"))
            _CONTRACT.add_evidence(
                str(item.get("ref", "")),
                kind if kind in _EVIDENCE_KINDS else "code",
                str(item.get("note", "")),
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _usage(message: str) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "usage_error"):
        return int(_CONTRACT.usage_error(message))
    print(f"skaldir voice: {message}", file=sys.stderr)
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# arguments → inputs                                                            #
# --------------------------------------------------------------------------- #


def _pact(args: argparse.Namespace):
    """The declared pact, when ``--pact`` names one (YAML or JSON)."""
    path_text = str(getattr(args, "pact", "") or "")
    if not path_text:
        return None, ""
    path = Path(path_text).expanduser()
    if not path.is_file():
        return None, f"no pact file at {path}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            import yaml

            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 — a malformed pact is the caller's input
        return None, f"{path} is not readable as YAML/JSON: {type(exc).__name__}: {exc}"
    if not isinstance(data, dict):
        return None, f"{path} must contain a mapping (goal/acceptance/scope_paths)"
    try:
        from .pact import Pact

        pact = Pact.from_dict(data)
        pact.validate()
    except Exception as exc:  # noqa: BLE001
        return None, f"{path} is not a valid pact: {type(exc).__name__}: {exc}"
    return pact, ""


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_status(args: argparse.Namespace) -> int:
    """What the supervisor is watching: sessions, leases, runs, policy (read-only)."""
    payload = V.status(runtime=str(getattr(args, "runtime", "") or "") or None,
                       limit=int(getattr(args, "limit", 20) or 20))
    events = payload["events"]
    pauses = payload["pauses"]
    evidence: list[dict] = []
    if events["exists"]:
        evidence.append({
            "ref": str(events["path"]),
            "kind": "file",
            "note": f"{events['events']} stored event(s) in {events['sessions']} session(s)",
        })
    if pauses["exists"]:
        evidence.append({
            "ref": str(pauses["path"]),
            "kind": "file",
            "note": f"{pauses['leases']} pause lease(s): {pauses['statuses']}",
        })
    if payload["runs"].get("exists"):
        evidence.append({
            "ref": str(payload["runs"]["path"]),
            "kind": "file",
            "note": f"{payload['runs'].get('runs')} run record(s), "
                    f"invariant ok={payload['runs'].get('ok')}",
        })
    evidence.append({
        "ref": "src/skaldir/voice_service.py",
        "kind": "code",
        "note": "status reads the stores read-only; it never opens a pause",
    })

    warnings: list[str] = []
    if not payload["runtime_exists"]:
        warnings.append(f"the runtime directory does not exist: {payload['runtime']}")
    elif not (events["exists"] or pauses["exists"] or payload["runs"].get("exists")):
        warnings.append("the runtime carries no store yet — nothing is being watched")
    if not payload["jev"]["voice_triage"]:
        warnings.append(
            "Jev has no provider/flags for voice_triage — a triage answers "
            "deterministically (the legacy path), with no advisory reading"
        )
    open_leases = pauses.get("open") or []
    if open_leases:
        warnings.append(f"{len(open_leases)} open pause lease(s) — see data.pauses.open")
    violations = payload["runs"].get("violations") or []
    if violations:
        warnings.append(f"{len(violations)} run-supervisor invariant violation(s)")

    _report(payload, evidence=evidence, warnings=warnings,
            next_actions=(("skaldir voice check --json",
                           "run one pass over the newest stored trajectory"),))
    _emit(args, payload,
          warn="" if payload["runtime_exists"] else
          f"skaldir voice: {payload['runtime']} is not a Skaldir runtime")
    if not payload["runtime_exists"]:
        return not_found(
            f"no runtime at {payload['runtime']}",
            hint="pass --runtime DIR (default: $SKALDIR_HOME, $HERMES_HOME/godsv, "
                 "~/.hermes/godsv)",
            details={"runtime": payload["runtime"]},
        )
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    """One pass over a trajectory — findings with evidence, nothing invented."""
    pact, pact_error = _pact(args)
    if pact_error:
        payload = {"checked": False, "blocked_reason": pact_error,
                   "session_id": session_id(args), "findings": [], "counts": {}}
        _report(payload, warnings=(pact_error,))
        _emit(args, payload, warn=pact_error)
        return _usage(pact_error)

    payload = V.check(
        runtime=str(getattr(args, "runtime", "") or "") or None,
        events_db=str(getattr(args, "events_db", "") or "") or None,
        session_id=session_id(args),
        step=int(getattr(args, "step", 0) or 0),
        window=int(getattr(args, "window", V.DEFAULT_WINDOW) or V.DEFAULT_WINDOW),
        goal=str(getattr(args, "goal", "") or ""),
        pact=pact,
        goal_from_contract=not bool(getattr(args, "no_recorded_goal", False)),
        incidents=not bool(getattr(args, "no_incidents", False)),
        limit=int(getattr(args, "limit", DEFAULT_LIMIT) or 0),
    )
    findings = payload.get("findings") or []
    evidence = _finding_evidence(payload)
    warnings: list[str] = []
    if not payload.get("checked"):
        warnings.append(str(payload.get("blocked_reason") or "nothing to check"))
    else:
        trajectory = payload.get("trajectory") or {}
        if payload.get("findings_truncated"):
            warnings.append(
                f"the finding list is capped at {len(findings)} of "
                f"{payload.get('findings_count')} — counts and severity describe the "
                "full pass (raise --limit)"
            )
        if payload.get("verdict") == "clean":
            warnings.append("the pass raised no finding")
        if not trajectory.get("exists"):  # pragma: no cover — checked implies exists
            warnings.append("the session has no stored events")
    _report(payload, evidence=evidence, warnings=warnings,
            next_actions=(
                (f"skaldir voice triage --session {payload.get('session_id') or '<id>'} "
                 "--json", "the Jev pre-filter: should the Voice be woken at all?"),
                ("skaldir voice explain --json",
                 "why the supervisor would or would not intervene"),
            ))

    if not payload.get("checked"):
        message = str(payload.get("blocked_reason") or "nothing to check")
        _emit(args, payload, warn=message)
        return blocked(
            message,
            hint="store a trajectory first (the plugin writes events.db while a session "
                 "runs), or pass --session ID of an existing one",
            next_command="skaldir voice status --json",
            reason=message,
            details={"events_db": payload.get("events_db"),
                     "sessions": [item["session_id"] for item in
                                  payload.get("sessions") or []][:20]},
        )

    _emit(args, payload)
    if payload.get("verdict") == "attention":
        counts = payload.get("counts") or {}
        return verification_failed(
            f"{payload.get('findings_count')} finding(s) in the trajectory "
            f"(loops {counts.get('loops', 0)}, goal drift {counts.get('goal_drift', 0)}, "
            f"missed errors {counts.get('missed_errors', 0)}, false claims "
            f"{counts.get('false_completion_claims', 0)}, bad tool use "
            f"{counts.get('bad_tool_use', 0)}, missing verification "
            f"{counts.get('missing_verification', 0)}); highest severity "
            f"{payload.get('highest_severity_name')}",
            hint="read data.findings — every finding carries its signal kind and event refs",
            details={"counts": counts,
                     "highest": payload.get("highest_severity_name"),
                     "examples": [item["message"] for item in findings[:3]]},
        )
    return EXIT_OK


def cmd_triage(args: argparse.Namespace) -> int:
    """Should the Voice be woken at all? The deterministic pre-filter + Jev's reading."""
    pact, pact_error = _pact(args)
    if pact_error:
        payload = {"checked": False, "blocked_reason": pact_error,
                   "session_id": session_id(args), "decision": "stay_silent"}
        _report(payload, warnings=(pact_error,))
        _emit(args, payload, warn=pact_error)
        return _usage(pact_error)

    payload = V.triage(
        runtime=str(getattr(args, "runtime", "") or "") or None,
        events_db=str(getattr(args, "events_db", "") or "") or None,
        session_id=session_id(args),
        step=int(getattr(args, "step", 0) or 0),
        window=int(getattr(args, "window", V.DEFAULT_WINDOW) or V.DEFAULT_WINDOW),
        goal=str(getattr(args, "goal", "") or ""),
        pact=pact,
        jev=not bool(getattr(args, "no_jev", False)),
        provider=str(getattr(args, "provider", "") or ""),
        limit=int(getattr(args, "limit", DEFAULT_LIMIT) or 0),
        record=bool(getattr(args, "record_advisory", False)),
    )
    jev = payload.get("jev") or None
    evidence = _triage_evidence(payload)
    warnings: list[str] = []
    if not payload.get("checked"):
        warnings.append(str(payload.get("blocked_reason") or "nothing to check"))
    if jev and jev.get("status") != "ok":
        warnings.append(
            f"no Jev advisory ({jev.get('refusal_reason') or jev.get('status')}) — the "
            "deterministic reading stands"
        )
    if jev and jev.get("status") == "ok" and not jev.get("agrees_with_decision"):
        warnings.append(
            f"the Jev reading says '{jev.get('chosen')}' while the deterministic "
            f"pre-filter says '{payload.get('decision')}' — the advisory is recorded, "
            "the deterministic decision stands"
        )
    _report(payload, evidence=evidence, warnings=warnings,
            next_actions=(("skaldir voice explain --json",
                           "the full chain behind this decision"),))

    if not payload.get("checked"):
        message = str(payload.get("blocked_reason") or "nothing to check")
        _emit(args, payload, warn=message)
        return blocked(message,
                        hint="pass --session ID of a session that stored events",
                        next_command="skaldir voice status --json",
                        reason=message)
    _emit(args, payload)
    if payload.get("wake"):
        return verification_failed(
            f"the Voice would be woken: {payload.get('reason')}",
            hint="read data.handled — the pre-filter states why it did not consider the "
                 "agent to be handling the problem",
            details={"decision": payload.get("decision"),
                     "counts": payload.get("counts"),
                     "session_id": payload.get("session_id")},
        )
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    """Why the supervisor would — or would not — intervene (read-only reasoning)."""
    pact, pact_error = _pact(args)
    if pact_error:
        payload = {"checked": False, "blocked_reason": pact_error,
                   "session_id": session_id(args), "would_intervene": False}
        _report(payload, warnings=(pact_error,))
        _emit(args, payload, warn=pact_error)
        return _usage(pact_error)

    payload = V.explain(
        runtime=str(getattr(args, "runtime", "") or "") or None,
        events_db=str(getattr(args, "events_db", "") or "") or None,
        session_id=session_id(args),
        step=int(getattr(args, "step", 0) or 0),
        window=int(getattr(args, "window", V.DEFAULT_WINDOW) or V.DEFAULT_WINDOW),
        goal=str(getattr(args, "goal", "") or ""),
        pact=pact,
        jev=not bool(getattr(args, "no_jev", False)),
        provider=str(getattr(args, "provider", "") or ""),
        limit=int(getattr(args, "limit", DEFAULT_LIMIT) or 0),
        record=bool(getattr(args, "record_advisory", False)),
    )
    evidence = _triage_evidence(payload)
    warnings: list[str] = []
    if not payload.get("checked"):
        warnings.append(str(payload.get("blocked_reason") or "nothing to check"))
    jev = payload.get("jev") or None
    if jev and jev.get("status") != "ok":
        warnings.append(
            f"no Jev advisory ({jev.get('refusal_reason') or jev.get('status')}) — the "
            "deterministic reasoning stands"
        )
    _report(payload, evidence=evidence, warnings=warnings)
    if not payload.get("checked"):
        message = str(payload.get("blocked_reason") or "nothing to check")
        _emit(args, payload, warn=message)
        return blocked(message,
                        hint="pass --session ID of a session that stored events",
                        next_command="skaldir voice status --json",
                        reason=message)
    _emit(args, payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# evidence + rendering                                                          #
# --------------------------------------------------------------------------- #


def _finding_evidence(payload: dict) -> list[dict]:
    """One evidence item per stored trajectory read, plus the findings' own refs."""
    items: list[dict] = []
    trajectory = payload.get("trajectory") or {}
    if trajectory.get("exists"):
        items.append({
            "ref": str(trajectory.get("events_db") or payload.get("events_db") or ""),
            "kind": "file",
            "note": (
                f"session {trajectory.get('session_id')}: {trajectory.get('events')} event(s), "
                f"steps {trajectory.get('first_step')}..{trajectory.get('last_step')}"
            ),
        })
    for finding in (payload.get("findings") or [])[:8]:
        for ref in finding.get("evidence") or ():
            items.append({
                "ref": str(ref),
                "kind": "file",
                "note": f"{finding.get('category')}: {str(finding.get('message'))[:120]}",
            })
    items.append({
        "ref": "src/skaldir/voice_service.py",
        "kind": "code",
        "note": "the pass replays monitor/patterns/guard/voice over the stored events",
    })
    return items


def _triage_evidence(payload: dict) -> list[dict]:
    items: list[dict] = []
    session_id = str(payload.get("session_id") or "")
    for ref in (payload.get("evidence") or [])[:10]:
        items.append({"ref": str(ref), "kind": "file",
                      "note": f"the reading for session {session_id}"})
    jev = payload.get("jev") or None
    if jev and jev.get("decision_id"):
        items.append({
            "ref": str(jev["decision_id"]),
            "kind": "verification",
            "note": (
                f"Jev {jev.get('question_pack_id')} {jev.get('question_pack_version')} "
                f"→ {jev.get('status')} / {jev.get('chosen')} (advisory)"
            ),
        })
    items.append({
        "ref": "src/skaldir/voice_service.py",
        "kind": "code",
        "note": "the deterministic pre-filter decides; the advisory never replaces it",
    })
    return items


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
    if command.endswith("status"):
        events = payload.get("events") or {}
        pauses = payload.get("pauses") or {}
        runs = payload.get("runs") or {}
        print(f"runtime: {payload.get('runtime')}  (exists: {payload.get('runtime_exists')})")
        print(f"events:  {events.get('events', 0)} in {events.get('sessions', 0)} session(s)"
              f"  {events.get('path')}")
        for row in events.get("list") or []:
            print(f"   {row['session_id']:<28} events={row['events']:<6} "
                  f"steps {row['first_step']}..{row['last_step']}")
        print(f"pauses:  {pauses.get('leases', 0)} lease(s) {pauses.get('statuses')} "
              f"strictness={pauses.get('strictness')}")
        print(f"runs:    {runs.get('runs', 0)} record(s) ok={runs.get('ok')} "
              f"invariant={'yes' if runs.get('invariant') else 'no store'}")
        print(f"jev:     {payload.get('jev', {}).get('effect')}")
        return
    if command.endswith("check"):
        print(f"session {payload.get('session_id')}  verdict={payload.get('verdict')}  "
              f"findings={payload.get('findings_count')}  "
              f"(showing {payload.get('findings_returned')})")
        for name, count in (payload.get("counts") or {}).items():
            print(f"  {name:<26} {count}")
        for finding in payload.get("findings") or []:
            print(f"  step {finding['step']:<5} {finding['severity_name']:<5} "
                  f"{finding['category']:<26} {str(finding['message'])[:70]}")
        return
    if command.endswith("triage"):
        print(f"decision: {payload.get('decision')}  wake={payload.get('wake')}  "
              f"({payload.get('reason_code')})")
        print(f"  {payload.get('reason')}")
        handled = payload.get("handled") or {}
        print(f"  agent already on it: {handled.get('already_working')}  "
              f"healthy={handled.get('healthy')}")
        jev = payload.get("jev") or {}
        if jev:
            print(f"  jev: {jev.get('status')} / {jev.get('chosen')} "
                  f"({jev.get('refusal_reason') or 'advisory'})")
        return
    if command.endswith("explain"):
        print(f"would_intervene: {payload.get('would_intervene')}  "
              f"decision={payload.get('decision')}")
        print(payload.get("statement") or "")
        for reason in payload.get("reasons") or []:
            print(f"  - [{reason['code']}] {reason['detail']}")
        for trigger in payload.get("triggers") or []:
            print(f"  * {trigger['kind']} x{trigger['occurrences']} "
                  f"(threshold {trigger['threshold']}, "
                  f"{'reaches' if trigger['reaches_threshold'] else 'below'})")
        return


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


def _trajectory_args(parser: Any) -> None:
    """The flags ``check``/``triage``/``explain`` share."""
    parser.add_argument("--runtime", default="", help="runtime dir (default: $SKALDIR_HOME)")
    parser.add_argument("--events-db", default="",
                        help="event store (default: <runtime>/events.db)")
    parser.add_argument("--session", default="", help="session id (default: the newest one)")
    parser.add_argument("--step", type=int, default=0,
                        help="read the trajectory up to this step (default: all of it)")
    parser.add_argument("--window", type=int, default=V.DEFAULT_WINDOW,
                        help="how many steps the health reading looks back")
    parser.add_argument("--goal", default="",
                        help="the goal to judge drift against (default: the recorded one)")
    parser.add_argument("--no-recorded-goal", action="store_true",
                        help="do not use the stored contract/goal for the drift reading")
    parser.add_argument("--no-incidents", action="store_true",
                        help="do not match errors against the incident memory")
    parser.add_argument("--no-jev", action="store_true",
                        help="skip the Jev advisory (deterministic reading only)")
    parser.add_argument("--provider", default="",
                        help="override the System-One provider Jev may use (default: this "
                             "runtime's system_one.provider — 'disabled' when it names none)")
    parser.add_argument("--pact", default="", help="pact file (YAML/JSON) for scope checks")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="max findings carried in the payload (0 = all)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")


def _record_advisory_arg(parser: Any) -> None:
    """``--record-advisory``: subscribe this advisory into the runtime's event store.

    Only the two verbs that produce a Jev advisory take it. It is opt-in because these verbs
    are read-only over the store they inspect (pinned by
    ``tests/test_voice_service.py::test_does_not_write_into_the_store``); the runtime paths
    that own a store subscribe without asking (MVP/87).
    """
    parser.add_argument("--record-advisory", dest="record_advisory", action="store_true",
                        help="also write the Jev advisory into the runtime's event store as a "
                             "jev_decision row (default: this read-only verb never writes "
                             "into the store it inspects)")


def _add_voice_verbs(sub: Any) -> Any:
    """Add the four ``voice`` verbs to a subparsers handle (one definition)."""
    p_status = sub.add_parser("status", help="what the supervisor is watching (read-only)")
    p_status.add_argument("--runtime", default="")
    p_status.add_argument("--limit", type=int, default=20, help="sessions listed")
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--text", action="store_true")
    p_status.set_defaults(func=cmd_status, command_path="voice.status")

    p_check = sub.add_parser("check", help="one pass over a trajectory: findings + evidence")
    _trajectory_args(p_check)
    p_check.set_defaults(func=cmd_check, command_path="voice.check")

    p_triage = sub.add_parser("triage", help="the Jev pre-filter: wake the Voice at all?")
    _trajectory_args(p_triage)
    _record_advisory_arg(p_triage)
    p_triage.set_defaults(func=cmd_triage, command_path="voice.triage")

    p_explain = sub.add_parser("explain", help="why the supervisor would (not) intervene")
    _trajectory_args(p_explain)
    _record_advisory_arg(p_explain)
    p_explain.set_defaults(func=cmd_explain, command_path="voice.explain")

    for parser in (p_status, p_check, p_triage, p_explain):
        share_common_flags(parser)
    return sub


def build_voice_parser(subparsers: Any) -> Any:
    """Add the ``voice`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "voice",
        help="the Voice supervisor (status / check / triage / explain)",
        description=(
            "The independent supervisor, over the trajectories this runtime already "
            "stores: what it is watching, one deterministic pass over a session "
            "(loops, goal drift, missed errors, false completion claims, bad tool use, "
            "missing verification — each with evidence), whether the Voice should be "
            "woken at all (the deterministic pre-filter plus the substrate's "
            "voice_triage_v1 pack through the Jev facade) and why it would or would not "
            "intervene. The Voice never interferes when the agent has already recognised "
            "the problem and is working on it sensibly."
        ),
    )
    sub = parser.add_subparsers(dest="voice_command", required=True)
    _add_voice_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    return build_voice_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir voice",
                                     description="the Voice supervisor")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_voice_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir voice …`` — returns the exit code, never raises for a request.

    With ``cli_contract`` present it runs through ``run_command``, so
    ``python -m skaldir.voice_cli --json`` emits the very same envelope the product
    CLI does (and honors the "no success without evidence" rule).
    """
    parser = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is None:
        return int(func(args) or 0)
    return int(_CONTRACT.run_command(args, func))


if __name__ == "__main__":  # pragma: no cover — module entry point
    raise SystemExit(main())


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_parser",
    "build_voice_parser",
    "cmd_check",
    "cmd_explain",
    "cmd_status",
    "cmd_triage",
    "main",
    "register_cli",
]


# --------------------------------------------------------------------------- #
# hand-over note for the CLI owner (this module never edits cli.py)             #
# --------------------------------------------------------------------------- #
#
# 1) src/skaldir/cli.py, inside build_parser(), right after the line
#        sub = parser.add_subparsers(dest="command", required=False)
#    add:
#
#        from .voice_cli import register_cli as register_voice_cli
#        register_voice_cli(sub)
#
# 2) src/skaldir/cli_contract.py: four COMMANDS entries. Layer "SUPERVISE".
#
#    "voice.status": _C("SUPERVISE",
#        (_ev("src/skaldir/voice_service.py", note="status reads the live stores read-only"),
#         _ev("src/skaldir/events.py", note="the stored sessions/events it counts"),
#         _ev("src/skaldir/pause.py", note="the pause leases and their statuses")),
#        "what the supervisor is watching: sessions, leases, runs, policy thresholds",
#        exit_codes=(0, 1, 5, 7)),
#
#    "voice.check": _C("SUPERVISE",
#        (_ev("src/skaldir/monitor.py", note="the deterministic step detectors"),
#         _ev("src/skaldir/patterns.py", note="Block-4 sequence patterns"),
#         _ev("src/skaldir/guard.py", note="claim/evidence guards + contradictions"),
#         _ev("src/skaldir/voice.py", note="read_trajectory_health")),
#        "one pass over a stored trajectory: six finding categories, each with evidence",
#        exit_codes=(0, 1, 2, 3, 7)),
#
#    "voice.triage": _C("SUPERVISE",
#        (_ev("src/skaldir/voice_service.py", note="the deterministic pre-filter"),
#         _ev("src/skaldir/jev/decide.py", note="the voice_triage pack through the facade"),
#         _ev("src/skaldir/pause.py", note="the durable interruption history")),
#        "should the Voice be woken at all? deterministic pre-filter + Jev advisory",
#        exit_codes=(0, 1, 2, 3, 7)),
#
#    "voice.explain": _C("SUPERVISE",
#        (_ev("src/skaldir/pause.py", note="TRIGGER_RULES + strictness thresholds"),
#         _ev("src/skaldir/voice.py", note="assess_intervention cost gate"),
#         _ev("src/skaldir/voice.py", note="TrajectoryHealth: the agent already on it")),
#        "why the supervisor would or would not intervene for a trajectory",
#        exit_codes=(0, 1, 3, 7)),
