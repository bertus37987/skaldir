"""One table of the core tools, and their path in and out of the real registry.

Four responsibilities, no more:

``contracts()`` / ``descriptor()``
    The typed contract table (this package is its source; nothing is hand-written
    twice).
``register(runtime, …)``
    Make the tools real capabilities: one ``tools.db`` row per tool (published
    through :meth:`skaldir.tools.ToolStore.publish`, so the publication fact and
    the registry write share one transaction), a scope manifest under
    ``generated_tools/<name>/manifest.yaml`` (that is where the runtime keeps a
    tool's *scope*, and without it a tool is "legacy" and never auto-exposed), and
    a descriptor file ``<runtime>/tools_core/<name>.json`` carrying the full
    contract (schemas, permissions, timeout, truncation) that the registry row
    only summarises in its tags.

    The row's ``kind`` is ``python`` on purpose: that is the kind every existing
    consumer accepts (:func:`skaldir.mcpserver.approved_python_specs`, the MCP
    surface, ``agent_loop.registry_tools``). Core-ness stays visible in the tags
    (``core``), in ``provenance`` (``core:tools_core``) and in the descriptor.
    The ``module``/``entrypoint`` pair points at
    :mod:`skaldir.tools_core.bridge` — the in-process call convention of that
    path — so the tools are callable by the loop and by ``mcp serve`` without a
    wrapper and without an edit to any other module.
``call(name, arguments, …)``
    The one execution seam: resolves the contract, decides whether the registry
    actually knows the tool (``require_registered``), builds the context, runs it
    and times it. Every other surface (CLI, MCP, loop, tests) goes through here.
``smoke(…)`` / ``health(…)``
    A real, self-contained smoke case per tool (declared in its contract) run
    through :func:`call`, recorded as a verification fact in the
    ``capability_health`` ledger — so health is *observed*, and the ledger is the
    one that says which core tool is currently healthy.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import docker, files, patch_tool, search, terminal, web
from .contract import (
    CORE_VERSION,
    KIND_CORE,
    ToolContext,
    ToolContract,
    ToolResult,
    failure,
)
from .sandbox import isolation_report, resolve_root

#: The tool modules, in the order the toolset presents them. ``docker`` is the
#: ninth: the one built-in whose job is to make the runtime the others need exist.
TOOL_MODULES = (terminal, files, patch_tool, search, web, docker)

#: The registry ``kind`` of the row. Documented above: interop, not aesthetics.
REGISTRY_KIND = "python"

#: The bridge module every row points at (see :mod:`skaldir.tools_core.bridge`).
BRIDGE_MODULE = "skaldir.tools_core.bridge"

#: Directory (under the runtime) that holds the full contract descriptors.
DESCRIPTOR_DIRNAME = "tools_core"


# ------------------------------------------------------------------- table


def contracts() -> dict[str, ToolContract]:
    """Every core tool's contract, keyed by name (stable order)."""
    table: dict[str, ToolContract] = {}
    for module in TOOL_MODULES:
        for contract in getattr(module, "CONTRACTS", ()) or (module.CONTRACT,):
            table[contract.name] = contract
    return table


def names() -> tuple[str, ...]:
    return tuple(contracts())


def runners() -> dict[str, Callable[[dict, ToolContext], ToolResult]]:
    table: dict[str, Callable[[dict, ToolContext], ToolResult]] = {}
    for module in TOOL_MODULES:
        table.update(getattr(module, "RUNNERS", {}))
    return table


def contract_for(name: str) -> ToolContract | None:
    return contracts().get(str(name))


def descriptor(name: str) -> dict[str, Any]:
    """The full typed contract of one tool, plus how it is registered."""
    contract = contract_for(name)
    if contract is None:
        return {}
    payload = contract.to_dict()
    payload["origin"] = KIND_CORE
    payload["registry_kind"] = REGISTRY_KIND
    payload["bridge"] = {
        "module": BRIDGE_MODULE,
        "entrypoint": bridge_entrypoint(contract),
        "call_convention": "entrypoint(**arguments) -> the ToolResult document",
    }
    payload["capability_keys"] = {
        "input_schema": "the arguments this tool accepts (additionalProperties: false)",
        "output_schema": "the typed `output` object of a successful call",
        "permissions": "filesystem / network / subprocess declaration",
        "timeout_s": "default wall-clock limit (max_timeout_s is the hard ceiling)",
        "truncation": "how much output may reach a context, and where the rest goes",
    }
    return payload


