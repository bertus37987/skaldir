"""``skaldir compose …`` — create, validate, run and inspect composite tools.

The command group is added by the product CLI through one call:

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), right after _register_jev_cli(sub)
    # (line ~2000) and therefore BEFORE the final cli_contract.annotate(parser)
    # (line ~2588) — annotate() is what puts the dotted ``command_path`` on every
    # leaf; registered after it, the envelope would say "skaldir", not "compose.run".
    from .compose_cli import register_cli as _register_compose_cli

    _register_compose_cli(sub)

``register_cli(subparsers)`` is the whole contract: it takes the CLI's own
``subparsers`` handle, adds the ``compose`` group with its five verbs and returns
the parser. Nothing here edits ``cli.py``; the five ``COMMANDS`` entries this group
needs live in ``src/skaldir/cli_contract.py`` (the CLI owner adds them) and the
``COMPOSE`` selftest probe there still pins the old "nothing can be composed"
verdict — both hand-overs are written out, and verified, in
``tests/test_compose_cli.py`` (``COMPOSE_COMMANDS``, ``probe_compose_replacement``).

Verbs and what they really do
-----------------------------

=========================  ==========================================================
``compose candidates``     chains the **real registry** can build (a heuristic
                           proposal: shared declared vocabulary) + the composites
                           already stored
``compose create``         store a composite (definition document + registry row)
                           and report its validation honestly
``compose validate``       structural + dataflow + executability check; ``--approve``
                           flips the registry row to ``approved`` **only** when clean
                           (``--force`` records the override, never fakes execution)
``compose run``            execute the chain through the runtime's real runners
``compose show``           one composite (or all of them) with per-step executability
=========================  ==========================================================

Exit codes (the product CLI's vocabulary — adopted when ``cli_contract`` exists,
the literals in brackets otherwise)::

    0            verified / clean
    1 [usage]    a malformed document, an unparsable ``--input``, a missing --name
    2            validation problems, or a step / end-state that did not hold
    3            blocked: a step kind with no runner (the kind is named), or the
                 composite is not approved yet
    5            unknown composite
    6            the name is already taken (``--replace`` overwrites)

stdout carries the human lines by default; with ``--json`` the shared envelope
(``cli_contract.run_command``) carries the payload and the human lines move to
stderr. Every successful verb hands ``set_data`` **and** ``add_evidence`` to it —
a success without evidence is a contract violation and is downgraded to exit 7.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .cli_shared import registry_from_args, share_common_flags
from .compose_service import (
    CompositeError,
    CompositeSpec,
    SubToolRegistry,
    buildable_chains,
    composite_files,
    composite_path,
    document_digest,
    executor_report,
    kind_reason,
    load_composite,
    parse_composite,
    run_composite,
    save_composite,
    validate_composite,
)
from .tools import KIND_COMPOSITION, STATUS_APPROVED, STATUS_CANDIDATE, ToolStore

try:  # the product contract wins; this module also runs standalone
    from . import cli_contract as _CONTRACT
except Exception:  # noqa: BLE001 — an older build without the contract module
    _CONTRACT = None

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

SPEC_EVIDENCE = "src/skaldir/compose_service.py"


# --------------------------------------------------------------------------- #
# envelope plumbing                                                             #
# --------------------------------------------------------------------------- #


def _report(
    payload: Any,
    *,
    evidence: tuple[dict, ...] = (),
    warnings: tuple[str, ...] = (),
    next_actions: tuple[dict, ...] = (),
) -> None:
    """Hand the payload and its evidence to the shared envelope (no-op standalone)."""
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
        for item in next_actions:
            _CONTRACT.next_action(str(item.get("command", "")), str(item.get("reason", "")))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _json_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False)) or str(
        getattr(args, "format", "") or ""
    ).strip().lower() in ("json", "jsonl")


def _print_payload(payload: Any, args: argparse.Namespace) -> None:
    """Machine payload on stdout when ``--json`` is asked for standalone."""
    if _CONTRACT is not None:
        return  # the envelope owns stdout; the human lines are enough
    if _json_mode(args):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _store_read(context) -> ToolStore | None:
    """Open the resolved tools DB read-only — ``None`` when it does not exist yet."""
    if not Path(context.tools_db).is_file():
        return None
    return ToolStore(str(context.tools_db))


def _store_write(context) -> ToolStore:
    Path(context.tools_db).parent.mkdir(parents=True, exist_ok=True)
    return ToolStore(str(context.tools_db))


def _subtools(context) -> SubToolRegistry:
    """The ACT sub-tool registry for this runtime — the resolver a ``gui`` step needs.

    Handed to ``validate_composite`` so a ``gui`` step that names a stored sub-tool is
    checked against the **real** registry (saved sub-tools shadowing the built-ins) instead
    of only warning that the name cannot be resolved here.
    """
    return SubToolRegistry(state=getattr(context, "runtime_dir", None))


def _parse_inputs(args: argparse.Namespace) -> dict | None:
    """``--inputs FILE`` + repeated ``--input k=v`` — a JSON object, or ``None``."""
    values: dict[str, Any] = {}
    path = str(getattr(args, "inputs", "") or "")
    if path:
        try:
            raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"compose run: cannot read --inputs: {exc}", file=sys.stderr)
            return None
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"compose run: --inputs is not JSON: {exc}", file=sys.stderr)
            return None
        if not isinstance(loaded, dict):
            print("compose run: --inputs must contain one JSON object", file=sys.stderr)
            return None
        values.update(loaded)
    for item in list(getattr(args, "input", None) or []):
        name, sep, text = str(item).partition("=")
        if not sep or not name.strip():
            print(f"compose run: --input expects KEY=VALUE, got {item!r}", file=sys.stderr)
            return None
        try:
            values[name.strip()] = json.loads(text)
        except json.JSONDecodeError:
            values[name.strip()] = text
    return values


def _load(args: argparse.Namespace, context, name: str) -> tuple[CompositeSpec | None, int]:
    """The definition document of one composite, or the exit code of the refusal."""
    try:
        comp = load_composite(context.runtime_dir, name)
    except CompositeError as exc:
        return None, _fail_usage(f"composite {name!r} is unreadable: {exc}")
    if comp is None:
        return None, _fail_missing(name, context)
    return comp, EXIT_OK


def _fail_missing(name: str, context) -> int:
    message = (
        f"no composite {name!r} — no definition at {composite_path(context.runtime_dir, name)}"
    )
    print(message, file=sys.stderr)
    _report(
        {"composite": name, "found": False, "runtime": str(context.runtime_dir)},
        evidence=(
            {
                "ref": str(composite_path(context.runtime_dir, name)),
                "kind": "file",
                "note": "the definition document does not exist",
            },
        ),
        next_actions=(
            {"command": "skaldir compose candidates --json",
             "reason": "list the chains this registry can build"},
        ),
    )
    if _CONTRACT is not None:
        return _CONTRACT.not_found(message, hint="see `skaldir compose show`")
    return EXIT_NOT_FOUND


def _fail_usage(message: str) -> int:
    print(message, file=sys.stderr)
    if _CONTRACT is not None:
        return _CONTRACT.usage_error(message, hint="see `skaldir compose --help`")
    return EXIT_USAGE


def _registry_row(store: ToolStore | None, name: str) -> dict:
    """The lifecycle facts of a composite from the registry (its approval carrier)."""
    if store is None:
        return {"present": False, "status": "", "version": "", "reason": "no tools db yet"}
    spec = store.get(name)
    if spec is None or spec.kind != KIND_COMPOSITION:
        return {
            "present": False,
            "status": "",
            "version": "",
            "reason": f"no registry row of kind {KIND_COMPOSITION!r} for {name!r}",
        }
    return {
        "present": True,
        "status": spec.status,
        "version": spec.version,
        "steps": len(spec.steps),
        "step_ids": [
            str(step.get("id", "")) for step in spec.steps if isinstance(step, dict)
        ],
        "scan_summary": spec.scan_summary,
        "approved": spec.status == STATUS_APPROVED,
    }


def _mirror(row: dict, comp: CompositeSpec) -> dict:
    """The definition and its registry mirror side by side (drift is a warning)."""
    definition_steps = [step.id for step in comp.steps]
    registry_steps = list(row.get("step_ids") or [])
    return {
        "definition_steps": definition_steps,
        "registry_steps": registry_steps,
        "registry_row_present": bool(row.get("present", False)),
        "drift_detected": bool(row.get("present", False) and registry_steps != definition_steps),
    }


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_candidates(args: argparse.Namespace) -> int:
    """The chains the real registry can build + the composites already stored."""
    context = registry_from_args(args)
    store = _store_read(context)
    if store is None:
        payload = {
            "registry": str(context.tools_db),
            "runtime": str(context.runtime_dir),
            "steps_considered": 0,
            "chains": [],
            "chain_count": 0,
            "composites": [],
            "composite_count": 0,
            "executors": executor_report(),
        }
        warning = (
            f"no tools db at {context.tools_db} — nothing to compose from; "
            "tools must be mined/built and approved first"
        )
        print(warning, file=sys.stderr)
        _report(
            payload,
            evidence=({"ref": str(context.tools_db), "kind": "file", "note": "absent"},),
            warnings=(warning,),
        )
        _print_payload(payload, args)
        return EXIT_OK
    try:
        payload = buildable_chains(
            store,
            need=str(getattr(args, "need", "") or ""),
            tags=list(getattr(args, "tag", None) or []),
            limit=int(getattr(args, "limit", 8) or 8),
        )
        composites = [
            {"name": spec.name, "status": spec.status, "steps": len(spec.steps)}
            for spec in store.all()
            if spec.kind == KIND_COMPOSITION
        ]
    finally:
        store.close()
    payload["composites"] = composites
    payload["composite_count"] = len(composites)
    warnings: list[str] = []
    if payload["steps_considered"] == 0:
        warnings.append(
            f"the registry at {payload['registry']} holds no approved, executable tool — "
            "no chain can be proposed yet"
        )
    evidence = [
        {"ref": str(payload["registry"]), "kind": "file",
         "note": f"{payload['steps_considered']} approved executable step(s) considered"},
        {"ref": f"{SPEC_EVIDENCE}:1472", "kind": "code",
         "note": "buildable_chains(): shared declared vocabulary (a proposal, not proof)"},
    ]
    _report(payload, evidence=tuple(evidence), warnings=tuple(warnings))
    if not _json_mode(args):
        print(
            f"Chains from {payload['registry']} "
            f"({payload['steps_considered']} approved step(s), "
            f"{payload['chain_count']} candidate(s)):"
        )
        for row in payload["chains"]:
            print(f"  - {' -> '.join(row['chain'])} (join: {', '.join(row['join_terms'])})")
        if payload["composites"]:
            print("Stored composites:")
            for row in payload["composites"]:
                print(f"  - {row['name']} [{row['status']}] {row['steps']} step(s)")
        if not payload["chains"] and not payload["composites"]:
            print("  (none)")
    _print_payload(payload, args)
    return EXIT_OK


def cmd_create(args: argparse.Namespace) -> int:
    """Store a composite definition + its registry row, and report the validation."""
    path = str(getattr(args, "spec", "") or "")
    if not path:
        return _fail_usage("compose create: --spec FILE (or - for stdin) is required")
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return _fail_usage(f"compose create: cannot read --spec: {exc}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _fail_usage(f"compose create: --spec is not JSON: {exc}")
    try:
        comp = parse_composite(payload)
    except CompositeError as exc:
        return _fail_usage(f"compose create: {exc}")

    context = registry_from_args(args)
    store = _store_write(context)
    try:
        existing = store.get(comp.name)
        on_disk = composite_path(context.runtime_dir, comp.name)
        replaced = bool(getattr(args, "replace", False))
        if (existing is not None or on_disk.is_file()) and not replaced:
            message = (
                f"composite {comp.name!r} already exists "
                f"({'registry row' if existing is not None else 'no row'}"
                f"{', definition file' if on_disk.is_file() else ''}) — use --replace"
            )
            print(message, file=sys.stderr)
            _report(
                {"composite": comp.name, "created": False, "path": str(on_disk)},
                evidence=({"ref": str(on_disk), "kind": "file", "note": "already present"},),
                next_actions=(
                    {"command": f"skaldir compose run --name {comp.name} --json",
                     "reason": "run the existing composite"},
                ),
            )
            if _CONTRACT is not None:
                return _CONTRACT.fail(
                    int(getattr(_CONTRACT, "EXIT_CONFLICT", EXIT_CONFLICT)), message
                )
            return EXIT_CONFLICT
        report = validate_composite(store, comp, subtools=_subtools(context))
        target = save_composite(context.runtime_dir, comp)
        registry_spec = comp.to_spec(
            status=STATUS_CANDIDATE,
            scan_summary=(
                "compose validation: clean" if report.ok
                else f"compose validation: {len(report.problems)} problem(s)"
            ),
        )
        store.upsert(registry_spec)
        digest = document_digest(comp.to_dict())
    finally:
        store.close()

    data = {
        "composite": comp.name,
        "created": True,
        "replaced": bool(getattr(args, "replace", False)),
        "path": str(target),
        "registry": str(context.tools_db),
        "status": STATUS_CANDIDATE,
        "sha256": digest,
        "steps": [step.id for step in comp.steps],
        "kinds": comp.kinds,
        "validation": report.to_dict(),
    }
    evidence = (
        {"ref": str(target), "kind": "file", "note": f"definition document sha256:{digest[:12]}"},
        {"ref": str(context.tools_db), "kind": "file",
         "note": f"registry row {comp.name} kind={KIND_COMPOSITION} status={STATUS_CANDIDATE}"},
        {"ref": f"{SPEC_EVIDENCE}:781", "kind": "code", "note": "validate_composite()"},
    )
    warnings = (
        ("the composite is stored but does NOT validate — see data.validation.problems",)
        if report.problems else ()
    )
    _report(
        data,
        evidence=evidence,
        warnings=warnings,
        next_actions=(
            {"command": f"skaldir compose validate --name {comp.name} --approve --json",
             "reason": "approve the composite once it validates cleanly"},
        ),
    )
    if not _json_mode(args):
        print(f"created {comp.name} (candidate) -> {target}")
        for problem in report.problems:
            print(f"  x {problem}")
        print(
            "validation: clean" if report.ok
            else f"validation: {len(report.problems)} blocking problem(s)"
        )
    _print_payload(data, args)
    return EXIT_OK if report.ok else EXIT_VERIFICATION_FAILED


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate one composite; ``--approve`` flips the registry row when it is clean."""
    name = str(getattr(args, "name", "") or "").strip()
    if not name:
        return _fail_usage("compose validate: --name is required")
    context = registry_from_args(args)
    comp, code = _load(args, context, name)
    if comp is None:
        return code
    store = _store_write(context)
    try:
        report = validate_composite(store, comp, subtools=_subtools(context))
        row_before = _registry_row(store, name)
        approve = bool(getattr(args, "approve", False))
        force = bool(getattr(args, "force", False))
        approved = False
        if approve and (report.ok or force):
            summary = (
                "compose validation: clean" if report.ok
                else f"approved with --force despite {len(report.problems)} problem(s)"
            )
            store.set_status(name, STATUS_APPROVED, scan_summary=summary)
            approved = True
        row_after = _registry_row(store, name)
    finally:
        store.close()

    expected_digest = document_digest(comp.to_dict())
    data = {
        "composite": name,
        "path": str(composite_path(context.runtime_dir, name)),
        "sha256": expected_digest,
        "validation": report.to_dict(),
        "ok": report.ok,
        "executable": report.executable,
        "missing_kinds": report.missing_kinds,
        "approved": approved,
        "status_before": row_before.get("status", ""),
        "status_after": row_after.get("status", ""),
        "mirror": _mirror(row_after, comp),
    }
    evidence = (
        {"ref": f"{SPEC_EVIDENCE}:807", "kind": "code", "note": "validate_composite()"},
        {
            "ref": str(context.tools_db),
            "kind": "file",
            "note": (
                f"registry status {row_before.get('status', '?')}"
                f" -> {row_after.get('status', '?')}"
            ),
        },
    )
    warnings: list[str] = list(report.warnings)
    if report.missing_kinds:
        warnings.append(
            "no runner for step kind(s) "
            + ", ".join(f"{kind} ({kind_reason(kind)})" for kind in report.missing_kinds)
            + " — `compose run` refuses such a chain with exit 3"
        )
    if not report.ok:
        warnings.append("the composite does not validate; it cannot be run")
    _report(
        data,
        evidence=evidence,
        warnings=tuple(warnings),
        next_actions=(
            {"command": f"skaldir compose run --name {name} --json",
             "reason": "run the approved composite"}
            if (approved and report.executable)
            else {"command": f"skaldir compose show --name {name} --json",
                  "reason": "inspect the composite and its per-step executability"},
        ),
    )
    if not _json_mode(args):
        print(f"{name}: {'clean' if report.ok else f'{len(report.problems)} problem(s)'}")
        for problem in report.problems:
            print(f"  x {problem}")
        for warning in warnings:
            print(f"  ! {warning}")
        print(f"steps: {len(comp.steps)} · executable: {report.executable}")
        if approved:
            print(f"approved: {name} (status {row_after.get('status', '')})")
    _print_payload(data, args)
    return EXIT_OK if report.ok else EXIT_VERIFICATION_FAILED


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the composite through the runtime's real runners and report the verdict."""
    name = str(getattr(args, "name", "") or "").strip()
    if not name:
        return _fail_usage("compose run: --name is required")
    context = registry_from_args(args)
    comp, code = _load(args, context, name)
    if comp is None:
        return code
    inputs = _parse_inputs(args)
    if inputs is None:
        return _fail_usage("compose run: the inputs could not be read")

    store = _store_write(context)
    try:
        row = _registry_row(store, name)
        if not row.get("approved", False):
            message = (
                f"composite {name!r} is not approved "
                f"(registry status {row.get('status') or 'absent'!r}) — a composite is "
                "runnable only while its registry row is approved"
            )
            print(message, file=sys.stderr)
            _report(
                {"composite": name, "status": "blocked", "ok": False, "verified": False,
                 "registry": row},
                evidence=(
                    {"ref": str(context.tools_db), "kind": "file",
                     "note": "the registry row is the approval carrier for a composite"},
                ),
                next_actions=(
                    {"command": f"skaldir compose validate --name {name} --approve --json",
                     "reason": "validate and approve the composite"},
                ),
            )
            if _CONTRACT is not None:
                return _CONTRACT.blocked(
                    message, hint="`compose validate --approve` after it validates cleanly"
                )
            return EXIT_BLOCKED
        run = run_composite(
            store,
            comp,
            inputs=inputs,
            runtime=context.runtime_dir,
            docker_bin=str(getattr(args, "docker_bin", "") or "") or None,
            subtools=_subtools(context),
        )
    finally:
        store.close()

    data = run.to_dict()
    data["path"] = str(composite_path(context.runtime_dir, name))
    data["registry"] = str(context.tools_db)
    data["inputs"] = {key: (value if isinstance(value, (str, int, float, bool)) else str(value))
                      for key, value in (inputs or {}).items()}
    evidence = tuple(
        {"ref": f"step:{step.id}", "kind": "run",
         "note": f"{step.kind} {step.target} -> {step.status} ({step.evidence})"}
        for step in run.steps
    )
    if run.status == "verified":
        evidence = evidence + (
            {"ref": str(composite_path(context.runtime_dir, name)), "kind": "file",
             "note": f"end_state {comp.end_state!r} held after {len(run.steps)} step(s)"},
        )
    _report(
        data,
        evidence=evidence,
        warnings=(run.reason,) if (not run.verified and run.reason) else (),
        next_actions=(
            {"command": f"skaldir compose show --name {name} --json",
             "reason": "inspect the step verdicts"},
        ) if not run.verified else (),
    )
    if not _json_mode(args):
        print(f"{name}: {run.status} ({len(run.steps)} step(s))")
        for step in run.steps:
            print(f"  {'ok' if step.ok else 'x '} {step.id} [{step.kind}] {step.detail[:90]}")
        if run.reason:
            print(f"  reason: {run.reason}")
    _print_payload(data, args)
    return int(run.exit_code)


