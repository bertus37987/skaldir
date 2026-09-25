"""CLI surface of the HTTP/API half of the self-expanding tool system (EXPAND, gap A).

The container half of the tool builder is ``skaldir.toolbuilder_cli`` (commands
under ``tools``: ``mine``/``adapter show``/``register``/``health``/``rollback``).
It inspects a container's **CLI** surface and refuses an API-only surface with
exit 3. This module is the CLI for the **API** surface; the pipeline itself is
``skaldir.adapter_http.HttpToolBuilder``:

    tools api inspect --base-url …    discover only: which document answered, what it
                                      documents, what was probed (no writes at all)
    tools api discover …              discover → map the capability onto a DOCUMENTED
                                      operation → generate a typed adapter → TEST it
                                      against the REAL service (independent oracle)
    tools api show …                  the typed adapter, its permissions, its evidence
    tools api register …              register the TESTED adapter live (version, health,
                                      permissions, rollback snapshot)
    tools api call …                  call the registered capability (real HTTP request)
    tools api health …                ledger state + live service facts (+ --probe)
    tools api rollback …              take it back: registry, files, health, containers

**Integration (the only change ``cli.py`` needs — 2 lines).** In
``cli.build_parser()``, directly after the ``tools`` subparsers and the
container half's registration exist::

    from .adapter_http_cli import register_cli as _register_http_api   # +
    _register_http_api(tools_sub)                                      # +

**Contract entries** (``src/skaldir/cli_contract.py`` — owned by the CLI centre;
these are the seven leaves this group adds, see ``EV_NEW_COMMANDS``)::

    tools.api.inspect  · tools.api.discover · tools.api.show
    tools.api.register · tools.api.call     · tools.api.health · tools.api.rollback

A capability is never registered without a passed test against the real service:
the exit codes carry that, not the prose — ``tools api register`` exits 2
(verification_failed) when the evidence is missing, failed or no longer covers
the adapter bytes; ``tools api discover`` exits 3 (blocked) when no
machine-readable surface exists, when the capability maps onto no documented
operation, or when no independent local oracle can certify the operation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import cli_contract
from .cli_shared import registry_from_args
from .toolbuilder_cli import FAILURE_EXIT as CONTAINER_FAILURE_EXIT

#: A structured failure class → the CLI exit code. The container table is reused
#: and extended — one table, no second mapping of the shared classes.
FAILURE_EXIT = {
    **CONTAINER_FAILURE_EXIT,
    # the service / the documented surface: nothing exists to adapt
    "service_required": cli_contract.EXIT_BLOCKED,
    "port_required": cli_contract.EXIT_BLOCKED,
    "surface_unavailable": cli_contract.EXIT_BLOCKED,
    "surface_unstable": cli_contract.EXIT_BLOCKED,
    "surface_changed": cli_contract.EXIT_BLOCKED,
    "oracle_missing": cli_contract.EXIT_BLOCKED,
    "mutating_operation": cli_contract.EXIT_BLOCKED,
    "service_unavailable": cli_contract.EXIT_BLOCKED,
    # evidence problems: the adapter exists but is NOT proven
    "adapter_changed": cli_contract.EXIT_VERIFICATION_FAILED,
    "unverified": cli_contract.EXIT_VERIFICATION_FAILED,
    "test_failed": cli_contract.EXIT_VERIFICATION_FAILED,
    "output_mismatch": cli_contract.EXIT_VERIFICATION_FAILED,
    # the service answered, but not in the documented shape
    "unparsable_answer": cli_contract.EXIT_VERIFICATION_FAILED,
}

EV_NEW_COMMANDS = (
    # only LEAVES need a contract entry (cli_contract.dump lists leaves)
    "tools.api.inspect: layer EXPAND, exit_codes=(0, 1, 3, 7), "
    "evidence=(src/skaldir/adapter_http.py inspect_http_surface, "
    "src/skaldir/adapter_http_cli.py _cmd_api_inspect), "
    "notes='read-only: which documented surface answered + every probe'",
    "tools.api.discover: layer EXPAND, mutates=True, exit_codes=(0, 1, 2, 3, 5, 6, 7), "
    "evidence=(src/skaldir/adapter_http.py HttpToolBuilder.mine_http, the staged "
    "adapter.json/http_evidence.json), "
    "notes='discovers a documented operation, tests it against the real service "
    "with an independent local oracle, stages it (does NOT register)'",
    "tools.api.show: layer EXPAND, exit_codes=(0, 3, 5, 7), "
    "evidence=(src/skaldir/adapter_http.py HttpToolBuilder.http_adapter,)",
    "tools.api.register: layer EXPAND, mutates=True, exit_codes=(0, 2, 5, 7), "
    "evidence=(src/skaldir/adapter_http.py HttpToolBuilder.register_http_tool, "
    "tools.ToolStore.publish, capability_health.record_verification), "
    "notes='exit 2 = the adapter is not (still) proven: no registration'",
    "tools.api.call: layer EXPAND, exit_codes=(0, 2, 3, 5, 7), "
    "evidence=(src/skaldir/adapter_http.py HttpToolBuilder.call_http_tool, "
    "the HTTP request that really ran and its status), "
    "notes='exit 3 = the pinned surface changed or the service is down', ",
    "tools.api.health: layer EXPAND, exit_codes=(0, 3, 5, 7), "
    "evidence=(src/skaldir/adapter_http.py HttpToolBuilder.http_health, "
    "capability_health.jsonl)",
    "tools.api.rollback: layer EXPAND, mutates=True, exit_codes=(0, 3, 5, 7), "
    "evidence=(src/skaldir/toolbuilder.py ContainerToolBuilder.rollback_container_tool, "
    "the label-scoped docker ps listing that removed OUR containers only)",
)


def _builder(args: argparse.Namespace):
    """An HTTP builder on the resolved runtime with the resolved tools DB."""
    from .adapter_http import HttpToolBuilder
    from .tools import ToolStore

    context = registry_from_args(args)
    Path(context.tools_db).parent.mkdir(parents=True, exist_ok=True)
    return HttpToolBuilder(context.runtime_dir, store=ToolStore(str(context.tools_db)))


def _exit_for(result: dict) -> int:
    """The exit code of a structured result — never a success for a failure."""
    if result.get("ok"):
        return cli_contract.EXIT_OK
    failure_class = str(result.get("failure_class") or "")
    reason = str(result.get("reason") or "the HTTP capability cannot be built")
    hints = {
        "reuse": "use the existing tool instead of building a duplicate",
        "unverified": "a capability is only registered on evidence — re-discover it",
        "test_failed": "re-discover: the adapter must pass against the real service first",
        "adapter_changed": "re-discover: the adapter was edited after its test",
        "not_found": "`tools api discover --base-url <url> --capability <text>` stages one first",
        "surface_unavailable": "point --document at the API documentation when the service "
                               "serves none on a well-known path",
        "oracle_missing": "pass --oracle <id> or declare x-godsv-oracle in the document",
        "service_unavailable": "start the service (or drop --base-url and use --image/--port)",
        "surface_changed": "the API changed after the test: re-discover it",
        "mutating_operation": "a write needs --allow-mutating (the default is a read)",
        "port_required": "name the port the API listens on inside the container",
    }
    code = FAILURE_EXIT.get(failure_class, cli_contract.EXIT_BLOCKED)
    print(reason)
    return cli_contract.fail(code, reason, hint=hints.get(failure_class, ""))


def _parse_json_argument(raw: str, *, what: str) -> dict:
    """One JSON argument — a usage error, never a silent empty payload."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{what} must be a JSON object")
    return parsed


