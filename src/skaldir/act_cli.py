"""``skaldir act …`` — the CLI surface of the Computer-Use (ACT) layer.

The command group is added by the product CLI through one call:

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), next to the Jev registration
    from .act_cli import register_cli as _register_act_cli

    _register_act_cli(sub)

``register_cli(subparsers)`` is the whole contract: it adds the ``act`` group with its
verbs, registers the group's ``cli_contract.COMMANDS`` entries for them (so a new command
cannot ship without a contract, and this module needs no edit to ``cli_contract.py``), and
returns the parser. Calling it twice on the same handle is safe — it returns the existing
group instead of raising argparse's ``conflicting subparser`` (the trap a second
registration caused for another group).

The verbs::

    act check                       what this host can really do (window/click/key/scroll)
    act check --require <name>      exit 3 when that capability is unavailable here
    act run --actions FILE|-        one declared action list, end state verified for real
    act subtool save --file FILE|-  store a sub-tool: preconditions + anchors + actions + end state
    act subtool list                the saved sub-tools plus the five built-ins
    act subtool show NAME           one sub-tool as stored (the exact schema)
    act subtool run NAME            observe → check preconditions/anchors → act → check end state

Exit codes (the product contract's vocabulary)::

    0 ok · 1 usage · 2 a declared check did not hold (an unverified action, an unobserved
    anchor, an end state that is not there) · 3 blocked — this host cannot do it today and
    the record names the missing capability · 5 not found · 6 conflict · 7 internal

Nothing here drives the live desktop by accident: the Computer-Use policy requires
isolation, and a run without a usable bridge exits 3 instead of pretending. The one path
that can reach the user's own session is `act run --runner live-desktop`, and only with the
explicit opt-in **and** a named target (`--allow-live-desktop --live-target <window>`). Even
then the runner builds every command itself from the declared kind/payload, bound to that
one window (keys/text/pointer/capture): a declared `cli`/`window` action is refused, so a
plan can never hand the live path a command of its own (report 18, F1/F8).

In `--json` mode the handler fills the shared envelope (`set_data` + `add_evidence`);
without it the human table goes to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import act_service as S
from .cli_shared import install_contract_entries, product_contract, share_common_flags

# --------------------------------------------------------------------------- #
# the shared envelope                                                           #
# --------------------------------------------------------------------------- #


_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", S.EXIT_OK))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", S.EXIT_USAGE))
EXIT_VERIFICATION_FAILED = int(
    getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", S.EXIT_VERIFICATION_FAILED)
)
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", S.EXIT_BLOCKED))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", S.EXIT_NOT_FOUND))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", S.EXIT_CONFLICT))

#: The dotted command paths this group owns, with the evidence each one reports.
COMMAND_EVIDENCE: dict[str, tuple[dict, ...]] = {
    "act.check": (
        {
            "ref": "src/skaldir/computer_use.py:464",
            "kind": "code",
            "note": "runner_availability() — the real isolation levels of this host",
        },
        {
            "ref": "src/skaldir/act_service.py:483",
            "kind": "code",
            "note": "capability_report() — bridge/driver/verification per capability",
        },
    ),
    "act.run": (
        {
            "ref": "src/skaldir/computer_use.py:357",
            "kind": "code",
            "note": "CUExecutor — act → observe → verify → audit, isolation enforced",
        },
        {
            "ref": "src/skaldir/act_service.py:938",
            "kind": "code",
            "note": "run_action_list() — the declared list plus the end-state check",
        },
    ),
    "act.subtool.save": (
        {
            "ref": "src/skaldir/act_service.py:1517",
            "kind": "code",
            "note": "subtool_errors() — the stored schema is validated, never assumed",
        },
    ),
    "act.subtool.list": (
        {
            "ref": "src/skaldir/act_service.py:1605",
            "kind": "code",
            "note": "list_subtools() — saved sub-tools plus the five built-ins",
        },
    ),
    "act.subtool.show": (
        {
            "ref": "src/skaldir/act_service.py:1637",
            "kind": "code",
            "note": "load_subtool() — one stored sub-tool, with its origin",
        },
    ),
    "act.subtool.run": (
        {
            "ref": "src/skaldir/system_one_subtools.py:696",
            "kind": "code",
            "note": "check_blind_replay() — a stored sequence only runs when it is observed",
        },
        {
            "ref": "src/skaldir/act_service.py:1819",
            "kind": "code",
            "note": "run_subtool() — applicability, anchors, actions, end state",
        },
    ),
    "act.live-setup": (
        {
            "ref": "src/skaldir/act_service.py:live_setup_report",
            "kind": "code",
            "note": "live_setup_report() — measure, list windows, write one target opt-in",
        },
        {
            "ref": "src/skaldir/cu_host_session.py:write_live_optin",
            "kind": "code",
            "note": "write_live_optin() — atomic settings write (backup + os.replace)",
        },
    ),
}


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entries for this group (registered at CLI build time).

    A new ``skaldir`` command cannot ship without a contract entry
    (``tests/test_cli_contract_dump.py``); the ACT group adds its own entries here so the
    CLI's module keeps its single ownership and nothing has to be pasted by hand.
    """
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    entries = {
        "act.check": spec(
            "ACT",
            COMMAND_EVIDENCE["act.check"],
            "what this host can really do (window/click/key/scroll) and what is missing",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, S.EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "host": {"type": "object"},
                    "bridges": {"type": "array"},
                    "capabilities": {"type": "array"},
                    "capability_names": {"type": "array"},
                    "policy": {"type": "object"},
                    "live_desktop": {"type": "boolean"},
                    "live_desktop_allowed": {"type": "boolean"},
                    "live_desktop_optin": {"type": "object"},
                    "verdict": {"type": "string"},
                    "summary": {"type": "object"},
                },
                "required": ["bridges", "capabilities", "live_desktop", "verdict"],
            },
            notes="exit 0 is the report itself; --require NAME exits 3 when that capability "
            "is unavailable here, and the record names the missing piece. `bridges` carries "
            "the `host-session` bridge (a display this run owns) and `live-desktop` (the "
            "opted-in one named window); `live_desktop`/`live_desktop_allowed` are the "
            "measured two-fact opt-in (switch + named target), never a literal",
        ),
        "act.run": spec(
            "ACT",
            COMMAND_EVIDENCE["act.run"],
            "run a declared action list in an isolated runner and verify the end state",
            exit_codes=(
                EXIT_OK,
                EXIT_USAGE,
                EXIT_VERIFICATION_FAILED,
                EXIT_BLOCKED,
                S.EXIT_INTERNAL,
            ),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "verified": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "goal": {"type": "string"},
                    "declared": {"type": "integer"},
                    "executed": {"type": "integer"},
                    "actions": {"type": "array"},
                    "end_state": {"type": ["object", "null"]},
                    "observation": {"type": ["object", "null"]},
                    "runner": {"type": "object"},
                    "policy": {"type": "object"},
                    "scope": {"type": "string"},
                    "live": {"type": "boolean"},
                    "record": {"type": "string"},
                    "missing_capability": {"type": "string"},
                    "screenshot": {"type": ["object", "null"]},
                    "cleanup": {"type": ["object", "null"]},
                },
                "required": ["status", "verified", "reason", "actions", "runner"],
            },
            notes="exit 2 when an action or the end state is not verified (an action the "
            "runner REFUSED is never 'verified' — the run stops instead of publishing "
            "evidence for something that did not run); exit 3 when no real bridge can carry "
            "the run; --runner dry-run declares data.scope='in-process fixture' and is never "
            "chosen by 'auto'. `runner.bridge` is `host-session` (a display this run owns, "
            "reported in data.cleanup with its display/closed/still_present) or "
            "`live-desktop` (one named window of the user's session, only with the opt-in); "
            "with --screenshot, data.screenshot carries {path, sha256, size_bytes, width, "
            "height, scope, label} of a real capture, and a capture that could not be taken "
            "is reported as absent (a warning), never as an empty file",
        ),
        "act.subtool.save": spec(
            "ACT",
            COMMAND_EVIDENCE["act.subtool.save"],
            "store a GUI sub-tool (preconditions + anchors + actions + end state)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_CONFLICT, S.EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "subtool": {"type": "object"},
                    "stored": {"type": "string"},
                    "replaced": {"type": "boolean"},
                },
                "required": ["subtool", "stored"],
            },
            notes="exit 6 without --force when the name is already stored (never a silent "
            "overwrite); a declaration that does not validate exits 1 with every reason",
        ),
        "act.subtool.list": spec(
            "ACT",
            COMMAND_EVIDENCE["act.subtool.list"],
            "the saved GUI sub-tools plus the five built-ins",
            exit_codes=(EXIT_OK, S.EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "subtools": {"type": "array"},
                    "count": {"type": "integer"},
                    "directory": {"type": "string"},
                    "builtin": {"type": "array"},
                },
                "required": ["subtools", "count", "directory"],
            },
        ),
        "act.subtool.show": spec(
            "ACT",
            COMMAND_EVIDENCE["act.subtool.show"],
            "one GUI sub-tool exactly as stored (the schema the owner specified)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_NOT_FOUND, S.EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "origin": {"type": "string"},
                    "path": {"type": "string"},
                    "spec": {"type": "object"},
                    "subtool": {"type": "object"},
                },
                "required": ["name", "origin", "spec"],
            },
        ),
        "act.subtool.run": spec(
            "ACT",
            COMMAND_EVIDENCE["act.subtool.run"],
            "run one GUI sub-tool: observe → anchors → actions → end state",
            exit_codes=(
                EXIT_OK,
                EXIT_USAGE,
                EXIT_VERIFICATION_FAILED,
                EXIT_BLOCKED,
                EXIT_NOT_FOUND,
                S.EXIT_INTERNAL,
            ),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "subtool": {"type": "string"},
                    "origin": {"type": "string"},
                    "status": {"type": "string"},
                    "verified": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "applicability": {"type": "object"},
                    "precondition_check": {"type": "object"},
                    "actions": {"type": "array"},
                    "end_state": {"type": ["object", "null"]},
                    "runner": {"type": "object"},
                    "missing_capability": {"type": "string"},
                    "missing_anchors": {"type": "array"},
                },
                "required": ["subtool", "status", "verified", "reason"],
            },
            notes="exit 3 when a declared check has no channel on this host (the missing "
            "capability is named); exit 2 when the channel works but the anchor is not "
            "observed — nothing is executed then; `--runner live-desktop` needs the "
            "opt-in (`act live-setup --target <window> --write` or "
            "`--allow-live-desktop --live-target <window>`)",
        ),
        "act.live-setup": spec(
            "ACT",
            COMMAND_EVIDENCE["act.live-setup"],
            "guided live-desktop opt-in: measure, list windows, write one named target",
            exit_codes=(EXIT_OK, EXIT_USAGE, S.EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "optin": {"type": "object"},
                    "allowed_now": {"type": "boolean"},
                    "host": {"type": "object"},
                    "windows": {"type": "array"},
                    "window_count": {"type": "integer"},
                    "requested_target": {"type": "string"},
                    "write": {"type": ["object", "null"]},
                    "next_steps": {"type": "array"},
                },
                "required": ["optin", "allowed_now", "windows", "next_steps"],
            },
            notes="report-only without --write; `--target <window> --write` stores "
            "computer_use.allow_live_desktop + live_desktop_target atomically (backup + "
            "os.replace). An empty target is refused — an unscoped live desktop is never "
            "written. `windows` lists the real session windows so a target can be picked "
            "from what is on screen",
        ),
        "act.subtool.learn": spec(
            "ACT",
            COMMAND_EVIDENCE["act.subtool.save"],
            "learn a draft GUI sub-tool from one recorded session's repeated sequence",
            exit_codes=(EXIT_OK, EXIT_NOT_FOUND, S.EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "learned": {"type": "boolean"},
                    "subtool": {"type": "object"},
                    "session_id": {"type": "string"},
                    "pattern": {"type": "array"},
                    "repeats": {"type": "integer"},
                    "steps_seen": {"type": "integer"},
                },
                "required": ["learned"],
            },
            notes="exit 5 when nothing is repeated to learn (or the session has no recorded "
            "computer-use events) — the reason is stated, never an invented draft; the "
            "learn happens only, the draft is NOT executed",
        ),
    }
    return entries