def cmd_show(args: argparse.Namespace) -> int:
    """One composite (or all of them) with its per-step executability."""
    name = str(getattr(args, "name", "") or "").strip()
    context = registry_from_args(args)
    store = _store_read(context)
    try:
        if not name:
            names = [path.stem for path in composite_files(context.runtime_dir)]
            rows = []
            for item in names:
                try:
                    comp = load_composite(context.runtime_dir, item)
                except CompositeError as exc:
                    rows.append({"name": item, "error": str(exc)})
                    continue
                if comp is None:
                    continue
                report = (
                    validate_composite(store, comp, subtools=_subtools(context))
                    if store is not None
                    else None
                )
                rows.append(
                    {
                        "name": item,
                        "status": _registry_row(store, item).get("status", ""),
                        "steps": [step.id for step in comp.steps],
                        "kinds": comp.kinds,
                        "ok": report.ok if report is not None else None,
                        "executable": report.executable if report is not None else None,
                        "path": str(composite_path(context.runtime_dir, item)),
                    }
                )
            payload = {
                "runtime": str(context.runtime_dir),
                "composites": rows,
                "count": len(rows),
                "executors": executor_report(),
            }
            _report(
                payload,
                evidence=(
                    {"ref": str(context.runtime_dir / "composites"), "kind": "file",
                     "note": f"{len(rows)} definition document(s)"},
                ),
                warnings=(
                    tuple(f"{row['name']}: {row['error']}" for row in rows if row.get("error"))
                ),
            )
            if not _json_mode(args):
                print(f"Composites in {context.runtime_dir}:")
                for row in rows:
                    print(
                        f"  - {row['name']} [{row.get('status', '')}] "
                        f"{len(row.get('steps', []))} step(s) "
                        f"executable={row.get('executable')}"
                    )
                if not rows:
                    print("  (none)")
            _print_payload(payload, args)
            return EXIT_OK

        comp, code = _load(args, context, name)
        if comp is None:
            return code
        report = (
                    validate_composite(store, comp, subtools=_subtools(context))
                    if store is not None
                    else None
                )
        row = _registry_row(store, name)
    finally:
        if store is not None:
            store.close()

    document = comp.to_dict()
    digest = document_digest(document)
    data = {
        "composite": name,
        "document": document,
        "sha256": digest,
        "path": str(composite_path(context.runtime_dir, name)),
        "registry": {
            "db": str(context.tools_db),
            **row,
        },
        "mirror": _mirror(row, comp),
        "validation": report.to_dict() if report is not None else None,
        "executors": executor_report(),
    }
    evidence = (
        {"ref": data["path"], "kind": "file", "note": f"definition sha256:{digest[:12]}"},
        {"ref": f"{SPEC_EVIDENCE}:781", "kind": "code",
         "note": "validate_composite() — dataflow + executability"},
    )
    warnings: list[str] = list(report.warnings) if report is not None else []
    if report is not None and report.missing_kinds:
        warnings.append(
            "not executable today: "
            + ", ".join(f"{kind} ({kind_reason(kind)})" for kind in report.missing_kinds)
        )
    _report(data, evidence=evidence, warnings=tuple(warnings))
    if not _json_mode(args):
        print(f"{name} [{row.get('status', '')}] {comp.title or name}")
        print(f"  definition: {data['path']} (sha256:{digest[:12]})")
        print(f"  inputs: {sorted(comp.inputs)} · outputs: {sorted(comp.outputs)}")
        print(f"  precondition: {comp.precondition}")
        print(f"  end_state: {comp.end_state}")
        for step in comp.steps:
            verdict = next(
                (item for item in (report.steps if report else []) if item.id == step.id), None
            )
            mark = "executable" if (verdict and verdict.executable) else "NOT executable"
            print(
                f"  - {step.id} [{step.kind}] {step.target or step.image or step.url} "
                f"-> {step.outputs} ({mark})"
            )
    _print_payload(data, args)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