def _surface_evidence(surface: dict) -> list[dict]:
    """The discovery probes as evidence entries (what was really fetched)."""
    items: list[dict] = []
    for probe in (surface.get("probes") or [])[:12]:
        status = probe.get("status")
        note = f"HTTP {status}" if status else str(probe.get("note") or "no answer")
        if probe.get("bytes"):
            note += f", {probe['bytes']} bytes"
        items.append({"ref": str(probe.get("url") or ""), "kind": "run", "note": note})
    return items


# --------------------------------------------------------------------------- #
# tools api inspect / discover
# --------------------------------------------------------------------------- #


def _cmd_api_inspect(args: argparse.Namespace) -> int:
    """Discover a service's HTTP surface — read-only, nothing is written."""
    try:
        from .adapter_http import inspect_http_surface, surface_summary
    except ImportError as exc:  # pragma: no cover — a broken install is reported
        return cli_contract.fail(cli_contract.EXIT_INTERNAL, f"adapter_http unavailable: {exc}")

    base_url = str(getattr(args, "base_url", "") or "")
    document = str(getattr(args, "document", "") or "")
    if not base_url and not document:
        return cli_contract.usage_error(
            "nothing to inspect",
            hint="tools api inspect --base-url http://127.0.0.1:8080 "
                 "(or --document /path/to/openapi.json)",
        )
    surface = inspect_http_surface(base_url, document=document)
    summary = surface_summary(surface)
    payload = dict(summary)
    payload["outcome"] = "inspected" if surface.get("inspected") else "refused"
    cli_contract.set_data(payload)
    for item in _surface_evidence(surface):
        cli_contract.add_evidence(item["ref"], "run", item["note"])
    if surface.get("inspected") and surface.get("source"):
        cli_contract.add_evidence(
            str(surface["source"]), "file",
            f"the {surface.get('kind')} surface document this inspection read "
            f"(sha256 {str(surface.get('document_sha256'))[:16]}…)",
        )
    print(
        f"{payload['outcome']}: {surface.get('kind') or 'no document'} "
        f"{surface.get('source') or '(no source)'} → "
        f"{surface.get('operation_count')} documented operation(s)"
    )
    if not surface.get("inspected"):
        return cli_contract.blocked(
            str(surface.get("reason") or "the HTTP surface could not be inspected"),
            hint="the probe list in data.probes is the evidence for the refusal",
        )
    return cli_contract.EXIT_OK


