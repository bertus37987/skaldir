"""Expose approved tool specs as an MCP server (fastmcp).

Only specs in status ``approved`` with ``kind="python"`` (module +
entrypoint) are registered — compositions are informational and stay out of
the executable surface. The server is standalone; run it with
``skaldir mcp serve`` or embed it via :func:`build_server` (the fastmcp
in-memory client makes this testable without any network).

Failure policy: calling a registered tool never crashes the server — import
and call errors are returned as structured error payloads.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import gates
from .tools import KIND_PYTHON, STATUS_APPROVED, ToolSpec, ToolStore

# A generated tool runs out of process; this bounds one call.
SANDBOX_TIMEOUT_S = 60.0
_RESULT_MARKER = "__SKALDIR_RESULT__"

#: The JSON-Schema type of a declared property, as the Python annotation the tool
#: callable carries (`Any` for anything this table does not name — the honest answer
#: for a fragment a generated tool declares that has no Python equivalent here).
_ANNOTATION_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}

# Health gate (§1.6): a capability the ledger has explicitly excluded must not be
# picked again. These states are fail-closed regardless of any richer API.
_HIDDEN_HEALTH_STATES = frozenset({"QUARANTINED", "RETIRED", "BROKEN"})


def health_verdict(runtime: str | Path, name: str) -> tuple[bool, str]:
    """``(selectable, reason)`` for one tool as the health ledger sees it (§1.6).

    Consumes ``capability_health`` defensively (that module is owned by Block B2):
    a richer ``selectable()`` API decides when it exists, otherwise the plain
    ``routable()`` verdict is used. A tool with **no ledger row at all** stays
    selectable — it was never observed as broken (conservative rule; a missing
    API must never hide the whole surface). Hard exclusion states are
    fail-closed even when no API is importable. Never raises.
    """
    try:
        from . import capability_health as health

        rows = health.events(runtime, name)
        state = str(health.file_state(runtime, name).get("state") or "")
    except Exception:
        return True, "health ledger unavailable — no exclusion applied"
    if not rows:
        return True, "no health record — not observed as broken"
    if state in _HIDDEN_HEALTH_STATES:
        return False, f"{state}: excluded from the exported surface"
    selectable = getattr(health, "selectable", None)
    if callable(selectable):
        try:
            verdict = selectable(runtime, name)
        except Exception:
            verdict = None
        if isinstance(verdict, tuple) and len(verdict) == 2:
            return bool(verdict[0]), str(verdict[1])
        if isinstance(verdict, bool):
            return verdict, f"{state}: {'selectable' if verdict else 'not selectable'}"
    routable = getattr(health, "routable", None)
    if callable(routable):
        try:
            ok = bool(routable(runtime, name))
        except Exception:
            ok = state not in _HIDDEN_HEALTH_STATES
        return ok, f"{state}: {'auto-routable' if ok else 'not auto-routable'}"
    return state not in _HIDDEN_HEALTH_STATES, f"{state or 'unknown'} (fallback verdict)"


def _generated_source(spec: ToolSpec, base_dir: Path) -> Path | None:
    """The generated module file for a spec, when the builder produced one."""
    if not spec.module:
        return None
    candidate = Path(base_dir) / "generated_tools" / spec.name / f"{spec.module}.py"
    return candidate if candidate.is_file() else None


def _run_generated_in_sandbox(
    spec: ToolSpec, *, source: Path, arguments: dict | None
) -> Any:
    """Execute a generated tool through ``gates._run`` (bwrap), fail-closed.

    Build-gates sandboxing proved nothing about RUNTIME: the MCP wrapper used
    to import the generated module into the server process and call it
    directly, so an approved tool had full host access (filesystem, network,
    env) while only its build gates were isolated (Block 1 / audit SEC-02/11).

    The call now happens in the same sandbox path as the gates: network
    namespace unshared, home/root/foreign tmp empty, the tool's own directory
    writable. Absolute paths are mandatory — bwrap cannot bind relative
    targets. Without a working sandbox NOTHING executes.

    ``allowed_domains`` stays declarative on purpose: there is no per-domain
    egress proxy, the sandbox removes the network entirely (stricter). That
    residual is documented in docs/SECURITY-BOUNDARIES.md.
    """
    payload = json.dumps(dict(arguments or {}), default=str)
    workdir = Path(source).parent.resolve()
    entry = spec.entrypoint or "main"
    script = (
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(workdir)!r})\n"
        f"import {spec.module} as _tool\n"
        f"_out = getattr(_tool, {entry!r})(**json.loads({payload!r}))\n"
        f"print({_RESULT_MARKER!r} + json.dumps(_out, default=str, sort_keys=True))\n"
    )
    try:
        code, output, sandboxed = gates._run(
            [sys.executable, "-c", script], workdir=workdir, timeout=SANDBOX_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {SANDBOX_TIMEOUT_S:.0f}s (sandboxed)"}
    except Exception as exc:  # never crash the server over a tool failure
        return {"error": f"{type(exc).__name__}: {exc}"}
    if not sandboxed:
        return {
            "error": (
                "sandbox unavailable — execution blocked (fail-closed); "
                f"generated tool {spec.name!r} was not run"
            )
        }
    marker_hits = [
        line[len(_RESULT_MARKER) :]
        for line in (output or "").splitlines()
        if line.startswith(_RESULT_MARKER)
    ]
    if code != 0 or not marker_hits:
        detail = " ".join((output or "").split())[-400:] or f"exit {code}"
        return {"error": f"sandboxed run failed: {detail}"}
    try:
        return json.loads(marker_hits[0])
    except json.JSONDecodeError:
        return {"error": "sandboxed run returned a non-JSON result"}


def approved_python_specs(
    store: ToolStore,
    *,
    runtime: str | Path | None = None,
    project_id: str = "",
    include_legacy: bool = False,
    apply_health: bool = True,
) -> list[ToolSpec]:
    """Approved python specs — executable surface, scope-filtered if runtime given.

    Scope semantics (§7): without a project context only global tools are
    exported; project tools require their matching project_id; legacy
    (scope-less) tools are never auto-exposed — ``include_legacy`` is the
    explicit all view. Visible entries resolve name collisions (project beats
    global, then higher version), so exactly one spec per name is exported.

    Health gate (§1.6): with a ``runtime`` the exported surface also respects
    the capability-health ledger — a quarantined/retired tool is not offered to
    the model again (``health_verdict``). ``apply_health=False`` is the explicit
    audit view and never the default.
    """
    specs = [
        spec
        for spec in store.all()
        if (
            spec.status == STATUS_APPROVED
            and spec.kind in (KIND_PYTHON, "http", "mcp")
            and spec.module
            and spec.entrypoint
        )
    ]
    if runtime is None:
        return specs
    from .capability import resolve_tool_names, visible_tools

    visible = visible_tools(runtime, project_id, include_legacy=include_legacy)
    resolved = resolve_tool_names(visible)[0]
    names = {entry["name"] for entry in resolved}
    if not apply_health:
        return [spec for spec in specs if spec.name in names]
    return [spec for spec in specs if spec.name in names and health_verdict(runtime, spec.name)[0]]


def scope_conflicts(entries: list[dict]) -> list[dict]:
    """Unresolvable name collisions among visible entries (T8).

    ``resolve_tool_names`` drops truly tied names from the pick; until now
    the export just lost them. Callers surface this instead.
    """
    from .capability import resolve_tool_names

    _resolved, conflicts = resolve_tool_names(entries)
    return list(conflicts)


def _wrapper_for(spec: ToolSpec, *, base_dir: Path | None = None):
    """Build an MCP tool function for a spec.

    Takes one object parameter ``arguments`` (MCP delivers object-shaped
    input) which is forwarded as ``**kwargs`` to the tool entrypoint. Errors
    return structured payloads instead of crashing the server.

    This is the *call convention* (``module.entrypoint(**arguments)``), unchanged
    and still used by ``capability_mining`` / ``liveinsert`` / ``step_runners``.
    The schema the MCP surface publishes is a separate concern
    (:func:`tool_surface`): this function stays object-shaped so every existing
    caller keeps working, while the server registers a ``**``-typed view of it.

    ``base_dir`` is the registry directory: tools generated by the builder
    live in ``<base_dir>/generated_tools/<name>/``. Those are **untrusted**
    generated code, so they never run in this process — they execute through
    ``gates._run`` (bwrap sandbox, Block 1 / SEC-02/11). Specs that resolve
    from ``sys.path`` are fixtures/manual registrations and stay in-process
    (documented residual).

    Import errors of a generated tool are reported as a failed sandboxed run,
    never by importing it here.
    """
    generated = _generated_source(spec, base_dir) if base_dir is not None else None

    def _load_module():
        try:
            return importlib.import_module(spec.module)
        except ImportError:
            if base_dir is not None:
                source = Path(base_dir) / "generated_tools" / spec.name / f"{spec.module}.py"
                if source.exists():
                    module_spec = importlib.util.spec_from_file_location(spec.module, source)
                    if module_spec is not None and module_spec.loader is not None:
                        module = importlib.util.module_from_spec(module_spec)
                        module_spec.loader.exec_module(module)
                        return module
            raise

    def _record(ok: bool, error: str, started: float) -> None:
        """Usage ledger (spec §15) — only when we know the registry dir."""
        if base_dir is None:
            return
        try:
            from .toolusage import record_usage

            record_usage(
                base_dir,
                spec.name,
                ok=ok,
                error=error,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:
            pass

    def wrapper(arguments: dict | None = None) -> Any:
        started = time.monotonic()
        if generated is not None:  # untrusted generated code → sandbox only
            out = _run_generated_in_sandbox(spec, source=generated, arguments=arguments)
            ok = not (isinstance(out, dict) and "error" in out)
            error = str(out.get("error", "")) if isinstance(out, dict) else ""
            _record(ok, error, started)
            return out
        try:
            module = _load_module()
            function = getattr(module, spec.entrypoint)
        except Exception as exc:  # structured error instead of a server crash
            _record(False, f"import failed: {type(exc).__name__}: {exc}", started)
            return {"error": f"import failed: {type(exc).__name__}: {exc}"}
        try:
            out = function(**(arguments or {}))
            _record(True, "", started)
            return out
        except Exception as exc:
            _record(False, f"{type(exc).__name__}: {exc}", started)
            return {"error": f"{type(exc).__name__}: {exc}"}

    wrapper.__name__ = spec.name
    wrapper.__doc__ = spec.description or spec.title
    wrapper.__qualname__ = spec.name
    return wrapper


# --------------------------------------------------------------------------- #
# the published schema surface (inputSchema / outputSchema)                    #
# --------------------------------------------------------------------------- #
#
# Every tool used to be registered as ``f(arguments: dict | None = None)``, so
# FastMCP derived ONE property for all of them — an untyped ``arguments`` bag —
# and named it in ``tools/list``. A measuring round found the cost: Skaldir's
# own server was **0/9 mappable** by the MCP adapter, refused as
# ``'arguments<anyOf[0]>': a free-form object carries no typed properties``.
# The fix is not to invent a schema — the tools already declare theirs
# (``tools_core`` contracts, and the descriptor files the registry writes) — but
# to **publish what the tool declares**: verbatim as the ``inputSchema``, and as
# a *projection* for the ``outputSchema`` where a field the contract leaves open
# (an array whose element type is only described in prose) is named in the
# description instead of being invented or silently dropped.

#: What a tool without any declared input schema accepts: an honest free-form bag.
#: It maps to nothing and says so — that is the truth, not a placeholder.
FREEFORM_INPUT = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "free-form: this tool declares no typed input schema (a builder-generated tool's "
        "arguments are whatever its generated module accepts), so no property can be "
        "named, typed or validated here"
    ),
}

#: The result envelope every registered core tool returns (``ToolResult.to_dict``):
#: the fixed fields are always present, so they are the part of the result shape
#: this server can state as a schema.
RESULT_ENVELOPE_FIELDS: dict[str, dict[str, Any]] = {
    "tool": {"type": "string", "description": "the tool that answered"},
    "ok": {"type": "boolean", "description": "whether the tool call itself succeeded"},
    "error_kind": {
        "type": "string",
        "description": "the contract error kind ('' on success) — it drives exit_code",
    },
    "error": {"type": "string", "description": "the failure detail ('' on success)"},
    "exit_code": {"type": "integer", "description": "the CLI exit code of the error kind"},
    "duration_ms": {"type": "integer", "description": "wall-clock cost of the call"},
    "warnings": {"type": "array", "items": {"type": "string"}},
    "evidence": {
        "type": "array",
        "description": "the facts the result rests on",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "ref": {"type": "string"},
                "kind": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["ref", "kind", "note"],
        },
    },
}

#: ``tools_core.contract.EXIT_INTERNAL`` — repeated so this module stays importable
#: without the tool package; used when a result does not carry the declared shape.
_INTERNAL_EXIT_CODE = 7

#: JSON Schema keywords that make a fragment a *statement* we can repeat verbatim.
_CARRIED_KEYWORDS = (
    "description",
    "enum",
    "const",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
)

_SCALAR_TYPES = ("string", "integer", "number", "boolean")


def declared_schemas(spec: ToolSpec, *, base_dir: Path | None = None) -> dict[str, Any]:
    """The schemas the *tool itself* declares — reused, never re-invented.

    Two sources, in order: the in-process core contract table
    (:func:`skaldir.tools_core.registry.contract_for`), then the descriptor file
    the registry wrote for the tool (``<runtime>/tools_core/<name>.json``, the
    same document ``tools_core register`` produces). A builder-generated tool has
    neither, and gets ``{}`` — which the callers turn into an honest "no typed
    schema is declared" instead of a guess. Never raises.
    """
    name = str(spec.name)
    descriptor = ""
    try:
        from .tools_core import registry as core

        dirname = str(getattr(core, "DESCRIPTOR_DIRNAME", "tools_core"))
        contract = core.contract_for(name)
    except Exception:  # noqa: BLE001 — no tools_core is a normal, honest case
        core = None  # type: ignore[assignment]
        dirname = "tools_core"
        contract = None
    if contract is not None:
        return {
            "input": dict(contract.input_schema),
            "output": dict(contract.output_schema),
            "source": f"the core contract of {name!r} (skaldir.tools_core.registry.contracts())",
            "descriptor": "",
            "origin": "core-contract",
        }
    if base_dir is not None:
        candidate = Path(base_dir) / dirname / f"{name}.json"
        if candidate.is_file():
            descriptor = str(candidate)
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — an unreadable descriptor is not a schema
                payload = {}
            if isinstance(payload.get("input_schema"), dict):
                return {
                    "input": dict(payload["input_schema"]),
                    "output": dict(payload.get("output_schema") or {}),
                    "source": f"the descriptor the registry wrote ({descriptor})",
                    "descriptor": descriptor,
                    "origin": "descriptor",
                }
    return {"input": {}, "output": {}, "source": "", "descriptor": descriptor, "origin": ""}


def _copy_json(fragment: dict[str, Any]) -> dict[str, Any]:
    """A JSON-safe deep copy — the published schema is never the contract's object."""
    return json.loads(json.dumps(fragment, default=str))