def register_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added."""
    global _CONTRACT
    _CONTRACT = product_contract()
    return install_contract_entries(contract_entries(), _CONTRACT)


def _report(payload: Any, *, evidence: Sequence[dict] = (), warnings: Sequence[str] = ()) -> None:
    """Fill the shared envelope (data + evidence); harmless when running standalone."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")), str(item.get("note", ""))
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _real_label(record: dict) -> str:
    """``real`` / ``fixture`` / ``not real`` — the honest label of one bridge record."""
    if record.get("real"):
        return "real"
    return "fixture" if record.get("fixture") else "not real"


def _json_mode(args: argparse.Namespace) -> bool:
    """Whether this invocation asked for the envelope (the shared flag when available)."""
    if _CONTRACT is not None:
        return bool(_CONTRACT.json_requested(args))
    return bool(getattr(args, "json", False))


def _next_action(command: str, reason: str) -> None:
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.next_action(command, reason)
    except Exception:  # noqa: BLE001
        return


def _fail(payload: Any, code: int, message: str, *, hint: str = "", details: dict | None = None,
          evidence: Sequence[dict] = (), next_command: str = "", reason: str = "") -> int:
    """One failure path: the payload for ``--json``, the message for the contract."""
    _report(payload, evidence=evidence)
    if next_command:
        _next_action(next_command, reason or message)
    if _CONTRACT is not None:
        try:
            return int(
                _CONTRACT.fail(code, message, hint=hint, details=dict(details or {}))
            )
        except Exception:  # noqa: BLE001 — fall back to the plain code
            return int(code)
    print(f"act: {message}", file=sys.stderr)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)
    return int(code)


