"""``skaldir tools call NAME --json-args '{…}'`` — call one approved registry tool.

Verbs::

    tools call NAME --json-args '{"a": 2}' [--runtime DIR] [--db PATH] [--project PATH]

This is the CLI's **manual** call verb for the same executable surface
``agent_loop.registry_tools`` wires into the loop: the spec is resolved through
:func:`skaldir.mcpserver.approved_python_specs` (approved + ``kind=python`` +
scope-visible), the health gate is :func:`skaldir.mcpserver.health_verdict`, and
the call goes through the very wrapper path the loop uses —
``functools.partial(skaldir.agent_loop._call_registry_tool, _wrapper_for(spec))``
(the ``partial`` pattern is imported defensively; a build without ``agent_loop``
falls back to the same one-line join by hand). Nothing here re-implements the
execution: a generated tool runs in the bwrap sandbox, an in-process spec stays
in-process, and the usage ledger records the call exactly as a live turn would.

``kind=container`` is callable here too (audit 62 #14): those specs are not on
the python surface (no module+entrypoint), so they resolve through their
**staged bin wrapper** — the exact call path ``tools register`` prints — after
the same health gate, with the wrapper's own permissions (pinned digest, network
none, read-only rootfs) doing the containing.

Refusals carry the exact reason the resolver produces — never a paraphrase:

* unknown name (not in the store at all) → exit 5 with the known count;
* present but not on the executable surface (wrong status/kind, or not visible
  in this project scope) → exit 3 with the spec's real status/kind;
* health ledger excludes it (QUARANTINED/RETIRED/…) → exit 3 with the verdict's
  own reason string (``health_verdict`` never raises, so the text is exact).

The one JSON envelope carries ``result`` plus the ``sha256`` over the emitted
JSON bytes — a caller can check what it received against what this command
declared, without trusting the prose around it.

**Integration — the only change ``cli.py`` needs (2 lines)**, after
``tools_sub`` exists (the ``tools`` group is created by the legacy subparsers,
then extended in place — same hook shape as ``toolbuilder_cli`` /
``adapter_http_cli``)::

    from .tools_call_cli import register_cli as _register_tools_call   # +
    _register_tools_call(tools_sub)                                    # +

The ``tools.call`` ``COMMANDS`` entry is installed by ``register_cli`` itself
through :func:`cli_shared.install_contract_entries` (idempotent add — a second
registration order cannot conflict, it only fills what is missing).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from functools import partial  # noqa: F401 — the documented join seam (see docstring)
from pathlib import Path
from typing import Any

try:  # the shared group plumbing (single implementation, never copied)
    from .cli_shared import (
        EXIT_BLOCKED,
        EXIT_NOT_FOUND,
        EXIT_VERIFICATION_FAILED,
        as_subparsers,
        dump,
        ev,
        install_contract_entries,
        product_contract,
        share_common_flags,
        text_mode,
    )
except Exception:  # pragma: no cover — standalone builds without the product contract
    def product_contract() -> Any:  # type: ignore[misc]
        return None

    EXIT_BLOCKED, EXIT_NOT_FOUND = 3, 5
    EXIT_VERIFICATION_FAILED = 2

    def as_subparsers(target: Any) -> Any:  # type: ignore[misc]
        raise TypeError("standalone build without the shared plumbing")

    def dump(payload: Any) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))

    def ev(ref: str, kind: str = "code", note: str = "") -> dict[str, Any]:
        return {"ref": str(ref), "kind": str(kind), "note": str(note)}

    install_contract_entries = None  # type: ignore[assignment]

    def share_common_flags(parser: Any) -> None:  # type: ignore[misc]
        return None

    def text_mode(args: argparse.Namespace) -> bool:  # type: ignore[misc]
        return bool(getattr(args, "text", False))


_CONTRACT = product_contract()

CALL_HELP = "call one approved registry tool by name (the loop's own call path)"


# --------------------------------------------------------------------------- #
# the service half — one function, resolve → refuse → call → envelope data       #
# --------------------------------------------------------------------------- #


def _join_call(wrapped: Any, arguments: dict[str, Any]) -> Any:
    """The loop's own keyword-to-object join (``agent_loop._call_registry_tool``).

    Imported defensively: both seams already exist (``agent_loop`` builds
    ``partial(_call_registry_tool, _wrapper_for(...))`` per tool); reusing the
    handler keeps this verb on the identical call path instead of a copy.
    """
    try:
        from .agent_loop import _call_registry_tool

        return _call_registry_tool(wrapped, **arguments)
    except Exception:  # noqa: BLE001 — the join is two lines; keep the verb working
        return wrapped(dict(arguments))


#: A container wrapper is one bounded docker one-shot — never unbounded wait.
CONTAINER_WRAPPER_TIMEOUT_S = 120.0


def _run_container_wrapper(wrapper: Path, arguments: dict[str, Any]) -> Any:
    """Run a staged container tool through its ``bin/`` wrapper — the call path.

    ``tools register`` prints exactly this wrapper as *the* way to call a
    container tool (``bin/<name> '{"text": …}'``): argv carries the JSON object,
    stdout answers with one JSON document. ``kind=container`` tools resolve
    through this here, so ``tools call`` can invoke them like every other
    approved tool (audit 62 #14). A failure is a structured error, never a
    crash or a swallowed traceback.
    """
    import subprocess

    argv = [str(wrapper), json.dumps(arguments, ensure_ascii=False, default=str)]
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell; the wrapper is ours
            argv, capture_output=True, text=True, timeout=CONTAINER_WRAPPER_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return {
            "__wrapper_error__": (
                f"the container wrapper timed out after {CONTAINER_WRAPPER_TIMEOUT_S:.0f}s"
            ),
        }
    except OSError as exc:
        return {
            "__wrapper_error__": (
                f"the container wrapper could not be executed: {type(exc).__name__}: {exc}"
            ),
        }
    out = str(proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (str(proc.stderr or "") or out).strip()[-400:]
        return {
            "__wrapper_error__": f"the container wrapper exited {proc.returncode}: {detail}"
        }
    if not out:
        return {"__wrapper_error__": "the container wrapper printed no result"}
    for candidate in (out, out.splitlines()[-1]):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return {
        "__wrapper_error__": f"the container wrapper printed a non-JSON result: {out[:200]}"
    }


def _sha256_of(payload: Any) -> str:
    """The digest a consumer recomputes against ``data.result`` (one encoding)."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def call_tool(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    runtime: str | Path | None = None,
    project_id: str = "",
    db: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve one approved spec and call it through the loop's wrapper path.

    Returns a machine payload and never raises — every refusal carries its
    exact reason plus the fields a caller needs to rerun the resolution
    (``status``/``kind`` for a surface refusal, ``health_reason`` for a ledger
    exclusion, the visible-name list it should have been on).
    """
    from .registry import resolve_registry
    from .tools import ToolStore

    context = resolve_registry(
        runtime=runtime, project=project_id or None, db=db
    )
    tools_db = context.tools_db
    payload: dict[str, Any] = {
        "name": str(name),
        "ok": False,
        "arguments": dict(arguments or {}),
        "tools_db": str(tools_db),
        "runtime": str(context.runtime_dir),
        "refusal": "",
        "failure_class": "unknown_tool",
        "result": None,
        "result_sha256": "",
        "duration_source": "skaldir.toolusage ledger (see the store's own usage rows)",
    }
    if not str(name).strip():
        payload.update(refusal="a tool NAME is required", failure_class="usage")
        return _finalize(payload)
    store = None
    try:
        store = ToolStore(str(tools_db))
        spec = store.get(str(name).strip())
        if spec is None:
            payload.update(
                refusal=f"unknown tool: {name} — nothing with that name in {tools_db}",
                failure_class="unknown_tool",
                known_count=len(store.all()),
            )
            store = None  # closed in the finally below; do not close it twice
            return _finalize(payload)
        # the ONE executable-surface rule: same source `agent_loop.registry_tools` uses
        from .mcpserver import approved_python_specs, health_verdict

        selective = approved_python_specs(
            store, runtime=runtime, project_id=project_id or context.project_id
        )
    except Exception as exc:  # noqa: BLE001 — an unreadable registry is a refusal, not a crash
        payload.update(
            refusal=f"the tools DB is not readable at {tools_db}: {type(exc).__name__}: {exc}",
            failure_class="unknown_tool",
        )
        return _finalize(payload)
    finally:
        try:
            if store is not None:
                store.close()
        except Exception:  # noqa: BLE001 — a close failure is not a refusal reason
            pass
    payload.update(
        status=str(spec.status),
        kind=str(spec.kind),
        version=str(spec.version),
        surface=[item.name for item in selective],
    )
    # -- kind=container: callable through its staged bin wrapper (audit 62 #14).
    # The python executable-surface rule (module+entrypoint, scope-filtered) does
    # not describe a container tool — its contract is: approved + health gate +
    # a staged wrapper, which runs the pinned digest under the adapter's own
    # permissions (network none, read-only rootfs, no_new_privileges).
    if str(spec.kind) == "container" and str(spec.status) == "approved":
        verdict_ok, verdict_reason = health_verdict(
            runtime or context.runtime_dir, spec.name
        )
        if not verdict_ok:
            payload.update(
                refusal=verdict_reason,
                failure_class="health_blocked",
                health_reason=verdict_reason,
            )
            return _finalize(payload)
        from .toolbuilder import container_tool_paths

        wrapper = Path(container_tool_paths(context.runtime_dir, spec.name)["wrapper"])
        if not wrapper.is_file():
            payload.update(
                refusal=(
                    f"{spec.name} is an approved container tool, but its staged wrapper is "
                    f"missing at {wrapper} — re-mine it with `tools mine --image … "
                    f"--capability …` so there is a certified call path to run"
                ),
                failure_class="not_exported",
            )
            return _finalize(payload)
        result = _run_container_wrapper(wrapper, dict(arguments or {}))
        if isinstance(result, dict) and result.get("__wrapper_error__"):
            payload.update(
                ok=False,
                refusal=str(result["__wrapper_error__"]),
                failure_class="tool_error",
                result=None,
            )
            return _finalize(payload)
        payload.update(
            ok=True,
            result=result,
            failure_class="",
            refusal="",
            wrapper=str(wrapper),
        )
        return _finalize(payload)
    if spec.name not in {item.name for item in selective}:
        verdict_ok, verdict_reason = health_verdict(runtime or context.runtime_dir, spec.name)
        if not verdict_ok:
            # the health ledger is the exclusion — its own words are the reason
            payload.update(
                refusal=verdict_reason,
                failure_class="health_blocked",
                health_reason=verdict_reason,
            )
        else:
            payload.update(
                refusal=(
                    f"{spec.name} is not on the executable surface: status={spec.status!r}, "
                    f"kind={spec.kind!r}, module={spec.module!r}, entrypoint={spec.entrypoint!r} "
                    "(approved python tools with module+entrypoint in this project scope only; "
                    "kind=container IS callable here — through its staged bin wrapper — "
                    "everything else stays off this surface)"
                ),
                failure_class="not_exported",
            )
        return _finalize(payload)

    # the loop's own wrapper path (defensively imported, same semantics)
    try:
        from .mcpserver import _wrapper_for

        wrapper = _wrapper_for(spec, base_dir=context.runtime_dir)
    except Exception as exc:  # noqa: BLE001 — a wrapper import failure refuses honestly
        payload.update(
            refusal=f"the tool wrapper could not be built: {type(exc).__name__}: {exc}",
            failure_class="internal",
        )
        return _finalize(payload)
    result = _join_call(wrapper, dict(arguments or {}))
    ok = not (isinstance(result, dict) and "error" in result)
    payload.update(
        ok=ok,
        result=result,
        failure_class="" if ok else "tool_error",
        refusal=(
            "" if ok
            else str((result or {}).get("error", "")) if isinstance(result, dict) else ""
        ),
    )
    return _finalize(payload)


def _finalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Stamp the envelope facts every exit path shares (never a fabricated number)."""
    payload["result_sha256"] = _sha256_of(payload["result"])
    evidence: list[dict[str, Any]] = [
        {
            "ref": str(payload.get("tools_db") or "tools.db"),
            "kind": "file",
            "note": "the registry the spec was resolved from",
        },
    ]
    if payload.get("wrapper"):
        evidence.append(
            {
                "ref": str(payload["wrapper"]),
                "kind": "run",
                "note": (
                    "kind=container: the staged bin wrapper — the call path "
                    "`tools register` prints (docker one-shot, pinned digest, "
                    "the adapter's own permissions)"
                    if payload.get("ok")
                    else "kind=container: the staged bin wrapper was resolved, the call failed"
                ),
            }
        )
    else:
        evidence.append(
            {
                "ref": (
                    "skaldir.mcpserver._wrapper_for "
                    "(via skaldir.agent_loop._call_registry_tool)"
                ),
                "kind": "run",
                "note": (
                    "the call went through the loop's own wrapper path"
                    if payload.get("ok")
                    else "refused before the wrapper was built"
                ),
            }
        )
    payload["evidence"] = evidence
    return payload


class _ArgsShim:
    """The three fields :func:`cli_shared.registry_from_args` reads (never argparse)."""

    def __init__(self, runtime: Any, db: Any, project: str) -> None:
        self.runtime = str(runtime or "")
        self.db = str(db) if db else None
        self.project = project


# --------------------------------------------------------------------------- #
# the CLI half                                                                  #
# --------------------------------------------------------------------------- #


def _parse_json_args(raw: str) -> dict[str, Any] | None:
    """``--json-args`` as one mapping; ``None`` on a malformed document."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"__error__": f"malformed --json-args: {exc}"}
    if not isinstance(parsed, dict):
        return {"__error__": "--json-args must be a JSON object"}
    return parsed



def _exit_for(payload: dict[str, Any]) -> int:
    refusal_class = str(payload.get("failure_class") or "")
    if refusal_class == "unknown_tool":
        return EXIT_NOT_FOUND
    if refusal_class == "usage":
        return 1
    if refusal_class == "tool_error":
        return EXIT_VERIFICATION_FAILED
    return EXIT_BLOCKED


def _fail(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "fail"):
        return int(_CONTRACT.fail(code, message, hint=hint, details=details))
    print(message, file=sys.stderr)
    return int(code)


def _report(payload: dict[str, Any], *, warnings: tuple[str, ...] = ()) -> None:
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data({k: v for k, v in payload.items()})
        for item in payload.get("evidence") or []:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")), str(item.get("note", ""))
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if text_mode(args) or _envelope_driving():
        dump(payload)


def _envelope_driving() -> bool:
    """True when this verb prints the document itself (no product driver around it).

    Under the product CLI (``skaldir tools call``) the runner owns the one
    envelope and this verb prints nothing — the payload already rides in
    ``data`` via :func:`_report`. Standalone (tests, ``python -m``) *this*
    module owns stdout.
    """
    if _CONTRACT is None:
        return True
    try:
        context = _CONTRACT.current()
    except Exception:  # noqa: BLE001 — a context problem must not break a verb
        return True
    return context is None


def cmd_call(args: argparse.Namespace) -> int:
    """One call: resolve, refuse honestly, or run through the loop's path."""
    name = str(getattr(args, "name", "") or "").strip()
    arguments = _parse_json_args(getattr(args, "json_args", "") or "")
    if isinstance(arguments, dict) and arguments.get("__error__"):
        message = str(arguments["__error__"])
        payload = {"ok": False, "failure_class": "usage", "refusal": message, "name": name}
        _report(payload, warnings=(message,))
        dump(payload)
        return _fail(1, message, hint='--json-args \'{"a": 2}\'')

    payload = call_tool(
        name,
        arguments,
        runtime=str(getattr(args, "runtime", "") or "") or None,
        project_id=str(getattr(args, "project", "") or ""),
        db=getattr(args, "db", "") or None,
    )
    warnings = () if payload.get("ok") else (str(payload.get("refusal") or ""),)
    _report(payload, warnings=warnings)
    _emit(args, payload)
    if payload.get("ok"):
        return 0
    return _fail(
        _exit_for(payload),
        str(payload.get("refusal") or "the call was refused")[:400],
        hint="`skaldir tools list --json` shows the approved tools this runtime exports",
        details={"failure_class": payload.get("failure_class", "")},
    )


def register_cli(tools_sub: Any) -> None:
    """Add the ``call`` verb to the existing ``tools`` group (hook contract)."""
    proposed = proposed_contract_entries()
    if proposed and install_contract_entries is not None:
        install_contract_entries(proposed, _CONTRACT)
    sub = as_subparsers(tools_sub)
    choices = getattr(sub, "choices", {}) or {}
    p_call = choices.get("call") or sub.add_parser("call", help=CALL_HELP)
    _fill_call(p_call)


def _fill_call(p_call: Any) -> Any:
    """Fill only what the parser lacks (idempotent; a legacy ``call`` parser gains
    this verb's missing flags without losing its own)."""
    existing: set[str] = set()
    for action in p_call._actions:  # noqa: SLF001 — argparse has no public API
        existing.update(action.option_strings)
    if "name" not in {str(getattr(a, "dest", "") or "") for a in p_call._actions}:
        p_call.add_argument("name", help="the tool to run (see `tools list --json`)")
    if "--json-args" not in existing:
        p_call.add_argument(
            "--json-args",
            dest="json_args",
            default="",
            help="the arguments as ONE JSON object literal ('{}' when the tool takes none)",
        )
    if "--runtime" not in existing:
        p_call.add_argument("--runtime", default="", help="runtime dir override")
    if "--project" not in existing:
        p_call.add_argument(
            "--project",
            default="",
            help="project path for the visible-scope rule (default: cwd)",
        )
    if "--text" not in existing:
        p_call.add_argument("--text", action="store_true", help="humans over JSON")
    share_common_flags(p_call)
    if getattr(p_call.get_default("func"), "__name__", "") != "cmd_call":
        p_call.set_defaults(func=cmd_call, command_path="tools.call")
    return p_call


def build_parser() -> argparse.ArgumentParser:
    """A parser for just this verb (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir tools call", description=CALL_HELP)
    _fill_call(parser)
    return parser


def _tool_parser(parent: Any) -> Any:
    """The ``tools call`` parser under an existing ``tools`` group parser."""
    return _fill_call(parent)


def proposed_contract_entries() -> dict[str, Any]:
    """The ``tools.call`` ``COMMANDS`` entry this group installs."""
    if _CONTRACT is None:
        return {}
    spec = _CONTRACT.CommandSpec

    def _e(ref: str, note: str = "") -> dict:
        return {"ref": ref, "kind": "code", "note": note}

    return {
        "tools.call": spec(
            "EXPAND",
            (
                _e(
                    "src/skaldir/tools_call_cli.py",
                    "one call path: approved_python_specs + health_verdict resolve the spec, "
                    "the call goes through skaldir.agent_loop._call_registry_tool "
                    "(partial(_call_registry_tool, _wrapper_for(spec))) exactly as the loop does",
                ),
                _e(
                    "src/skaldir/mcpserver.py",
                    "the executable-surface rule (scope + health gate) the verb resolves by",
                ),
            ),
            "call one approved registry tool by name and report result + sha256",
            mutates=True,
            exit_codes=(0, 1, 3, 5, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "arguments": {"type": "object"},
                    "result": {},
                    "result_sha256": {"type": "string"},
                    "tools_db": {"type": "string"},
                    "refusal": {"type": "string"},
                    "failure_class": {"type": "string"},
                },
                "required": ["name", "ok", "result", "result_sha256"],
            },
            notes=(
                "unknown name exits 5; an off-surface or health-blocked tool exits 3 with the "
                "resolver's own reason. tools.db missing ⇒ exit 5 (nothing to resolve)"
            ),
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """``skaldir tools call …`` standalone (tests / `python -m skaldir.tools_call_cli`)."""
    parser = argparse.ArgumentParser(prog="skaldir tools call", description=CALL_HELP)
    _fill_call(parser)
    try:
        args = parser.parse_args(list(argv if argv is not None else sys.argv[1:]))
    except SystemExit as exc:
        return 0 if not exc.code else 1
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 1
    return int(handler(args))