def _carried(fragment: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    for keyword in _CARRIED_KEYWORDS:
        if keyword in fragment and keyword not in out:
            out[keyword] = fragment[keyword]
    return out


def _publishable(fragment: Any, *, depth: int = 0) -> dict[str, Any] | None:
    """What of one declared schema fragment this server can state as MCP schema.

    Returns a schema describing **exactly** what the contract states, or ``None``
    when the fragment is something this server cannot claim (an open array, a
    free-form object, a ``$ref``, a union this code does not read). A partial
    object keeps its typed fields and says which declared fields it leaves open
    (``additionalProperties: true`` + a description naming them) — a schema that
    states less is honest; a schema that states more is not.
    """
    if not isinstance(fragment, dict) or depth > 12:
        return None
    for opaque in ("$ref", "oneOf", "allOf", "anyOf", "not", "if", "then", "else"):
        if opaque in fragment:
            return None
    if "prefixItems" in fragment or "patternProperties" in fragment:
        return None
    declared = fragment.get("type")
    nullable = False
    if isinstance(declared, list):
        named = [str(item) for item in declared if str(item) != "null"]
        nullable = len(named) != len(declared)
        if len(named) != 1 or named[0] not in _SCALAR_TYPES:
            return None
        kind = named[0]
    elif isinstance(declared, str):
        kind = declared
        nullable = kind == "null"
    elif "properties" in fragment or "additionalProperties" in fragment:
        kind = "object"
    else:
        return None  # an untyped value is not a statement
    if kind not in (*_SCALAR_TYPES, "object", "array"):
        return None
    if kind == "object":
        properties = fragment.get("properties")
        if not isinstance(properties, dict) or not properties:
            return None  # a free-form object carries no typed properties
        kept: dict[str, Any] = {}
        for prop_name, prop_spec in properties.items():
            projection = _publishable(prop_spec, depth=depth + 1)
            if projection is not None:
                kept[str(prop_name)] = projection
        if not kept:
            return None
        out = _carried(fragment, {"type": "object", "properties": kept})
        open_fields = sorted(str(name) for name in properties if str(name) not in kept)
        # Nothing was dropped and the contract itself closes the object ⇒ we can
        # repeat that; anything else stays open, because we are stating less.
        closed = not open_fields and fragment.get("additionalProperties") is False
        out["additionalProperties"] = False if closed else True
        required = [str(item) for item in (fragment.get("required") or []) if str(item) in kept]
        if required:
            out["required"] = required
        if open_fields:
            note = (
                "the contract leaves these declared field(s) open, so they are not typed "
                "here: " + ", ".join(open_fields)
            )
            out["description"] = f"{out.get('description', '')} — {note}".strip(" —")
        return out
    if kind == "array":
        items = fragment.get("items")
        if not isinstance(items, dict):
            return None  # an array without an item schema is not typed
        projection = _publishable(items, depth=depth + 1)
        if projection is None:
            return None
        out = _carried(fragment, {"type": "array", "items": projection})
        return out
    out: dict[str, Any] = {"type": kind}
    if nullable:
        out["type"] = [kind, "null"]
    return _carried(fragment, out)


def mcp_input_schema(spec: ToolSpec, declared: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """``(inputSchema, note)`` for one tool — the declared types, or an honest bag."""
    candidate = declared.get("input")
    if (
        isinstance(candidate, dict)
        and candidate
        and ("properties" in candidate or "additionalProperties" in candidate)
    ):
        schema = _copy_json(candidate)
        schema.setdefault("type", "object")
        return schema, ""
    return _copy_json(FREEFORM_INPUT), FREEFORM_INPUT["description"]


def mcp_output_schema(spec: ToolSpec, declared: dict[str, Any]) -> dict[str, Any] | None:
    """``outputSchema`` for one tool — the result envelope, plus what the payload states.

    ``None`` when the tool declares no output schema at all: then this server has
    nothing to state about its own result, and MCP's absence of ``outputSchema``
    is that statement (a client reads the text content). When one is declared,
    the envelope is typed field by field and the tool's payload is added as
    ``output`` *as far as the contract states it*; a payload field the contract
    leaves open is named in the description, never invented.
    """
    declared_output = declared.get("output")
    if not isinstance(declared_output, dict) or not declared_output:
        return None
    properties = {name: _copy_json(field) for name, field in RESULT_ENVELOPE_FIELDS.items()}
    required = list(RESULT_ENVELOPE_FIELDS)
    payload = _publishable(declared_output)
    note = ""
    if payload is not None:
        properties["output"] = payload
        required.append("output")
    else:
        note = (
            "the tool's own payload travels under 'output'; it is not declared here because "
            "the contract leaves its shape open (the full declared schema is in "
            f"{declared.get('descriptor') or declared.get('source')})"
        )
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
        "properties": properties,
        "required": required,
        "description": (
            "the tool's result document: the envelope fields are always present; "
            + ("'output' carries the typed payload" if payload is not None else note)
        ),
    }
    return schema


def _accepts_null(fragment: Any) -> bool:
    """Whether a declared property admits ``null`` — the rule for dropping ``None``."""
    if not isinstance(fragment, dict):
        return False
    declared = fragment.get("type")
    if declared == "null":
        return True
    if isinstance(declared, list):
        return "null" in [str(item) for item in declared]
    for alternative in fragment.get("anyOf") or ():
        if isinstance(alternative, dict) and alternative.get("type") == "null":
            return True
    return False


def _parameter_names(schema: dict[str, Any]) -> list[str]:
    """The declared property names, when every one of them can be a Python key."""
    import keyword

    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return []
    names = sorted(str(name) for name in properties)
    if not all(name.isidentifier() and not keyword.iskeyword(name) for name in names):
        return []
    return names


def _envelope(spec: ToolSpec, out: Any) -> Any:
    """Publish an unexpected result in the declared envelope shape (or pass it through).

    A registered core tool answers with ``ToolResult.to_dict()``. When something
    short of that comes back (an import failed, a wrapper returned a bare
    ``{"error": …}``), the declared shape must still be the answer — reporting it
    as a failed call with the envelope intact, rather than handing a client a
    document its own declared schema does not describe.
    """
    if isinstance(out, dict) and "ok" in out and "tool" in out:
        return out
    detail = out.get("error") if isinstance(out, dict) else out
    return {
        "tool": str(spec.name),
        "ok": False,
        "error_kind": "internal",
        "error": str(detail or "the tool returned a result outside its declared shape"),
        "exit_code": _INTERNAL_EXIT_CODE,
        "duration_ms": 0,
        "warnings": [],
        "evidence": [],
        "output": {},
    }


def _typed_callable(spec: ToolSpec, schema: dict[str, Any], *, base_dir: Path | None):
    """``(callable, convention)`` for one tool: the declared parameters, then the call.

    FastMCP derives what it validates AND what it advertises from the callable's
    signature, so a tool that declares eight properties must be a function of
    eight parameters — a function of one ``arguments`` bag cannot be validated
    against them. The signature is built here as an :class:`inspect.Signature`
    from the declared schema (name, type, required) and attached to the callable,
    which is what FastMCP (and pydantic) read; the published schema is set from
    the same source, so the two never disagree. ``None`` is dropped for a property
    that does not admit null, which is what leaves the tool's own default in place
    instead of sending an explicit null it never asked for. Falls back to the
    object-parameter convention when a property name cannot be a parameter (never
    invented, named in ``convention``).
    """
    inner = _wrapper_for(spec, base_dir=base_dir)
    properties = dict(schema.get("properties") or {})
    names = _parameter_names(schema)
    if not names:
        return inner, "object-parameter"

    def dispatch(payload: dict[str, Any]) -> Any:
        cleaned = {
            key: value
            for key, value in payload.items()
            if not (value is None and not _accepts_null(properties.get(key)))
        }
        return _envelope(spec, inner(cleaned))

    required = {str(item) for item in (schema.get("required") or [])}
    # required parameters first: Python does not allow a defaulted parameter
    # before one without a default (the order on the wire is unaffected).
    ordered = [name for name in names if name in required] + [
        name for name in names if name not in required
    ]
    def call(*args: Any, **kwargs: Any) -> Any:
        """The declared parameters — collected by the synthetic signature above.

        There is no generated source anywhere in this path: the parameters arrive
        as ordinary arguments and are handed to the tool as the same named mapping
        the schema describes. What the signature does not accept is refused here,
        the way a real signature would: a missing required argument, an argument the
        tool never declared, or more positional arguments than it has parameters.
        """
        supplied = signature.bind(*args, **kwargs).arguments
        return dispatch({name: supplied.get(name) for name in names})

    # The signature FastMCP validates and advertises from, and the signature the
    # tool's own declared schema is published as: one construction, so they cannot
    # drift. The annotations are real objects (not strings) because this module is
    # executed here, not compiled from text.
    parameters = []
    for name in ordered:
        annotation = _python_annotation(properties.get(name))
        if name in required:
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=annotation,
                )
            )
        else:
            parameters.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=None,
                    annotation=annotation | None,
                )
            )
    signature = inspect.Signature(parameters)
    call.__signature__ = signature  # type: ignore[attr-defined]
    # pydantic reads the annotations off ``__annotations__`` (its own type-hint
    # lookup, not the synthetic signature), so the same real annotation objects
    # travel in both places: one construction per declared property.
    call.__annotations__ = {param.name: param.annotation for param in parameters}
    call.__name__ = "_mcp_" + "".join(
        char if (char.isalnum() or char == "_") else "_" for char in str(spec.name)
    )
    return call, "declared-properties"