def _exit_code_for(record: dict) -> int:
    """The record's own verdict (it carries the exit code it was built with)."""
    return int(record.get("exit_code", EXIT_BLOCKED))


def _scope_warning(record: dict) -> tuple[str, ...]:
    runner = dict(record.get("runner") or {})
    if runner.get("fixture"):
        return (
            "in-process fixture: the DryRunRunner was requested explicitly — it drives no "
            "display, so nothing about this host was proven",
        )
    return ()


# --------------------------------------------------------------------------- #
# input                                                                         #
# --------------------------------------------------------------------------- #


def _read_json(path: str | None, *, what: str) -> tuple[Any | None, int]:
    """Read one JSON document from ``--… FILE`` or stdin (``-``); JSON only."""
    try:
        if path and str(path) != "-":
            text = Path(str(path)).read_text(encoding="utf-8")
        elif not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print(
                f"act: no {what} on stdin and no --file/--actions path given", file=sys.stderr
            )
            return None, EXIT_USAGE
    except OSError as exc:
        print(f"act: cannot read the {what}: {exc}", file=sys.stderr)
        return None, EXIT_USAGE
    if not str(text).strip():
        print(f"act: the {what} is empty", file=sys.stderr)
        return None, EXIT_USAGE
    try:
        return json.loads(text), EXIT_OK
    except json.JSONDecodeError as exc:
        print(f"act: the {what} is not JSON: {exc}", file=sys.stderr)
        return None, EXIT_USAGE


