"""CLI surface of the container half of the self-expanding tool system.

Feature #1 ("self-expanding tool system") has a second half: a capability that
lives in a CONTAINER image. This module is the CLI for it, and nothing else —
the pipeline itself is ``skaldir.toolbuilder.ContainerToolBuilder``:

    tools mine --image …      search → inspect the container surface → generate a
                              typed adapter → TEST it against the real container
    tools adapter show …      the typed adapter, its permissions, its evidence
    tools register …          register the TESTED adapter live (version, health,
                              permissions, rollback snapshot)
    tools rollback …          take it back: registry, files, health, containers
    tools health …            ledger state + live container facts (+ --probe)

**Integration (the only change cli.py needs — 3 lines).** In
``cli.build_parser()``, directly after the ``tools`` subparsers exist::

    p_tools = sub.add_parser("tools", help="tool-suite utilities (propose-first, gated)")
    tools_sub = p_tools.add_subparsers(dest="subcommand", required=True)
    from .toolbuilder_cli import register_cli as _register_container_tools   # +
    _register_container_tools(tools_sub)                                     # +

``register_cli`` adds the new commands AND upgrades two existing parsers
(``mine`` and ``build``) with the container flags, so the previously unreachable
docker half of the builder is reachable from the same command names. The
contract entries for the new commands belong in ``cli_contract.COMMANDS``
(they are listed in the module docstring of the wave report, since that file is
owned by the centre).

A capability is never registered without a passed test: the exit codes carry
that, not the prose — ``tools register`` exits 2 (verification_failed) when the
evidence is missing, failed or no longer covers the adapter bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import cli_contract
from .cli_shared import registry_from_args

#: A structured failure class → the CLI exit code. Nothing else maps a failure
#: onto a code, so no handler can invent a green.
FAILURE_EXIT = {
    # an existing capability is not a build candidate (a conflict, not a bug)
    "reuse": cli_contract.EXIT_CONFLICT,
    # evidence problems: the tool exists but is NOT proven → never registered
    "test_failed": cli_contract.EXIT_VERIFICATION_FAILED,
    "unverified": cli_contract.EXIT_VERIFICATION_FAILED,
    "adapter_changed": cli_contract.EXIT_VERIFICATION_FAILED,
    # unknown tool / no staged adapter
    "not_found": cli_contract.EXIT_NOT_FOUND,
    # the surface could not be inspected or the capability does not map onto it
    "docker_unreachable": cli_contract.EXIT_BLOCKED,
    "surface_unavailable": cli_contract.EXIT_BLOCKED,
    "pin_required": cli_contract.EXIT_BLOCKED,
    "image_required": cli_contract.EXIT_BLOCKED,
    "image_missing": cli_contract.EXIT_BLOCKED,
    # a registry rate limit is infrastructure, not a defect of the capability:
    # BLOCKED (retryable), never verification_failed
    "quota_limit": cli_contract.EXIT_BLOCKED,
    "arch_mismatch": cli_contract.EXIT_BLOCKED,
    "docker_error": cli_contract.EXIT_BLOCKED,
    "adapter_refused": cli_contract.EXIT_BLOCKED,
    "adapter_unreadable": cli_contract.EXIT_BLOCKED,
    "unknown_verb": cli_contract.EXIT_BLOCKED,
    "service_unavailable": cli_contract.EXIT_BLOCKED,
}

EV_NEW_COMMANDS = (
    # only LEAVES need a contract entry (cli_contract.dump lists leaves); the
    # `adapter` group itself carries no entry.
    "tools.adapter.show: layer EXPAND, exit_codes=(0, 3, 5, 7), "
    "evidence=(src/skaldir/toolbuilder.py ContainerToolBuilder.container_adapter, "
    "src/skaldir/toolbuilder_cli.py _cmd_tools_adapter_show)",
    "tools.register: layer EXPAND, mutates=True, exit_codes=(0, 2, 5, 7), "
    "notes='exit 2 = the adapter is not (still) proven: no registration'",
    "tools.rollback: layer EXPAND, mutates=True, exit_codes=(0, 5, 7, 3)",
    "tools.health: layer EXPAND, exit_codes=(0, 3, 5, 7)",
    "tools.mine: gains --image/--capability/--name/--verb/--digest/--pull/"
    "--test-input/--expect/--runtime/--db, exit_codes=(0, 2, 3, 5, 6, 7)",
    "tools.build: gains --image/--digest/--pull (runtime=docker), "
    "exit_codes=(0, 2, 3, 4, 6, 7)",
)


def _builder(args: argparse.Namespace):
    """A container builder on the resolved runtime with the resolved tools DB."""
    from .toolbuilder import ContainerToolBuilder
    from .tools import ToolStore

    context = registry_from_args(args)
    Path(context.tools_db).parent.mkdir(parents=True, exist_ok=True)
    return ContainerToolBuilder(context.runtime_dir, store=ToolStore(str(context.tools_db)))


def _exit_for(result: dict) -> int:
    """The exit code of a structured result — never a success for a failure."""
    if result.get("ok"):
        return cli_contract.EXIT_OK
    failure_class = str(result.get("failure_class") or "")
    reason = str(result.get("reason") or "the container capability cannot be built")
    hints = {
        "reuse": "use the existing tool instead of building a duplicate",
        "unverified": "a capability is only registered on evidence — re-mine it",
        "test_failed": "re-mine: the adapter must pass against the real container first",
        "adapter_changed": "re-mine: the adapter was edited after its test",
        "not_found": "`tools mine --image <img> --capability <text>` stages one first",
        "quota_limit": (
            "the registry is rate-limiting this machine (429 Too Many Requests) — "
            "wait, or build from the digest already local (drop --pull)"
        ),
        "image_missing": "that image/tag does not exist in the registry — check the name",
    }
    code = FAILURE_EXIT.get(failure_class, cli_contract.EXIT_BLOCKED)
    print(reason)
    return cli_contract.fail(code, reason, hint=hints.get(failure_class, ""))


def _surface_summary(surface: dict, *, verb: str = "") -> dict:
    """The inspection record, small enough for an envelope (full one on disk)."""
    verbs = list((surface.get("cli") or {}).get("verbs") or [])
    from .toolbuilder import VERB_TEMPLATES, _template_verbs

    generator_verbs = sorted(
        {
            name
            for item in VERB_TEMPLATES
            for name in _template_verbs(item)
            if name in set(verbs)
        }
    )
    return {
        "inspected": bool(surface.get("inspected")),
        "reason": surface.get("reason", ""),
        "image": surface.get("image", ""),
        "image_digest": surface.get("image_digest", ""),
        # a surface that was only reachable with the image's ENTRYPOINT cleared is
        # a material fact about the capability, not a detail of the probe
        "entrypoint": list(surface.get("entrypoint") or []),
        "entrypoint_bypass": bool(surface.get("entrypoint_bypass")),
        "blocked_by": str(surface.get("blocked_by") or ""),
        "os": dict(surface.get("os") or {}),
        "verbs": {
            "count": len(verbs),
            "matched": verb,
            # every verb some generator template really implements, on THIS surface
            "generator_verbs": generator_verbs[:12],
        },
        "api": dict(surface.get("api") or {}),
        "mcp": dict(surface.get("mcp") or {}),
        "probes": [
            {"probe": probe.get("probe"), "ok": probe.get("ok"),
             "exit_code": probe.get("exit_code"), "argv": probe.get("argv"),
             "entrypoint_bypass": bool(probe.get("entrypoint_bypass"))}
            for probe in surface.get("probes") or []
        ],
    }


# --------------------------------------------------------------------------- #
# tools mine --image (the container half) / tools build --image
# --------------------------------------------------------------------------- #


def _cmd_tools_mine_container(args: argparse.Namespace) -> int:
    """Mine a capability from a container image: inspect → adapt → TEST."""
    capability = str(getattr(args, "capability", "") or "").strip()
    if not capability:
        print("--image needs --capability: say what the capability must do")
        return cli_contract.usage_error(
            "--image needs --capability",
            hint='tools mine --image alpine:3.20 --capability "compute the sha-256 '
                 'digest of a string"',
        )
    test_input: dict = {}
    raw_input = str(getattr(args, "test_input", "") or "").strip()
    if raw_input:
        try:
            test_input = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            print(f"--test-input is not JSON: {exc}")
            return cli_contract.usage_error("--test-input is not JSON")

    schema = None
    schema_path = str(getattr(args, "schema", "") or "").strip()
    if schema_path:
        from .toolbuilder import AdapterRefusal, load_schema_template

        try:
            schema = load_schema_template(schema_path)
        except AdapterRefusal as exc:
            print(str(exc))
            return cli_contract.usage_error(str(exc))

    builder = _builder(args)
    result = builder.mine_container(
        capability,
        image=str(getattr(args, "image", "") or ""),
        name=str(getattr(args, "name", "") or ""),
        image_digest=str(getattr(args, "digest", "") or ""),
        pull=bool(getattr(args, "pull", False)),
        verb=str(getattr(args, "verb", "") or ""),
        test_input=test_input or None,
        expect=str(getattr(args, "expect", "") or ""),
        session_id=str(getattr(args, "session", "") or ""),
        schema=schema,
    )

    surface = result.get("surface") or {}
    payload = {
        "name": result.get("name", ""),
        "capability": capability,
        "image": result.get("image", ""),
        "image_digest": result.get("image_digest", ""),
        "search": result.get("search", {}),
        "surface": _surface_summary(
            surface, verb=str((result.get("adapter") or {}).get("verb") or "")
        ),
        "adapter": result.get("adapter", {}),
        "test": {
            **{key: (result.get("test") or {}).get(key) for key in
               ("ok", "input", "expected", "observed", "oracle", "exit_code", "detail")},
            # an argument-mapping call is certified output by output, and the
            # artefacts it wrote are named — both are part of the verdict
            "verdicts": list((result.get("test") or {}).get("verdicts") or []),
            "artefacts": dict((result.get("test") or {}).get("artefacts") or {}),
            "oracle_note": (result.get("test") or {}).get("oracle_note", ""),
        },
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
    if result.get("staged"):
        cli_contract.add_evidence(str(result.get("adapter_path")), "file",
                                 "the typed adapter that was generated")
        cli_contract.add_evidence(str(result.get("evidence_path")), "file",
                                 "the real container test that proves it")
    else:
        cli_contract.add_evidence(
            "src/skaldir/toolbuilder.py inspect_container_surface", "code",
            "the surface probe that refused to generate an adapter",
        )
    for stage in result.get("stages", []):
        print(f"  {stage['stage']:16s} {'ok' if stage['ok'] else 'FAILED'}")
    if result.get("ok"):
        print(
            f"staged {result['name']} → {result['test'].get('observed')} "
            f"(expected {result['test'].get('expected')}); NOT registered yet — "
            f"run `tools register {result['name']}`"
        )
        cli_contract.next_action(f"tools register {result['name']}",
                                 "register the tested adapter live")
        return cli_contract.EXIT_OK
    code = _exit_for(result)
    print(f"mine did not produce a capability: {result.get('reason')}")
    return code


def _docker_build_request(args: argparse.Namespace):
    """A ``ToolBuildRequest`` for the container build path (runtime=docker)."""
    from .toolbuilder import ToolBuildRequest

    sample: dict = {}
    raw_sample = str(getattr(args, "sample", "") or "").strip()
    if raw_sample:
        try:
            sample = json.loads(raw_sample)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--sample is not JSON: {exc}") from exc
    context = registry_from_args(args)
    return ToolBuildRequest(
        capability=str(getattr(args, "capability", "") or ""),
        reason=str(getattr(args, "reason", "") or ""),
        requested_by=str(getattr(args, "requested_by", "") or "cli"),
        kind=str(getattr(args, "kind", "") or "auto"),
        name=str(getattr(args, "name", "") or ""),
        source_path=str(getattr(args, "source", "") or ""),
        tests_path=str(getattr(args, "tests", "") or ""),
        module=str(getattr(args, "module", "") or ""),
        entrypoint=str(getattr(args, "entrypoint", "") or ""),
        sample_input=sample,
        required_secrets=list(getattr(args, "secret", None) or []),
        allowed_domains=list(getattr(args, "domain", None) or []),
        dependencies=list(getattr(args, "dependency", None) or []),
        project_id=context.project_id,
        runtime="docker",
        image=str(getattr(args, "image", "") or ""),
        image_digest=str(getattr(args, "digest", "") or ""),
        # default is hub-independent (the local digest is the pin); --pull asks
        # the registry even then
        pull=bool(getattr(args, "pull", False)),
    )


def _cmd_tools_build_docker(args: argparse.Namespace) -> int:
    """`tools build --image …`: the container half of the gated build pipeline."""
    from .toolbuilder import ToolBuilder
    from .tools import ToolStore

    context = registry_from_args(args)
    Path(context.tools_db).parent.mkdir(parents=True, exist_ok=True)
    request = _docker_build_request(args)
    builder = ToolBuilder(context.runtime_dir, store=ToolStore(str(context.tools_db)))
    result = builder.build(request)
    probe = result.get("container_probe") or {}
    payload = {
        "build_id": result.get("build_id", ""),
        "name": result.get("name", ""),
        "kind": result.get("kind", ""),
        "runtime": "docker",
        "image": request.image,
        "image_digest": result.get("image_digest") or probe.get("image_digest", ""),
        "container_probe": {
            "docker": probe.get("docker", {}),
            "probe": probe.get("probe", {}),
            "policy_problems": probe.get("policy_problems", []),
            "teardown": probe.get("teardown", {}),
        },
        "gates": result.get("gates", []),
        "ok": bool(result.get("ok")),
        "failure_class": result.get("failure_class", ""),
        "error": result.get("error", ""),
        "blocked": result.get("blocked", ""),
        "flow_outcome": result.get("flow_outcome", ""),
        "wrapper": result.get("wrapper", ""),
    }
    cli_contract.set_data(payload)
    cli_contract.add_evidence(
        "src/skaldir/toolbuilder.py ToolBuilder.manager", "code",
        "the docker runtime the container probe of this build runs through",
    )
    if probe:
        cli_contract.add_evidence(
            "docker run (one-shot, --rm)", "command",
            f"image {request.image} → probe {probe.get('probe', {}).get('exit_code')}",
        )
    for check in result.get("gates", []) or []:
        state = "ok" if check["ok"] else "FAILED"
        print(f"  {check['name']:20s} {state}  {check.get('detail', '')[:60]}")
    if result.get("ok"):
        print(f"built and registered {result.get('name')} ({result.get('wrapper')})")
        return cli_contract.EXIT_OK
    print(f"build did not complete: {result.get('error')}")
    if result.get("blocked"):
        return cli_contract.blocked(str(result.get("error") or "build blocked"),
                                    hint="see data.container_probe for the structured reason")
    return cli_contract.verification_failed(
        str(result.get("error") or "the build failed its gates"),
        details=payload.get("container_probe"),
    )


# --------------------------------------------------------------------------- #
# tools adapter show / register / rollback / health
# --------------------------------------------------------------------------- #


def _cmd_tools_adapter_show(args: argparse.Namespace) -> int:
    """The typed adapter of a staged/registered container tool."""
    report = _builder(args).container_adapter(str(args.name))
    if not report.get("ok"):
        print(str(report.get("reason") or "unknown container tool"))
        return cli_contract.not_found(
            str(report.get("reason") or f"unknown container tool: {args.name}"),
            hint="`tools mine --image <img> --capability <text>` stages one first",
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
    cli_contract.add_evidence(
        f"docker image {adapter.get('image')}@{adapter.get('image_digest')}", "command",
        "the image the adapter is pinned to",
    )
    print(
        f"{report['name']}: {adapter.get('verb')} in {adapter.get('image')} "
        f"@{str(adapter.get('image_digest'))[:19]}… "
        f"inputs={sorted((adapter.get('operation') or {}).get('inputs') or {})} "
        f"outputs={sorted((adapter.get('operation') or {}).get('outputs') or {})}"
    )
    print(f"  permissions: {json.dumps(report.get('permissions', {}), ensure_ascii=False)}")
    return cli_contract.EXIT_OK


def _cmd_tools_register(args: argparse.Namespace) -> int:
    """Register a TESTED container adapter live (version/health/permissions)."""
    result = _builder(args).register_container_tool(
        str(args.name),
        version=str(getattr(args, "version", "") or ""),
        project_id=str(getattr(args, "project_id", "") or ""),
        requested_by=str(getattr(args, "requested_by", "") or "cli"),
    )
    payload = {
        key: result.get(key) for key in
        ("name", "version", "status", "kind", "image", "image_digest", "permissions",
         "health_state", "rollback_id", "publish", "wrapper", "test", "failure_class",
         "reason")
    }
    payload["registered"] = bool(result.get("registered"))
    payload["health"] = result.get("health", {})
    payload["spec"] = result.get("spec", {})
    cli_contract.set_data(payload)
    if result.get("registered"):
        cli_contract.add_evidence(str(result.get("wrapper")), "file",
                                 "the registered tool's wrapper (the call path)")
        cli_contract.add_evidence(
            "src/skaldir/toolbuilder.py ContainerToolBuilder.register_container_tool", "code",
            "the evidence gate that refuses an unverified registration",
        )
        print(
            f"registered {result['name']} v{result['version']} ({result['status']}) — "
            f"health {result.get('health_state')}, rollback {result.get('rollback_id')}"
        )
        print(f"  call: {result['wrapper']} '{{\"text\": \"…\"}}'")
        return cli_contract.EXIT_OK
    print(f"NOT registered: {result.get('reason')}")
    return _exit_for(result)


def _teardown_labels(result: dict) -> dict[str, str]:
    """The teardown scope as DATA: the label pairs the sweep really used.

    ``result["teardown_labels"]`` is the list of ``key=value`` pairs the
    builder's rollback passed to ``cleanup_by_labels`` — data, not prose — so a
    label VALUE containing a comma survives the trip. Only a result that carries
    no such list (an older/foreign result) falls back to the comma-joined TEXT
    form ``teardown_scope`` (``docker_runtime.label_filters``), where a comma
    inside a value cannot be told from the pair separator — which is exactly why
    the data form exists. An empty scope yields ``{}``: the runtime refuses a
    teardown without our labels, so there is nothing to list.
    """
    pairs = result.get("teardown_labels")
    labels: dict[str, str] = {}
    if isinstance(pairs, (list, tuple)) and pairs:
        for pair in pairs:
            key, _separator, value = str(pair).partition("=")
            if key.strip():
                labels[key.strip()] = value
        if labels:
            return labels
    for pair in str(result.get("teardown_scope") or "").split(","):
        key, _separator, value = pair.partition("=")
        if key.strip():
            labels[key.strip()] = value
    return labels


def _teardown_listing_command(labels: dict[str, str]) -> str:
    """The listing command a teardown really ran — ONE ``--filter`` per label.

    The label pairs are carried as DATA from the builder's rollback result
    (``teardown_labels``; see :func:`_teardown_labels`), never rebuilt from the
    comma-joined scope TEXT: putting the text into a single ``--filter label=…``
    would print the very comma-joined form the daemon reads as ONE label pair —
    the form that matched nothing and made every multi-label sweep a silent
    no-op — so the evidence would show a command line the runtime never
    executed. The argument is therefore built by the runtime's own
    ``label_filter_args`` (imported, not re-implemented), which is the one place
    the filter line comes from.

    No labels at all returns ``""``: the runtime refuses a teardown without our
    labels, so the caller claims no evidence instead of inventing a command.
    """
    from .docker_runtime import label_filter_args

    if not labels:
        return ""
    return " ".join(["docker", "ps", "-a", *label_filter_args(labels)])


def _cmd_tools_rollback(args: argparse.Namespace) -> int:
    """Roll a registered container capability back (registry/files/health/containers)."""
    result = _builder(args).rollback_container_tool(
        str(args.name), reason=str(getattr(args, "reason", "") or "")
    )
    payload = {
        key: result.get(key) for key in
        ("name", "rolled_back", "reason", "quarantine", "containers_removed",
         "teardown_scope", "teardown_labels", "teardown_ok", "teardown_detail",
         "health", "removed_registration", "restored_spec", "failure_class")
    }
    payload["rolled_back"] = bool(result.get("rolled_back"))
    cli_contract.set_data(payload)
    if not result.get("ok"):
        print(str(result.get("reason") or "nothing to roll back"))
        return cli_contract.not_found(
            str(result.get("reason") or f"unknown container tool: {args.name}")
        )
    cli_contract.add_evidence(str(result.get("quarantine") or ""), "file",
                             "the quarantined tool directory (nothing deleted)")
    scope_text = str(result.get("teardown_scope") or "")
    note = f"removed {len(result.get('containers_removed') or [])} container(s) of ours"
    if scope_text:
        note += f" — scope {scope_text}"
    # the listing form really executed: one --filter per label, built from the
    # label pairs the sweep was given (DATA, not the comma-joined scope text).
    # kind "run" (a declared evidence kind) — the earlier literal "command" is not
    # one of cli_contract.EVIDENCE_KINDS and was silently downgraded to "code".
    cli_contract.add_evidence(
        _teardown_listing_command(_teardown_labels(result)), "run", note
    )
    print(
        f"rolled back {result['name']}: registration {result.get('removed_registration')}, "
        f"containers removed {len(result.get('containers_removed') or [])}, "
        f"health {(result.get('health') or {}).get('state')}"
    )
    return cli_contract.EXIT_OK


def _lookup_spec(context, name: str):
    """The registry spec of one tool (``None`` when the DB has no such name)."""
    from .tools import ToolStore

    try:
        store = ToolStore(str(context.tools_db))
        try:
            return store.get(str(name))
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — an unreadable DB is "no spec", the refusal below
        return None


def _python_health(args: argparse.Namespace, context, name: str, report: dict) -> int:
    """Health of a NON-container tool: its capability-health ledger (audit 62 #6).

    ``tools health`` used to serve only staged container adapters — a built
    python tool with a full scan/import/test/determinism verification record in
    ``capability_health.jsonl`` answered ``exit 5: no staged container adapter``.
    For kind=python (and every other non-container kind) the health IS that
    ledger: the folded state, what it is selectable for, and the last
    verification events that produced it.
    """
    from . import capability_health as health

    state = health.file_state(context.runtime_dir, name)
    selectable, why = health.selectable(context.runtime_dir, name)
    events = health.events(context.runtime_dir, name)
    verification = next(
        (row for row in reversed(events) if str(row.get("kind", "")) == "verification"),
        {},
    )
    payload = {
        "name": name,
        "kind": str(getattr(report.get("spec") or {}, "kind", "") or ""),
        "health": state,
        "auto_routable": bool(state.get("auto_routable")),
        "selectable": bool(selectable),
        "selectable_reason": why,
        "verification": {
            "ok": bool(verification.get("ok")),
            "detail": str(verification.get("detail") or ""),
        },
        "events": events[-5:],
        "note": "python tool: health is its test status (the capability-health ledger)",
    }
    spec = report.get("spec")
    if spec is not None:
        payload["kind"] = str(getattr(spec, "kind", "") or "")
        payload["status"] = str(getattr(spec, "status", "") or "")
    cli_contract.set_data(payload)
    cli_contract.add_evidence(
        str(Path(context.runtime_dir) / "capability_health.jsonl"),
        "file",
        "the health ledger: for a python tool the recorded gates "
        "(scan/import/test/determinism) ARE its health",
    )
    if getattr(args, "probe", False):
        cli_contract.warn(
            "--probe is a container-adapter call; the ledger state above is a python tool's health"
        )

    print(
        f"{name}: python tool: health is its test status — "
        f"state={state.get('state')} selectable={selectable}"
        + (
            f" last verification: {'ok' if verification.get('ok') else 'FAILED'} "
            f"{str(verification.get('detail') or '')[:80]}"
            if verification
            else ""
        )
    )
    return cli_contract.EXIT_OK


def _cmd_tools_health(args: argparse.Namespace) -> int:
    """Health of a capability per KIND: container adapter facts, or the ledger.

    kind=container → the adapter + live container facts it always answered with;
    kind=python (any non-container spec in the registry) → its own verification
    ledger instead of ``exit 5: no staged container adapter`` (audit 62 #6);
    no such tool at all → exit 5, unchanged.
    """
    context = registry_from_args(args)
    name = str(args.name)
    report = _builder(args).container_health(name, probe=bool(getattr(args, "probe", False)))
    if not report.get("ok"):
        spec = _lookup_spec(context, name)
        if spec is not None and str(spec.kind) != "container":
            report["spec"] = spec
            return _python_health(args, context, name, report)
        print(str(report.get("reason") or "unknown container tool"))
        return cli_contract.not_found(
            str(report.get("reason") or f"unknown container tool: {name}"),
        )
    payload = {
        key: report.get(key) for key in
        ("name", "health", "auto_routable", "selectable", "selectable_reason",
         "registered", "version", "container", "permissions", "service", "probe")
    }
    cli_contract.set_data(payload)
    cli_contract.add_evidence(
        str(Path(registry_from_args(args).runtime_dir) / "capability_health.jsonl"), "file",
        "the health ledger the state was folded from",
    )
    if report.get("container", {}).get("available"):
        cli_contract.add_evidence(
            f"docker image inspect {report['container'].get('image')}", "command",
            "the local digest compared against the pinned one",
        )
    print(
        f"{report['name']}: health={(report.get('health') or {}).get('state')} "
        f"selectable={report.get('selectable')} "
        f"digest_match={report.get('container', {}).get('digest_match')}"
    )
    if not report.get("container", {}).get("available"):
        return cli_contract.blocked(
            f"docker is not reachable: {report.get('container', {}).get('detail')}",
            hint="the health ledger state above is still the truth of the last verification",
        )
    return cli_contract.EXIT_OK


# --------------------------------------------------------------------------- #
# registration of the CLI surface
# --------------------------------------------------------------------------- #


def _upgrade_mine(tools_sub) -> None:
    """`tools mine` learns ``--image`` — and keeps its runtime-scan behaviour."""
    parser = tools_sub.choices.get("mine")
    if parser is None:  # a CLI without a mine command at all
        parser = tools_sub.add_parser(
            "mine", help="scan sessions for recurring capabilities (tool candidates)"
        )
        parser.set_defaults(func=lambda args: cli_contract.EXIT_OK)
    original = parser.get_default("func")
    parser.add_argument(
        "--image", default="",
        help="mine a capability from this container image (inspect → adapt → test)",
    )
    parser.add_argument(
        "--capability", default="",
        help="what the capability must do (required with --image)",
    )
    parser.add_argument("--name", default="", help="tool name for the mined capability")
    parser.add_argument("--verb", default="", help="pin the container verb (e.g. sha256sum)")
    parser.add_argument(
        "--schema",
        default="",
        help=(
            "JSON template schema for a verb no catalogue family knows "
            "(container_cli_templates.load_schema_template — a declared fixture, "
            "never invented from --help prose)"
        ),
    )
    parser.add_argument("--digest", default="", help="pin the image by sha256 digest")
    parser.add_argument("--pull", action="store_true", help="pull the image when it is not local")
    parser.add_argument("--test-input", default="", help="JSON inputs for the adapter test")
    parser.add_argument("--expect", default="", help="the value the test must observe")
    parser.add_argument("--session", default="", help="session id recorded with the build")
    parser.add_argument("--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)")
    parser.add_argument("--db", default=None, help="explicit tools DB override")

    def dispatch(args: argparse.Namespace) -> int:
        if str(getattr(args, "image", "") or ""):
            return _cmd_tools_mine_container(args)
        if original is None:
            return cli_contract.usage_error("no `tools mine` handler is registered")
        return original(args)

    parser.set_defaults(func=dispatch)


def _upgrade_build(tools_sub) -> None:
    """`tools build` learns ``--image`` — the runtime=docker path was dead code."""
    parser = tools_sub.choices.get("build")
    if parser is None:
        return
    original = parser.get_default("func")
    parser.add_argument(
        "--image", default="",
        help="build a container tool from this image (runtime=docker, pinned by digest)",
    )
    parser.add_argument("--digest", default="", help="pin the image by sha256 digest")
    parser.add_argument(
        "--pull", action="store_true",
        help=(
            "ask the registry even when the image is already local "
            "(default: pin the LOCAL digest — no network, no rate limit)"
        ),
    )

    def dispatch(args: argparse.Namespace) -> int:
        if str(getattr(args, "image", "") or ""):
            return _cmd_tools_build_docker(args)
        if original is None:
            return cli_contract.usage_error("no `tools build` handler is registered")
        return original(args)

    parser.set_defaults(func=dispatch)


def register_cli(tools_sub) -> None:
    """Add the container commands to an existing ``tools`` subparsers action.

    Called from ``cli.build_parser()`` right after ``tools_sub`` exists; it does
    not touch any other parser and returns nothing. Upgrading an existing
    parser (``mine``/``build``) happens in place, so the command names the owner
    already knows keep working — with the container flags added.
    """
    _upgrade_mine(tools_sub)
    _upgrade_build(tools_sub)

    p_adapter = tools_sub.add_parser(
        "adapter", help="the typed container adapter of a staged/registered tool"
    )
    adapter_sub = p_adapter.add_subparsers(dest="adapter_command", required=True)
    p_adapter_show = adapter_sub.add_parser(
        "show", help="print the typed adapter, its permissions and its test evidence"
    )
    p_adapter_show.add_argument("name")
    p_adapter_show.add_argument("--runtime", default="", help="runtime dir")
    p_adapter_show.add_argument("--db", default=None, help="explicit tools DB override")
    p_adapter_show.set_defaults(func=_cmd_tools_adapter_show)

    p_register = tools_sub.add_parser(
        "register", help="register a TESTED container adapter live (evidence or nothing)"
    )
    p_register.add_argument("name")
    p_register.add_argument(
        "--version", default="",
        help="explicit version (default: 0.1.0; re-registering bumps the patch)",
    )
    p_register.add_argument("--project-id", default="", help="project id recorded in the manifest")
    p_register.add_argument("--requested-by", default="cli")
    p_register.add_argument("--runtime", default="", help="runtime dir")
    p_register.add_argument("--db", default=None, help="explicit tools DB override")
    p_register.set_defaults(func=_cmd_tools_register)

    p_rollback = tools_sub.add_parser(
        "rollback", help="take a container capability back (registry/files/health/containers)"
    )
    p_rollback.add_argument("name")
    p_rollback.add_argument("--reason", default="")
    p_rollback.add_argument("--runtime", default="", help="runtime dir")
    p_rollback.add_argument("--db", default=None, help="explicit tools DB override")
    p_rollback.set_defaults(func=_cmd_tools_rollback)

    p_health = tools_sub.add_parser(
        "health", help="health of a container capability (ledger + live container facts)"
    )
    p_health.add_argument("name")
    p_health.add_argument("--probe", action="store_true", help="run the adapter once (real call)")
    p_health.add_argument("--runtime", default="", help="runtime dir")
    p_health.add_argument("--db", default=None, help="explicit tools DB override")
    p_health.set_defaults(func=_cmd_tools_health)


__all__ = [
    "EV_NEW_COMMANDS",
    "FAILURE_EXIT",
    "register_cli",
]