def _python_annotation(fragment: Any) -> Any:
    """The Python annotation object for one declared property (``Any`` when unstated)."""
    if not isinstance(fragment, dict):
        return Any
    declared = fragment.get("type")
    if isinstance(declared, list):
        named = [str(item) for item in declared if str(item) != "null"]
        if len(named) == 1 and named[0] in _ANNOTATION_TYPES:
            return _ANNOTATION_TYPES[named[0]]
        return Any
    return _ANNOTATION_TYPES.get(str(declared), Any)


def tool_surface(spec: ToolSpec, *, base_dir: Path | None = None) -> dict[str, Any]:
    """One tool's MCP surface: what to register, and the schemas it publishes.

    ``inputSchema``/``outputSchema`` come from the tool's own declaration
    (:func:`declared_schemas`); nothing is hand-written here and nothing the
    contract states is dropped silently.
    """
    declared = declared_schemas(spec, base_dir=base_dir)
    input_schema, input_note = mcp_input_schema(spec, declared)
    output_schema = mcp_output_schema(spec, declared)
    function, convention = _typed_callable(spec, input_schema, base_dir=base_dir)
    notes = [note for note in (input_note,) if note]
    if convention == "object-parameter" and input_schema.get("properties"):
        notes.append(
            "the declared property names cannot all be Python parameters — the tool is "
            "published under the one-object call convention"
        )
    return {
        "name": str(spec.name),
        "title": spec.title or "",
        "description": spec.description or spec.title,
        "function": function,
        "call_convention": convention,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "declared_by": str(declared.get("source") or ""),
        "descriptor": str(declared.get("descriptor") or ""),
        "notes": notes,
    }