def _actions_from(payload: Any) -> tuple[list | None, Any, str, int]:
    """Accept either a bare action list or a request object with ``actions`` + ``end_state``."""
    if isinstance(payload, list):
        return list(payload), None, "", EXIT_OK
    if isinstance(payload, dict):
        actions = payload.get("actions")
        if not isinstance(actions, list):
            print(
                "act run: the request has no 'actions' list — one object with "
                "{actions: [...], end_state: {...}} or a bare JSON list",
                file=sys.stderr,
            )
            return None, None, "", EXIT_USAGE
        return (
            list(actions),
            payload.get("end_state"),
            str(payload.get("goal", "") or ""),
            EXIT_OK,
        )
    print("act run: the request must be a JSON object or a JSON list", file=sys.stderr)
    return None, None, "", EXIT_USAGE


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_check(args: argparse.Namespace) -> int:
    """`act check` — the honest capability matrix (exit 0), or exit 3 for --require."""
    probe = bool(getattr(args, "probe_image", False))
    report = S.capability_report(probe_image=probe)
    required = [str(name) for name in (getattr(args, "require", None) or [])]
    missing = [
        S.capability_status(name, probe_image=probe) for name in required
    ]
    unavailable = [row for row in missing if not row.get("available")]
    data = dict(report)
    data["required"] = {
        "asked": required,
        "unavailable": [
            {
                "name": row.get("name"),
                "missing_capability": row.get("missing_capability")
                or row.get("reason", ""),
                "reason": row.get("reason", ""),
            }
            for row in unavailable
        ],
    }
    evidence = tuple(
        dict(item) for item in COMMAND_EVIDENCE["act.check"]
    )
    _report(data, evidence=evidence)
    json_mode = _json_mode(args)
    if not json_mode:
        _print_capability_table(data)
    if unavailable:
        first = unavailable[0]
        return _fail(
            data,
            EXIT_BLOCKED,
            "capability unavailable on this host: "
            f"{first.get('name')} — {first.get('missing_capability') or first.get('reason', '')}",
            hint="`skaldir act check --json` lists every capability, bridge and driver",
            details={
                "required": required,
                "unavailable": [row.get("name") for row in unavailable],
                "missing_capability": first.get("missing_capability"),
            },
            evidence=evidence,
            next_command="skaldir act check --probe-image --json" if not probe else "",
            reason="settle the driver tools inside the isolated image in a real container",
        )
    return EXIT_OK


def _print_capability_table(data: dict) -> None:
    facts = dict(data.get("host") or {})
    print(f"host: {facts.get('platform')} · godsv {facts.get('godsv_version')} "
          f"· session {facts.get('session_type') or 'unknown'}")
    optin = dict(data.get("live_desktop_optin") or {})
    print(
        "live desktop: "
        + (
            f"OPTED IN — target {optin.get('target')!r}"
            if data.get("live_desktop_allowed")
            else "never driven by default"
        )
        + f" (policy: require_isolation; opt-in: {'on' if optin.get('opted_in') else 'off'})"
    )
    if not data.get("live_desktop_allowed"):
        print(f"  to opt in: {optin.get('reason', '')}")
    print("bridges:")
    for record in data.get("bridges", []):
        mark = "available" if record.get("available") else "UNAVAILABLE"
        real = _real_label(record)
        print(f"  {record.get('id'):<16} {mark:<12} {real:<9} {record.get('reason', '')}")
    print("capabilities:")
    for record in data.get("capabilities", []):
        mark = "available" if record.get("available") else "UNAVAILABLE"
        print(
            f"  {record.get('name'):<22} {mark:<12} {record.get('bridge', ''):<15} "
            f"{record.get('driver', '')}"
        )
        if not record.get("available") and record.get("missing_capability"):
            print(f"      missing: {record['missing_capability']}")


def cmd_run(args: argparse.Namespace) -> int:
    """`act run` — one declared action list, executed and verified against the real state."""
    payload, code = _read_json(getattr(args, "actions", None), what="action list")
    if payload is None:
        return code
    actions, end_state, goal, code = _actions_from(payload)
    if actions is None:
        return code
    if end_state is None and getattr(args, "end_state", None):
        end_state, code = _read_json(getattr(args, "end_state", None), what="end state")
        if end_state is None:
            return code
    if end_state is None and str(getattr(args, "expect_end_state", "") or "").strip():
        end_state = {
            "channel": "window_title",
            "description": "declared on the command line",
            "expect_observe_contains": str(args.expect_end_state),
        }

    record = S.run_action_list(
        actions,
        runner_kind=str(getattr(args, "runner", "auto") or "auto"),
        workdir=getattr(args, "workdir", None),
        end_state=end_state,
        goal=goal,
        runtime=getattr(args, "state", "") or None,
        timeout=float(getattr(args, "timeout", 90.0) or 90.0),
        session_id="act-run",
        allow_live_desktop=bool(getattr(args, "allow_live_desktop", False)),
        live_target=str(getattr(args, "live_target", "") or ""),
        screenshot=bool(getattr(args, "screenshot", False))
        or bool(getattr(args, "screenshot_dir", None)),
        screenshot_dir=getattr(args, "screenshot_dir", None),
    )
    return _finish_run(record, args, command="act.run")