def _cmd_api_discover(args: argparse.Namespace) -> int:
    """Discover → adapt → TEST an HTTP capability against the real service."""
    capability = str(getattr(args, "capability", "") or "").strip()
    if not capability:
        print("--capability is required: say what the capability must do")
        return cli_contract.usage_error(
            "--capability is required",
            hint='tools api discover --base-url http://127.0.0.1:8080 '
                 '--capability "compute the sha-256 digest of a string"',
        )
    try:
        test_input = _parse_json_argument(str(getattr(args, "test_input", "") or ""),
                                         what="--test-input")
    except ValueError as exc:
        print(str(exc))
        return cli_contract.usage_error(str(exc))
    service_argv = str(getattr(args, "service_argv", "") or "").split(" ")
    service_argv = [part for part in service_argv if part]

    builder = _builder(args)
    result = builder.mine_http(
        capability,
        base_url=str(getattr(args, "base_url", "") or ""),
        image=str(getattr(args, "image", "") or ""),
        image_digest=str(getattr(args, "digest", "") or ""),
        container_port=int(getattr(args, "port", 0) or 0),
        service_argv=service_argv,
        document=str(getattr(args, "document", "") or ""),
        name=str(getattr(args, "name", "") or ""),
        operation_id=str(getattr(args, "operation_id", "") or ""),
        oracle=str(getattr(args, "oracle", "") or ""),
        test_input=test_input or None,
        expect=str(getattr(args, "expect", "") or ""),
        allow_mutating=bool(getattr(args, "allow_mutating", False)),
        pull=bool(getattr(args, "pull", False)),
        session_id=str(getattr(args, "session", "") or ""),
    )
    surface = result.get("surface") or {}
    adapter = result.get("adapter") or {}
    test = result.get("test") or {}
    payload = {
        "name": result.get("name", ""),
        "capability": capability,
        "runtime": result.get("runtime", "http"),
        "service": result.get("service", {}),
        "search": result.get("search", {}),
        "surface": _surface_summary_for_cli(
            surface, operation_id=str((adapter.get("operation") or {}).get("id") or "")
        ),
        "document": result.get("document", ""),
        "adapter": adapter,
        "test": {
            key: test.get(key) for key in
            ("ok", "input", "expected", "observed", "oracle", "oracle_detail",
             "request", "status", "detail", "duration_ms")
        },
        "container": result.get("container", {}),
        "stages": result.get("stages", []),
        "staged": bool(result.get("staged")),
        "failure_class": result.get("failure_class", ""),
        "reason": result.get("reason", ""),
        "tool_dir": result.get("tool_dir", ""),
        "adapter_path": result.get("adapter_path", ""),
        "evidence_path": result.get("evidence_path", ""),
        "wrapper": result.get("wrapper", ""),
    }
    cli_contract.set_data(payload)
    for item in _surface_evidence(surface):
        cli_contract.add_evidence(item["ref"], "run", item["note"])
    if result.get("staged"):
        cli_contract.add_evidence(str(result.get("adapter_path")), "file",
                                 "the typed adapter that was generated")
        cli_contract.add_evidence(str(result.get("evidence_path")), "file",
                                 "the real service test that proves it")
        request = test.get("request") or {}
        if request.get("url"):
            cli_contract.add_evidence(
                f"{request.get('method')} {request.get('url')}", "run",
                f"the real call: HTTP {test.get('status')} → {test.get('observed')} "
                f"(independent oracle {test.get('oracle')})",
            )
        cli_contract.add_evidence(
            str(result.get("evidence_path") or ""), "test",
            f"{test.get('observed')} == {test.get('expected')} "
            f"({test.get('oracle_detail')})",
        )
    else:
        cli_contract.add_evidence(
            "src/skaldir/adapter_http.py inspect_http_surface", "code",
            "the surface discovery that refused to generate an adapter",
        )
    for stage in result.get("stages", []):
        print(f"  {stage['stage']:16s} {'ok' if stage['ok'] else 'FAILED'}")
    if result.get("ok"):
        print(
            f"staged {result['name']} → {test.get('observed')} (expected "
            f"{test.get('expected')}, oracle {test.get('oracle')}); NOT registered yet — "
            f"run `tools api register {result['name']}`"
        )
        cli_contract.next_action(f"tools api register {result['name']}",
                                 "register the tested adapter live")
        return cli_contract.EXIT_OK
    code = _exit_for(result)
    print(f"discover did not produce a capability: {result.get('reason')}")
    return code