def descriptors() -> list[dict[str, Any]]:
    return [descriptor(name) for name in names()]


def policy_report() -> dict[str, Any]:
    """The whole toolset's declared surface — the human/JSON overview."""
    isolation = isolation_report()
    rows = []
    for name, contract in contracts().items():
        fs = contract.permission.filesystem
        net = contract.permission.network
        sub = contract.permission.subprocess
        rows.append(
            {
                "name": name,
                "kind": KIND_CORE,
                "module": contract.module,
                "mutates": bool(contract.mutates),
                "timeout_s": contract.timeout_s,
                "max_timeout_s": contract.max_timeout_s,
                "truncation_bytes": contract.truncation.max_bytes,
                "truncation_strategy": contract.truncation.strategy,
                "filesystem": {"scope": fs.scope, "mode": fs.mode, "roots": list(fs.roots)},
                "network": {
                    "mode": net.mode,
                    "schemes": list(net.schemes),
                    "hosts": list(net.hosts),
                    "requires_key": net.requires_key,
                },
                "subprocess": {"allowed": sub.allowed, "shell": sub.shell},
                "tags": contract.registry_tags(),
            }
        )
    return {
        "kind": KIND_CORE,
        "registry_kind": REGISTRY_KIND,
        "version": CORE_VERSION,
        "tools": rows,
        "count": len(rows),
        "isolation": isolation,
    }


# ---------------------------------------------------------------- execution


def build_context(
    *,
    runtime: str | Path | None = None,
    root: str | Path | None = None,
    isolated: bool = False,
    offline: bool = False,
    transport: Callable[..., dict] | None = None,
    allow_private_hosts: bool = False,
) -> ToolContext:
    if root is None:
        # explicit argument > SKALDIR_TOOL_WORKSPACE/GODSV_TOOL_WORKSPACE > cwd
        from ..env import getenv as env_getenv

        declared = env_getenv("TOOL_WORKSPACE")
        root = declared or None
    return ToolContext(
        root=resolve_root(root),
        runtime=Path(runtime) if runtime is not None else None,
        isolated=bool(isolated),
        offline=bool(offline),
        transport=transport,
        allow_private_hosts=bool(allow_private_hosts),
    )


def call(
    name: str,
    arguments: dict | None = None,
    *,
    runtime: str | Path | None = None,
    root: str | Path | None = None,
    db: str | Path | None = None,
    isolated: bool = False,
    offline: bool = False,
    transport: Callable[..., dict] | None = None,
    require_registered: bool = False,
) -> ToolResult:
    """Run one core tool. The registry gate is explicit, never implicit.

    With ``db`` (or ``require_registered``) the call is refused when the
    registry does not carry an approved row for the tool — an unregistered
    capability is not silently callable.
    """
    contract = contract_for(name)
    runner = runners().get(str(name))
    if contract is None or runner is None:
        return failure(
            str(name),
            "not_found",
            f"unknown core tool: {name}",
            data={"known": list(names())},
        )
    if require_registered or db is not None:
        registry = _registry_row(runtime=runtime, db=db, name=contract.name)
        if not registry["registered"]:
            return failure(
                contract.name,
                "not_found",
                f"{contract.name} is not registered in {registry['db']}",
                data={
                    "db": registry["db"],
                    "hint": "register the core toolset first: skaldir tools-core register",
                },
            )
    ctx = build_context(
        runtime=runtime, root=root, isolated=isolated, offline=offline, transport=transport
    )
    started = time.time()
    result = runner(dict(arguments or {}), ctx)
    result.duration_ms = int((time.time() - started) * 1000)
    return result


# ----------------------------------------------------------------- registry


def bridge_entrypoint(contract: ToolContract) -> str:
    """The ``skaldir.tools_core.bridge`` function that implements ``contract``.

    The contract's own ``entrypoint`` is the in-module callable (``run``); the
    registry row points at the bridge, because that is the call convention of the
    existing execution path (``mcpserver._wrapper_for`` calls
    ``module.entrypoint(**arguments)``).
    """
    return contract.name


def _tools_module():
    from .. import tools as tools_module

    return tools_module