def _finish_run(record: dict, args: argparse.Namespace, *, command: str) -> int:
    """Report one run record: envelope + evidence, human table when asked, exit code."""
    evidence = tuple(COMMAND_EVIDENCE[command])
    extra: list[dict] = []
    for action in record.get("actions") or []:
        extra.append(
            {
                "ref": f"action#{action.get('index')}:{action.get('kind')}",
                "kind": "run",
                "note": f"{action.get('status')}: {str(action.get('detail', ''))[:120]}",
            }
        )
    end = record.get("end_state") or {}
    if end.get("declared"):
        extra.append(
            {
                "ref": f"end_state:{end.get('channel', '')}",
                "kind": "verification",
                "note": (
                    "observed" if end.get("ok") else "NOT observed: "
                    + ", ".join(repr(item) for item in end.get("missing") or [])
                ),
            }
        )
    if record.get("record"):
        extra.append(
            {
                "ref": str(record["record"]),
                "kind": "file",
                "note": "the run record the panel view reads (cu_runs.jsonl)",
            }
        )
    shot = record.get("screenshot")
    if isinstance(shot, dict) and shot.get("path"):
        extra.append(
            {
                "ref": str(shot["path"]),
                "kind": "file",
                "note": (
                    f"end-state screenshot ({shot.get('width')}x{shot.get('height')}, "
                    f"{shot.get('size_bytes')} B, sha256 {str(shot.get('sha256'))[:16]}, "
                    f"named after {shot.get('label') or 'the window'})"
                ),
            }
        )
    cleanup = record.get("cleanup")
    if isinstance(cleanup, dict):
        extra.append(
            {
                "ref": str(cleanup.get("container") or cleanup.get("display") or "lifecycle"),
                "kind": "verification",
                "note": (
                    (
                        "closed by this run"
                        if cleanup.get("owned")
                        else str(cleanup.get("detail", ""))
                    )
                    + f"; still present: {bool(cleanup.get('still_present'))}"
                ),
            }
        )
    warnings = list(_scope_warning(record))
    _report(record, evidence=tuple(list(evidence) + extra), warnings=tuple(warnings))
    json_mode = _json_mode(args)
    if not json_mode:
        runner = dict(record.get("runner") or {})
        print(
            f"{record.get('status', ''):<20} verified={bool(record.get('verified'))} "
            f"runner={runner.get('bridge', '') or runner.get('isolation', '')} "
            f"({record.get('scope', '')})"
        )
        for action in record.get("actions") or []:
            print(
                f"  [{action.get('index')}] {action.get('kind'):<10} "
                f"{action.get('channel', ''):<7} {action.get('status', ''):<10} "
                f"{str(action.get('detail', ''))[:80]}"
            )
        if record.get("end_state"):
            print(f"  end state: {json.dumps(record['end_state'], ensure_ascii=False)}")
        if isinstance(shot, dict) and shot.get("path"):
            print(
                f"  screenshot: {shot['path']} "
                f"({shot.get('width')}x{shot.get('height')}, sha256 {str(shot.get('sha256'))[:16]})"
            )
        if isinstance(cleanup, dict):
            print(
                "  lifecycle: "
                + (
                    f"closed {cleanup.get('container') or cleanup.get('display') or 'the runner'}"
                    if cleanup.get("owned")
                    else str(cleanup.get("detail", ""))
                )
                + f" (still present: {bool(cleanup.get('still_present'))})"
            )
        print(f"  {record.get('reason', '')}")
        if record.get("missing_capability"):
            print(f"  missing capability: {record['missing_capability']}")
        if warnings:
            print(f"  warning: {warnings[0]}")
    code = _exit_code_for(record)
    if code == EXIT_OK:
        return EXIT_OK
    return _fail(
        record,
        code,
        str(record.get("reason", "the run did not verify")),
        hint=str(record.get("hint", "")),
        details={
            "status": record.get("status"),
            "missing_capability": record.get("missing_capability"),
            "missing_anchors": record.get("missing_anchors"),
        },
        evidence=tuple(list(evidence) + extra),
        next_command="skaldir act check --json" if code == EXIT_BLOCKED else "",
        reason=str(record.get("reason", "")),
    )


def cmd_subtool_save(args: argparse.Namespace) -> int:
    """`act subtool save` — validate and store a sub-tool declaration."""
    payload, code = _read_json(getattr(args, "file", None), what="sub-tool declaration")
    if payload is None:
        return code
    try:
        summary = S.save_subtool(
            payload,
            state=getattr(args, "state", "") or None,
            force=bool(getattr(args, "force", False)),
        )
    except ValueError as exc:
        return _fail(
            {"error": "invalid_declaration", "problems": str(exc)},
            EXIT_USAGE,
            str(exc),
            hint=(
                "a sub-tool is {name, version, description, applicability, preconditions, "
                "anchors, actions, end_state, verification_contract} — "
                "`skaldir act subtool show open_browser --json` is a working example"
            ),
        )
    except FileExistsError as exc:
        return _fail(
            {"error": "already_stored", "stored": str(exc)},
            EXIT_CONFLICT,
            str(exc),
            hint="pass --force to replace the stored sub-tool deliberately",
        )
    data = {
        "subtool": summary,
        "stored": summary.get("path", ""),
        "replaced": bool(getattr(args, "force", False)),
        "directory": str(S.subtool_dir(getattr(args, "state", "") or None)),
    }
    _report(data, evidence=COMMAND_EVIDENCE["act.subtool.save"])
    json_mode = _json_mode(args)
    if not json_mode:
        print(f"stored sub-tool {summary['name']}@{summary['version']} → {summary['path']}")
    return EXIT_OK