def _surface_summary_for_cli(surface: dict, *, operation_id: str = "") -> dict:
    """The inspection record for the discover envelope (full one on disk)."""
    try:
        from .adapter_http import surface_summary
    except ImportError:  # pragma: no cover — a broken install is reported elsewhere
        return dict(surface)
    return surface_summary(surface, operation_id=operation_id)


# --------------------------------------------------------------------------- #
# tools api show / register / call / health / rollback
# --------------------------------------------------------------------------- #


def _cmd_api_show(args: argparse.Namespace) -> int:
    """The typed adapter, its permissions and its test evidence."""
    report = _builder(args).http_adapter(str(args.name))
    if not report.get("ok"):
        print(str(report.get("reason") or "unknown HTTP capability"))
        return cli_contract.not_found(
            str(report.get("reason") or f"unknown HTTP capability: {args.name}"),
            hint="`tools api discover --base-url <url> --capability <text>` stages one first",
        )
    adapter = report["adapter"]
    payload = {
        "name": report["name"],
        "adapter": adapter,
        "manifest": report.get("manifest", {}),
        "permissions": report.get("permissions", {}),
        "test": report.get("test", {}),
        "wrapper": report.get("wrapper", ""),
        "tool_dir": report.get("tool_dir", ""),
    }
    cli_contract.set_data(payload)
    cli_contract.add_evidence(str(Path(report["tool_dir"]) / "adapter.json"), "file",
                             "the typed adapter that is shown")
    operation = adapter.get("operation") or {}
    cli_contract.add_evidence(
        f"{operation.get('method')} {adapter.get('service', {}).get('base_url')}"
        f"{operation.get('path')}", "code",
        "the documented operation this adapter calls",
    )
    cli_contract.add_evidence(
        str((adapter.get("pin") or {}).get("document_url") or ""), "run",
        f"the pinned surface document (sha256 "
        f"{str((adapter.get('pin') or {}).get('document_sha256'))[:16]}…)",
    )
    print(
        f"{report['name']}: {operation.get('method')} "
        f"{adapter.get('service', {}).get('base_url')}{operation.get('path')} "
        f"({operation.get('id')}) "
        f"inputs={sorted(operation.get('inputs') or {})} "
        f"outputs={sorted(operation.get('outputs') or {})}"
    )
    print(f"  service: {json.dumps(adapter.get('service', {}), ensure_ascii=False)}")
    print(f"  permissions: {json.dumps(report.get('permissions', {}), ensure_ascii=False)}")
    return cli_contract.EXIT_OK