def build_server(
    store: ToolStore,
    *,
    name: str = "godsv-tools",
    runtime: str | Path | None = None,
    project_id: str = "",
    include_legacy: bool = False,
):
    """Build a FastMCP server exposing the approved python tools.

    With ``runtime`` the surface is scope-filtered for ``project_id`` (§7):
    project tools of other projects never appear. Every server start reads
    the registry fresh — tools built during a run are exposed on the next
    start without any manual activation.

    Each tool is registered with **its own declared schemas**
    (:func:`tool_surface`): the properties/types/required/defaults/enums of the
    tool's contract as the ``inputSchema``, and the typed result envelope as the
    ``outputSchema``. A tool that declares no schema is published as what it is —
    a free-form bag, said out loud — instead of every tool being flattened into
    that shape.
    """
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover — dev envs have it via extra
        raise RuntimeError(
            "fastmcp is missing — install the extra: uv pip install 'godsv[mcp]'"
        ) from exc
    try:  # fastmcp>=2 exposes it here; without it the surface stays object-shaped
        from fastmcp.tools import FunctionTool
    except ImportError:  # pragma: no cover — an older fastmcp
        FunctionTool = None  # type: ignore[assignment]
    server = FastMCP(name)
    base_dir = (
        Path(runtime)
        if runtime is not None
        else (Path(store.path).parent if getattr(store, "path", None) else None)
    )
    for spec in approved_python_specs(
        store, runtime=runtime, project_id=project_id, include_legacy=include_legacy
    ):
        surface = tool_surface(spec, base_dir=base_dir)
        if FunctionTool is None:  # pragma: no cover — degraded, old fastmcp
            server.tool(name=spec.name, description=surface["description"])(
                _wrapper_for(spec, base_dir=base_dir)
            )
            continue
        tool = FunctionTool.from_function(
            surface["function"],
            name=surface["name"],
            title=surface["title"] or None,
            description=surface["description"],
            output_schema=surface["output_schema"],
        )
        # The callable's signature gives FastMCP the shape; the tool's own declared
        # schema is the authority for descriptions, enums, bounds and defaults.
        tool.parameters = surface["input_schema"]
        server.add_tool(tool)
    return server


def run_serve(
    db_path: str | Path,
    *,
    name: str = "godsv-tools",
    runtime: str | Path | None = None,
    project_id: str = "",
    include_legacy: bool = False,
) -> None:
    """Run the stdio MCP server for the approved tools of a store (blocking)."""
    store = ToolStore(db_path)
    server = build_server(
        store,
        name=name,
        runtime=runtime,
        project_id=project_id,
        include_legacy=include_legacy,
    )
    server.run()