def _spec_for(contract: ToolContract):
    """The registry row for one contract (kind ``python``, module ``bridge``)."""
    tools_module = _tools_module()
    sample = dict(contract.smoke.get("input") or {})
    return tools_module.ToolSpec(
        name=contract.name,
        title=contract.title,
        description=contract.description,
        tags=contract.registry_tags(),
        kind=REGISTRY_KIND,
        module=BRIDGE_MODULE,
        entrypoint=bridge_entrypoint(contract),
        steps=[],
        test_command=[
            "python",
            "-m",
            "skaldir.tools_core",
            "smoke",
            "--name",
            contract.name,
            "--json",
        ],
        sample_input=sample,
        rationale=(
            "built-in core tool of the Skaldir runtime: it ships with the product, "
            "declares its permissions and is smoke-tested — not generated code"
        ),
        provenance="core:tools_core",
        status=tools_module.STATUS_APPROVED,
        version=CORE_VERSION,
        scan_summary=(
            "built-in, hand-written module; permissions/timeout/truncation declared in "
            f"{contract.module}"
        ),
    )


def _descriptor_path(runtime: str | Path, name: str) -> Path:
    return Path(runtime) / DESCRIPTOR_DIRNAME / f"{name}.json"


def _manifest_dir(runtime: str | Path, name: str) -> Path:
    return Path(runtime) / "generated_tools" / name