def _cmd_api_register(args: argparse.Namespace) -> int:
    """Register a TESTED HTTP adapter live (version, health, permissions, rollback)."""
    result = _builder(args).register_http_tool(
        str(args.name),
        version=str(getattr(args, "version", "") or ""),
        project_id=str(getattr(args, "project_id", "") or ""),
        requested_by=str(getattr(args, "requested_by", "") or "cli"),
    )
    payload = {
        key: result.get(key) for key in
        ("name", "version", "status", "kind", "runtime", "service", "operation",
         "permissions", "health_state", "rollback_id", "publish", "wrapper", "test",
         "failure_class", "reason")
    }
    payload["registered"] = bool(result.get("registered"))
    payload["health"] = result.get("health", {})
    payload["spec"] = result.get("spec", {})
    cli_contract.set_data(payload)
    if result.get("registered"):
        cli_contract.add_evidence(str(result.get("wrapper")), "file",
                                 "the registered tool's wrapper (the call path)")
        cli_contract.add_evidence(
            "src/skaldir/adapter_http.py HttpToolBuilder.register_http_tool", "code",
            "the evidence gate that refuses an unverified registration",
        )
        cli_contract.add_evidence(
            "src/skaldir/tools.py ToolStore.publish", "code",
            "the registry commit (spec row + outbox in one transaction)",
        )
        operation = result.get("operation") or {}
        cli_contract.add_evidence(
            f"{operation.get('method')} "
            f"{(result.get('service') or {}).get('base_url')}{operation.get('path')}", "run",
            f"the call that was tested: {result.get('test', {}).get('observed')} == "
            f"{result.get('test', {}).get('expected')}",
        )
        print(
            f"registered {result['name']} v{result['version']} ({result['status']}) — "
            f"health {result.get('health_state')}, rollback {result.get('rollback_id')}"
        )
        print(f"  call: skaldir tools api call {result['name']} '{{\"text\": \"…\"}}'")
        return cli_contract.EXIT_OK
    print(f"NOT registered: {result.get('reason')}")
    return _exit_for(result)


def _cmd_api_call(args: argparse.Namespace) -> int:
    """Call a registered HTTP capability (a real request, real outputs)."""
    raw = str(getattr(args, "payload", "") or "") or str(getattr(args, "input", "") or "")
    try:
        payload = _parse_json_argument(raw, what="the payload")
    except ValueError as exc:
        print(str(exc))
        return cli_contract.usage_error(str(exc))
    result = _builder(args).call_http_tool(str(args.name), payload)
    request = result.get("request") or {}
    data = {
        key: result.get(key) for key in
        ("name", "mode", "base_url", "input", "output", "request", "status",
         "body_excerpt", "pin", "service", "failure_class", "reason")
    }
    data["ok"] = bool(result.get("ok"))
    data["duration_ms"] = result.get("duration_ms", 0)
    cli_contract.set_data(data)
    if request.get("url"):
        cli_contract.add_evidence(
            f"{request.get('method')} {request.get('url')}", "run",
            f"HTTP {result.get('status')} in {result.get('duration_ms')} ms",
        )
    if result.get("pin", {}).get("checked"):
        cli_contract.add_evidence(
            str(result.get("pin", {}).get("live")), "verification",
            str(result.get("pin", {}).get("reason")),
        )
    if result.get("ok"):
        cli_contract.add_evidence(
            "src/skaldir/adapter_http.py HttpToolBuilder.call_http_tool", "code",
            "the call path that rendered and parsed this request",
        )
        print(f"{result['name']} → {json.dumps(result.get('output'), ensure_ascii=False)}")
        return cli_contract.EXIT_OK
    print(f"call failed: {result.get('reason') or result.get('failure_class')}")
    failure_class = str(result.get("failure_class") or "")
    code = FAILURE_EXIT.get(failure_class, cli_contract.EXIT_BLOCKED)
    if code == cli_contract.EXIT_VERIFICATION_FAILED:
        return cli_contract.verification_failed(
            str(result.get("reason") or "the answer did not match the declared schema"),
            details=data,
        )
    return cli_contract.fail(code, str(result.get("reason") or "the call did not succeed"),
                             details={"failure_class": failure_class})