def cmd_subtool_list(args: argparse.Namespace) -> int:
    """`act subtool list` — saved sub-tools plus the five built-ins."""
    directory = S.subtool_dir(getattr(args, "state", "") or None)
    rows = S.list_subtools(state=getattr(args, "state", "") or None)
    data = {
        "subtools": rows,
        "count": len(rows),
        "directory": str(directory),
        "builtin": sorted(S.builtin_subtools()),
    }
    _report(data, evidence=COMMAND_EVIDENCE["act.subtool.list"])
    json_mode = _json_mode(args)
    if not json_mode:
        print(f"sub-tools in {directory}:")
        for row in rows:
            print(
                f"  {row.get('name', ''):<22} {row.get('origin', ''):<8} "
                f"{row.get('version', ''):<6} pre={row.get('preconditions', 0)} "
                f"anchors={row.get('anchors', 0)} actions={row.get('actions', 0)} "
                f"{'shadows builtin' if row.get('shadows_builtin') else ''}"
            )
    return EXIT_OK


def cmd_subtool_show(args: argparse.Namespace) -> int:
    """`act subtool show NAME` — the exact stored schema, no invented fields."""
    try:
        spec, origin, path = S.load_subtool(
            args.name, state=getattr(args, "state", "") or None
        )
    except (FileNotFoundError, ValueError) as exc:
        return _fail(
            {
                "name": str(getattr(args, "name", "")),
                "known": sorted(
                    set(S.builtin_subtools())
                    | {
                        row.get("name", "")
                        for row in S.list_subtools(state=getattr(args, "state", "") or None)
                    }
                ),
            },
            EXIT_NOT_FOUND,
            str(exc),
            hint="`skaldir act subtool list --json` names every sub-tool",
        )
    problems = S.subtool_errors(spec)
    summary = S.subtool_summary(spec, origin=origin, path=path)
    # The count and the list used to disagree: `subtool.actions` carried the COUNT while
    # the real action list lived only under `spec`. The list now lands where the count
    # was (the count keeps its own, honestly named key), so a consumer reading
    # `data.subtool.actions` gets the actions themselves. `spec` stays exactly as stored.
    summary["action_count"] = summary["actions"]
    summary["actions"] = list(spec.get("actions") or [])
    data = {
        "name": str(spec.get("name", "")),
        "origin": origin,
        "path": path,
        "spec": spec,
        "subtool": summary,
        "valid": not problems,
        "problems": problems,
    }
    _report(data, evidence=COMMAND_EVIDENCE["act.subtool.show"])
    json_mode = _json_mode(args)
    if not json_mode:
        print(f"{data['name']}@{spec.get('version', '')} ({origin}{', ' + path if path else ''})")
        print(f"  {spec.get('description', '')}")
        print(f"  preconditions: {json.dumps(spec.get('preconditions'), ensure_ascii=False)}")
        print(f"  anchors:       {json.dumps(spec.get('anchors'), ensure_ascii=False)}")
        for index, action in enumerate(spec.get("actions") or []):
            print(
                f"  action[{index}] {action.get('kind')} → "
                f"{json.dumps(action.get('verify'), ensure_ascii=False)}"
            )
        print(f"  end state:     {json.dumps(spec.get('end_state'), ensure_ascii=False)}")
        print(f"  verification:  {spec.get('verification_contract', '')}")
        if problems:
            print(f"  problems: {'; '.join(problems)}", file=sys.stderr)
    return EXIT_OK


def cmd_subtool_learn(args: argparse.Namespace) -> int:
    """`act subtool learn --session ID` — replay a recorded session, save a draft, run nothing."""
    learned = S.learn_subtool(
        args.session,
        runtime=getattr(args, "state", "") or None,
        state=getattr(args, "state", "") or None,
        name=str(getattr(args, "name", "") or ""),
        min_repeats=int(getattr(args, "min_repeats", 2) or 2),
        force=bool(getattr(args, "force", False)),
    )
    if learned.get("learned"):
        _report({"learned": True, **learned}, evidence=COMMAND_EVIDENCE["act.subtool.save"])
        if not _json_mode(args):
            summary = learned.get("subtool") or {}
            print(
                f"learned sub-tool {summary.get('name', '')}@{summary.get('version', '')} "
                f"→ {summary.get('path', '')} (pattern: {' → '.join(learned.get('pattern', []))})"
            )
        return EXIT_OK
    return _fail(
        learned,
        EXIT_NOT_FOUND,
        str(learned.get("reason", "")),
        hint=str(learned.get("hint", "") or ""),
    )


def cmd_subtool_run(args: argparse.Namespace) -> int:
    """`act subtool run NAME` — observe, check preconditions/anchors, act, check end state."""
    record = S.run_subtool(
        args.name,
        state=getattr(args, "state", "") or None,
        runner_kind=str(getattr(args, "runner", "auto") or "auto"),
        workdir=getattr(args, "workdir", None),
        app_version=str(getattr(args, "app_version", "") or ""),
        locale=str(getattr(args, "locale", "") or "en"),
        runtime=getattr(args, "state", "") or None,
        timeout=float(getattr(args, "timeout", 90.0) or 90.0),
        allow_live_desktop=bool(getattr(args, "allow_live_desktop", False)),
        live_target=str(getattr(args, "live_target", "") or ""),
    )
    if record.get("status") == "not_found":
        return _fail(
            record,
            EXIT_NOT_FOUND,
            str(record.get("reason", "")),
            hint="`skaldir act subtool list --json` names every sub-tool",
        )
    return _finish_run(record, args, command="act.subtool.run")