def _resource_flags(parser: Any) -> None:
    """How the registry is resolved (the same flags ``tools compose`` accepts)."""
    parser.add_argument("--db", default=None,
                        help="explicit tools DB (default: the runtime DB)")
    parser.add_argument("--runtime", default="", help="runtime dir (default: $HERMES_HOME/godsv)")
    parser.add_argument("--project", default="", help="project path for the project id")


def _add_compose_verbs(sub: Any) -> Any:
    """Add the five ``compose`` verbs to a subparsers handle (one definition)."""
    p_candidates = sub.add_parser(
        "candidates", help="buildable chains from the real registry (+ stored composites)"
    )
    p_candidates.add_argument("need", nargs="?", default="",
                              help="optional need text (ranks the seed steps)")
    p_candidates.add_argument("--tag", action="append", default=None)
    p_candidates.add_argument("--limit", type=int, default=8)
    _resource_flags(p_candidates)
    p_candidates.set_defaults(func=cmd_candidates)

    p_create = sub.add_parser("create", help="store a composite (definition + registry row)")
    p_create.add_argument("--spec", default="", help="composite JSON file ('-' for stdin)")
    p_create.add_argument("--replace", action="store_true",
                          help="overwrite an existing composite of that name")
    _resource_flags(p_create)
    p_create.set_defaults(func=cmd_create)

    p_validate = sub.add_parser(
        "validate", help="check a composite; --approve flips the registry row when clean"
    )
    p_validate.add_argument("--name", default="")
    p_validate.add_argument("--approve", action="store_true")
    p_validate.add_argument("--force", action="store_true",
                            help="approve despite validation problems (recorded, never hidden)")
    _resource_flags(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_run = sub.add_parser("run", help="execute an approved composite, step by step")
    p_run.add_argument("--name", default="")
    p_run.add_argument("--input", action="append", default=None,
                       help="KEY=VALUE (repeatable); VALUE is JSON when it parses")
    p_run.add_argument("--inputs", default="", help="JSON object file ('-' for stdin)")
    p_run.add_argument("--docker-bin", default="", help="docker CLI to use for docker steps")
    _resource_flags(p_run)
    p_run.set_defaults(func=cmd_run)

    p_show = sub.add_parser("show", help="one composite (or all) with per-step executability")
    p_show.add_argument("--name", default="")
    _resource_flags(p_show)
    p_show.set_defaults(func=cmd_show)

    for parser in (p_candidates, p_create, p_validate, p_run, p_show):
        parser.add_argument("--json", action="store_true")
        share_common_flags(parser)
    return sub


def build_compose_parser(subparsers: Any) -> Any:
    """Add the ``compose`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "compose",
        help="learned composite tools: create, validate, run, inspect",
        description=(
            "A composite is a named, validated chain of steps — each step a normal tool, "
            "a Docker tool, an API call or a Computer-Use sub-tool — with declared "
            "inputs/outputs, a precondition and an end-state check. It runs through the "
            "runtime's existing runners; a step kind without a runner is refused, never faked."
        ),
    )
    sub = parser.add_subparsers(dest="compose_command")
    _add_compose_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    return build_compose_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``python -m``)."""
    parser = argparse.ArgumentParser(prog="skaldir compose", description="composite tools")
    sub = parser.add_subparsers(dest="command")
    _add_compose_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir compose …`` — returns the exit code, never raises for a request."""
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
    "build_compose_parser",
    "build_parser",
    "cmd_candidates",
    "cmd_create",
    "cmd_run",
    "cmd_show",
    "cmd_validate",
    "main",
    "register_cli",
]