def _cmd_api_health(args: argparse.Namespace) -> int:
    """Health of an HTTP capability: ledger state + live service facts."""
    report = _builder(args).http_health(
        str(args.name), probe=bool(getattr(args, "probe", False))
    )
    if not report.get("ok"):
        print(str(report.get("reason") or "unknown HTTP capability"))
        return cli_contract.not_found(
            str(report.get("reason") or f"unknown HTTP capability: {args.name}")
        )
    payload = {
        key: report.get(key) for key in
        ("name", "health", "auto_routable", "selectable", "selectable_reason",
         "registered", "version", "operation", "permissions", "service", "pin", "probe")
    }
    cli_contract.set_data(payload)
    cli_contract.add_evidence(
        str(Path(registry_from_args(args).runtime_dir) / "capability_health.jsonl"), "file",
        "the health ledger the state was folded from",
    )
    service = report.get("service") or {}
    if service.get("base_url"):
        cli_contract.add_evidence(
            str(service.get("base_url")), "run",
            f"the service address of this capability ({service.get('mode')} mode)",
        )
    pin = report.get("pin") or {}
    if pin.get("checked"):
        cli_contract.add_evidence(str(pin.get("live")), "verification",
                                 str(pin.get("reason")))
    mode = str(service.get("mode") or "")
    running = service.get("reachable") if mode == "external" else service.get("started")
    print(
        f"{report['name']}: health={(report.get('health') or {}).get('state')} "
        f"selectable={report.get('selectable')} "
        f"service_running={running} "
        f"pin_match={pin.get('ok') if pin.get('checked') else 'unpinned'}"
    )
    if service.get("mode") == "external" and not service.get("reachable", True):
        return cli_contract.blocked(
            f"the service is not reachable: {service.get('detail')}",
            hint="the ledger state above is still the truth of the last verification",
        )
    return cli_contract.EXIT_OK


def _cmd_api_rollback(args: argparse.Namespace) -> int:
    """Take a registered HTTP capability back (registry/files/health/containers)."""
    result = _builder(args).rollback_http_tool(
        str(args.name), reason=str(getattr(args, "reason", "") or "")
    )
    payload = {
        key: result.get(key) for key in
        ("name", "rolled_back", "runtime", "reason", "quarantine", "containers_removed",
         "teardown_scope", "teardown_ok", "teardown_detail", "health",
         "removed_registration", "restored_spec", "failure_class")
    }
    payload["rolled_back"] = bool(result.get("rolled_back"))
    cli_contract.set_data(payload)
    if not result.get("ok"):
        print(str(result.get("reason") or "nothing to roll back"))
        return cli_contract.not_found(
            str(result.get("reason") or f"unknown HTTP capability: {args.name}")
        )
    cli_contract.add_evidence(str(result.get("quarantine") or ""), "file",
                             "the quarantined tool directory (nothing deleted)")
    removed = list(result.get("containers_removed") or [])
    cli_contract.add_evidence(
        f"godsv.capability={args.name}", "run",
        f"label-scoped teardown removed {len(removed)} container(s) of ours",
    )
    print(
        f"rolled back {result['name']}: registration {result.get('removed_registration')}, "
        f"containers removed {len(removed)}, "
        f"health {(result.get('health') or {}).get('state')}"
    )
    return cli_contract.EXIT_OK


# --------------------------------------------------------------------------- #
# registration of the CLI surface
# --------------------------------------------------------------------------- #


def _add_runtime_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)")
    parser.add_argument("--db", default=None, help="explicit tools DB override")