def cmd_live_setup(args: argparse.Namespace) -> int:
    """`act live-setup` — the guided live-desktop opt-in (measure, list, write one target)."""
    data = S.live_setup_report(
        target=str(getattr(args, "target", "") or ""),
        runtime=getattr(args, "state", "") or None,
        write=bool(getattr(args, "write", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    written = data.get("write") or {}
    if getattr(args, "write", False) and written and written.get("ok") is False:
        return _fail(
            data,
            EXIT_USAGE,
            str(written.get("reason", "")),
            hint="pass --target with one window id or exact title (see `windows` in the report)",
        )
    _report(data, evidence=COMMAND_EVIDENCE["act.live-setup"])
    if not _json_mode(args):
        optin = data.get("optin") or {}
        print(f"live-desktop opt-in: {'ALLOWED' if data.get('allowed_now') else 'off'}")
        if optin.get("target"):
            print(f"  target: {optin['target']!r} (via {optin.get('source', '')})")
        print(f"  reason: {optin.get('reason', '')}")
        if data.get("windows"):
            print(f"windows on {data.get('host', {}).get('display', '') or 'the session'}:")
            for row in data["windows"][:20]:
                print(f"  {row.get('id', ''):<12} {row.get('title', '')}")
        elif data.get("windows_reason"):
            print(f"windows: {data['windows_reason']}")
        for step in data.get("next_steps") or []:
            print(f"next: {step}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


def _add_act_verbs(sub: Any) -> Any:
    """Add the six ``act`` verbs to a subparsers handle (one definition)."""
    p_check = sub.add_parser(
        "check",
        help="what this host can really do: window/click/key/scroll capabilities, honestly",
        description=(
            "Reports every bridge (isolated container display, bwrap sandbox, in-process "
            "fixture) and every capability with its driver, its verification channel and — "
            "when it is unavailable — the precise missing piece. The live desktop is never "
            "driven: the Computer Use policy requires isolation."
        ),
    )
    p_check.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="NAME",
        help="exit 3 when this capability is unavailable here (repeatable)",
    )
    p_check.add_argument(
        "--probe-image",
        action="store_true",
        help="start the isolated display once and verify the driver tools inside the image",
    )
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser(
        "run",
        help="run a declared action list and verify the end state against the real state",
        description=(
            "Actions come from --actions FILE (or stdin, or '-'). Every action must declare "
            "how it is verified; the run is executed in an isolated runner and the end state "
            "is read from the real state afterwards. Exit 2 when an action or the end state "
            "is not verified, exit 3 when no real bridge can carry the run."
        ),
    )
    p_run.add_argument(
        "--actions",
        default="",
        help="JSON request file: {actions: [...], end_state: {...}, goal: '...'} or a bare list",
    )
    p_run.add_argument(
        "--end-state",
        default="",
        dest="end_state",
        help="JSON file with the end-state check (overrides the request's end_state)",
    )
    p_run.add_argument(
        "--expect-end-state",
        default="",
        dest="expect_end_state",
        help="shortcut: the string the end-state observation must contain",
    )
    p_run.add_argument(
        "--runner",
        default="auto",
        choices=[
            "auto",
            "container-gui",
            "container",
            "host-session",
            "live-desktop",
            "sandbox",
            "dry-run",
        ],
        help="which bridge to run on (auto = strongest real one; never the fixture)",
    )
    p_run.add_argument(
        "--screenshot",
        action="store_true",
        default=None,
        help=(
            "capture the end state from the runner and record its path + sha256 "
            "(named after the observed window); also implied by --screenshot-dir"
        ),
    )
    p_run.add_argument(
        "--screenshot-dir",
        default=None,
        help="where the end-state screenshots go (default: <runtime>/act-screenshots)",
    )
    p_run.add_argument(
        "--allow-live-desktop",
        action="store_true",
        help=(
            "opt in to driving a real host display — requires --live-target naming exactly "
            "one window; without both, the live desktop stays refused"
        ),
    )
    p_run.add_argument(
        "--live-target",
        default="",
        help=(
            "the one window (id or exact title) a live-desktop run may touch; never the "
            "whole desktop"
        ),
    )
    p_run.add_argument("--workdir", default="", help="working directory of the sandbox runner")
    p_run.add_argument("--timeout", type=float, default=90.0, help="per-action timeout in seconds")
    p_run.add_argument(
        "--state", default="", help="runtime directory (default: SKALDIR_HOME)"
    )
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_subtool = sub.add_parser(
        "subtool",
        help="GUI sub-tools as data: preconditions + UI anchors + actions + end-state check",
    )
    subtool_sub = p_subtool.add_subparsers(dest="act_subtool", required=True)

    p_save = subtool_sub.add_parser(
        "save", help="store a sub-tool declaration (validated, never silently overwritten)"
    )
    p_save.add_argument("--file", default="", help="sub-tool JSON file ('-' for stdin)")
    p_save.add_argument("--force", action="store_true", help="replace an existing sub-tool")
    p_save.add_argument("--state", default="", help="runtime directory (default: SKALDIR_HOME)")
    p_save.add_argument("--json", action="store_true")
    p_save.set_defaults(func=cmd_subtool_save)

    p_list = subtool_sub.add_parser("list", help="the saved sub-tools plus the built-ins")
    p_list.add_argument("--state", default="", help="runtime directory (default: SKALDIR_HOME)")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_subtool_list)

    p_show = subtool_sub.add_parser("show", help="one sub-tool exactly as stored")
    p_show.add_argument("name", help="sub-tool name")
    p_show.add_argument("--state", default="", help="runtime directory (default: SKALDIR_HOME)")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_subtool_show)

    p_learn = subtool_sub.add_parser(
        "learn",
        help="learn a draft sub-tool from one recorded session's repeated GUI sequence",
    )
    p_learn.add_argument("--session", required=True, help="the recorded session id to replay")
    p_learn.add_argument(
        "--name", default="", help="name for the learned draft (default: learned_<session>)"
    )
    p_learn.add_argument(
        "--min-repeats",
        type=int,
        default=2,
        help="how many times a sequence must repeat (default 2)",
    )
    p_learn.add_argument("--force", action="store_true", help="replace an existing learned draft")
    p_learn.add_argument("--state", default="", help="runtime directory (default: SKALDIR_HOME)")
    p_learn.add_argument("--json", action="store_true")
    p_learn.set_defaults(func=cmd_subtool_learn)

    p_subrun = subtool_sub.add_parser(
        "run",
        help="observe → check preconditions/anchors → act → check the end state",
    )
    p_subrun.add_argument("name", help="sub-tool name")
    p_subrun.add_argument(
        "--runner",
        default="auto",
        choices=[
            "auto",
            "container-gui",
            "container",
            "host-session",
            "live-desktop",
            "sandbox",
            "dry-run",
        ],
        help="which bridge to run on (auto = strongest real one; never the fixture)",
    )
    p_subrun.add_argument("--workdir", default="", help="working directory of the sandbox runner")
    p_subrun.add_argument(
        "--app-version",
        default="",
        dest="app_version",
        help="the target app's version (applicability is refused when unknown/inapplicable)",
    )
    p_subrun.add_argument("--locale", default="en", help="the target app's locale")
    p_subrun.add_argument("--timeout", type=float, default=90.0, help="per-action timeout")
    p_subrun.add_argument(
        "--allow-live-desktop",
        action="store_true",
        help=(
            "opt in to driving a real host display — requires --live-target naming exactly "
            "one window; without both, the live desktop stays refused"
        ),
    )
    p_subrun.add_argument(
        "--live-target",
        default="",
        help=(
            "the one window (id or exact title) a live-desktop run may touch; never the "
            "whole desktop"
        ),
    )
    p_subrun.add_argument("--state", default="", help="runtime directory (default: SKALDIR_HOME)")
    p_subrun.add_argument("--json", action="store_true")
    p_subrun.set_defaults(func=cmd_subtool_run)

    p_live = sub.add_parser(
        "live-setup",
        help="guided live-desktop opt-in: measure, list windows, write one named target",
        description=(
            "Reports the current opt-in state, the host tools and the windows really on "
            "screen. Only with --target <window> --write does it store "
            "computer_use.allow_live_desktop + live_desktop_target in settings.yaml "
            "(atomic, backed up). An opt-in without a target is never written."
        ),
    )
    p_live.add_argument(
        "--target",
        default="",
        help="one window id or exact title the live-desktop runs may touch",
    )
    p_live.add_argument(
        "--write",
        action="store_true",
        help="store the opt-in for --target in settings.yaml (without this: report only)",
    )
    p_live.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="compute the settings diff but write nothing",
    )
    p_live.add_argument("--state", default="", help="runtime directory (default: SKALDIR_HOME)")
    p_live.add_argument("--json", action="store_true")
    p_live.set_defaults(func=cmd_live_setup)

    for parser in (p_check, p_run, p_save, p_list, p_show, p_learn, p_subrun, p_live):
        share_common_flags(parser)
    return sub


