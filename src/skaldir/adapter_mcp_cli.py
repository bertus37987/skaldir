"""CLI surface of the MCP half of the self-expanding tool system.

The EXPAND layer has two implemented surfaces today: a **container image**
(``tools mine / adapter show / register / rollback / health``) and now an
**MCP server** — a container or a local process. This module is the CLI for the
MCP surface and nothing else; the pipeline itself is
:mod:`skaldir.adapter_mcp`:

    mcp probe …       start/connect → handshake → list the tools with their
                      JSON schemas (+ the raw transcript)
    mcp map …         each tool → a TYPED Skaldir capability; a tool whose
                      schema cannot be validated is REFUSED with its reason and
                      is never registered. The mappable ones are staged.
    mcp test …        call the chosen tool for REAL through the adapter and
                      record the evidence (bound to the adapter bytes)
    mcp register …    register the TESTED capability through the existing
                      registry path (version, health, permissions, rollback)
    mcp call …        call a registered MCP capability through its adapter

**Integration (the only change ``cli.py`` needs — 2 lines).** In
``cli.build_parser()``, directly after the ``mcp`` subparsers exist::

    p_mcp = sub.add_parser("mcp", help="MCP export of approved tools (fastmcp)")
    mcp_sub = p_mcp.add_subparsers(dest="subcommand", required=True)
    ...
    from .adapter_mcp_cli import register_cli as _register_mcp_adapter   # +
    _register_mcp_adapter(mcp_sub)                                       # +

``register_cli`` adds the five new verbs to the EXISTING ``mcp`` group (the
same pattern as ``toolbuilder_cli.register_cli(tools_sub)``) and returns
nothing; ``mcp serve`` keeps working. The five contract entries for the new
leaves belong in ``cli_contract.COMMANDS`` — they are exported here as
``EV_NEW_COMMANDS`` because that file is owned by the centre. Proven against the
real parser: with the snippet and the entries applied in a scratch copy,
``cli_contract.dump(cli.build_parser())`` reports all five leaves with
``registered: true``, layer ``EXPAND`` and these exit codes, and
``tests/test_cli_contract_dump.py`` stays green.

Exit codes carry the rules, not the prose: ``mcp test`` exits 2 when the real
call does not return the declared outputs; ``mcp register`` exits 2 when the
evidence is missing, failed or no longer covers the adapter bytes; a refused
tool / unreachable server / unsupported protocol exits 3 (blocked).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import adapter_mcp as A
from .cli_shared import dump, envelope_mode, runtime_dir, share_common_flags, text_mode

try:  # the product contract wins; this module also runs standalone
    from . import cli_contract as _contract
except Exception:  # noqa: BLE001 — an older build without the contract module
    _contract = None  # type: ignore[assignment]

EXIT_OK = int(getattr(_contract, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_contract, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_contract, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_contract, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_contract, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_contract, "EXIT_INTERNAL", 7))

#: A structured failure class → the CLI exit code. Nothing else maps a failure
#: onto a code, so no handler can invent a green.
FAILURE_EXIT = {
    # evidence problems: the capability exists but is NOT proven
    "test_failed": EXIT_VERIFICATION_FAILED,
    "unverified": EXIT_VERIFICATION_FAILED,
    "adapter_changed": EXIT_VERIFICATION_FAILED,
    "output_mismatch": EXIT_VERIFICATION_FAILED,
    "expectation_mismatch": EXIT_VERIFICATION_FAILED,
    # the payload does not satisfy the declared typed inputs (a capability boundary)
    "invalid_input": EXIT_BLOCKED,
    # unknown capability / no staged adapter
    "not_found": EXIT_NOT_FOUND,
    # nothing to map onto, or the surface could not be reached
    "command_required": EXIT_BLOCKED,
    "url_required": EXIT_BLOCKED,
    "transport_unsupported": EXIT_BLOCKED,
    "handshake_failed": EXIT_BLOCKED,
    "call_failed": EXIT_BLOCKED,
    "protocol_unsupported": EXIT_BLOCKED,
    "no_tools": EXIT_BLOCKED,
    "nothing_mappable": EXIT_BLOCKED,
    "adapter_unreadable": EXIT_BLOCKED,
    "name_required": EXIT_USAGE,
    "stage_failed": EXIT_INTERNAL,
}

EV_NEW_COMMANDS = (
    # only LEAVES need a contract entry (cli_contract.dump lists leaves)
    "mcp.probe: layer EXPAND, exit_codes=(0, 3, 7), "
    "notes='handshake + tools/list with the raw transcript; nothing is written', "
    "evidence=(src/skaldir/adapter_mcp.py handshake, "
    "src/skaldir/adapter_mcp.py probe_server)",
    "mcp.map: layer EXPAND, mutates=True, exit_codes=(0, 1, 3, 7), "
    "notes='a tool whose JSON schema cannot be typed is refused with its reason, "
    "never registered; --stage writes the adapter/module/wrapper/manifest'",
    "mcp.test: layer EXPAND, mutates=True, exit_codes=(0, 2, 3, 5, 7), "
    "notes='exit 2 = the real call did not return the declared outputs (or missed "
    "--expect); the evidence binds to the adapter bytes'",
    "mcp.register: layer EXPAND, mutates=True, exit_codes=(0, 2, 3, 5, 7), "
    "notes='exit 2 = the adapter is not (still) proven: no registration'",
    "mcp.call: layer EXPAND, exit_codes=(0, 2, 3, 5, 7), "
    "notes='calls the registered capability through its adapter (--via wrapper "
    "runs the generated CLI wrapper)'",
)


# --------------------------------------------------------------------------- #
# envelope plumbing                                                             #
# --------------------------------------------------------------------------- #


def _report(
    payload: Any,
    *,
    evidence: tuple[tuple[str, str, str], ...] = (),
    warnings: tuple[str, ...] = (),
    next_actions: tuple[tuple[str, str], ...] = (),
) -> None:
    """Hand the payload and its evidence to the shared envelope (when wired).

    The warnings are ALSO put into the payload: the contract's ``warn()`` only
    records while the product driver runs, so a standalone call would otherwise
    lose them.
    """
    if isinstance(payload, dict) and warnings:
        payload.setdefault("warnings", list(warnings))
    if _contract is None:
        return
    try:
        _contract.set_data(payload)
        for ref, kind, note in evidence:
            _contract.add_evidence(str(ref), str(kind), str(note))
        for text in warnings:
            _contract.warn(str(text))
        for command, reason in next_actions:
            _contract.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _fail(
    code: int, message: str, *, hint: str = "", details: dict | None = None, retryable: bool = False
) -> int:
    """Exit through the shared helper when present, else by hand."""
    if _contract is not None and hasattr(_contract, "fail"):
        return int(_contract.fail(code, message, hint=hint, retryable=retryable, details=details))
    print(message, file=sys.stderr)
    return int(code)


def _exit_for(result: dict, *, default: int = EXIT_BLOCKED) -> int:
    """The exit code of a structured failure — never a success for a failure."""
    failure_class = str(result.get("failure_class") or "")
    return FAILURE_EXIT.get(failure_class, default)


def _usage(
    args: argparse.Namespace, message: str, *, hint: str = "", extra: dict | None = None
) -> int:
    """A usage error that still prints exactly one document (never an empty stdout)."""
    payload: dict = {"ok": False, "failure_class": "usage", "reason": message}
    payload.update(extra or {})
    _report(payload, warnings=(message,))
    _emit(args, payload, warn=message)
    return _fail(EXIT_USAGE, message, hint=hint)


def _emit(args: argparse.Namespace, payload: dict, *, warn: str = "") -> None:
    if warn:
        print(warn, file=sys.stderr)
    if text_mode(args):
        _print_text(payload)
        return
    if not envelope_mode():
        dump(payload)


# --------------------------------------------------------------------------- #
# shared arguments                                                              #
# --------------------------------------------------------------------------- #


def _server_spec(args: argparse.Namespace) -> A.MCPServerSpec:
    """The server to talk to: ``--command … [--arg …]`` or ``--url …``."""
    return A.MCPServerSpec.from_strings(
        command=str(getattr(args, "command", "") or ""),
        args=list(getattr(args, "arg", None) or []),
        url=str(getattr(args, "url", "") or ""),
        name=str(getattr(args, "name", "") or ""),
        env=list(getattr(args, "env", None) or []),
        headers=list(getattr(args, "header", None) or []),
        timeout=float(getattr(args, "timeout", A.DEFAULT_TIMEOUT) or A.DEFAULT_TIMEOUT),
    )


def _json_arg(args: argparse.Namespace, flag: str) -> tuple[dict, str]:
    """``(payload, error)`` for a JSON object argument."""
    attribute = flag.lstrip("-").replace("-", "_")
    raw = str(getattr(args, attribute, "") or "").strip()
    if not raw:
        return {}, ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"{flag} is not JSON: {exc}"
    if not isinstance(parsed, dict):
        return {}, f"{flag} must be a JSON object, got {type(parsed).__name__}"
    return parsed, ""


def _add_server_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--command", default="", help='the MCP server command, e.g. "python -m my.server"'
    )
    parser.add_argument(
        "--arg", action="append", default=[], help="one extra argv for --command (repeatable)"
    )
    parser.add_argument(
        "--url", default="", help="a streamable-HTTP MCP endpoint (instead of --command)"
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="K=V",
        help="environment for the stdio server (repeatable)",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="K=V",
        help="HTTP header for the endpoint (repeatable)",
    )
    parser.add_argument("--name", default="", help="a short name for this server")


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_probe(args: argparse.Namespace) -> int:
    """Start/connect → handshake → list tools (+ the raw transcript)."""
    spec = _server_spec(args)
    report = A.probe_server(
        spec,
        protocol_version=str(
            getattr(args, "protocol", A.DEFAULT_PROTOCOL_VERSION) or A.DEFAULT_PROTOCOL_VERSION
        ),
        allow_unknown_protocol=bool(getattr(args, "allow_unknown_protocol", False)),
    )
    payload = {
        "ok": bool(report.get("ok")),
        "server": report.get("server"),
        "transport": report.get("transport") or spec.transport,
        "protocol_version": report.get("protocol_version", ""),
        "protocol_supported": report.get("protocol_supported", False),
        "server_info": report.get("server_info", {}),
        "server_capabilities": report.get("server_capabilities", {}),
        "tools_count": report.get("tools_count", 0),
        "tools": report.get("summary", {}).get("tools", []),
        "refused_schema": [
            item["name"]
            for item in report.get("summary", {}).get("tools", [])
            if not item.get("typed")
        ],
        "transcript": report.get("transcript", [])
        if bool(getattr(args, "transcript", False))
        else [
            {
                "seq": entry.get("seq"),
                "direction": entry.get("direction"),
                "kind": entry.get("kind"),
                "method": (entry.get("message") or {}).get("method") or entry.get("kind"),
            }
            for entry in report.get("transcript", [])
        ],
        "failure_class": report.get("failure_class", ""),
        "reason": report.get("reason", ""),
        "stderr": str(report.get("stderr") or "")[:400],
        "duration_ms": report.get("duration_ms", 0),
    }
    evidence: list[tuple[str, str, str]] = [
        (
            "src/skaldir/adapter_mcp.py handshake",
            "code",
            "the MCP handshake and the tools/list it drives",
        ),
    ]
    if report.get("ok"):
        evidence.append(
            (
                f"mcp:{spec.name} initialize+notifications/initialized+tools/list",
                "run",
                f"{report.get('tools_count')} tool(s) listed over {payload['transport']}, "
                f"protocol {payload['protocol_version']}",
            )
        )
    else:
        evidence.append(
            (
                f"mcp:{spec.name}",
                "run",
                f"handshake refused: {report.get('failure_class')} — "
                f"{str(report.get('reason'))[:200]}",
            )
        )
    warnings: list[str] = []
    if payload["refused_schema"]:
        warnings.append(
            "tool schema(s) that cannot be typed and will be refused by `mcp map`: "
            + ", ".join(payload["refused_schema"])
        )
    _report(
        payload,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
        next_actions=(
            ("skaldir mcp map --command … --all", "map the listed tools onto typed capabilities"),
        ),
    )
    _emit(args, payload)
    if report.get("ok"):
        return EXIT_OK
    message = (
        f"the MCP surface could not be reached: {report.get('failure_class')} — "
        f"{report.get('reason')}"
    )
    return _fail(
        _exit_for(report),
        message,
        hint="check --command/--url; a stdio server is started as a child process",
        details={
            "failure_class": report.get("failure_class", ""),
            "stderr": str(report.get("stderr") or "")[:200],
        },
    )


def cmd_map(args: argparse.Namespace) -> int:
    """Map the listed tools onto typed capabilities; refuse the untypable one."""
    spec = _server_spec(args)
    report = A.probe_server(
        spec,
        protocol_version=str(
            getattr(args, "protocol", A.DEFAULT_PROTOCOL_VERSION) or A.DEFAULT_PROTOCOL_VERSION
        ),
        allow_unknown_protocol=bool(getattr(args, "allow_unknown_protocol", False)),
    )
    if not report.get("ok"):
        payload = {
            "ok": False,
            "server": report.get("server"),
            "failure_class": report.get("failure_class", ""),
            "reason": report.get("reason", ""),
            "mapped": [],
            "refused": [],
            "staged": [],
        }
        _report(
            payload,
            evidence=(
                (
                    "src/skaldir/adapter_mcp.py handshake",
                    "code",
                    "the handshake that had to succeed before mapping",
                ),
            ),
            warnings=(str(report.get("reason") or ""),),
        )
        _emit(args, payload)
        return _fail(
            _exit_for(report),
            f"nothing to map: {report.get('failure_class')} — {report.get('reason')}",
        )

    wanted = list(getattr(args, "tool", None) or [])
    mapped = A.map_surface(
        report,
        capability=str(getattr(args, "capability", "") or ""),
        tools=wanted,
        name=str(getattr(args, "adapter_name", "") or ""),
        timeout=float(getattr(args, "timeout", A.DEFAULT_TIMEOUT) or A.DEFAULT_TIMEOUT),
    )
    runtime = runtime_dir(args)
    staged: list[dict] = []
    stage = not bool(getattr(args, "no_stage", False))
    if stage:
        for adapter in mapped["mapped"]:
            result = A.stage_mcp_tool(adapter, runtime=runtime, report=report)
            staged.append(
                {
                    "name": result.get("name", adapter.get("name", "")),
                    "tool": adapter.get("tool", ""),
                    "ok": bool(result.get("ok")),
                    "tool_dir": result.get("tool_dir", ""),
                    "adapter_path": result.get("adapter_path", ""),
                    "adapter_digest": result.get("adapter_digest", ""),
                    "reason": result.get("reason", ""),
                }
            )

    payload = {
        "ok": bool(mapped["mapped"]),
        "server": report.get("server"),
        "transport": report.get("transport") or spec.transport,
        "protocol_version": report.get("protocol_version", ""),
        "tools_count": mapped.get("tools_count", 0),
        "mapped": [
            {
                "name": adapter["name"],
                "tool": adapter["tool"],
                "capability": adapter["capability"],
                "inputs": adapter["operation"]["inputs"],
                "outputs": adapter["operation"]["outputs"],
                "output_mode": adapter["operation"]["output_mode"],
            }
            for adapter in mapped["mapped"]
        ],
        "refused": mapped.get("refused", []),
        "staged": staged,
        "runtime": str(runtime),
        "failure_class": mapped.get("failure_class", ""),
        "reason": mapped.get("reason", ""),
    }
    evidence: list[tuple[str, str, str]] = [
        (
            "src/skaldir/adapter_mcp.py map_tool_to_capability",
            "code",
            "every tool is typed from the server's own JSON schemas",
        ),
    ]
    for item in staged:
        if item["ok"]:
            evidence.append(
                (
                    item["adapter_path"],
                    "file",
                    f"staged typed adapter for '{item['name']}' "
                    f"(tool '{item.get('tool')}', digest "
                    f"{str(item['adapter_digest'])[:16]}; not registered yet)",
                )
            )
    if not staged and mapped["mapped"]:
        evidence.append(
            (
                "src/skaldir/adapter_mcp.py map_surface",
                "code",
                "staging was skipped on request (--no-stage)",
            )
        )
    warnings: list[str] = []
    for refused in mapped.get("refused", []):
        warnings.append(f"refused '{refused.get('tool')}': {refused.get('reason')}")
    _report(
        payload,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
        next_actions=(
            (
                "skaldir mcp test <name> --input '{…}' --expect '{…}'",
                "prove the staged capability with a real call",
            ),
        ),
    )
    _emit(args, payload)
    if mapped["mapped"]:
        return EXIT_OK
    if mapped.get("failure_class") == "nothing_mappable":
        message = (
            "no listed tool mapped onto a typed capability: "
            + "; ".join(
                f"{item.get('tool')} ({item.get('reason')})" for item in mapped.get("refused", [])
            )[:400]
        )
        return _fail(
            EXIT_BLOCKED, message, hint="the surface offers no tool with a typable input schema"
        )
    return _fail(_exit_for(mapped), str(mapped.get("reason") or "nothing was mapped"))


def cmd_test(args: argparse.Namespace) -> int:
    """Call the staged tool for REAL and record the evidence."""
    name = str(getattr(args, "name", "") or "").strip()
    if not name:
        return _usage(
            args, "mcp test needs a capability name", hint="skaldir mcp map --command … --all"
        )
    payload_arg, error = _json_arg(args, "--input")
    if error:
        return _usage(args, error, hint='--input \'{"text": "hello"}\'', extra={"name": name})
    expect, error = _json_arg(args, "--expect")
    if error:
        return _usage(args, error, hint='--expect \'{"echo": "hello"}\'', extra={"name": name})

    runtime = runtime_dir(args)
    evidence = A.test_mapped_tool(
        runtime,
        name,
        payload=payload_arg,
        expect=expect or None,
        timeout=float(getattr(args, "timeout", 0) or 0) or None,
    )
    payload = {
        "ok": bool(evidence.get("ok")),
        "name": name,
        "tool": evidence.get("tool", ""),
        "transport": evidence.get("transport", ""),
        "input": evidence.get("input", {}),
        "expected": evidence.get("expected", {}),
        "observed": evidence.get("observed", {}),
        "match": evidence.get("match", False),
        "difference": evidence.get("difference", ""),
        "oracle": evidence.get("oracle", ""),
        "call": evidence.get("seen_at", {}),
        "evidence_path": evidence.get("evidence_path", ""),
        "adapter_digest": evidence.get("adapter_digest", ""),
        "duration_ms": evidence.get("duration_ms", 0),
        "failure_class": evidence.get("failure_class", ""),
        "reason": evidence.get("reason", ""),
    }
    if not evidence.get("ok") and evidence.get("failure_class") == "not_found":
        _report(
            payload,
            evidence=(
                (
                    "src/skaldir/adapter_mcp.py load_mapped_tool",
                    "code",
                    "the staged adapter that was searched for",
                ),
            ),
            warnings=(str(evidence.get("reason") or ""),),
        )
        _emit(args, payload)
        return _fail(
            EXIT_NOT_FOUND,
            str(evidence.get("reason")),
            hint="`skaldir mcp map --command … --tool <name>` stages one first",
        )
    items: list[tuple[str, str, str]] = [
        (
            str(evidence.get("adapter_file") or name),
            "file",
            f"the adapter that was called (digest {str(evidence.get('adapter_digest'))[:16]})",
        ),
        (
            "mcp:tools/call",
            "run",
            f"real call of '{evidence.get('tool')}' over {evidence.get('transport')}: "
            f"{json.dumps(evidence.get('observed'), sort_keys=True, default=str)}",
        ),
    ]
    if evidence.get("evidence_path"):
        items.append(
            (
                str(evidence["evidence_path"]),
                "verification",
                "the recorded evidence (bound to the adapter bytes)",
            )
        )
    warnings: list[str] = []
    if not evidence.get("ok"):
        warnings.append(str(evidence.get("reason") or evidence.get("difference") or ""))
    _report(
        payload,
        evidence=tuple(items),
        warnings=tuple(warnings),
        next_actions=((f"skaldir mcp register {name}", "register the proven capability"),),
    )
    _emit(args, payload)
    if evidence.get("ok"):
        return EXIT_OK
    return _fail(
        _exit_for(evidence, default=EXIT_BLOCKED),
        f"the real call did not prove '{name}': "
        f"{evidence.get('reason') or evidence.get('difference') or 'no detail'}"[:400],
        hint="fix the input/expectation and re-run `mcp test`; nothing is registered",
        details={
            "input": evidence.get("input"),
            "expected": evidence.get("expected"),
            "observed": evidence.get("observed"),
        },
    )


def cmd_register(args: argparse.Namespace) -> int:
    """Register the TESTED capability through the existing registry path."""
    name = str(getattr(args, "name", "") or "").strip()
    if not name:
        return _usage(args, "mcp register needs a capability name")
    from .registry import resolve_registry
    from .tools import ToolStore

    runtime = runtime_dir(args)
    context = resolve_registry(
        runtime=str(runtime),
        project=str(getattr(args, "project", "") or "") or None,
        db=str(getattr(args, "db", "") or "") or None,
    )
    Path(context.tools_db).parent.mkdir(parents=True, exist_ok=True)
    store = ToolStore(str(context.tools_db))
    try:
        result = A.register_mapped_tool(
            runtime,
            name,
            store,
            version=str(getattr(args, "version", "") or ""),
            project_id=str(getattr(args, "project_id", "") or "") or context.project_id,
            requested_by=str(getattr(args, "requested_by", "") or "cli"),
        )
    finally:
        store.close()
    payload = {
        "ok": bool(result.get("ok")),
        "registered": bool(result.get("registered")),
        "name": result.get("name", name),
        "version": result.get("version", ""),
        "status": result.get("status", ""),
        "kind": result.get("kind", A.MCP_KIND),
        "tool": result.get("tool", ""),
        "server": result.get("server", {}),
        "permissions": result.get("permissions", {}),
        "health": result.get("health", {}),
        "health_state": result.get("health_state", ""),
        "rollback_id": result.get("rollback_id", ""),
        "publish": result.get("publish", {}),
        "manifest": result.get("manifest", ""),
        "module": result.get("module", ""),
        "wrapper": result.get("wrapper", ""),
        "registry_db": str(context.tools_db),
        "test": result.get("test", {}),
        "failure_class": result.get("failure_class", ""),
        "reason": result.get("reason", ""),
    }
    warnings = list(result.get("warnings") or [])
    if result.get("ok"):
        evidence = [
            (
                str(result.get("manifest") or name),
                "file",
                "the tool manifest written for the registered capability",
            ),
            (
                f"{context.tools_db}::{name}@{result.get('version')}",
                "verification",
                "registry row + publication outbox entry in one transaction",
            ),
            (
                f"mcp:health:{name}",
                "verification",
                f"health ledger state {result.get('health_state')} after the real call",
            ),
        ]
    else:
        evidence = [
            (
                "src/skaldir/adapter_mcp.py register_mapped_tool",
                "code",
                "the evidence check that refused the registration",
            )
        ]
        warnings.append(str(result.get("reason") or ""))
    _report(
        payload,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
        next_actions=(
            (
                f"skaldir mcp call {name} --input '{{…}}'",
                "call the registered capability through its adapter",
            ),
        ),
    )
    _emit(args, payload)
    if result.get("ok"):
        return EXIT_OK
    return _fail(
        _exit_for(result, default=EXIT_BLOCKED),
        f"'{name}' was not registered: {result.get('reason')}"[:400],
        hint="a capability is only registered on evidence — `mcp test` first",
        details={"failure_class": result.get("failure_class", "")},
    )


def cmd_call(args: argparse.Namespace) -> int:
    """Call a registered MCP capability through its adapter."""
    name = str(getattr(args, "name", "") or "").strip()
    if not name:
        return _usage(args, "mcp call needs a capability name")
    payload_arg, error = _json_arg(args, "--input")
    if error:
        return _usage(args, error, hint="--input '{\"a\": 2}'", extra={"name": name})
    runtime = runtime_dir(args)
    loaded = A.load_mapped_tool(runtime, name)
    if not loaded["ok"]:
        payload = {
            "ok": False,
            "name": name,
            "output": {},
            "failure_class": loaded.get("failure_class", ""),
            "reason": loaded.get("reason", ""),
        }
        _report(
            payload,
            evidence=(
                (
                    "src/skaldir/adapter_mcp.py load_mapped_tool",
                    "code",
                    "the staged adapter that was searched for",
                ),
            ),
            warnings=(str(loaded.get("reason") or ""),),
        )
        _emit(args, payload)
        return _fail(
            EXIT_NOT_FOUND,
            str(loaded.get("reason")),
            hint="`skaldir mcp map --command … --tool <name>` stages one first",
        )

    via = str(getattr(args, "via", "adapter") or "adapter")
    if via == "wrapper":
        result = _call_via_wrapper(loaded, payload_arg)
    else:
        result = A.call_mapped_tool(
            loaded["adapter"],
            payload_arg,
            timeout=float(getattr(args, "timeout", 0) or 0) or None,
        )
    payload = {
        "ok": bool(result.get("ok")),
        "name": name,
        "tool": result.get("tool", ""),
        "via": via,
        "transport": result.get("transport")
        or loaded["adapter"].get("server", {}).get("transport", ""),
        "input": result.get("input", {}),
        "output": result.get("output", {}),
        "raw": result.get("raw", {}),
        "duration_ms": result.get("duration_ms", 0),
        "failure_class": result.get("failure_class", ""),
        "reason": result.get("reason", ""),
    }
    evidence = [
        (str(loaded["paths"]["adapter"]), "file", "the typed adapter the call went through"),
        (
            "mcp:tools/call",
            "run",
            f"'{result.get('tool')}' answered "
            f"{json.dumps(result.get('output'), sort_keys=True, default=str)}"
            f" via {via}",
        ),
    ]
    warnings = [] if result.get("ok") else [str(result.get("reason") or "")]
    _report(payload, evidence=tuple(evidence), warnings=tuple(warnings))
    _emit(args, payload)
    if result.get("ok"):
        return EXIT_OK
    return _fail(
        _exit_for(result, default=EXIT_BLOCKED),
        f"the call of '{name}' did not return the declared outputs: {result.get('reason')}"[:400],
    )


def _call_via_wrapper(loaded: dict, payload_arg: dict) -> dict:
    """Call through the GENERATED wrapper script (the shell-facing path)."""
    import subprocess

    wrapper = Path(loaded["paths"]["wrapper"])
    if not wrapper.is_file():
        return {
            "ok": False,
            "failure_class": "not_found",
            "reason": f"no wrapper script at {wrapper}",
        }
    try:
        proc = subprocess.run(  # noqa: S603 — our own generated wrapper, fixed argv
            [str(wrapper), json.dumps(payload_arg, default=str)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "failure_class": "call_failed",
            "reason": f"the wrapper did not run: {exc}",
        }
    if proc.returncode != 0:
        return {
            "ok": False,
            "failure_class": "call_failed",
            "reason": f"the wrapper exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}",
            "output": {},
        }
    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "failure_class": "output_mismatch",
            "reason": f"the wrapper printed something that is not JSON: {proc.stdout[:200]}",
        }
    return {
        "ok": True,
        "tool": loaded["adapter"].get("tool", ""),
        "input": dict(payload_arg),
        "output": output,
        "raw": {"stdout": proc.stdout.strip()},
        "wrapper": str(wrapper),
    }


def _print_text(payload: dict) -> None:
    """The human view (machine-first: text only on explicit request)."""
    if "tools" in payload and "tools_count" in payload:
        info = payload.get("server_info") or {}
        print(
            f"{payload.get('transport')}  {info.get('name')} {info.get('version')}  "
            f"protocol {payload.get('protocol_version')}  {payload.get('tools_count')} tool(s)"
        )
        for item in payload.get("tools") or []:
            flag = "typed" if item.get("typed") else "REFUSED"
            print(
                f"  {flag:<8} {item.get('name'):<20} "
                f"in={','.join(item.get('inputs') or []) or '-'} "
                f"out={','.join(item.get('outputs') or []) or '-'}"
            )
        return
    if "observed" in payload:
        print(
            f"{payload.get('name')}: {'PASS' if payload.get('ok') else 'FAIL'}  "
            f"observed={json.dumps(payload.get('observed'))} "
            f"expected={json.dumps(payload.get('expected'))}"
        )
        return
    if "mapped" in payload:
        print(
            f"{payload.get('mapped') and len(payload['mapped'])} mapped, "
            f"{len(payload.get('refused') or [])} refused"
        )
        for item in payload.get("mapped") or []:
            print(f"  mapped  {item.get('name')} <- {item.get('tool')}")
        for item in payload.get("refused") or []:
            print(f"  refused {item.get('tool')}: {str(item.get('reason'))[:100]}")
        return
    if "output" in payload:
        print(f"{payload.get('name')}: {json.dumps(payload.get('output'), sort_keys=True)}")
        return
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


def _add_mcp_verbs(sub: Any) -> Any:
    """Add the five MCP-surface verbs to a subparsers handle (one definition)."""
    p_probe = sub.add_parser(
        "probe", help="handshake with an MCP server and list its tools (+ transcript)"
    )
    _add_server_flags(p_probe)
    p_probe.add_argument(
        "--protocol",
        default=A.DEFAULT_PROTOCOL_VERSION,
        help=f"protocol version to request (default {A.DEFAULT_PROTOCOL_VERSION})",
    )
    p_probe.add_argument(
        "--allow-unknown-protocol",
        action="store_true",
        help="continue even when the server negotiates an unknown version",
    )
    p_probe.add_argument(
        "--transcript",
        action="store_true",
        help="print every raw JSON-RPC message of the handshake",
    )
    p_probe.add_argument("--json", action="store_true")
    p_probe.add_argument("--text", action="store_true")
    p_probe.set_defaults(func=cmd_probe, command_path="mcp.probe")

    p_map = sub.add_parser(
        "map", help="map the listed tools onto TYPED capabilities (refusals are named)"
    )
    _add_server_flags(p_map)
    p_map.add_argument(
        "--tool",
        action="append",
        default=[],
        help="map only this tool (repeatable; default: all of them)",
    )
    p_map.add_argument(
        "--capability", default="", help="the capability text recorded on the adapter"
    )
    p_map.add_argument(
        "--adapter-name", default="", help="registry name for a single --tool mapping"
    )
    p_map.add_argument(
        "--all", action="store_true", help="explicitly map every listed tool (the default)"
    )
    p_map.add_argument(
        "--no-stage", action="store_true", help="show the mapping without writing anything to disk"
    )
    p_map.add_argument("--protocol", default=A.DEFAULT_PROTOCOL_VERSION)
    p_map.add_argument("--allow-unknown-protocol", action="store_true")
    p_map.add_argument("--timeout", type=float, default=A.DEFAULT_TIMEOUT)
    p_map.add_argument("--runtime", default="", help="runtime dir (default: $SKALDIR_HOME)")
    p_map.add_argument("--json", action="store_true")
    p_map.add_argument("--text", action="store_true")
    p_map.set_defaults(func=cmd_map, command_path="mcp.map")

    p_test = sub.add_parser(
        "test", help="call a staged capability for REAL and record the evidence"
    )
    p_test.add_argument("name")
    p_test.add_argument("--input", default="{}", help="the call payload as JSON")
    p_test.add_argument("--expect", default="", help="the expected outputs as JSON")
    p_test.add_argument("--timeout", type=float, default=0.0)
    p_test.add_argument("--runtime", default="")
    p_test.add_argument("--json", action="store_true")
    p_test.add_argument("--text", action="store_true")
    p_test.set_defaults(func=cmd_test, command_path="mcp.test")

    p_register = sub.add_parser(
        "register", help="register a TESTED MCP capability live (evidence or nothing)"
    )
    p_register.add_argument("name")
    p_register.add_argument(
        "--version",
        default="",
        help="explicit version (default: 0.1.0; re-registering bumps the patch)",
    )
    p_register.add_argument("--project-id", default="", help="project id recorded in the manifest")
    p_register.add_argument("--project", default="", help="project context for the registry")
    p_register.add_argument("--requested-by", default="cli")
    p_register.add_argument("--runtime", default="")
    p_register.add_argument("--db", default="", help="explicit tools DB override")
    p_register.add_argument("--json", action="store_true")
    p_register.add_argument("--text", action="store_true")
    p_register.set_defaults(func=cmd_register, command_path="mcp.register")

    p_call = sub.add_parser("call", help="call a registered MCP capability through its adapter")
    p_call.add_argument("name")
    p_call.add_argument("--input", default="{}", help="the call payload as JSON")
    p_call.add_argument(
        "--via",
        choices=("adapter", "wrapper"),
        default="adapter",
        help="adapter = the typed call path; wrapper = the generated CLI script",
    )
    p_call.add_argument("--timeout", type=float, default=0.0)
    p_call.add_argument("--runtime", default="")
    p_call.add_argument("--json", action="store_true")
    p_call.add_argument("--text", action="store_true")
    p_call.set_defaults(func=cmd_call, command_path="mcp.call")

    for parser in (p_probe, p_map, p_test, p_register, p_call):
        share_common_flags(parser)
    return sub


def register_cli(subparsers: Any) -> None:
    """Add the MCP-surface verbs to an EXISTING ``mcp`` group's subparsers handle.

    Called from ``cli.build_parser()`` right after ``mcp_sub`` exists (the same
    pattern as ``toolbuilder_cli.register_cli(tools_sub)``); it touches no other
    parser and returns nothing.
    """
    _add_mcp_verbs(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(
        prog="skaldir mcp", description="MCP surface adapter (handshake, map, register)"
    )
    sub = parser.add_subparsers(dest="mcp_command", required=True)
    _add_mcp_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir mcp …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(func(args))
    except (A.MCPError, A.MCPRefusal) as exc:
        # Safety net: an unexpected refusal must still produce exactly one
        # document on stdout (the contract's "stdout carries the envelope and
        # nothing else"), never an empty answer.
        payload = {
            "ok": False,
            "command": str(getattr(args, "command_path", "")),
            "failure_class": type(exc).__name__,
            "reason": str(exc)[:400],
        }
        _report(payload, warnings=(str(exc)[:400],))
        _emit(args, payload, warn=str(exc)[:400])
        return _fail(EXIT_BLOCKED, str(exc)[:400])


__all__ = [
    "EV_NEW_COMMANDS",
    "FAILURE_EXIT",
    "build_parser",
    "cmd_call",
    "cmd_map",
    "cmd_probe",
    "cmd_register",
    "cmd_test",
    "main",
    "register_cli",
]


if __name__ == "__main__":  # pragma: no cover — `python -m skaldir.adapter_mcp_cli`
    raise SystemExit(main())