def register_cli(tools_sub) -> None:
    """Add the ``tools api`` commands to an existing ``tools`` subparsers action.

    Called from ``cli.build_parser()`` after ``tools_sub`` exists; it touches no
    other parser and returns nothing. It also teaches the container half's
    ``tools mine`` nothing — the API chain lives under its own ``api`` group so
    the two halves stay distinguishable in the contract dump.
    """
    p_api = tools_sub.add_parser(
        "api", help="HTTP/API surface capabilities: inspect, discover, register, call"
    )
    api_sub = p_api.add_subparsers(dest="api_command", required=True)

    p_inspect = api_sub.add_parser(
        "inspect", help="which documented HTTP surface a service offers (read-only)"
    )
    p_inspect.add_argument("--base-url", default="", help="a running service, e.g. http://127.0.0.1:8080")
    p_inspect.add_argument("--document", default="",
                           help="an OpenAPI/Swagger document or endpoint list (path or URL)")
    _add_runtime_flags(p_inspect)
    p_inspect.set_defaults(func=_cmd_api_inspect)

    p_discover = api_sub.add_parser(
        "discover", help="discover a documented operation, adapt it, TEST it (stages)"
    )
    p_discover.add_argument("--capability", default="", help="what the capability must do")
    p_discover.add_argument("--base-url", default="", help="a running service to adapt")
    p_discover.add_argument("--image", default="",
                            help="a container image whose API must be adapted (started by us)")
    p_discover.add_argument("--port", type=int, default=0,
                            help="the port the API listens on inside that container")
    p_discover.add_argument("--service-argv", default="",
                            help="command that starts the API inside the container "
                                 '(e.g. "httpd -f -p 8080 -h /www")')
    p_discover.add_argument("--document", default="",
                            help="an OpenAPI/Swagger document or endpoint list (path or URL)")
    p_discover.add_argument("--operation-id", default="",
                            help="pin the documented operation (no guessing)")
    p_discover.add_argument("--oracle", default="",
                            help="the independent local oracle (sha256-text, echo-text, …)")
    p_discover.add_argument("--name", default="", help="tool name for the mined capability")
    p_discover.add_argument("--test-input", default="", help="JSON inputs for the adapter test")
    p_discover.add_argument("--expect", default="", help="the value the test must observe")
    p_discover.add_argument("--allow-mutating", action="store_true",
                            help="acknowledge a documented POST/PUT/PATCH/DELETE operation")
    p_discover.add_argument("--digest", default="", help="pin the image by sha256 digest")
    p_discover.add_argument("--pull", action="store_true", help="pull the image when not local")
    p_discover.add_argument("--session", default="", help="session id recorded with the build")
    _add_runtime_flags(p_discover)
    p_discover.set_defaults(func=_cmd_api_discover)

    p_show = api_sub.add_parser(
        "show", help="the typed HTTP adapter, its permissions and its test evidence"
    )
    p_show.add_argument("name")
    _add_runtime_flags(p_show)
    p_show.set_defaults(func=_cmd_api_show)

    p_register = api_sub.add_parser(
        "register", help="register a TESTED HTTP adapter live (evidence or nothing)"
    )
    p_register.add_argument(
        "name", help="the staged capability to register"
    )
    p_register.add_argument(
        "--version", default="",
        help="explicit version (default 0.1.0; re-registering bumps the patch)",
    )
    p_register.add_argument("--project-id", default="", help="project id recorded in the manifest")
    p_register.add_argument("--requested-by", default="cli")
    _add_runtime_flags(p_register)
    p_register.set_defaults(func=_cmd_api_register)

    p_call = api_sub.add_parser("call", help="call a registered HTTP capability")
    p_call.add_argument("name")
    p_call.add_argument("payload", nargs="?", default="",
                        help='the inputs as JSON, e.g. \'{"text": "skaldir"}\'')
    p_call.add_argument("--input", default="", help="the inputs as JSON (alternative form)")
    _add_runtime_flags(p_call)
    p_call.set_defaults(func=_cmd_api_call)

    p_health = api_sub.add_parser(
        "health", help="health of an HTTP capability (ledger + live service facts)"
    )
    p_health.add_argument("name")
    p_health.add_argument("--probe", action="store_true",
                          help="call the capability once (a real request)")
    _add_runtime_flags(p_health)
    p_health.set_defaults(func=_cmd_api_health)

    p_rollback = api_sub.add_parser(
        "rollback", help="take an HTTP capability back (registry/files/health/containers)"
    )
    p_rollback.add_argument("name")
    p_rollback.add_argument("--reason", default="")
    _add_runtime_flags(p_rollback)
    p_rollback.set_defaults(func=_cmd_api_rollback)


__all__ = [
    "EV_NEW_COMMANDS",
    "FAILURE_EXIT",
    "register_cli",
]