def build_act_parser(subparsers: Any) -> Any:
    """Add the ``act`` command group to a CLI's ``subparsers`` handle and return it."""
    parser = subparsers.add_parser(
        "act",
        help="Computer Use: what this host can really do, declared runs, GUI sub-tools",
        description=(
            "The ACT layer's CLI surface. It drives isolated environments only (the "
            "container display ':90' or the bwrap sandbox) and verifies the end state "
            "against the real state — never against a claim."
        ),
    )
    sub = parser.add_subparsers(dest="act_command")
    _add_act_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent: called twice on the same handle it returns the existing ``act`` group
    rather than letting argparse raise ``conflicting subparser``. It also adds this group's
    ``cli_contract.COMMANDS`` entries, so a wired-in group never ships without a contract.
    """
    existing = getattr(subparsers, "choices", {}).get("act")
    if existing is not None:
        register_contract_entries()
        return existing
    parser = build_act_parser(subparsers)
    register_contract_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (``python -m skaldir.act_cli`` / tests).

    Same verbs as ``build_act_parser`` minus the wrapping ``act`` word, so
    ``main(["check", "--json"])`` and ``skaldir act check --json`` run the same handler.
    """
    parser = argparse.ArgumentParser(prog="skaldir act", description="Computer Use (ACT)")
    sub = parser.add_subparsers(dest="command")
    _add_act_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir act …`` — returns the exit code, never raises for a bad request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already chose a code
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


__all__ = [
    "COMMAND_EVIDENCE",
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_act_parser",
    "build_parser",
    "cmd_check",
    "cmd_live_setup",
    "cmd_run",
    "cmd_subtool_list",
    "cmd_subtool_run",
    "cmd_subtool_save",
    "cmd_subtool_show",
    "contract_entries",
    "main",
    "register_cli",
    "register_contract_entries",
]