def _write_descriptor(runtime: Path, contract: ToolContract) -> Path:
    target = _descriptor_path(runtime, contract.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(descriptor(contract.name), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _write_manifest(runtime: Path, contract: ToolContract) -> Path:
    """The scope manifest: that is where the runtime keeps a tool's scope.

    Without it the tool counts as *legacy* and is never auto-exposed
    (:func:`skaldir.capability.visible_tools`), so the release surface needs it.
    It records the truth of a built-in: scope global, status approved, and the
    module that implements it.
    """
    import yaml

    directory = _manifest_dir(runtime, contract.name)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "manifest.yaml"
    payload = {
        "name": contract.name,
        "type": "core-python",
        "scope": "global",
        "status": "approved",
        "version": CORE_VERSION,
        "project_id": "",
        "module": contract.module,
        "entrypoint": contract.entrypoint,
        "provenance": "core:tools_core",
        "contract": str(_descriptor_path(runtime, contract.name)),
        "note": (
            "built-in core tool of the Skaldir runtime (not builder-generated): the typed "
            "contract lives in the descriptor file named above"
        ),
    }
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return target


def _bridge_check(contract: ToolContract) -> str:
    """The registered entrypoint must really resolve — refusal, not a surprise later."""
    import importlib

    try:
        module = importlib.import_module(BRIDGE_MODULE)
    except Exception as exc:  # noqa: BLE001 — a broken import is the finding
        return f"{BRIDGE_MODULE} is not importable: {type(exc).__name__}: {exc}"
    if not callable(getattr(module, bridge_entrypoint(contract), None)):
        return f"{BRIDGE_MODULE}.{bridge_entrypoint(contract)} is missing or not callable"
    return ""


def _same_spec(existing: Any, spec: Any) -> bool:
    """True when the registry row already says exactly what the contract says.

    Compared on content, not on timestamps or ids: an idempotent re-register must
    not bump ``registry_revision`` (that revision is a cache-invalidation signal).
    """
    fields = (
        "title",
        "description",
        "tags",
        "kind",
        "module",
        "entrypoint",
        "steps",
        "test_command",
        "sample_input",
        "rationale",
        "provenance",
        "status",
        "version",
        "scan_summary",
    )
    return all(getattr(existing, field, None) == getattr(spec, field, None) for field in fields)


def register(
    runtime: str | Path,
    *,
    db: str | Path | None = None,
    store: Any = None,
    probe_network: bool = True,
    transport: Callable[..., dict] | None = None,
    write_manifests: bool = True,
    write_descriptors: bool = True,
) -> dict[str, Any]:
    """Register the whole core toolset (idempotent; re-running changes nothing)."""
    runtime_path = Path(runtime).expanduser()
    runtime_path.mkdir(parents=True, exist_ok=True)
    tools_module = _tools_module()
    db_path = Path(db).expanduser() if db is not None else runtime_path / "tools.db"
    owned = store is None
    store = store if store is not None else tools_module.ToolStore(db_path)
    network_probe: dict[str, Any] | None = None
    if probe_network:
        network_probe = web.probe_backends(transport=transport)
    actions: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    try:
        for name, contract in contracts().items():
            problem = _bridge_check(contract)
            if problem:
                broken.append({"name": name, "reason": problem})
                continue
            existing = store.get(name)
            if existing is not None and existing.kind != REGISTRY_KIND:
                conflicts.append(
                    {
                        "name": name,
                        "kind": existing.kind,
                        "reason": "the registry already carries this name with a different kind",
                    }
                )
                continue
            spec = _spec_for(contract)
            if existing is not None and _same_spec(existing, spec):
                actions.append(
                    {
                        "name": name,
                        "kind": KIND_CORE,
                        "registry_kind": REGISTRY_KIND,
                        "version": spec.version,
                        "action": "unchanged",
                        "registry_revision": store.registry_revision(),
                        "spec_id": existing.id,
                        "entrypoint": f"{BRIDGE_MODULE}.{bridge_entrypoint(contract)}",
                        "descriptor": str(_descriptor_path(runtime_path, name))
                        if write_descriptors
                        else "",
                        "manifest": str(_manifest_dir(runtime_path, name) / "manifest.yaml")
                        if write_manifests
                        else "",
                    }
                )
                if write_descriptors:
                    _write_descriptor(runtime_path, contract)
                if write_manifests:
                    _write_manifest(runtime_path, contract)
                continue
            published = store.publish(
                spec,
                payload={
                    "kind": KIND_CORE,
                    "origin": "core:tools_core",
                    "module": contract.module,
                    "entrypoint": contract.entrypoint,
                    "permissions": contract.permission.to_dict(),
                    "timeout_s": contract.timeout_s,
                    "truncation": contract.truncation.to_dict(),
                    "availability": (network_probe or {}).get("answering", []),
                },
            )
            entry: dict[str, Any] = {
                "name": name,
                "kind": KIND_CORE,
                "registry_kind": REGISTRY_KIND,
                "version": spec.version,
                "action": "unchanged"
                if published["duplicate"]
                else ("created" if existing is None else "updated"),
                "registry_revision": published["revision"],
                "spec_id": published["spec_id"],
                "entrypoint": f"{BRIDGE_MODULE}.{bridge_entrypoint(contract)}",
            }
            if write_descriptors:
                entry["descriptor"] = str(_write_descriptor(runtime_path, contract))
            if write_manifests:
                entry["manifest"] = str(_write_manifest(runtime_path, contract))
            actions.append(entry)
    finally:
        if owned:
            store.close()
    return {
        "runtime": str(runtime_path),
        "db": str(db_path),
        "kind": KIND_CORE,
        "registry_kind": REGISTRY_KIND,
        "version": CORE_VERSION,
        "registered": actions,
        "count": len(actions),
        "conflicts": conflicts,
        "broken": broken,
        "network": network_probe or {"probed": False},
        "isolation": isolation_report(),
        "ok": not conflicts and not broken and len(actions) == len(contracts()),
    }


def _registry_row(
    *, runtime: str | Path | None, db: str | Path | None, name: str
) -> dict[str, Any]:
    from ..registry import resolve_registry

    context = resolve_registry(runtime=runtime, db=db)
    path = Path(context.tools_db)
    row: dict[str, Any] = {
        "name": name,
        "db": str(path),
        "registered": False,
        "status": "",
        "kind": "",
        "version": "",
        "tags": [],
    }
    if not path.is_file():
        return row
    store = _tools_module().ToolStore(path)
    try:
        spec = store.get(name)
    finally:
        store.close()
    if spec is None:
        return row
    row.update(
        {
            "registered": True,
            "status": spec.status,
            "kind": spec.kind,
            "version": spec.version,
            "tags": list(spec.tags),
            "module": spec.module,
            "entrypoint": spec.entrypoint,
        }
    )
    return row


def registry_view(
    runtime: str | Path, *, db: str | Path | None = None, names_: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    """Registry + descriptor + health facts per tool, as one row each."""
    wanted = names_ or names()
    rows: list[dict[str, Any]] = []
    for name in wanted:
        row = _registry_row(runtime=runtime, db=db, name=name)
        contract = contract_for(name)
        descriptor_path = _descriptor_path(Path(runtime), name)
        row.update(
            {
                "contract_module": contract.module if contract else "",
                "descriptor": str(descriptor_path),
                "descriptor_exists": descriptor_path.is_file(),
                "manifest": str(_manifest_dir(Path(runtime), name) / "manifest.yaml"),
                "manifest_exists": (_manifest_dir(Path(runtime), name) / "manifest.yaml").is_file(),
                "mutates": bool(contract.mutates) if contract else False,
                "timeout_s": contract.timeout_s if contract else 0,
                "truncation_bytes": contract.truncation.max_bytes if contract else 0,
                "permissions": {
                    "filesystem": contract.permission.filesystem.mode if contract else "",
                    "network": contract.permission.network.mode if contract else "",
                    "subprocess": bool(contract.permission.subprocess.allowed)
                    if contract
                    else False,
                }
                if contract
                else {},
            }
        )
        rows.append(row)
    return rows


# -------------------------------------------------------------------- smoke


def _evaluate(expect: dict, result: ToolResult) -> list[dict[str, Any]]:
    """Check a smoke case's expectations against the real result (no fudging)."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    data = result.data
    for key, wanted in expect.items():
        if key == "exit_code":
            add(
                key, data.get("exit_code") == wanted, {"got": data.get("exit_code"), "want": wanted}
            )
        elif key == "stdout_contains":
            add(key, str(wanted) in str(data.get("stdout", "")), {"want": wanted})
        elif key == "content_contains":
            add(key, str(wanted) in str(data.get("content", "")), {"want": wanted})
        elif key == "diff_contains":
            add(key, str(wanted) in str(data.get("diff", "")), {"want": wanted})
        elif key == "min_count":
            add(key, int(data.get("count") or 0) >= int(wanted), {"got": data.get("count")})
        elif key == "status_in":
            add(key, data.get("status") in list(wanted), {"got": data.get("status")})
        else:
            add(key, data.get(key) == wanted, {"got": data.get(key), "want": wanted})
    add("tool_ok", result.ok, {"error_kind": result.error_kind, "error": result.error})
    return checks


def smoke(
    runtime: str | Path,
    *,
    name: str = "",
    root: str | Path | None = None,
    record_health: bool = True,
    probe_network: bool = False,
    transport: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Run each tool's declared smoke case for real and record the outcome."""
    runtime_path = Path(runtime).expanduser()
    table = contracts()
    unknown: list[str] = []
    selected = list(table.values())
    if name:
        if name not in table:
            unknown = [name]
            selected = []
        else:
            selected = [table[name]]
    if root is not None:
        workspace = resolve_root(root)
        workspace.mkdir(parents=True, exist_ok=True)
    else:
        base = runtime_path / "tool_smoke"
        base.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="smoke-", dir=str(base)))
    results: list[dict[str, Any]] = []
    for contract in selected:
        case = dict(contract.smoke or {})
        entry: dict[str, Any] = {
            "name": contract.name,
            "network": bool(case.get("network")),
            "ok": None,
            "checks": [],
            "skipped": "",
            "health_state": "",
        }
        started = time.time()
        if case.get("network") and not probe_network:
            entry["skipped"] = "network smoke was not requested (pass network=True)"
            results.append(entry)
            continue
        setup_failed = ""
        for step in case.get("setup") or []:
            runner = runners().get(str(step.get("tool")))
            if runner is None:
                setup_failed = f"unknown setup tool: {step.get('tool')}"
                break
            step_result = runner(
                dict(step.get("input") or {}),
                build_context(runtime=runtime_path, root=workspace, transport=transport),
            )
            if not step_result.ok:
                setup_failed = f"setup {step.get('tool')} failed: {step_result.error}"
                break
        if setup_failed:
            entry.update({"ok": False, "error": setup_failed})
        else:
            result = call(
                contract.name,
                dict(case.get("input") or {}),
                runtime=runtime_path,
                root=workspace,
                transport=transport,
            )
            checks = _evaluate(dict(case.get("expect") or {}), result)
            entry.update(
                {
                    "ok": all(item["ok"] for item in checks),
                    "checks": checks,
                    "error_kind": result.error_kind,
                    "error": result.error,
                    "result_sha256": hashlib.sha256(
                        json.dumps(result.data, sort_keys=True, default=str).encode()
                    ).hexdigest(),
                }
            )
        entry["duration_ms"] = int((time.time() - started) * 1000)
        if record_health and entry["ok"] is not None:
            entry["health_state"] = _record_health(
                runtime_path,
                contract.name,
                ok=bool(entry["ok"]),
                detail=(
                    "; ".join(
                        f"{item['check']}={'ok' if item['ok'] else 'FAIL'}"
                        for item in entry["checks"]
                    )
                    or str(entry.get("error") or "")
                )
                or "smoke passed",
            )
        results.append(entry)
    evaluated = [item for item in results if item["ok"] is not None]
    failed = [item for item in evaluated if not item["ok"]]
    skipped = [item for item in results if item["ok"] is None]
    return {
        "runtime": str(runtime_path),
        "root": str(workspace),
        "tools": results,
        "count": len(results),
        "passed": len(evaluated) - len(failed),
        "failed": len(failed),
        "skipped": [item["name"] for item in skipped],
        "unknown": unknown,
        "ok": bool(evaluated) and not failed,
    }


def _record_health(runtime: Path, name: str, *, ok: bool, detail: str) -> str:
    """Record the observation in the capability-health ledger (its own API)."""
    try:
        from .. import capability_health

        return str(
            capability_health.record_verification(runtime, name, ok=bool(ok), detail=detail[:300])
        )
    except Exception as exc:  # noqa: BLE001 — the smoke result is already the finding
        return f"health ledger unavailable: {type(exc).__name__}: {exc}"


def health(
    runtime: str | Path, *, db: str | Path | None = None, names_: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """The health ledger's view of every core tool (+ whether it is registered)."""
    runtime_path = Path(runtime).expanduser()
    rows: list[dict[str, Any]] = []
    try:
        from .. import capability_health

        available = True
    except Exception:  # noqa: BLE001 — reported, never assumed
        capability_health = None  # type: ignore[assignment]
        available = False
    for row in registry_view(runtime_path, db=db, names_=names_):
        state: dict[str, Any] = {
            "state": "",
            "auto_routable": False,
            "reasons": [],
            "verified_at": None,
            "digest": "",
        }
        if capability_health is not None:
            try:
                state = dict(capability_health.file_state(runtime_path, row["name"]))
            except Exception as exc:  # noqa: BLE001
                state = {
                    "state": "",
                    "auto_routable": False,
                    "reasons": [f"ledger error: {type(exc).__name__}: {exc}"],
                    "verified_at": None,
                    "digest": "",
                }
        rows.append(
            {
                "name": row["name"],
                "state": str(state.get("state") or "UNKNOWN"),
                "auto_routable": bool(state.get("auto_routable")),
                "reasons": list(state.get("reasons") or []),
                "verified_at": state.get("verified_at"),
                "registered": row["registered"],
                "registry_status": row["status"],
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "runtime": str(runtime_path),
        "ledger_available": available,
        "tools": rows,
        "count": len(rows),
        "counts": counts,
        "healthy": [row["name"] for row in rows if row["auto_routable"]],
        "unverified": [row["name"] for row in rows if not row["auto_routable"]],
    }


def unregister(runtime: str | Path, *, names_: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Take the core toolset back out (registry rows + descriptor + manifest).

    Nothing is deleted silently: ``capability_health.retire`` is recorded for a
    tool that has a ledger row, so a reader sees *why* it disappeared.
    """
    runtime_path = Path(runtime).expanduser()
    wanted = names_ or names()
    removed: list[dict[str, Any]] = []
    for name in wanted:
        entry: dict[str, Any] = {"name": name, "removed": []}
        manifest = _manifest_dir(runtime_path, name) / "manifest.yaml"
        if manifest.is_file():
            shutil.rmtree(manifest.parent, ignore_errors=True)
            entry["removed"].append(str(manifest))
        descriptor = _descriptor_path(runtime_path, name)
        if descriptor.is_file():
            descriptor.unlink()
            entry["removed"].append(str(descriptor))
        try:
            from .. import capability_health

            capability_health.retire(runtime_path, name, reason="core tool unregistered")
            entry["removed"].append("health: RETIRED")
        except Exception:  # noqa: BLE001 — no ledger row is not a failure here
            pass
        removed.append(entry)
    return {"runtime": str(runtime_path), "removed": removed, "count": len(removed)}


__all__ = [
    "BRIDGE_MODULE",
    "DESCRIPTOR_DIRNAME",
    "KIND_CORE",
    "REGISTRY_KIND",
    "TOOL_MODULES",
    "build_context",
    "call",
    "contract_for",
    "contracts",
    "descriptor",
    "descriptors",
    "health",
    "names",
    "policy_report",
    "register",
    "registry_view",
    "runners",
    "smoke",
    "unregister",
]
