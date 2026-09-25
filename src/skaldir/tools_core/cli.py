"""``skaldir tools-core …`` — the CLI surface of the built-in (core) toolset.

Verbs::

    tools-core list          the core tools with kind, permissions, timeout, truncation
    tools-core show NAME     one tool's full typed contract (schemas + declarations)
    tools-core register      publish the toolset into the runtime registry (idempotent)
    tools-core call NAME     run one tool for real and return its typed result
    tools-core smoke         run every tool's declared smoke case and record health
    tools-core health        the capability-health view of the core tools
    tools-core policy        the declared surface of the whole toolset
    tools-core unregister    take the toolset back out (descriptor + manifest + retire)

Exit codes are the product CLI's vocabulary (``cli_contract``): ``0`` ok ·
``1`` usage · ``2`` verification failed (a smoke check really failed) · ``3``
blocked (a permission refusal, an offline context, every smoke skipped) · ``5``
unknown tool / not registered · ``6`` a registry name conflict · ``7`` internal ·
``8`` a timeout or a network failure. Those are the *tool layer's* failures; a
command that exits non-zero is a successful observation and keeps exit ``0``
(``--fail-on-nonzero`` turns the child's status into this command's status).

**Integration — the only change ``cli.py`` needs (3 lines).** In
``cli.build_parser()``, right after ``sub = parser.add_subparsers(...)``::

    from .tools_core.cli import register_cli as _register_tools_core   # +
    _register_tools_core(sub)                                          # +

(The third line is the ``COMMANDS`` half: ``register_cli`` installs its own
contract entries through ``cli_shared.install_contract_entries`` — the same
mechanism ``act_cli``/``host_cli``/``project_context_cli`` use — so nothing has
to be pasted into ``cli_contract.py``. :func:`proposed_contract_entries` returns
the identical objects for a reviewer who prefers the literal table.)

The eight entries it installs::

    tools-core.list, tools-core.show, tools-core.register, tools-core.call,
    tools-core.smoke, tools-core.health, tools-core.policy, tools-core.unregister
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import registry
from .contract import (
    ERROR_EXIT,
    EXIT_BLOCKED,
    EXIT_CONFLICT,
    EXIT_INTERNAL,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_UNKNOWN,
    EXIT_USAGE,
    EXIT_VERIFICATION_FAILED,
)
from .sandbox import isolation_report

try:  # the shared group plumbing (single implementation, never copied)
    from ..cli_shared import (
        install_contract_entries,
        product_contract,
        registry_from_args,
        runtime_dir,
        share_common_flags,
        text_mode,
    )
except Exception:  # pragma: no cover — a build without the product CLI helpers
    install_contract_entries = None  # type: ignore[assignment]

    def product_contract() -> Any:  # type: ignore[misc]
        return None

    def registry_from_args(args: argparse.Namespace) -> Any:  # type: ignore[misc]
        return None

    def runtime_dir(args: argparse.Namespace) -> Path:  # type: ignore[misc]
        return Path(str(getattr(args, "runtime", "") or Path.cwd()))

    def share_common_flags(parser: Any) -> None:  # type: ignore[misc]
        return None

    def text_mode(args: argparse.Namespace) -> bool:  # type: ignore[misc]
        return False


_CONTRACT = product_contract()

_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")

#: True while ``python -m skaldir.tools_core`` drives the verbs itself (no product
#: CLI envelope around them): then *this* module prints, human or ``--json``.
_STANDALONE = False
_JSON_REQUESTED = False


def _standalone() -> bool:
    return _STANDALONE


def _human(args: argparse.Namespace) -> bool:
    """Print human text? Standalone does unless ``--json``; otherwise ``--text``."""
    if _standalone():
        return not _JSON_REQUESTED
    return text_mode(args) or _CONTRACT is None


# --------------------------------------------------------------- envelope glue


def _report(
    payload: Any,
    *,
    evidence: tuple[dict, ...] = (),
    warnings: tuple[str, ...] = (),
    next_actions: tuple[tuple[str, str], ...] = (),
) -> None:
    """Hand the payload and its evidence to the shared envelope (when wired).

    Standalone (``python -m skaldir.tools_core``) there is no envelope: with
    ``--json`` this prints the payload as one JSON document instead.
    """
    if _standalone():
        if _JSON_REQUESTED:
            _dump(payload)
        return
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


def _fail(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and not _standalone() and hasattr(_CONTRACT, "fail"):
        return int(_CONTRACT.fail(code, message, hint=hint, details=details))
    print(f"error: {message}", file=sys.stderr, flush=True)
    return int(code)


def _usage(message: str, *, hint: str = "") -> int:
    if _CONTRACT is not None and not _standalone() and hasattr(_CONTRACT, "usage_error"):
        return int(_CONTRACT.usage_error(message, hint=hint))
    print(f"usage: {message}", file=sys.stderr, flush=True)
    return EXIT_USAGE


def _not_found(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and not _standalone() and hasattr(_CONTRACT, "not_found"):
        return int(_CONTRACT.not_found(message, hint=hint, details=details))
    print(f"not found: {message}", file=sys.stderr, flush=True)
    return EXIT_NOT_FOUND


def _verification_failed(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and not _standalone() and hasattr(_CONTRACT, "verification_failed"):
        return int(_CONTRACT.verification_failed(message, hint=hint, details=details))
    print(f"verification failed: {message}", file=sys.stderr, flush=True)
    return EXIT_VERIFICATION_FAILED


def _blocked(
    message: str,
    *,
    hint: str = "",
    next_command: str = "",
    reason: str = "",
    details: dict | None = None,
) -> int:
    if _CONTRACT is not None and not _standalone() and hasattr(_CONTRACT, "blocked"):
        return int(
            _CONTRACT.blocked(
                message, hint=hint, next_command=next_command, reason=reason, details=details
            )
        )
    print(f"blocked: {message}", file=sys.stderr, flush=True)
    return EXIT_BLOCKED


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


# --------------------------------------------------------------------- verbs


def _permission_line(contract: Any) -> str:
    fs = contract.permission.filesystem
    net = contract.permission.network
    sub = contract.permission.subprocess
    proc = "shell" if sub.shell else ("none" if not sub.allowed else "exec")
    return f"fs={fs.mode or fs.scope} net={net.mode} proc={proc}"


def _cmd_list(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    rows = registry.registry_view(runtime, db=getattr(args, "db", None) or None)
    payload = {
        "kind": registry.KIND_CORE,
        "registry_kind": registry.REGISTRY_KIND,
        "runtime": str(runtime),
        "tools": rows,
        "count": len(rows),
        "registered": sum(1 for row in rows if row["registered"]),
    }
    evidence = [
        {
            "ref": str(Path(runtime) / "tools.db"),
            "kind": "file",
            "note": "registry the rows were read from",
        }
    ]
    _report(payload, evidence=tuple(evidence))
    if _human(args):
        for row in rows:
            mark = "registered" if row["registered"] else "not registered"
            print(
                f"[{mark:14s}] {row['name']:12s} kind={registry.KIND_CORE} "
                f"(row kind={row['kind'] or registry.REGISTRY_KIND}) — "
                f"{_permission_line(registry.contract_for(row['name']))} "
                f"timeout={row['timeout_s']}s truncate={row['truncation_bytes']}b "
                f"mutates={row['mutates']}"
            )
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    contract = registry.contract_for(args.name)
    if contract is None:
        return _not_found(
            f"unknown core tool: {args.name}",
            hint="`skaldir tools-core list` names the nine core tools",
            details={"known": list(registry.names())},
        )
    runtime = runtime_dir(args)
    row = registry.registry_view(
        runtime, db=getattr(args, "db", None) or None, names_=(contract.name,)
    )[0]
    payload = registry.descriptor(contract.name)
    payload["runtime_facts"] = row
    _report(
        payload,
        evidence=tuple(
            [
                {
                    "ref": row["descriptor"],
                    "kind": "file",
                    "note": "descriptor written by tools-core register",
                },
                {"ref": f"src/skaldir/tools_core/{Path(contract.module).name}.py", "kind": "code"},
            ]
        ),
    )
    if _human(args):
        print(f"{contract.name} — {contract.title}")
        print(f"  kind:      {registry.KIND_CORE} (registry row kind: {registry.REGISTRY_KIND})")
        print(f"  module:    {contract.module}.{contract.entrypoint}")
        print(f"  timeout:   {contract.timeout_s}s (max {contract.max_timeout_s}s)")
        print(f"  truncation:{contract.truncation.max_bytes} bytes, {contract.truncation.strategy}")
        print(f"  perms:     {_permission_line(contract)}")
        print(f"  registered:{row['registered']} ({row['status'] or 'no row'})")
    return EXIT_OK


def _cmd_register(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    report = registry.register(
        runtime,
        db=getattr(args, "db", None) or None,
        probe_network=not bool(getattr(args, "no_network_probe", False)),
        write_manifests=not bool(getattr(args, "no_manifests", False)),
    )
    evidence: list[dict] = [
        {"ref": report["db"], "kind": "file", "note": "registry the specs were published to"}
    ]
    for row in report["registered"]:
        if row.get("descriptor"):
            evidence.append(
                {"ref": row["descriptor"], "kind": "file", "note": "typed contract descriptor"}
            )
        if row.get("manifest"):
            evidence.append({"ref": row["manifest"], "kind": "file", "note": "scope manifest"})
    warnings = [f"{item['name']}: {item['reason']}" for item in report["conflicts"]]
    warnings += [f"{item['name']}: {item['reason']}" for item in report["broken"]]
    _report(
        report,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
        next_actions=(
            ("skaldir tools-core smoke --json", "record a real verification for every core tool"),
        ),
    )
    if _human(args):
        for row in report["registered"]:
            print(f"[{row['action']:9s}] {row['name']:12s} -> {row['entrypoint']}")
        print(f"{report['count']} core tool(s) registered in {report['db']}")
        network = report.get("network") or {}
        if network.get("probed"):
            print(
                "key-free search backends answering: "
                f"{', '.join(network.get('answering') or []) or 'none'}"
            )
    if report["conflicts"]:
        return _fail(
            EXIT_CONFLICT,
            "the registry already carries a core tool name with a different kind",
            hint="rename or revoke the foreign row before registering the core toolset",
            details={"conflicts": report["conflicts"]},
        )
    if report["broken"]:
        return _fail(
            EXIT_INTERNAL,
            "a core tool's registered entrypoint does not resolve",
            details={"broken": report["broken"]},
        )
    return EXIT_OK


def _load_arguments(args: argparse.Namespace) -> tuple[dict | None, str]:
    source = str(getattr(args, "input_file", "") or "")
    text = str(getattr(args, "input", "") or "")
    if source:
        path = Path(source).expanduser()
        if not path.is_file():
            return None, f"--input-file does not exist: {source}"
        text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}, ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"the input is not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "the input must be a JSON object"
    return parsed, ""


def _cmd_call(args: argparse.Namespace) -> int:
    contract = registry.contract_for(args.name)
    if contract is None:
        return _not_found(
            f"unknown core tool: {args.name}", details={"known": list(registry.names())}
        )
    arguments, problem = _load_arguments(args)
    if arguments is None:
        return _usage(problem, hint="pass --input '{\"…\": …}' or --input-file path.json")
    runtime = runtime_dir(args)
    workspace = str(getattr(args, "workspace", "") or "")
    resolved_db = getattr(args, "db", None) or None
    if not resolved_db and registry_from_args is not None:
        try:
            resolved_db = registry_from_args(args).tools_db
        except Exception:  # noqa: BLE001 — a registry that cannot resolve is reported below
            resolved_db = None
    result = registry.call(
        contract.name,
        arguments,
        runtime=runtime,
        root=workspace or None,
        db=None if getattr(args, "allow_unregistered", False) else resolved_db,
        isolated=bool(getattr(args, "isolate", False)),
        offline=bool(getattr(args, "offline", False)),
    )
    payload = result.to_dict()
    next_actions: tuple[tuple[str, str], ...] = ()
    if result.error_kind == "not_found" and "not registered" in result.error:
        next_actions = (
            (
                f"skaldir tools-core register --runtime {runtime}",
                "the toolset is not registered in this runtime",
            ),
        )
    _report(
        payload,
        evidence=tuple(result.evidence),
        warnings=tuple(result.warnings),
        next_actions=next_actions,
    )
    if _human(args):
        output = payload.get("output") or {}
        if result.ok:
            print(f"{contract.name}: ok (exit_code={output.get('exit_code', 0)})")
            for key in ("stdout", "content", "text", "diff"):
                value = output.get(key)
                if value:
                    print(str(value)[:2000])
                    break
        else:
            print(f"{contract.name}: {result.error_kind} — {result.error}")
    code = result.exit_code
    if code:
        return _fail(
            code,
            result.error or f"{contract.name} failed ({result.error_kind})",
            details={"error_kind": result.error_kind, "tool": contract.name},
        )
    if getattr(args, "fail_on_nonzero", False):
        observed = int((payload.get("output") or {}).get("exit_code") or 0)
        if observed:
            return _fail(
                observed,
                f"the command exited {observed} (--fail-on-nonzero)",
                details={"observed_exit_code": observed},
            )
    return EXIT_OK


def _cmd_smoke(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    report = registry.smoke(
        runtime,
        name=str(getattr(args, "name", "") or ""),
        root=str(getattr(args, "workspace", "") or "") or None,
        record_health=not bool(getattr(args, "no_record", False)),
        probe_network=bool(getattr(args, "network", False)),
    )
    if report["unknown"]:
        return _not_found(
            f"unknown core tool: {report['unknown'][0]}", details={"known": list(registry.names())}
        )
    evidence = tuple(
        [
            {
                "ref": f"skaldir tools-core smoke --name {row['name']}",
                "kind": "run",
                "note": (
                    f"{'passed' if row['ok'] else ('skipped' if row['ok'] is None else 'FAILED')} "
                    f"({row.get('duration_ms', 0)} ms)"
                )
            }
            for row in report["tools"]
        ]
        + [{"ref": report["root"], "kind": "file", "note": "smoke workspace"}]
    )
    warnings = tuple(
        f"{row['name']}: {row['skipped']}" for row in report["tools"] if row.get("skipped")
    )
    more: tuple[tuple[str, str], ...] = ()
    if report["skipped"]:
        more = (("skaldir tools-core smoke --network --json", "cover the network tools too"),)
    _report(report, evidence=evidence, warnings=warnings, next_actions=more)
    if _human(args):
        for row in report["tools"]:
            state = "pass" if row["ok"] else ("skip" if row["ok"] is None else "FAIL")
            print(f"[{state:4s}] {row['name']:12s} {row.get('health_state', '')}")
        print(
            f"{report['passed']} passed, {report['failed']} failed, "
            f"{len(report['skipped'])} skipped"
        )
    if report["failed"]:
        return _verification_failed(
            f"{report['failed']} core tool smoke case(s) failed",
            hint="see the checks in data.tools — a failing check names the observation",
            details={"failed": [row["name"] for row in report["tools"] if row["ok"] is False]},
        )
    if not report["ok"]:
        return _blocked(
            "no core tool smoke case ran (all were skipped)",
            hint="pass --network to include the network tools, or check the runtime",
            next_command="skaldir tools-core smoke --network --json",
            reason="nothing was measured",
            details={"skipped": report["skipped"]},
        )
    return EXIT_OK


def _cmd_health(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    report = registry.health(runtime, db=getattr(args, "db", None) or None)
    _report(
        report,
        evidence=tuple(
            [
                {
                    "ref": str(Path(runtime) / "capability_health.jsonl"),
                    "kind": "file",
                    "note": "health ledger that was read",
                },
                {
                    "ref": str(Path(runtime) / "tools.db"),
                    "kind": "file",
                    "note": "registry that was read",
                },
            ]
        ),
    )
    if _human(args):
        for row in report["tools"]:
            print(
                f"[{row['state']:11s}] {row['name']:12s} registered={row['registered']} "
                f"routable={row['auto_routable']}"
            )
        print(f"counts: {report['counts']}")
    return EXIT_OK


def _cmd_policy(args: argparse.Namespace) -> int:
    report = registry.policy_report()
    _report(
        report,
        evidence=tuple(
            {
                "ref": "src/" + str(row["module"]).replace(".", "/") + ".py",
                "kind": "code",
                "note": f"{row['name']} contract",
            }
            for row in report["tools"]
        ),
    )
    if _human(args):
        for row in report["tools"]:
            print(
                f"{row['name']:12s} kind={row['kind']:5s} "
                f"fs={row['filesystem']['mode'] or 'none':9s} "
                f"net={row['network']['mode']:8s} proc={row['subprocess']} "
                f"timeout={row['timeout_s']}s truncate={row['truncation_bytes']}b"
            )
        isolation = report["isolation"]
        print(f"isolation: {isolation['mode']} (bubblewrap available={isolation['available']})")
    return EXIT_OK


def _cmd_unregister(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    wanted = tuple(getattr(args, "name", None) or ()) or None
    if wanted:
        unknown = [name for name in wanted if registry.contract_for(name) is None]
        if unknown:
            return _not_found(
                f"unknown core tool: {unknown[0]}", details={"known": list(registry.names())}
            )
    report = registry.unregister(runtime, names_=wanted)
    _report(
        report,
        evidence=tuple(
            {"ref": path, "kind": "file", "note": "removed"}
            for row in report["removed"]
            for path in row["removed"]
            if path.startswith("/")
        ),
    )
    if _human(args):
        for row in report["removed"]:
            print(f"removed {row['name']}: {', '.join(row['removed']) or '(nothing on disk)'}")
    return EXIT_OK


# -------------------------------------------------------------------- parser


def _add_runtime_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime", default="", help="runtime dir (default: the product runtime)")
    parser.add_argument("--db", default=None, help="explicit tools DB override")


def _add_verbs(sub: Any) -> None:
    p_list = sub.add_parser("list", help="the core tools and their declarations")
    _add_runtime_flags(p_list)
    share_common_flags(p_list)
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="one tool's full typed contract")
    p_show.add_argument("name")
    _add_runtime_flags(p_show)
    share_common_flags(p_show)
    p_show.set_defaults(func=_cmd_show)

    p_register = sub.add_parser(
        "register", help="publish the core toolset into the runtime registry (idempotent)"
    )
    _add_runtime_flags(p_register)
    p_register.add_argument(
        "--no-network-probe",
        action="store_true",
        help="do not probe the key-free search backends while registering",
    )
    p_register.add_argument(
        "--no-manifests",
        action="store_true",
        help="skip the scope manifests (the tools then count as 'legacy' and are not auto-exposed)",
    )
    share_common_flags(p_register)
    p_register.set_defaults(func=_cmd_register)

    p_call = sub.add_parser("call", help="run one core tool for real")
    p_call.add_argument("name")
    p_call.add_argument("--input", default="", help="arguments as a JSON object")
    p_call.add_argument("--input-file", default="", help="arguments read from a JSON file")
    p_call.add_argument("--workspace", default="", help="sandbox root (default: the current dir)")
    p_call.add_argument("--isolate", action="store_true", help="escalate to bubblewrap when usable")
    p_call.add_argument("--offline", action="store_true", help="refuse every network tool")
    p_call.add_argument(
        "--allow-unregistered",
        action="store_true",
        help="call even when the registry has no row for the tool",
    )
    p_call.add_argument(
        "--fail-on-nonzero",
        action="store_true",
        help="exit with the observed process status when it is non-zero",
    )
    _add_runtime_flags(p_call)
    share_common_flags(p_call)
    p_call.set_defaults(func=_cmd_call)

    p_smoke = sub.add_parser("smoke", help="run each tool's declared smoke case and record health")
    p_smoke.add_argument("--name", default="", help="only this tool")
    p_smoke.add_argument("--network", action="store_true", help="include the network tools")
    p_smoke.add_argument("--workspace", default="", help="smoke workspace (default: a temp dir)")
    p_smoke.add_argument("--no-record", action="store_true", help="do not write health facts")
    _add_runtime_flags(p_smoke)
    share_common_flags(p_smoke)
    p_smoke.set_defaults(func=_cmd_smoke)

    p_health = sub.add_parser("health", help="the capability-health view of the core tools")
    _add_runtime_flags(p_health)
    share_common_flags(p_health)
    p_health.set_defaults(func=_cmd_health)

    p_policy = sub.add_parser("policy", help="the declared surface of the whole toolset")
    share_common_flags(p_policy)
    p_policy.set_defaults(func=_cmd_policy)

    p_unregister = sub.add_parser("unregister", help="take the core toolset back out")
    p_unregister.add_argument("name", nargs="*", help="only these tools (default: all)")
    _add_runtime_flags(p_unregister)
    share_common_flags(p_unregister)
    p_unregister.set_defaults(func=_cmd_unregister)


def proposed_contract_entries() -> dict[str, Any]:
    """The ``COMMANDS`` entries this group installs (identical objects)."""
    if _CONTRACT is None:
        return {}
    spec = _CONTRACT.CommandSpec

    def evidence(ref: str, note: str = "") -> dict:
        return {"ref": ref, "kind": "code", "note": note}

    registry_ref = evidence("src/skaldir/tools_core/registry.py")
    contract_ref = evidence("src/skaldir/tools_core/contract.py")
    return {
        "tools-core.list": spec(
            "EXPAND",
            (registry_ref,),
            "the core toolset with kind, permissions, timeout and truncation",
            data_schema={
                "type": "object",
                "properties": {
                    "tools": {"type": "array"},
                    "count": {"type": "integer"},
                    "registered": {"type": "integer"},
                },
                "required": ["tools", "count", "registered"],
            },
        ),
        "tools-core.show": spec(
            "EXPAND",
            (contract_ref,),
            "one core tool's typed contract and registry facts",
            exit_codes=(0, 5, 7),
            data_schema={"type": "object", "additionalProperties": True},
            notes="an unknown tool name exits 5",
        ),
        "tools-core.register": spec(
            "EXPAND",
            (registry_ref,),
            "publish the core tools into the runtime registry",
            mutates=True,
            exit_codes=(0, 6, 7),
            data_schema={"type": "object", "additionalProperties": True},
            notes="exit 6 when a core tool name is already taken by a foreign kind; "
            "the operation is idempotent (re-running re-publishes the same version)",
        ),
        "tools-core.call": spec(
            "EXPAND",
            (registry_ref,),
            "run one core tool for real and return its typed result",
            mutates=True,
            exit_codes=(0, 1, 3, 5, 6, 7, 8),
            data_schema={
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "error_kind": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "output": {"type": "object"},
                },
                "required": ["tool", "ok", "output"],
            },
            notes="a command that exits non-zero is a successful observation (exit 0 here); "
            "timeouts/network failures exit 8, refusals exit 3, unregistered tools exit 5",
        ),
        "tools-core.smoke": spec(
            "EXPAND",
            (registry_ref,),
            "run every core tool's declared smoke case and record the health fact",
            mutates=True,
            exit_codes=(0, 2, 3, 5, 7),
            data_schema={"type": "object", "additionalProperties": True},
            notes="exit 2 when a check really failed, 3 when nothing was measured "
            "(all smoke cases skipped), 5 for an unknown --name",
        ),
        "tools-core.health": spec(
            "EXPAND",
            (registry_ref,),
            "the capability-health view of the core tools",
            exit_codes=(0, 7),
            data_schema={"type": "object", "additionalProperties": True},
            notes="never fails on an unverified tool — it reports the state",
        ),
        "tools-core.policy": spec(
            "EXPAND",
            (contract_ref,),
            "the declared filesystem/network/subprocess surface of the toolset",
            data_schema={"type": "object", "additionalProperties": True},
            notes="read-only; the isolation probe result is included",
        ),
        "tools-core.unregister": spec(
            "EXPAND",
            (registry_ref,),
            "take the core toolset back out (manifest, descriptor, health retire)",
            mutates=True,
            exit_codes=(0, 5, 7),
            data_schema={"type": "object", "additionalProperties": True},
            notes="nothing is deleted silently: a retire fact is recorded when a ledger row exists",
        ),
    }


def register_cli(subparsers: Any) -> Any:
    """Add the ``tools-core`` group (accepts the subparsers action or a parser)."""
    from ..cli_shared import as_subparsers

    sub = as_subparsers(subparsers)
    if "tools-core" in getattr(sub, "choices", {}):
        return sub.choices["tools-core"]
    parent = sub.add_parser(
        "tools-core",
        help="the built-in (core) toolset: terminal, files, patch, search, web",
    )
    verbs = parent.add_subparsers(dest="tools_core_command", required=True)
    _add_verbs(verbs)
    for parser in (parent, verbs):
        try:
            share_common_flags(parser)
        except Exception:  # noqa: BLE001 — the flags are a convenience, not the contract
            continue
    if install_contract_entries is not None and _CONTRACT is not None:
        install_contract_entries(proposed_contract_entries(), _CONTRACT)
    return parent


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(
        prog="skaldir tools-core", description="the built-in core toolset"
    )
    verbs = parser.add_subparsers(dest="tools_core_command", required=True)
    _add_verbs(verbs)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir tools-core …`` — returns the exit code, never raises for a request."""
    global _STANDALONE, _JSON_REQUESTED
    invoked = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    _STANDALONE = True
    _JSON_REQUESTED = "--json" in invoked
    try:
        args = parser.parse_args(invoked)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


__all__ = [
    "ERROR_EXIT",
    "EXIT_UNKNOWN",
    "build_parser",
    "isolation_report",
    "main",
    "proposed_contract_entries",
    "register_cli",
]
