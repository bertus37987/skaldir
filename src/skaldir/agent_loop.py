"""The agent loop: tool schema → ``tool_calls`` → execute → feed back → stop.

This module is the limb that was missing between *tool runtime* and *agent*.
Before it, Skaldir could call a model (text in, text out), and it could execute
a registered tool — but nothing let the **model** ask for one: no ``tools``
array on the request, no ``tool_calls`` read off the answer.

What lives here, in the order the task names it:

1. **Tool schema generation** — :func:`input_schema_for_spec` +
   :func:`definition_for_spec` turn a registered :class:`skaldir.tools.ToolSpec`
   into the JSON Schema an OpenAI-compatible endpoint expects. The typed input
   comes from the tool's own signature, read with :mod:`ast` from the generated
   module (never imported — generated code is untrusted), falling back to
   ``sample_input`` and to a documented free-form object. Built-in tools carry
   their schema literally.
2. **The request shape** — :func:`skaldir.llm_provider.chat_request_body` (pure
   builder), :meth:`skaldir.llm_provider.OpenAICompatibleLLM.chat` (the call,
   same §43 retry policy as ``complete``), :func:`tool_calls_from_message` and
   :func:`accumulate_stream_chunks` (fragmented deltas, folded per ``index``).
   A model that returns no tool call is a normal answer, not an error.
3. **The loop** — assistant → ``tool_calls`` → execute each (in parallel where
   the tool declares :attr:`AgentTool.parallel_safe`, sequentially otherwise) →
   append one ``role: tool`` message per call → repeat until the model stops
   asking or a bound is hit.
4. **Stop conditions and honesty** — step limit, token budget, cost budget
   (only from a *declared* price, never an invented one), wall clock and
   cancellation. A run that hits a bound ends ``incomplete`` with the bound
   named, and **carries no answer**: whatever text the model produced last is
   recorded as ``last_text``, a diagnostic, never as the result.
5. **The transcript** — one JSONL file per run under
   ``<state_dir>/runs/<run_id>.jsonl``: the run header (task, limits, the tool
   schemas offered), every request body verbatim (the ``tools`` array included),
   every answer, every tool call with its arguments, policy verdict, duration,
   result digest and a bounded result preview. ``agent transcript <run-id>``
   reads it back; nothing about a run has to be taken on faith.

Pre-execution policy: every model-requested call goes through
:func:`skaldir.toolcall.judge_tool_call` before it runs (the same deterministic
interception the rest of the runtime uses). ``BLOCK`` / ``ASK_APPROVAL`` /
``PAUSE_AND_INVESTIGATE`` mean the call is **not executed** and the model
receives the decision text as the tool result — an unknown tool, a schema
violation and a tool error are also fed back as text, so the model can correct
itself instead of the loop crashing.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from .env import getenv
from .llm import LLMError, estimate_tokens
from .llm_provider import (
    ChatResult,
    ProviderAuthError,
    ProviderConfigError,
    ToolCall,
    chat_request_body,
)
from .progress_guard import ACTION_REPLAN, CommitPoint, RetryGate
from .tool_archive import RETRIEVE_TOOL_NAME as ARCHIVE_READER_TOOL
from .tool_archive import ToolArchive
from .toolcall import ToolIntent, judge_tool_call

# --------------------------------------------------------------------------- #
# statuses + exit codes (mirrors skaldir.cli_contract — adopted lazily)        #
# --------------------------------------------------------------------------- #

STATUS_ANSWERED = "answered"
STATUS_INCOMPLETE = "incomplete"
STATUS_FAILED = "failed"
STATUSES = (STATUS_ANSWERED, STATUS_INCOMPLETE, STATUS_FAILED)

#: Why a run stopped without an answer. Every one of them is a *bound*, and a
#: bound is reported as such — never translated into a "done".
REASON_MAX_STEPS = "max_steps"
REASON_TOKEN_BUDGET = "token_budget"
REASON_COST_BUDGET = "cost_budget"
REASON_WALL_CLOCK = "wall_clock"
REASON_CANCELLED = "cancelled"
REASON_EMPTY_ANSWER = "empty_answer"
REASON_PROVIDER_ERROR = "provider_error"
REASON_NEEDS_CREDENTIALS = "needs_credentials"
REASON_INTERNAL_ERROR = "internal_error"
#: the progress guard decided the run is stuck and no strategy change followed —
#: a **named** stop, never a silent loop (§PROGRESS-GUARD).
REASON_NO_PROGRESS = "no_progress"
REASON_NO_STRATEGY_CHANGE = "no_strategy_change_after_directive"
BOUND_REASONS = (
    REASON_MAX_STEPS,
    REASON_TOKEN_BUDGET,
    REASON_COST_BUDGET,
    REASON_WALL_CLOCK,
    REASON_CANCELLED,
    REASON_NO_PROGRESS,
    REASON_NO_STRATEGY_CHANGE,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_NEEDS_CREDENTIALS = 4
EXIT_INTERNAL = 7

DEFAULT_MAX_STEPS = 64
DEFAULT_MAX_RESULT_CHARS = 4000
DEFAULT_MAX_WORKERS = 4

#: How often a *streaming* model call tells the live-progress renderer how many deltas have
#: arrived (ms). The content itself is never throttled — this is only the counter that keeps
#: a slow stream's progress line honest. One line per event is the renderer's own rule; the
#: loop just refuses to hand it a thousand events a second.
_DELTA_NOTE_MS = 100

TRANSCRIPT_SCHEMA_VERSION = "1"

#: ``SKALDIR_PERF_NOCACHE=1`` restores the pre-fix transcript behaviour: derive
#: ``seq`` by re-reading and re-counting the whole file on every event (O(N²)
#: I/O over a run). It exists so ``tools/gv_perf_codex_compare.py`` can measure a
#: true before/after with one switch — it is not a supported running mode.
_LEGACY_TRANSCRIPT_COUNT = str(getenv("SKALDIR_PERF_NOCACHE", "") or "").strip() in {
    "1",
    "true",
    "yes",
}

SYSTEM_PROMPT = (
    "You are Skaldir's agent loop. Work the task with the tools you are actually given.\n"
    "Call a tool when you need information or an effect; never describe a call you did not make.\n"
    "Read a tool error and correct your next call; a failed step is never a success.\n"
    "Answer without a tool call only when the task is actually finished. The answer must be true of what the tools returned.\n"  # noqa: E501
    "The harness records every call and may stop the run at a step, token, cost or time bound. If you are stopped, say plainly what is still open — a partial answer is never presented as done.\n"  # noqa: E501
    "If you are uncertain, say so instead of guessing; uncertainty is better than an invented result.\n"  # noqa: E501
)


class AgentLoopError(RuntimeError):
    """The loop cannot run as asked (no tool-calling client, unusable limits)."""


# --------------------------------------------------------------------------- #
# JSON Schema generation                                                       #
# --------------------------------------------------------------------------- #

#: annotation name → JSON Schema type. Unknown names become a free-form value.
PRIMITIVE_TYPES: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "bytes": "string",
    "list": "array",
    "tuple": "array",
    "set": "array",
    "dict": "object",
    "None": "null",
    "NoneType": "null",
    "Any": None,  # type: ignore[dict-item]  — free-form on purpose
    "object": "object",
}


def _annotation_name(node: ast.AST) -> str:
    """``ast.Name('str')`` / ``ast.Attribute(...typing.Optional)`` → its bare name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "None"
        return type(node.value).__name__
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    return ""


def schema_for_annotation(node: ast.AST | None) -> dict[str, Any]:
    """Type annotation (AST) → JSON Schema. Optional[X] is X (documented).

    Deliberately small: only the shapes a tool input realistically uses. A
    ``dict``/``Any``/unknown name stays free-form instead of guessing a type —
    a wrong ``type`` in the schema makes a correct call invalid.
    """
    if node is None:
        return {}
    if isinstance(node, ast.Subscript):
        base = _annotation_name(node.value)
        inner = node.slice
        if base in ("Optional", "Union"):
            members = inner.elts if isinstance(inner, ast.Tuple) else [inner]
            for member in members:
                if _annotation_name(member) not in ("None", "NoneType"):
                    return schema_for_annotation(member)
            return {"type": "null"}
        if base in ("list", "List", "Sequence", "Iterable", "tuple", "Tuple", "set", "Set"):
            schema: dict[str, Any] = {"type": "array"}
            items = inner.elts[0] if isinstance(inner, ast.Tuple) and inner.elts else inner
            item_schema = schema_for_annotation(items)
            if item_schema:
                schema["items"] = item_schema
            return schema
        if base in ("dict", "Dict", "Mapping"):
            return {"type": "object"}
        if base == "Literal" and isinstance(inner, ast.Tuple):
            values = [
                element.value
                for element in inner.elts
                if isinstance(element, ast.Constant)
            ]
            if values:
                return {"enum": values}
        return {}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # ``str | None`` — the non-None side wins (same rule as Optional)
        left, right = schema_for_annotation(node.left), schema_for_annotation(node.right)
        if left.get("type") == "null":
            return right
        return left or right
    name = _annotation_name(node)
    if name in ("None", "NoneType"):
        return {"type": "null"}
    mapped = PRIMITIVE_TYPES.get(name, "?")
    if mapped is None:
        return {}
    if mapped == "?":
        return {}
    return {"type": mapped}


def schema_for_value(value: Any) -> dict[str, Any]:
    """A sample/default value → JSON Schema (used when nothing is annotated)."""
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, Mapping):
        properties = {str(key): schema_for_value(item) for key, item in value.items()}
        return {"type": "object", "properties": properties}
    if isinstance(value, (list, tuple)):
        schema: dict[str, Any] = {"type": "array"}
        if value:
            schema["items"] = schema_for_value(value[0])
        return schema
    key = type(value).__name__
    return {} if key else {}


def input_schema_from_sample(sample: Mapping[str, Any] | None) -> dict[str, Any]:
    """``sample_input`` → free-form object schema (keys typed, nothing required).

    A sample is *evidence of a call shape*, not a contract: it never marks an
    argument required (a run must not be refused because a sample omitted one).
    """
    if not isinstance(sample, Mapping) or not sample:
        return {}
    return {
        "type": "object",
        "properties": {str(key): schema_for_value(value) for key, value in sample.items()},
        "additionalProperties": True,
    }


def input_schema_from_source(source_text: str, entrypoint: str) -> dict[str, Any]:
    """Read one function's signature out of Python **source** (no import, no exec).

    Generated tools are untrusted code and are executed in a sandbox — importing
    them to introspect a signature would defeat that. :mod:`ast` gives the
    parameter names, their annotations and their defaults without running
    anything.
    """
    if not source_text or not entrypoint:
        return {}
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return {}
    function: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == entrypoint:
            function = node
            break
    if function is None:
        return {}
    args = function.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.AST | None] = [None] * (len(positional) - len(args.defaults))
    defaults.extend(list(args.defaults))
    properties: dict[str, Any] = {}
    required: list[str] = []
    for argument, default in zip(positional, defaults, strict=False):
        if argument.arg in ("self", "cls"):
            continue
        _add_parameter(properties, required, argument, default)
    for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        _add_parameter(properties, required, argument, default)
    has_var_keyword = args.kwarg is not None
    if not properties and not has_var_keyword:
        return {}
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": bool(has_var_keyword),
    }
    if required:
        schema["required"] = sorted(required)
    return schema


def _add_parameter(
    properties: dict[str, Any],
    required: list[str],
    argument: ast.arg,
    default: ast.AST | None,
) -> None:
    """One parameter → one property (annotation first, literal default as a hint)."""
    schema = dict(schema_for_annotation(argument.annotation))
    if not schema and default is not None and isinstance(default, ast.Constant):
        schema = schema_for_value(default.value)
    if default is not None:
        try:
            schema["default"] = ast.literal_eval(default)
        except (ValueError, TypeError, SyntaxError):
            pass  # a non-literal default is documented, never guessed
    properties[argument.arg] = schema
    if default is None:
        required.append(argument.arg)


def input_schema_for_spec(spec: Any, *, base_dir: str | Path | None = None) -> dict[str, Any]:
    """JSON Schema for one registered tool spec, best source first.

    1. an explicit ``input_schema`` on the spec (a spec may already carry one);
    2. the entrypoint's own signature, read from the generated module source
       under ``<base_dir>/generated_tools/<name>/<module>.py`` via :mod:`ast`;
    3. ``sample_input`` keys with inferred types;
    4. a free-form object (``additionalProperties: true``) that accepts anything —
       an honest "unknown shape" instead of a schema that would reject valid calls.
    """
    explicit = getattr(spec, "input_schema", None)
    if isinstance(explicit, Mapping) and explicit.get("type") == "object":
        return dict(explicit)
    entrypoint = str(getattr(spec, "entrypoint", "") or "")
    module = str(getattr(spec, "module", "") or "")
    name = str(getattr(spec, "name", "") or "")
    if base_dir is not None and module and name:
        source = Path(base_dir) / "generated_tools" / name / f"{module}.py"
        if source.is_file():
            try:
                text = source.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            from_source = input_schema_from_source(text, entrypoint)
            if from_source:
                return from_source
    from_sample = input_schema_from_sample(getattr(spec, "sample_input", None))
    if from_sample:
        return from_sample
    return {"type": "object", "properties": {}, "additionalProperties": True}


_WIRE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


def wire_tool_name(name: str, *, taken: Iterable[str] = ()) -> str:
    """A tool name the wire accepts: ``^[A-Za-z0-9_-]{1,64}$`` and unique.

    Registry names are dotted (``tools.list``) and dotted names are not valid
    function names on an OpenAI-compatible endpoint, so they are normalised —
    the mapping back to the registry name stays in :class:`AgentTool.source_name`.
    """
    cleaned = _WIRE_NAME_RE.sub("_", str(name or "").strip())
    if not cleaned:
        cleaned = "tool"
    if not cleaned[0].isalpha():
        cleaned = f"t_{cleaned}"
    cleaned = cleaned[:64]
    existing = set(taken)
    if cleaned not in existing:
        return cleaned
    suffix = 2
    while f"{cleaned[:60]}_{suffix}" in existing:
        suffix += 1
    return f"{cleaned[:60]}_{suffix}"


def function_definition(
    name: str, description: str, parameters: Mapping[str, Any]
) -> dict[str, Any]:
    """One entry of the ``tools`` array (the exact wire shape)."""
    return {
        "type": "function",
        "function": {
            "name": str(name),
            "description": str(description or name),
            "parameters": dict(parameters),
        },
    }


def definition_for_spec(spec: Any, *, base_dir: str | Path | None = None) -> dict[str, Any]:
    """A registered spec → its ``tools`` entry (schema from the typed input)."""
    name = str(getattr(spec, "name", "") or "")
    return function_definition(
        name,
        str(getattr(spec, "description", "") or getattr(spec, "title", "") or name),
        input_schema_for_spec(spec, base_dir=base_dir),
    )


def tools_array(
    tools: Sequence[Any], *, tool_choice: Any = "auto"
) -> tuple[list[dict[str, Any]], Any]:
    """``(tools=[...], tool_choice)`` for a request — the pair the provider needs.

    ``tool_choice`` is dropped when there is no tool: a choice without tools is
    invalid, and an empty ``tools: []`` is refused by several servers.
    """
    definitions: list[dict[str, Any]] = []
    taken: list[str] = []
    for tool in tools:
        definition = tool.definition() if hasattr(tool, "definition") else dict(tool)
        function = definition.get("function") or {}
        name = str(function.get("name") or "")
        unique = wire_tool_name(name, taken=taken)
        if unique != name:
            function["name"] = unique
            definition = {"type": definition.get("type", "function"), "function": function}
        taken.append(str(function.get("name")))
        definitions.append(definition)
    return definitions, (tool_choice if definitions else None)


# --------------------------------------------------------------------------- #
# tools the loop can actually call                                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AgentTool:
    """One callable the model may ask for.

    ``parallel_safe`` is the tool's own declaration: a read-only tool is safe to
    run alongside its siblings in the same answer, a mutating one is not (two
    writes in "parallel" is a race, not a speed-up). ``mutating`` additionally
    feeds :func:`skaldir.toolcall.judge_tool_call`, whose ``WRITE_TOOLS`` set
    decides how strictly the call is judged.
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Callable[..., Any]
    parallel_safe: bool = True
    mutating: bool = False
    source: str = "builtin"
    source_name: str = ""

    def definition(self) -> dict[str, Any]:
        return function_definition(self.name, self.description, self.input_schema)


@dataclass
class ToolSet:
    """The tools one run offers the model (wire name → :class:`AgentTool`)."""

    tools: list[AgentTool] = field(default_factory=list)

    @classmethod
    def of(cls, tools: Sequence[AgentTool]) -> ToolSet:
        taken: list[str] = []
        normalised: list[AgentTool] = []
        for tool in tools:
            name = wire_tool_name(tool.name, taken=taken)
            taken.append(name)
            if name != tool.name:
                tool = AgentTool(
                    name=name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    handler=tool.handler,
                    parallel_safe=tool.parallel_safe,
                    mutating=tool.mutating,
                    source=tool.source,
                    source_name=tool.source_name or tool.name,
                )
            normalised.append(tool)
        return cls(normalised)

    def get(self, name: str) -> AgentTool | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def names(self) -> list[str]:
        return [tool.name for tool in self.tools]

    def definitions(self, *, tool_choice: Any = "auto") -> tuple[list[dict[str, Any]], Any]:
        return tools_array(self.tools, tool_choice=tool_choice)

    def __len__(self) -> int:
        return len(self.tools)

    def __iter__(self):
        return iter(self.tools)


def _workspace_path(raw: Any, workspace: Path) -> tuple[Path | None, str]:
    """Resolve a tool's path argument **inside** the workspace (or refuse).

    A tool the model drives must not become an escape hatch: an absolute path
    outside the workspace, or a relative one walking out with ``..``, is an error
    the model is told about, not a file that gets read or written.
    """
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None, "path is empty"
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        return None, f"cannot resolve path {text!r}: {exc}"
    root = workspace.resolve()
    if resolved != root and root not in resolved.parents:
        return None, f"path leaves the workspace: {text!r}"
    return resolved, ""


def _relative(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return str(path)


def builtin_tools(workspace: str | Path) -> list[AgentTool]:
    """The loop's own toolset: real, typed, workspace-fenced file operations.

    Nothing here is a fixture: every call reads or writes the real filesystem in
    the workspace, and every failure is a real error string the model receives.
    Mutating tools declare ``parallel_safe=False`` and ``mutating=True``.
    """
    root = Path(workspace).expanduser()

    def read_text(path: str, max_bytes: int = 20000) -> dict[str, Any]:
        target, problem = _workspace_path(path, root)
        if target is None:
            return {"error": problem or "no usable path"}
        if not target.is_file():
            return {"error": f"not a file: {_relative(target, root)}"}
        limit = max(1, int(max_bytes))
        data = target.read_bytes()[: limit]
        return {
            "path": _relative(target, root),
            "bytes": target.stat().st_size,
            "read_bytes": len(data),
            "truncated": target.stat().st_size > len(data),
            "text": data.decode("utf-8", errors="replace"),
        }

    def list_dir(path: str = ".", limit: int = 200) -> dict[str, Any]:
        target, problem = _workspace_path(path, root)
        if target is None:
            return {"error": problem or "no usable path"}
        if not target.is_dir():
            return {"error": f"not a directory: {_relative(target, root)}"}
        cap = max(1, int(limit))
        entries: list[dict[str, Any]] = []
        children = sorted(target.iterdir(), key=lambda item: item.name)
        for child in children[:cap]:
            try:
                size = child.stat().st_size if child.is_file() else None
            except OSError:
                size = None
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": size,
                }
            )
        return {
            "path": _relative(target, root),
            "count": len(entries),
            "truncated": len(children) > len(entries),
            "entries": entries,
        }

    def sha256_file(path: str) -> dict[str, Any]:
        target, problem = _workspace_path(path, root)
        if target is None:
            return {"error": problem or "no usable path"}
        if not target.is_file():
            return {"error": f"not a file: {_relative(target, root)}"}
        digest = hashlib.sha256()
        total = 0
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
                total += len(chunk)
        return {"path": _relative(target, root), "bytes": total, "sha256": digest.hexdigest()}

    def write_text(path: str, text: str, create_dirs: bool = False) -> dict[str, Any]:
        target, problem = _workspace_path(path, root)
        if target is None:
            return {"error": problem or "no usable path"}
        if not isinstance(text, str):
            return {"error": "text must be a string"}
        parent = target.parent
        if not parent.is_dir():
            if not create_dirs:
                return {"error": f"parent directory does not exist: {_relative(parent, root)}"}
            parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(text, encoding="utf-8")
        return {
            "path": _relative(target, root),
            "bytes": len(text.encode("utf-8")),
            "created": not existed,
        }

    tools: list[AgentTool] = [
        AgentTool(
            name="read_text",
            description="Read a UTF-8 text file inside the workspace (bounded).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "workspace-relative path"},
                    "max_bytes": {"type": "integer", "default": 20000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_text,
        ),
        AgentTool(
            name="list_dir",
            description="List one directory inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "limit": {"type": "integer", "default": 200},
                },
                "additionalProperties": False,
            },
            handler=list_dir,
        ),
        AgentTool(
            name="sha256_file",
            description="sha256 of a file inside the workspace (streamed).",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=sha256_file,
        ),
        AgentTool(
            name="write_text",
            description="Write a UTF-8 text file inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string"},
                    "create_dirs": {"type": "boolean", "default": False},
                },
                "required": ["path", "text"],
                "additionalProperties": False,
            },
            handler=write_text,
            parallel_safe=False,
            mutating=True,
        ),
    ]
    return tools


#: Verified effect metadata for the core tools (Paket 4 / audit F06).
#:
#: The registry tools used to be marked ``parallel_safe=False, mutating=True`` **as a
#: whole**, which serialised reads and searches too — the opposite of the fast independent
#: batching the product wants. These rows are derived from what each tool actually does,
#: and they are named rather than inferred: an entry here is a claim about the code that
#: somebody checked. Anything NOT in the table stays conservative, because "Fremde Tools
#: bleiben ohne belastbaren Vertrag konservativ".
#:
#: ``True``/``False`` = (parallel_safe, mutating).
CORE_EFFECTS: dict[str, tuple[bool, bool]] = {
    # reads: no side effect on the workspace, safe to run alongside siblings
    "read_file": (True, False),
    "list_dir": (True, False),
    "search_files": (True, False),
    "web_search": (True, False),
    "web_fetch": (True, False),
    # writes: one writer at a time
    "write_file": (False, True),
    "patch": (False, True),
    "terminal": (False, True),       # it can do anything a shell can
    "docker_ensure": (False, True),  # starts infrastructure
    "docker.ensure": (False, True),
}


def effect_for(name: str, *, default: tuple[bool, bool] = (False, True)) -> tuple[bool, bool]:
    """``(parallel_safe, mutating)`` for a tool name.

    The default is the conservative one. Only a name in :data:`CORE_EFFECTS` gets the
    faster treatment — an unverified tool is never assumed read-only, because that is how a
    parallel write race gets shipped.
    """
    return CORE_EFFECTS.get(str(name or ""), default)


def registry_tools(
    store: Any,
    *,
    runtime: str | Path | None = None,
    project_id: str = "",
    base_dir: str | Path | None = None,
) -> list[AgentTool]:
    """Approved registry specs as callable tools — the **tool runtime** path.

    Execution goes through :func:`skaldir.mcpserver._wrapper_for`, which is the
    one place that runs a tool: generated tools execute in the bwrap sandbox
    (fail-closed without it), in-process specs stay in-process. The schema comes
    from the tool's own typed input (:func:`input_schema_for_spec`).

    Registry tools declare ``parallel_safe=False``/``mutating=True``: their side
    effects are unknown to the loop, and "unknown" must not be scheduled as if it
    were read-only.
    """
    from .mcpserver import _wrapper_for, approved_python_specs, health_verdict

    directory = (
        Path(base_dir)
        if base_dir is not None
        else (Path(runtime) if runtime is not None else None)
    )
    # scope + health filtering is the runtime's own rule (one implementation)
    specs = approved_python_specs(store, runtime=runtime, project_id=project_id)
    tools: list[AgentTool] = []
    for spec in specs:
        if runtime is not None and not health_verdict(runtime, spec.name)[0]:
            continue
        # the runtime's wrapper takes ONE object parameter (the MCP shape); the loop
        # calls with keyword arguments. ``partial`` binds the spec's own wrapper (a
        # closure over the loop variable would hand every tool the *last* one).
        tools.append(
            AgentTool(
                name=spec.name,
                description=spec.description or spec.title or spec.name,
                input_schema=input_schema_for_spec(spec, base_dir=directory),
                handler=partial(
                    _call_registry_tool, _wrapper_for(spec, base_dir=directory)
                ),
                # Paket 4 (audit F06): reads and searches may run alongside their
                # siblings; only verified effect metadata gets the fast treatment and
                # anything unverified stays conservative.
                parallel_safe=effect_for(spec.name)[0],
                mutating=effect_for(spec.name)[1],
                source="registry",
                source_name=spec.name,
            )
        )
    return tools


def tool_set_for(
    *,
    workspace: str | Path,
    kind: str = "builtin",
    store: Any = None,
    runtime: str | Path | None = None,
    project_id: str = "",
) -> ToolSet:
    """Assemble a run's toolset: ``builtin``, ``registry`` or ``both``."""
    selection = str(kind or "builtin").strip().lower()
    if selection not in ("builtin", "registry", "both"):
        raise AgentLoopError(
            f"unknown tool set {kind!r} (expected builtin|registry|both)"
        )
    tools: list[AgentTool] = []
    if selection in ("builtin", "both"):
        tools.extend(builtin_tools(workspace))
        # The product gap behind the user finding ("behauptet danach aber, keine Shell-
        # oder Python-Ausfuehrung zu besitzen"): `builtin_tools` alone is four file tools,
        # so a model that had just written hello.py had to say it could not run it. The
        # shared capability set is the one build_agent_toolset and goal_run already use.
        # Placed HERE and not inside `builtin_tools`, because that one is what
        # core_capability_tools itself builds on — calling back into it recursed.
        from .core_capabilities import core_capability_tools

        already = {getattr(t, "name", "") for t in tools}
        tools.extend(core_capability_tools(
            workspace=workspace, exclude=already,
        ))
    if selection in ("registry", "both"):
        # Deliberate: the *loop* surface refuses to invent a store. `store=None` here is a
        # caller bug, and `tests/test_agent_loop.py::test_the_registry_tool_set_mixes_builtin_and_registered_tools`
        # pins exactly that (`tool_set_for(kind="registry", store=None)` must raise). The
        # convenience of resolving `<runtime>/tools.db` lives in
        # `agent_toolset.build_agent_toolset`, whose signature advertises `store=None`.
        if store is None:
            raise AgentLoopError("the registry tool set needs a tool store (--db)")
        tools.extend(registry_tools(store, runtime=runtime, project_id=project_id))
    return ToolSet.of(tools)


# --------------------------------------------------------------------------- #
# tool execution                                                               #
# --------------------------------------------------------------------------- #


def _call_registry_tool(wrapped: Callable[[dict[str, Any]], Any], **arguments: Any) -> Any:
    """Join the loop's keyword arguments to the runtime wrapper's single object arg."""
    return wrapped(dict(arguments))


def _argument_problem(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str:
    """Schema check that matters for *calling*: required present, no unknown key."""
    properties = schema.get("properties") or {}
    required = [str(name) for name in (schema.get("required") or [])]
    missing = [name for name in required if arguments.get(name) is None]
    if missing:
        return f"missing required argument(s): {', '.join(sorted(missing))}"
    if properties and not schema.get("additionalProperties", True):
        unknown = sorted(str(key) for key in arguments if key not in properties)
        if unknown:
            known = ", ".join(sorted(properties)) or "(none)"
            return f"unexpected argument(s): {', '.join(unknown)}; allowed: {known}"
    return ""


@dataclass
class ToolOutcome:
    """What one tool call produced — and whether it ran at all."""

    name: str
    call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    executed: bool = False
    result: Any = None
    error: str = ""
    decision: str = ""
    policy_reasons: list[str] = field(default_factory=list)
    duration_ms: int = 0
    digest: str = ""
    truncated: bool = False
    #: the archive facts of an oversized result (``skaldir.tool_archive``), or ``None``
    #: when the result travelled inline. When it is set, ``content()`` carries the id,
    #: the digest and a short summary instead of the full payload.
    archive: dict[str, Any] | None = None

    def content(self, *, limit: int = DEFAULT_MAX_RESULT_CHARS) -> str:
        """The ``role: tool`` content the model receives (bounded, honest).

        Two bounded forms, and they are not the same thing:

        * **archived** — the full payload lives in the archive, and this carries the
          id, the digest, the real size and a short summary, so the model can name
          exactly what it did not read and ask for it back. Retrievable, therefore
          *complete*.
        * **truncated** — no archive is attached (archiving off, or the payload is
          not oversized): the head, and the digest of the whole. This is the old
          behaviour, kept for a loop that runs without an archive.
        """
        if self.archive is not None:
            payload: dict[str, Any] = {"ok": bool(self.ok) and self.executed}
            if not self.executed:
                payload["executed"] = False
                payload["error"] = self.error or "not executed"
            elif self.ok:
                payload["archived"] = dict(self.archive)
            else:
                payload["error"] = self.error or "the tool failed without a message"
                payload["archived"] = dict(self.archive)
            text = json.dumps(payload, ensure_ascii=False, default=str)
            if len(text) <= limit:
                return text
        payload = {"ok": bool(self.ok) and self.executed}
        if not self.executed:
            payload["executed"] = False
            payload["error"] = self.error or "not executed"
        elif self.ok:
            payload["result"] = self.result
        else:
            payload["error"] = self.error or "the tool failed without a message"
            payload["result"] = self.result
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 40)] + ' … [truncated; sha256 of the full result below]'

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "call_id": self.call_id,
            "arguments": dict(self.arguments),
            "ok": self.ok,
            "executed": self.executed,
            "error": self.error,
            "decision": self.decision,
            "policy_reasons": list(self.policy_reasons),
            "duration_ms": self.duration_ms,
            "result_sha256": self.digest,
            "archived": dict(self.archive) if self.archive else None,
        }


def _digest(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _bounded_json(value: Any, limit: int) -> tuple[str, bool]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= limit:
        return text, False
    return text[:limit], True


# --------------------------------------------------------------------------- #
# transcript                                                                   #
# --------------------------------------------------------------------------- #


def agent_state_dir(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser()


def runs_dir(state_dir: str | Path) -> Path:
    return agent_state_dir(state_dir) / "runs"


def run_path(state_dir: str | Path, run_id: str) -> Path:
    """``<state>/runs/<run-id>.jsonl`` — the run id is a file name, so it is checked."""
    text = str(run_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", text) or ".." in text:
        raise AgentLoopError(f"not a usable run id: {run_id!r}")
    return runs_dir(state_dir) / f"{text}.jsonl"


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{uuid.uuid4().hex[:6]}"


class Transcript:
    """Append-only JSONL record of one run — one line per fact, written as it happens.

    Thread-safe by a lock: parallel tool calls finish in arbitrary order and each
    writes its own ``tool.call`` line. A run that was killed mid-flight still has
    everything up to that point (that is the point of writing as we go).
    """

    def __init__(self, path: str | Path, *, truncate: bool = True) -> None:
        """Open the run's file. ``truncate=True`` (default): a run id names ONE run.

        Appending into an existing file would mix two runs under one id and make the
        read-back lie, so a re-used id starts a fresh file — and :meth:`AgentLoop.run`
        records the replaced file's size and digest in the ``run.started`` event, so
        nothing disappears without a trace.

        The line count is kept **in memory** from here on. The first version
        re-read and re-counted the whole file on every ``event()`` to derive
        ``seq``, which is O(N²) I/O over a run (measured: a 100-turn run spends
        seconds re-reading its own transcript). Counted once at open, then
        incremented — the sequence is byte-identical.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if truncate:
            with self.path.open("w", encoding="utf-8"):
                pass
            self._count = 0
        else:
            self._count = self._count_existing()
        self._lock = threading.Lock()
        self._closed = False

    def _count_existing(self) -> int:
        """Line count of an existing file, read exactly once at open."""
        try:
            with self.path.open("rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0

    def event(self, kind: str, **fields: Any) -> dict[str, Any]:
        record = {"seq": 0, "kind": str(kind), "at": datetime.now(UTC).isoformat()}
        record.update(fields)
        with self._lock:
            if self._closed:
                return record
            if _LEGACY_TRANSCRIPT_COUNT:  # the measured pre-fix path (see the constant)
                self._count = self._count_existing()
            self._count += 1
            record["seq"] = self._count
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record

    def close(self) -> None:
        with self._lock:
            self._closed = True


def read_transcript(path: str | Path) -> dict[str, Any]:
    """The whole run as data: header, requests, answers, tool calls, verdict."""
    file = Path(path)
    if not file.is_file():
        raise AgentLoopError(f"no transcript at {file}")
    events: list[dict[str, Any]] = []
    with file.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                events.append({"kind": "unreadable", "raw": line[:200]})
    header = next((event for event in events if event.get("kind") == "run.started"), {})
    finished = next((event for event in events if event.get("kind") == "run.finished"), {})
    requests = [event for event in events if event.get("kind") == "model.request"]
    answers = [event for event in events if event.get("kind") == "model.response"]
    calls = [event for event in events if event.get("kind") == "tool.call"]
    return {
        "run_id": header.get("run_id", file.stem),
        "path": str(file),
        "task": header.get("task", ""),
        "started_at": header.get("at", ""),
        "status": finished.get("status", ""),
        "reason": finished.get("reason", ""),
        "answer": finished.get("answer"),
        "steps": finished.get("steps", len(requests)),
        "tool_calls": calls,
        "tool_call_count": len(calls),
        "requests": requests,
        "answers": answers,
        "events": events,
        "usage": finished.get("usage", {}),
        "usage_estimated": bool(finished.get("usage_estimated", False)),
        "cost_usd": finished.get("cost_usd", 0.0),
        "duration_ms": finished.get("duration_ms", 0),
        "limits": header.get("limits", {}),
        "tools": header.get("tools", []),
        "workspace": header.get("workspace", ""),
        "previous_transcript": header.get("previous_transcript", {}),
    }


def transcript_history(path: str | Path) -> list[dict[str, Any]]:
    """The conversation a previous run left behind, ready to be carried into the next one.

    The loop records every request body it sent, so the **newest** ``model.request`` is the
    exact history that went out. The system prompt is dropped (the next run writes its own)
    and the run's own final answer is appended, because an answer never appears inside a
    request body — it is the reply to one. A transcript with no recorded body (an older
    format, or a run that died before its first request) returns ``[]``, and the caller then
    runs exactly as it did before there was a conversation to continue.
    """
    summary = read_transcript(path)
    body: Mapping[str, Any] = {}
    for event in reversed(list(summary.get("requests") or [])):
        candidate = event.get("body") if isinstance(event, Mapping) else None
        if isinstance(candidate, Mapping) and isinstance(candidate.get("messages"), list):
            body = candidate
            break
    messages = [
        dict(item)
        for item in (body.get("messages") or [])
        if isinstance(item, Mapping) and str(item.get("role") or "") != "system"
    ]
    answer = str(summary.get("answer") or "").strip()
    if answer and (not messages or str(messages[-1].get("role") or "") != "assistant"):
        messages.append({"role": "assistant", "content": answer})
    return messages


def list_runs(state_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first run summaries (a run whose header is missing is reported as such)."""
    directory = runs_dir(state_dir)
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for file in files[: max(1, int(limit))]:
        try:
            summary = read_transcript(file)
        except (AgentLoopError, OSError):
            continue
        out.append(
            {
                "run_id": summary["run_id"],
                "status": summary["status"],
                "reason": summary["reason"],
                "task": summary["task"],
                "steps": summary["steps"],
                "tool_calls": summary["tool_call_count"],
                "started_at": summary["started_at"],
                "duration_ms": summary["duration_ms"],
                "path": summary["path"],
            }
        )
    return out


# --------------------------------------------------------------------------- #
# limits + result                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class LoopLimits:
    """Every bound a run can hit. ``0``/``None`` means "not bounded by this".

    ``cost_budget_usd`` is only enforceable together with a declared
    ``price_per_1k_tokens`` — the loop never invents a price, so an unpriced
    budget is a usage error (checked in :meth:`AgentLoop.run`).
    """

    max_steps: int = DEFAULT_MAX_STEPS
    token_budget: int = 0
    cost_budget_usd: float = 0.0
    price_per_1k_tokens: float = 0.0
    wall_clock_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": int(self.max_steps),
            "token_budget": int(self.token_budget),
            "cost_budget_usd": float(self.cost_budget_usd),
            "price_per_1k_tokens": float(self.price_per_1k_tokens),
            "wall_clock_s": float(self.wall_clock_s),
        }


def _takes_keyword(target: Any, method: str, keyword: str) -> bool:
    """Whether ``target.method`` accepts ``keyword`` (by name, or through ``**kwargs``).

    Used for *optional* protocol extensions — the loop tells a client to stream only when
    that client's ``chat`` can hear it. An unreadable signature answers ``False``: the
    caller then keeps the behaviour it always had instead of risking a ``TypeError`` on
    somebody else's client.
    """
    callable_method = getattr(target, method, None)
    if not callable(callable_method):
        return False
    try:
        parameters = inspect.signature(callable_method).parameters
    except (TypeError, ValueError):
        return False
    if keyword in parameters:
        return True
    return any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())

@dataclass
class AgentRunResult:
    """One run's honest verdict: an answer, or the bound that stopped it."""

    run_id: str
    status: str = STATUS_FAILED
    reason: str = ""
    answer: str | None = None
    last_text: str = ""
    message: str = ""
    steps: int = 0
    tool_calls: int = 0
    executed_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    usage_estimated: bool = False
    cost_usd: float = 0.0
    duration_ms: int = 0
    transcript_path: str = ""
    tool_names: list[str] = field(default_factory=list)
    #: the tools that really executed, in order (the progress guard reads these to tell
    #: a *search* that finds nothing new from an answer that merely repeats)
    tools_used: list[str] = field(default_factory=list)
    #: What the *streaming* path measured, when one was used (``on_delta`` attached and the
    #: client streamed). Empty when nothing streamed — and then ``streamed: False`` with the
    #: reason is reported instead of a zero, so a caller can tell "no tokens yet" from "this
    #: provider/this flag did not stream at all" (``stream_render``/``llm_provider`` fill it).
    streaming: dict[str, Any] = field(default_factory=dict)
    #: PROGRESS-GUARD — this run's commit point (``visible_output`` / ``tool_executed``).
    #: A caller that owns whole-turn retries reads it: past the commit point a retry is
    #: refused and turned into an explicit re-plan, so a tool can never run twice.
    commit: dict[str, Any] = field(default_factory=dict)
    #: every retry decision taken or refused, in order (named, with its reason)
    retry: list[dict[str, Any]] = field(default_factory=list)
    #: TOKEN-ECONOMY — what the tool-result archive saved this run (or an empty dict)
    archive: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_ANSWERED

    @property
    def exit_code(self) -> int:
        if self.status == STATUS_ANSWERED:
            return EXIT_OK
        if self.reason == REASON_NEEDS_CREDENTIALS:
            return EXIT_NEEDS_CREDENTIALS
        if self.reason == REASON_INTERNAL_ERROR:
            return EXIT_INTERNAL
        return EXIT_BLOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "reason": self.reason,
            "answer": self.answer,
            "last_text": self.last_text,
            "message": self.message,
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "executed_calls": self.executed_calls,
            "usage": dict(self.usage),
            "usage_estimated": self.usage_estimated,
            "cost_usd": round(float(self.cost_usd), 6),
            "duration_ms": self.duration_ms,
            "transcript_path": self.transcript_path,
            "tools": list(self.tool_names),
            "tools_used": list(self.tools_used),
            "ok": self.ok,
            "exit_code": self.exit_code,
            "streaming": dict(self.streaming),
            "commit": dict(self.commit),
            "retry": list(self.retry),
            "archive": dict(self.archive),
        }


# --------------------------------------------------------------------------- #
# the loop                                                                     #
# --------------------------------------------------------------------------- #


class AgentLoop:
    """One task, one model, one toolset, one transcript.

    The model is any object with ``chat(messages, *, tier, tools, tool_choice)``
    returning a :class:`skaldir.llm_provider.ChatResult` — in practice
    :class:`skaldir.llm_provider.OpenAICompatibleLLM`. A client that can only
    answer text (``complete()``) is refused loudly: without ``tool_calls`` there
    is no loop, and pretending otherwise is exactly the gap this module closes.
    """

    def __init__(
        self,
        client: Any,
        tools: ToolSet | Sequence[AgentTool],
        *,
        state_dir: str | Path,
        workspace: str | Path = ".",
        limits: LoopLimits | None = None,
        goal: str = "",
        system_prompt: str = SYSTEM_PROMPT,
        tool_choice: Any = "auto",
        tier: str = "main",
        policy: bool = True,
        approvals: Iterable[str] = (),
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
        max_workers: int = DEFAULT_MAX_WORKERS,
        cancel_path: str | Path | None = None,
        cancel_event: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
        runs_root: str | Path | None = None,
        progress: Callable[[str, dict[str, Any]], Any] | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_request: Callable[[], None] | None = None,
        on_answer: Callable[[], None] | None = None,
        stream: bool | None = None,
        decisions: Any = None,
        history: Sequence[Mapping[str, Any]] = (),
        archive: ToolArchive | None = None,
        retry_gate: RetryGate | None = None,
        approval_mode: Any = None,
        approval_gate: Any = None,
    ) -> None:
        self.client = client
        self.tools = tools if isinstance(tools, ToolSet) else ToolSet.of(list(tools))
        self.state_dir = agent_state_dir(state_dir)
        self.runs_root = Path(runs_root) if runs_root is not None else runs_dir(self.state_dir)
        self.workspace = Path(workspace).expanduser().resolve()
        self.limits = limits or LoopLimits()
        self.goal = str(goal or "")
        self.system_prompt = str(system_prompt or SYSTEM_PROMPT)
        self.tool_choice = tool_choice
        self.tier = str(tier or "main")
        self.policy = bool(policy)
        self.approvals = frozenset(str(item) for item in approvals)
        # APR-01/02/04 — how often a human is asked. Built here so every loop has one;
        # callers may pass a prebuilt gate (e.g. one sharing a mode store across runs).
        self.approval_gate = approval_gate
        if self.approval_gate is None and approval_mode is not None:
            from .approval_gate import ApprovalGate as _ApprovalGate

            self.approval_gate = _ApprovalGate(approval_mode)
        self.max_result_chars = int(max_result_chars)
        self.max_workers = max(1, int(max_workers))
        self.cancel_path = Path(cancel_path).expanduser() if cancel_path else None
        self.cancel_event = cancel_event
        self._clock = clock
        #: Optional live-progress sink, called as ``progress(kind, fields)`` with one
        #: mapping per event (``skaldir.progress.RunProgress.event`` is the product's
        #: implementation). Purely observational: it is never awaited, never inspected and
        #: never allowed to change the run's outcome — a renderer that raises is swallowed
        #: by ``_note``. Kinds: ``model.request`` · ``model.response`` · ``tool.start`` ·
        #: ``tool.end`` · ``model.tokens``.
        self.progress = progress if callable(progress) else None
        #: The **content** sink: every streamed delta the provider hands us goes straight to
        #: this callable, unbuffered and unthrottled by the loop — the renderer owns the pace
        #: (``skaldir.stream_render.StreamRenderer.delta``). This is what makes an answer
        #: appear while the model is still writing it; without it the first content a user
        #: sees is the *completed* answer (1.3-3.7 s against a stub, measured in
        #: ``MVP/58-streaming.md``). ``None`` = today's behaviour, exactly.
        self.on_delta = on_delta if callable(on_delta) else None
        #: Called immediately **before** each model request goes out — the clock a streaming
        #: renderer arms itself with, so "first token at 0.31 s" is measured from the
        #: request, not from process start. Observational, like the two sinks above.
        self.on_request = on_request if callable(on_request) else None
        #: Called **immediately after** the client returns and **before** any progress line
        #: is written for that answer: the renderer flushes its tail here, so no other
        #: writer can land inside a half-written line of the answer (a defect this hook was
        #: added to fix, measured on a pty — MVP/58-streaming.md §6). Observational.
        self.on_answer = on_answer if callable(on_answer) else None
        #: Whether to *ask* the client for a streamed answer (``chat(..., stream=...)``).
        #: ``None`` = whatever the provider config says; ``True`` = the caller has a content
        #: sink and wants tokens as they arrive. Never silently downgraded: the run result
        #: reports whether the client really streamed.
        self.stream = stream
        #: measured per step, merged into the run result (see ``AgentRunResult.streaming``)
        self._stream_steps: list[dict[str, Any]] = []
        #: resolved lazily: does this client's ``chat`` accept ``stream=``?
        self._accepts_stream: bool | None = None
        #: sink failures this loop swallowed for the step in flight (merged with the
        #: client's own count in ``_harvest_stream``)
        self._delta_sink_errors = 0
        #: MVP/87 — the Jev decision layer subscribed to this run's **turn boundaries**.
        #: A :class:`skaldir.jev.wiring.TurnDecisions` (or any object with the same two
        #: methods) or ``None`` for exactly the old behaviour. The loop *publishes* the
        #: boundary and the offered tool surface; the layer asks Jev and writes the
        #: ``jev_decision`` rows into the runtime's event store. The advisory is recorded,
        #: never obeyed: no field of it reaches the tool dispatch below.
        self.decisions = decisions
        #: The **conversation** this run continues: the messages a previous turn left behind
        #: (``transcript_history`` reads them out of a real transcript). They are placed
        #: between the system prompt and this turn's user message, so a session accumulates
        #: context instead of every line being an independent ``agent run``. ``()`` is
        #: exactly the old behaviour: a run with one user turn and nothing before it.
        self.history: list[dict[str, Any]] = [
            dict(item) for item in history if isinstance(item, Mapping)
        ]
        #: TOKEN-ECONOMY — the tool-result archive of this run (``skaldir.tool_archive``).
        #: When set, an oversized tool result is written to a content-addressed artifact
        #: and the hot context carries its id, digest and a short summary instead of the
        #: bytes. ``None`` = exactly the old behaviour (inline, truncated at
        #: ``max_result_chars``).
        self.archive = archive
        #: the read half of the same lever: offered only when there is an archive to
        #: read, so a run without one keeps its tool surface byte-identical
        self._archive_reader_offered = False
        if self.archive is not None:
            self._offer_archive_reader()
        #: PROGRESS-GUARD — the commit point a provider retry must stay behind. When set,
        #: it is installed on the client (``client.retry_gate``) so a client that honours
        #: the hook refuses to re-send a request once anything visible has been committed.
        self.retry_gate = retry_gate
        #: every retry decision this run took or refused, in order — the record names them
        self.retry_decisions: list[dict[str, Any]] = []
        #: chars of this run that reached a user-visible sink (the commit point, run-level)
        self._run_visible_chars = 0
        #: TRUST-WIRING (audit 46 #5) — the injection defence of ``toolcall`` becomes live.
        #: ``_feedback_untrusted()`` runs the *existing* product scan
        #: (:func:`skaldir.untrusted.scan_untrusted`, the same detector
        #: ``skaldir.monitor`` ingests) over the external text the trajectory
        #: actually carried: every ``role: tool`` result and, from ``_note_untrusted``,
        #: the newest model **question/ask** text (ask == potentially a foreign pull).
        #: A confirmed attempt contributes (a) the bounded text itself
        #: (``recent_text``) and (b) the ignored directive line
        #: (``_untrusted_directives``) to the next ToolIntent, so a planned call
        #: that *reproduces* the foreign instruction is refused by
        #: ``toolcall._trust_findings`` before it runs. The model's own answer
        #: text is trusted (``TRUSTED_SOURCES`` owns it) — it is never scanned,
        #: so today's ALLOW/ALLOW paths stay byte-identical when nothing is
        #: flagged. Empty state = exactly the old behaviour.
        self._recent_untrusted_text = ""
        self._untrusted_directives: list[str] = []
        self._install_retry_gate()

    def commit_point(self, *, tool_executed: bool = False) -> dict[str, Any]:
        """This run's commit point — what a caller may retry, and what it may not.

        Read by the layer that owns whole-turn retries (``skaldir.goal_run``): once
        ``committed`` is true, re-running the turn would either re-emit visible output
        or run a tool a **second** time, so a retry is refused and turned into an
        explicit re-plan instead.
        """
        visible = self._run_visible_chars > 0
        commit = CommitPoint(
            turn=0,
            attempt=0,
            visible_output=visible,
            visible_chars=self._run_visible_chars,
            tool_executed=bool(tool_executed),
        )
        return commit.to_wire()

    # -- the retry gate ----------------------------------------------------- #

    def _offer_archive_reader(self) -> None:
        """Offer the model the read half of the tool-result archive (TOKEN-ECONOMY).

        The hot form of an archived result carries ``tool_archive:<sha256>``, a digest
        and a bounded head — and nothing else. Without a tool that resolves that ref the
        model was told a digest existed and could **not** ask for the bytes, so the
        archive turned a large result into an unreachable one: a context loss dressed up
        as a token saving. This adds exactly one read-only tool while an archive is
        attached (``ToolArchive.retrieve`` re-checks the digest on read, so a corrupted
        artifact is reported, never returned as evidence).

        It is deliberately not a write or exec tool: reading back what the runtime
        already stored cannot widen any permission.
        """
        from .tool_archive import (
            RETRIEVE_MAX_CHARS,
            RETRIEVE_TOOL_NAME,
        )

        if self._archive_reader_offered or self.tools.get(RETRIEVE_TOOL_NAME) is not None:
            self._archive_reader_offered = True
            return
        archive = self.archive
        if archive is None:  # pragma: no cover - only called with an archive attached
            return

        def read_archive(ref: str, max_chars: int = RETRIEVE_MAX_CHARS) -> dict[str, Any]:
            try:
                blob = archive.retrieve(ref)
            except Exception as exc:  # noqa: BLE001 — an unresolvable ref is a result
                return {"error": f"{type(exc).__name__}: {exc}", "ref": str(ref or "")}
            text = blob.decode("utf-8", errors="replace")
            limit = max(1, int(max_chars))
            return {
                "ref": f"tool_archive:{archive.path_for(ref).stem}",
                "bytes": len(blob),
                "chars": len(text),
                "returned_chars": min(len(text), limit),
                "truncated": len(text) > limit,
                "text": text[:limit],
            }

        tool = AgentTool(
            name=RETRIEVE_TOOL_NAME,
            description=(
                "Read back an archived tool result byte-for-byte by its "
                "tool_archive:<sha256> ref. Use it when a tool result was archived "
                "instead of shown inline and you need the payload itself."
            ),
            handler=read_archive,
            input_schema={
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "the tool_archive:<sha256> ref from an archived result",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "how much of the payload to return (bounded)",
                    },
                },
                "required": ["ref"],
            },
            parallel_safe=True,
            mutating=False,
            source="tool_archive",
        )
        self.tools = ToolSet.of([*self.tools.tools, tool])
        self._archive_reader_offered = True

    def _install_retry_gate(self) -> None:
        """Hand the gate to the client, if the client can hear it.

        The gate is *consulted* by the provider's own attempt loop (``retry_gate``
        hook, same shape as ``on_delta``): a client without the attribute is
        untouched and keeps its behaviour exactly. Nothing here mutates settings.
        """
        if self.retry_gate is None:
            return
        try:
            self.client.retry_gate = self.retry_gate
        except Exception:  # noqa: BLE001 — a client without a settable hook is untouched
            return

    def _detach_retry_gate(self) -> None:
        if self.retry_gate is None:
            return
        try:
            if getattr(self.client, "retry_gate", None) is self.retry_gate:
                self.client.retry_gate = None
        except Exception:  # noqa: BLE001
            return

    # -- public entry points ------------------------------------------------ #

    def run(self, task: str, *, run_id: str = "") -> AgentRunResult:
        """Run one task to an answer, or to the bound that stopped it."""
        self._validate(task)
        run_id = str(run_id or "").strip() or new_run_id()
        path = run_path(self.state_dir, run_id)
        if self.runs_root != runs_dir(self.state_dir):
            path = self.runs_root / f"{run_id}.jsonl"
        previous: dict[str, Any] = {}
        if path.exists():
            try:
                blob = path.read_bytes()
            except OSError:
                blob = b""
            previous = {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        transcript = Transcript(path)
        started = self._clock()
        self._started_at = started  # the wall-clock bound is measured from here
        definitions, choice = self.tools.definitions(tool_choice=self.tool_choice)
        result = AgentRunResult(
            run_id=run_id,
            transcript_path=str(path),
            tool_names=self.tools.names(),
        )
        transcript.event(
            "run.started",
            schema_version=TRANSCRIPT_SCHEMA_VERSION,
            run_id=run_id,
            previous_transcript=previous,
            history_messages=len(self.history),
            task=str(task),
            goal=self.goal,
            workspace=str(self.workspace),
            limits=self.limits.to_dict(),
            tool_choice=choice,
            tools=definitions,
            provider=self._provider_note(),
            policy=self.policy,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *[dict(item) for item in self.history],
            {"role": "user", "content": str(task)},
        ]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        estimated = False
        steps = 0
        tool_calls = 0
        executed = 0
        if self.decisions is not None:
            # the layer files its rows under this run — before the first turn boundary
            self.decisions.start_run(run_id=run_id, goal=self.goal or str(task), task=str(task))
        status, reason, answer, last_text, message = STATUS_FAILED, "", None, "", ""
        try:
            while True:
                bound = self._bound_reason(steps=steps, usage=usage, cost=result.cost_usd)
                if bound:
                    status, reason, message = STATUS_INCOMPLETE, bound, self._bound_message(bound)
                    transcript.event("step.bound", step=steps, reason=bound, usage=dict(usage))
                    break
                steps += 1
                if self.decisions is not None:
                    # the turn boundary: the offered tool surface goes to Jev, the advisory
                    # (or its named blocker) is recorded in the runtime's event store
                    self.decisions.before_turn(
                        step=steps,
                        tool_names=self.tools.names(),
                        intent=str(task),
                    )
                request_body = self._request_body(messages, definitions, choice)
                transcript.event(
                    "model.request",
                    step=steps,
                    body=request_body,
                    body_source=self._body_source(),
                    tool_names=[
                        str((item.get("function") or {}).get("name", ""))
                        for item in definitions
                    ],
                    messages=len(messages),
                )
                self._note(
                    "model.request",
                    step=steps,
                    messages=len(messages),
                    tool_names=[
                        str((item.get("function") or {}).get("name", ""))
                        for item in definitions
                    ],
                )
                call_started = self._clock()
                if self.retry_gate is not None:
                    # a fresh commit point per attempt: "nothing visible yet" is a
                    # property of *this* turn, never inherited from the last one
                    self.retry_gate.begin(turn=steps)
                self._note_request()
                self._attach_delta_sink(steps)
                try:
                    answer_or_calls = self._chat(messages, definitions, choice)
                except (ProviderConfigError, ProviderAuthError) as exc:
                    self._detach_delta_sink()
                    status, reason = STATUS_FAILED, REASON_NEEDS_CREDENTIALS
                    message = str(exc)
                    transcript.event("run.error", step=steps, reason=reason, error=message)
                    break
                except LLMError as exc:
                    self._detach_delta_sink()
                    message = f"{type(exc).__name__}: {exc}"
                    decision = self._record_retry_decision(error=message)
                    if decision is not None and decision.action == ACTION_REPLAN:
                        # past the visible commit point: a blind retry would duplicate
                        # what the user already saw — so the failure becomes an explicit
                        # re-plan instead, and the case is named on the record.
                        transcript.event("retry_refused", step=steps, **decision.to_dict())
                        messages.append({"role": "user", "content": decision.replan})
                        self._note("retry.replan", step=steps, reason=decision.reason)
                        continue
                    status, reason = STATUS_FAILED, REASON_PROVIDER_ERROR
                    if decision is not None:
                        transcript.event("retry_gate", step=steps, **decision.to_dict())
                    transcript.event("run.error", step=steps, reason=reason, error=message)
                    break
                elapsed_ms = int((self._clock() - call_started) * 1000)
                stream_report = self._harvest_stream(steps, duration_ms=elapsed_ms)
                # the answer's tail reaches the terminal before anything else is printed
                self._note_answer()
                step_usage, step_estimated = self._usage_for(
                    getattr(answer_or_calls, "usage", {}) or {}, messages, answer_or_calls
                )
                estimated = estimated or step_estimated
                for key in usage:
                    usage[key] += int(step_usage.get(key, 0))
                # A counter the provider reports and the loop did not track yet (the
                # reasoning tokens of §94) is adopted the first time it appears, so the
                # run total carries it — and a backend that reports none never gains a
                # zero that would read as "this one reasons for free".
                for key, value in step_usage.items():
                    if key not in usage:
                        usage[key] = int(value)
                transcript.event(
                    "model.response",
                    step=steps,
                    duration_ms=elapsed_ms,
                    streaming=stream_report,
                    finish_reason=str(getattr(answer_or_calls, "finish_reason", "")),
                    text=str(getattr(answer_or_calls, "text", "")),
                    tool_calls=[
                        call.to_dict()
                        for call in (getattr(answer_or_calls, "tool_calls", []) or [])
                    ],
                    usage=dict(step_usage),
                    usage_estimated=step_estimated,
                )
                result.cost_usd = self._cost(usage)
                result.usage = dict(usage)
                result.usage_estimated = estimated
                result.steps = steps
                self._note(
                    "model.response",
                    step=steps,
                    duration_ms=elapsed_ms,
                    streaming=dict(stream_report),
                    tool_calls=len(getattr(answer_or_calls, "tool_calls", []) or []),
                    text_chars=len(str(getattr(answer_or_calls, "text", "") or "")),
                )
                # every answer's text is kept as a diagnostic — a run that stops at a
                # bound still shows what the model last said, without calling it the answer
                last_text = str(getattr(answer_or_calls, "text", "") or "") or last_text
                pending = list(getattr(answer_or_calls, "tool_calls", []) or [])
                if not pending:
                    text = str(getattr(answer_or_calls, "text", "") or "")
                    if text.strip():
                        status, reason, answer = STATUS_ANSWERED, "", text
                    else:
                        status, reason = STATUS_INCOMPLETE, REASON_EMPTY_ANSWER
                        message = (
                            "the model answered with neither text nor a tool call — there is "
                            "no result to report"
                        )
                        transcript.event("step.bound", step=steps, reason=reason)
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": str(getattr(answer_or_calls, "text", "") or ""),
                        "tool_calls": [call.to_wire() for call in pending],
                    }
                )
                outcomes = self._execute(pending, transcript=transcript, step=steps)
                self._feedback_untrusted(outcomes)
                if self.retry_gate is not None:
                    # PROGRESS-GUARD: this attempt is now past the *other* half of the
                    # commit point — tools really ran. The gate records it so a retry of
                    # this attempt can never be called "safe": re-running it would run
                    # the same tool a second time. (The visible half is marked by the
                    # delta sink; together they are what `result.commit` reports.)
                    ran = sum(1 for outcome in outcomes if outcome.executed)
                    if ran:
                        self.retry_gate.note_tool_executed(ran)
                if self.decisions is not None:
                    # what the run really routed to, tied to this turn's advisory
                    self.decisions.after_turn(
                        step=steps,
                        tool_names=[str(outcome.name) for outcome in outcomes],
                    )
                tool_calls += len(outcomes)
                executed += sum(1 for outcome in outcomes if outcome.executed)
                for outcome in outcomes:
                    if outcome.executed:
                        result.tools_used.append(str(outcome.name))
                for call, outcome in zip(pending, outcomes, strict=False):
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": outcome.name,
                            "content": outcome.content(limit=self.max_result_chars),
                        }
                    )
                result.tool_calls = tool_calls
                result.executed_calls = executed
        except Exception as exc:  # noqa: BLE001 - a loop bug is reported, never a "done"
            status, reason = STATUS_FAILED, REASON_INTERNAL_ERROR
            message = f"{type(exc).__name__}: {exc}"
            transcript.event("run.error", reason=reason, error=message)
        finally:
            result.status = status
            result.reason = reason
            result.answer = answer
            result.last_text = last_text
            result.message = message
            result.steps = steps
            result.tool_calls = tool_calls
            result.executed_calls = executed
            result.usage = dict(usage)
            result.usage_estimated = estimated
            result.cost_usd = self._cost(usage)
            result.streaming = self.streaming_report()
            result.commit = self.commit_point(tool_executed=executed > 0)
            result.retry = list(self.retry_decisions)
            result.archive = self.archive.stats() if self.archive is not None else {}
            result.duration_ms = int((self._clock() - started) * 1000)
            self._detach_retry_gate()
            transcript.event(
                "run.finished",
                run_id=run_id,
                status=status,
                reason=reason,
                answer=answer,
                last_text=last_text,
                message=message,
                steps=steps,
                tool_calls=tool_calls,
                executed_calls=executed,
                usage=dict(usage),
                usage_estimated=estimated,
                cost_usd=round(float(result.cost_usd), 6),
                duration_ms=result.duration_ms,
            )
            transcript.close()
        return result

    # -- live progress (observational only) --------------------------------- #

    def _record_retry_decision(self, *, error: str, attempt: int | None = None) -> Any:
        """Read the gate's verdict for this failed model call, and put it on the record.

        Returns the :class:`skaldir.progress_guard.RetryDecision` (or ``None`` when no
        gate is installed, i.e. exactly the old behaviour). The decision is appended to
        ``retry_decisions`` so the run can report **what it refused and why** — the
        difference between a named refusal and a silent duplicate.

        **The provider may already have asked.** When a client honours the gate
        (``skaldir.llm_provider.OpenAICompatibleLLM.chat`` consults it before re-sending),
        the refusal — and the re-plan text — belong to *that* decision. Asking the same
        gate a second time from here used to flip a recorded refusal into "a retry is
        allowed", count a retry that never happened, and hand the run a report that
        disagreed with what it did. One failure, one decision.
        """
        if self.retry_gate is None:
            return None
        prior = self.retry_gate.last()
        if prior is not None and not prior.retry:
            self.retry_decisions.append(prior.to_dict())
            return prior
        decision = self.retry_gate.decide_caller_retry(
            error=str(error or ""), provider_attempts=int(attempt or 0)
        )
        self.retry_decisions.append(decision.to_dict())
        return decision

    # -- the untrusted-content scan (TRUST-WIRING, audit 46 #5) ------------- #

    #: each remembered directive keeps its factual line, bounded like the plugin
    #: trader does; twelve is the same small window.
    _UNTRUSTED_TEXT_CHARS = 2000
    _UNTRUSTED_DIRECTIVES_MAX = 12

    def _feedback_untrusted(self, outcomes: Sequence[ToolOutcome]) -> None:
        """Scan tool results for embedded instructions; remember confirmed ones.

        The detector is the product's own (:func:`skaldir.untrusted.scan_untrusted`,
        the same one ``skaldir.monitor`` ingests) — no new pattern is invented
        here. A *confirmed attempt* (addressed + actionable, never a bare signal)
        contributes ``recent_text`` (bounded) and the ignored directive line to
        the tool intents that follow, so a call that acts on the ignored
        instruction is refused before execution. The scan is advisory to the
        loop: a broken detector must never take the run down.
        """
        try:
            from .untrusted import scan_untrusted
        except Exception:  # pragma: no cover - absence degrades to old behaviour
            return
        for outcome in outcomes:
            text = outcome.content(limit=self._UNTRUSTED_TEXT_CHARS)
            if not text:
                continue
            try:
                assessment = scan_untrusted(text, source="tool_output")
            except Exception:
                continue
            if not assessment.attempted:
                continue
            self._recent_untrusted_text = text[: self._UNTRUSTED_TEXT_CHARS]
            finding = assessment.findings[0] if assessment.findings else None
            line = str(getattr(finding, "line", "") or "")
            if line and line not in self._untrusted_directives:
                self._untrusted_directives.append(line[:300])
                del self._untrusted_directives[: -self._UNTRUSTED_DIRECTIVES_MAX]

    def _archive_source(self, call: Any, tool: AgentTool | None) -> str:
        """Which bulk source a result came from — named honestly, never guessed.

        The tool's own declared ``source`` wins; otherwise the tool name is used as
        given. :func:`skaldir.tool_archive.summarise_payload` maps the known bulk
        sources (browser dumps, container logs, test output, computer-use traces) to
        a readable phrase and leaves an unknown name alone.
        """
        for candidate in (getattr(tool, "source", ""), getattr(call, "source", "")):
            if str(candidate or "").strip():
                return str(candidate).strip()
        return str(getattr(call, "name", "") or "")

    def _note(self, kind: str, **fields: Any) -> None:
        """Tell the progress sink what is really happening. Never affects the run."""
        if self.progress is None:
            return
        try:
            self.progress(kind, fields)
        except Exception:  # noqa: BLE001 — a broken renderer must not break a run
            return

    # -- the content stream (token by token) --------------------------------- #

    def _chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        definitions: Sequence[Mapping[str, Any]],
        choice: Any,
    ) -> ChatResult:
        """One model call, with ``stream=`` handed only to a client that can take it.

        The loop's own policy is one word: *ask for a stream when a content sink is
        attached and the caller said so*. A client whose ``chat`` has no ``stream``
        parameter (a scripted stand-in, a third-party implementation) is called exactly as
        before — the answer is then reported as not streamed with that reason, rather than
        pretending. Never a ``TypeError`` from a keyword somebody else's client does not
        know.
        """
        if self.stream is not None and self._chat_accepts_stream():
            return self.client.chat(
                messages, tier=self.tier, tools=definitions or None, tool_choice=choice,
                stream=bool(self.stream),
            )
        return self.client.chat(
            messages, tier=self.tier, tools=definitions or None, tool_choice=choice
        )

    def _chat_accepts_stream(self) -> bool:
        """Whether this client's ``chat`` can be told to stream (resolved once, cached)."""
        if self._accepts_stream is None:
            self._accepts_stream = _takes_keyword(self.client, "chat", "stream")
        return bool(self._accepts_stream)

    def _note_request(self) -> None:
        """Tell the caller a request is being sent (never allowed to break the call)."""
        if self.on_request is None:
            return
        try:
            self.on_request()
        except Exception:  # noqa: BLE001 — a clock that raises is not a run failure
            return

    def _note_answer(self) -> None:
        """Tell the caller the answer is complete (never allowed to break the run)."""
        if self.on_answer is None:
            return
        try:
            self.on_answer()
        except Exception:  # noqa: BLE001 — a renderer must never change a run
            return

    def _attach_delta_sink(self, step: int) -> None:
        """Point the client's content sink at this run's renderer, for this step only.

        ``on_delta`` is called by the provider client for **every** content delta as it
        comes off the wire, so the text reaches the terminal while the model is still
        generating. A client that cannot stream (a scripted stand-in, another
        implementation) simply never calls it — that is reported, not papered over
        (``streaming.streamed: false`` with the reason).
        """
        if self.on_delta is None:
            self._detach_delta_sink()
            return
        seen = {"at": 0.0, "deltas": 0, "chars": 0, "reported": False}
        self._delta_sink_errors = 0

        def sink(text: str) -> None:
            """Forward the delta *first* (the user's text is never delayed by counting).

            A renderer that raises must never change a run — and this wrapper does not
            assume the client protects us (a third-party client need not), so it catches
            its own sink's failure, counts it, and keeps the stream flowing.
            """
            try:
                self.on_delta(text)  # type: ignore[misc]
            except Exception:  # noqa: BLE001 — a broken renderer is a fact, not a failure
                self._delta_sink_errors += 1
            seen["deltas"] += 1
            seen["chars"] += len(text)
            # PROGRESS-GUARD: this attempt is now PAST the visible commit point. A
            # provider retry from here would stream the same answer to the user twice,
            # so the gate refuses it (``client.retry_gate``) and the case is named.
            if self.retry_gate is not None:
                self.retry_gate.note_visible_output(len(text))
            self._run_visible_chars += len(text)
            now = self._clock()
            if not seen["reported"] or (now - seen["at"]) * 1000.0 >= _DELTA_NOTE_MS:
                seen["at"], seen["reported"] = now, True
                self._note(
                    "model.tokens",
                    step=step,
                    deltas=seen["deltas"],
                    chars=seen["chars"],
                    tokens_estimate=seen["chars"] // 4 if seen["chars"] >= 4 else None,
                )

        try:
            self.client.on_delta = sink
        except Exception:  # noqa: BLE001 — a client without a settable sink simply cannot
            return

    def _detach_delta_sink(self) -> None:
        """Never leave our sink on a client another caller may reuse."""
        if self.on_delta is None:
            return
        try:
            if getattr(self.client, "on_delta", None) is not None:
                self.client.on_delta = None
        except Exception:  # noqa: BLE001
            return

    def _harvest_stream(self, step: int, *, duration_ms: int) -> dict[str, Any]:
        """Read back what the *client* measured, honestly, and detach the sink.

        The numbers come from the provider client's own ``stream_stats`` (counted where the
        bytes arrive), never from the renderer — a renderer that is switched off must still
        be able to say how fast the stream was.
        """
        stats = dict(getattr(self.client, "stream_stats", None) or {})
        loop_errors = int(self._delta_sink_errors)
        self._detach_delta_sink()
        report: dict[str, Any] = {
            "step": int(step),
            "streaming_requested": bool(stats.get("streaming_requested")),
            "deltas": int(stats.get("deltas") or 0),
            "chars": int(stats.get("chars") or 0),
            "first_content_ms": stats.get("first_content_ms"),
            "last_content_ms": stats.get("last_content_ms"),
            "duration_ms": int(duration_ms),
            "sink_errors": int(stats.get("sink_errors") or 0) + loop_errors,
            "source": "provider",
        }
        chars = int(report["chars"] or 0)
        report["tokens_estimate"] = chars // 4 if chars >= 4 else None
        report["token_method"] = "chars/4"
        report["token_source"] = "estimate"
        # "streamed" answers the question a user would ask — *did tokens reach the screen?*
        # — so it needs three facts: deltas off the wire, a sink to put them on, and a sink
        # that did not fail on every one of them.
        delivered = max(0, int(report["deltas"]) - int(report["sink_errors"]))
        report["deltas_rendered"] = delivered
        report["streamed"] = delivered > 0 and self.on_delta is not None
        if not report["streamed"]:
            if self.on_delta is None:
                report["reason"] = "no content sink was attached"
            elif report["sink_errors"] and not delivered:
                report["reason"] = (
                    f"the content sink raised on every delta ({report['sink_errors']} error(s))"
                )
            elif stats.get("streaming_requested"):
                report["reason"] = "the stream was requested but carried no content delta"
            else:
                report["reason"] = "the provider config did not stream (stream: false)"
        self._stream_steps.append(report)
        return report

    def streaming_report(self) -> dict[str, Any]:
        """The run's streaming facts: one measured record per model call that ran.

        Aggregated, not guessed: ``first_content_ms`` is the **first** call's (the one the
        user felt), ``deltas``/``chars`` are summed, and ``streamed`` is true only when a
        delta really arrived. Every reason a stream did not happen is named.
        """
        steps = list(self._stream_steps)
        if not steps:
            return {}
        firsts = [s["first_content_ms"] for s in steps if s.get("first_content_ms") is not None]
        totals = {
            "calls": len(steps),
            "streamed": any(s["streamed"] for s in steps),
            "deltas": sum(int(s["deltas"]) for s in steps),
            "chars": sum(int(s["chars"]) for s in steps),
            "sink_errors": sum(int(s["sink_errors"]) for s in steps),
            "steps": steps,
        }
        chars = int(totals["chars"])
        totals["deltas_rendered"] = sum(int(s.get("deltas_rendered") or 0) for s in steps)
        totals["tokens_estimate"] = chars // 4 if chars >= 4 else None
        totals["token_method"] = "chars/4"
        totals["token_source"] = "estimate"
        totals["source"] = "provider"
        totals["streaming_requested"] = any(bool(s.get("streaming_requested")) for s in steps)
        totals["first_content_ms"] = firsts[0] if firsts else None
        if not totals["streamed"]:
            reasons = [str(s["reason"]) for s in steps if s.get("reason")]
            totals["reason"] = "; ".join(dict.fromkeys(reasons)) or (
                "no content delta reached a sink"
            )
        return totals

    # -- validation + bounds ------------------------------------------------ #

    def _validate(self, task: str) -> None:
        if not str(task or "").strip():
            raise AgentLoopError("the task is empty — there is nothing to run")
        if not callable(getattr(self.client, "chat", None)):
            raise AgentLoopError(
                "this client cannot call tools: it has no chat() (only complete()) — "
                "tool calling needs an OpenAI-compatible provider "
                "(skaldir.llm_provider.OpenAICompatibleLLM)"
            )
        if int(self.limits.max_steps) < 1:
            raise AgentLoopError(f"max_steps must be >= 1, got {self.limits.max_steps}")
        if self.limits.cost_budget_usd and not self.limits.price_per_1k_tokens:
            raise AgentLoopError(
                "a cost budget needs a declared price: pass price_per_1k_tokens "
                "(the loop never invents a price for a model)"
            )
        if not len(self.tools):
            raise AgentLoopError("the run has no tools — an agent without tools is a chat")

    def cancelled(self) -> bool:
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        return bool(self.cancel_path is not None and self.cancel_path.exists())

    def _bound_reason(
        self, *, steps: int, usage: Mapping[str, int], cost: float
    ) -> str:
        """The first bound that is hit — checked *before* a request and after each answer."""
        if steps >= int(self.limits.max_steps):
            return REASON_MAX_STEPS
        if self.cancelled():
            return REASON_CANCELLED
        if self.limits.token_budget and int(usage.get("total_tokens", 0)) >= int(
            self.limits.token_budget
        ):
            return REASON_TOKEN_BUDGET
        if self.limits.cost_budget_usd and float(cost) >= float(self.limits.cost_budget_usd):
            return REASON_COST_BUDGET
        if self.limits.wall_clock_s and (
            self._clock() - self._started_at >= float(self.limits.wall_clock_s)
        ):
            return REASON_WALL_CLOCK
        return ""

    def _bound_message(self, reason: str) -> str:
        if reason == REASON_MAX_STEPS:
            return f"step limit reached ({self.limits.max_steps} model calls) — no answer"
        if reason == REASON_TOKEN_BUDGET:
            return f"token budget reached ({self.limits.token_budget}) — no answer"
        if reason == REASON_COST_BUDGET:
            return f"cost budget reached ({self.limits.cost_budget_usd} USD) — no answer"
        if reason == REASON_WALL_CLOCK:
            return f"wall clock reached ({self.limits.wall_clock_s}s) — no answer"
        if reason == REASON_CANCELLED:
            return "the run was cancelled — no answer"
        return "the run was stopped by a bound — no answer"

    # -- request/usage/cost ------------------------------------------------- #

    def _provider_note(self) -> dict[str, Any]:
        cfg = getattr(self.client, "config", None)
        if cfg is None:
            return {
                "client": type(self.client).__name__,
                "model": getattr(self.client, "name", ""),
                "endpoint": "",
                "source": "client",
            }
        return {
            "client": type(self.client).__name__,
            "provider": str(getattr(cfg, "provider", "")),
            "model": str(getattr(cfg, "model", "")),
            "base_url": str(getattr(cfg, "base_url", "")),
            "stream": bool(getattr(cfg, "stream", False)),
            "api_key": "set" if getattr(cfg, "api_key", "") else "unset",
            "source": "client.config",
        }

    def _request_body(
        self,
        messages: Sequence[Mapping[str, Any]],
        definitions: Sequence[Mapping[str, Any]],
        choice: Any,
    ) -> dict[str, Any]:
        """The body that is *sent* — the client's own builder when it has a config.

        For :class:`skaldir.llm_provider.OpenAICompatibleLLM` this is the request
        itself: ``chat()`` builds its body with the very same function, so no key
        is added here (the ``body_source`` note travels next to it in the
        transcript, never inside it). For a client without a provider config the
        returned mapping is the loop's view of the request, and the transcript
        says so.
        """
        cfg = getattr(self.client, "config", None)
        if cfg is not None:
            return chat_request_body(
                cfg,
                messages,
                tier=self.tier,
                tools=definitions,
                tool_choice=choice,
            )
        return {
            "model": self.tier,
            "messages": [dict(message) for message in messages],
            "tools": [dict(item) for item in definitions],
            "tool_choice": choice,
        }

    def _body_source(self) -> str:
        return "client.config" if getattr(self.client, "config", None) is not None else "loop"

    def _usage_for(
        self,
        reported: Mapping[str, Any],
        messages: Sequence[Mapping[str, Any]],
        answer: Any,
    ) -> tuple[dict[str, int], bool]:
        """Provider usage when it reports one, otherwise a chars/4 estimate.

        ``estimated`` travels with every number that is not the provider's own, so
        a budget decision is never mistaken for real accounting.
        """
        reported = {str(key): int(value) for key, value in (reported or {}).items()}
        if reported:
            total = int(
                reported.get("total_tokens")
                or int(reported.get("prompt_tokens", 0)) + int(reported.get("completion_tokens", 0))
            )
            reported["total_tokens"] = total
            return reported, False
        try:
            prompt_text = json.dumps(list(messages), ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            prompt_text = str(messages)
        calls = getattr(answer, "tool_calls", []) or []
        completion_text = str(getattr(answer, "text", "") or "") + "".join(
            str(getattr(call, "arguments_raw", "")) for call in calls
        )
        prompt_tokens = estimate_tokens(prompt_text)
        completion_tokens = estimate_tokens(completion_text) if completion_text else 1
        return (
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            True,
        )

    def _cost(self, usage: Mapping[str, int]) -> float:
        price = float(self.limits.price_per_1k_tokens or 0.0)
        if price <= 0:
            return 0.0
        return (int(usage.get("total_tokens", 0)) / 1000.0) * price

    # -- execution ---------------------------------------------------------- #

    def _execute(
        self, calls: Sequence[ToolCall], *, transcript: Transcript, step: int
    ) -> list[ToolOutcome]:
        """Run the calls of one answer: parallel where declared safe, else sequential.

        CTX-03 Read-Dedup: identical ``read_file`` calls (same path + arguments)
        within one step are answered from a cache instead of re-reading the file.
        """
        # CTX-03: one-step read cache
        read_cache: dict[str, ToolOutcome] = {}
        outcomes: list[ToolOutcome | None] = [None] * len(calls)
        index = 0
        while index < len(calls):
            group: list[int] = [index]
            tool = self.tools.get(calls[index].name)
            if tool is not None and tool.parallel_safe:
                while index + len(group) < len(calls):
                    nxt = self.tools.get(calls[index + len(group)].name)
                    if nxt is None or not nxt.parallel_safe:
                        break
                    group.append(index + len(group))
            for position in group:
                call = calls[position]
                # CTX-03: serve identical reads from cache
                if call.name == "read_file":
                    cache_key = json.dumps(
                        dict(call.arguments or {}), sort_keys=True
                    )
                    if cache_key in read_cache:
                        outcomes[position] = read_cache[cache_key]
                        continue
                self._note(
                    "tool.start",
                    name=call.name,
                    arguments=dict(call.arguments or {}),
                    index=position + 1,
                    total=len(calls),
                )
            if len(group) > 1:
                with ThreadPoolExecutor(max_workers=min(self.max_workers, len(group))) as pool:
                    futures = {
                        pool.submit(
                            self._run_one, calls[position], transcript, step, True, position
                        ): position
                        for position in group
                    }
                    for future, position in futures.items():
                        try:
                            outcomes[position] = future.result()
                        except Exception as exc:  # noqa: BLE001 - a worker bug is reported
                            outcomes[position] = self._failed_outcome(
                                calls[position],
                                f"executor failed: {type(exc).__name__}: {exc}",
                                transcript,
                                step,
                                position,
                            )
            else:
                if outcomes[index] is None:
                    outcomes[index] = self._run_one(calls[index], transcript, step, False, index)
            # CTX-03: populate cache from fresh results
            for position in group:
                call = calls[position]
                if call.name == "read_file" and outcomes[position] is not None:
                    cache_key = json.dumps(
                        dict(call.arguments or {}), sort_keys=True
                    )
                    if cache_key not in read_cache:
                        read_cache[cache_key] = outcomes[position]
            index += len(group)
        return [outcome for outcome in outcomes if outcome is not None]

    def _failed_outcome(
        self, call: ToolCall, error: str, transcript: Transcript, step: int, call_index: int = 0
    ) -> ToolOutcome:
        """A call that never reached the tool: recorded, and the model is told."""
        outcome = ToolOutcome(
            name=call.name,
            call_id=call.id,
            arguments=dict(call.arguments or {}),
            error=error,
            decision="executor_error",
        )
        outcome.digest = _digest({"ok": False, "executed": False, "error": error})
        transcript.event(
            "tool.call",
            step=step,
            call_index=call_index,
            call_id=outcome.call_id,
            name=outcome.name,
            arguments=outcome.arguments,
            decision=outcome.decision,
            policy_reasons=[],
            executed=False,
            ok=False,
            error=error,
            duration_ms=0,
            parallel=True,
            result_sha256=outcome.digest,
            result_preview="",
            result_truncated=False,
        )
        return outcome

    def _run_one(
        self,
        call: ToolCall,
        transcript: Transcript,
        step: int,
        parallel: bool,
        call_index: int = 0,
    ) -> ToolOutcome:
        outcome = ToolOutcome(
            name=call.name,
            call_id=call.id,
            arguments=dict(call.arguments or {}),
        )
        started = self._clock()
        tool = self.tools.get(call.name)
        if tool is None:
            outcome.decision = "unknown_tool"
            outcome.error = (
                f"unknown tool {call.name!r}; this run offers: "
                f"{', '.join(self.tools.names()) or '(none)'}"
            )
        elif call.parse_error:
            outcome.decision = "invalid_arguments"
            outcome.error = call.parse_error
        else:
            problem = _argument_problem(tool.input_schema, outcome.arguments)
            if problem:
                outcome.decision = "invalid_arguments"
                outcome.error = problem
            else:
                allowed = True
                if self.policy:
                    decision = judge_tool_call(
                        ToolIntent(
                            tool=tool.name,
                            args=dict(outcome.arguments),
                            goal=self.goal or "",
                            workspace=str(self.workspace),
                            project_root=str(self.workspace),
                            approvals=self.approvals,
                            recent_text=self._recent_untrusted_text,
                            untrusted_directives=tuple(self._untrusted_directives),
                        )
                    )
                    outcome.decision = decision.classification
                    outcome.policy_reasons = [str(reason) for reason in decision.reasons]
                    allowed = decision.allowed
                    if not allowed:
                        outcome.error = (
                            f"the call was not executed (policy: {decision.classification}). "
                            f"{decision.render()}"
                        )

                    # APR-01/02/04 — the approval *mode* decides how often a human is
                    # asked. It can only tighten the judge's verdict (see
                    # approval_gate.ApprovalGate: never FORBID -> ALLOW, never
                    # PROMPT -> ALLOW), so this sits after the judge and not instead of it.
                    if allowed and self.approval_gate is not None:
                        from .approval import Decision as _Decision

                        judge_decision = (
                            _Decision.ALLOW if decision.allowed else _Decision.FORBID
                        )
                        gate_verdict = self.approval_gate.decide(
                            tool=tool.name,
                            text=" ".join(str(v) for v in outcome.arguments.values()),
                            judge_decision=judge_decision,
                            run_id=str(
                                getattr(self, "run_id", "") or getattr(self, "session_id", "") or ""
                            ),
                        )
                        if gate_verdict.decision is not _Decision.ALLOW:
                            allowed = False
                            outcome.decision = f"approval_{gate_verdict.decision.value}"
                            outcome.policy_reasons = list(outcome.policy_reasons) + [
                                f"approval[{gate_verdict.mode}]: {gate_verdict.reason}"
                            ]
                            outcome.error = (
                                f"the call was not executed (approval: {gate_verdict.decision.value}). "
                                f"{gate_verdict.reason}"
                                + (
                                    " — this needs your explicit approval"
                                    if gate_verdict.requires_explicit else ""
                                )
                            )
                if allowed:
                    outcome.executed = True
                    try:
                        result = tool.handler(**outcome.arguments)
                    except Exception as exc:  # noqa: BLE001 - a tool error is data
                        outcome.ok = False
                        outcome.error = f"{type(exc).__name__}: {exc}"
                        outcome.result = None
                    else:
                        outcome.result = result
                        if isinstance(result, Mapping) and set(result) == {"error"}:
                            outcome.ok = False
                            outcome.error = str(result.get("error") or "the tool failed")
                        elif isinstance(result, Mapping) and result.get("ok") is False:
                            # a tool that reports ok:false failed in its own vocabulary
                            # (skill_act verification_failed / usage / blocked) — never
                            # `ok` just because the handler returned a dict
                            outcome.ok = False
                            outcome.error = str(
                                result.get("reason")
                                or result.get("error")
                                or result.get("message")
                                or result.get("status")
                                or "the tool reported ok=false"
                            )
                        else:
                            outcome.ok = True
        outcome.duration_ms = int((self._clock() - started) * 1000)
        outcome.digest = _digest(
            {
                "ok": outcome.ok,
                "executed": outcome.executed,
                "result": outcome.result,
                "error": outcome.error,
            }
        )
        preview, truncated = _bounded_json(
            {
                "ok": outcome.ok,
                "executed": outcome.executed,
                "result": outcome.result,
                "error": outcome.error,
            },
            self.max_result_chars,
        )
        outcome.truncated = truncated
        # TOKEN-ECONOMY: an oversized result goes to the archive as a whole; the hot
        # context then carries its id, digest and a short summary instead of the bytes.
        # The archived payload is *exactly* the object the inline form would have
        # carried, so the before/after token numbers describe a real substitution.
        #
        # The archive reader's own answer is exempt: it is a deliberate, bounded read of
        # something the runtime already stored. Re-archiving it would hand the model the
        # ref it just asked to resolve — the read half would be a loop instead of a read.
        if self.archive is not None and str(outcome.name) != ARCHIVE_READER_TOOL:
            payload = {
                "ok": outcome.ok,
                "executed": outcome.executed,
                "result": outcome.result,
                "error": outcome.error,
            }
            try:
                inline = json.dumps(payload, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                inline = ""
            if inline and self.archive.should_archive(inline):
                try:
                    archived = self.archive.put(payload, source=self._archive_source(call, tool))
                except Exception as exc:  # noqa: BLE001 — the archive is a lever, not a gate
                    self._note(
                        "tool.archive.failed",
                        name=outcome.name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    outcome.archive = archived.to_wire()
                    self._note(
                        "tool_archived",
                        name=outcome.name,
                        ref=archived.ref,
                        chars=archived.chars,
                        tokens_before=archived.token_estimate_before,
                        tokens_after=archived.token_estimate_after,
                        tokens_saved=archived.saved_tokens,
                    )
                    if transcript is not None:
                        transcript.event(
                            "tool_archived",
                            step=step,
                            call_index=call_index,
                            call_id=outcome.call_id,
                            name=outcome.name,
                            source=archived.source,
                            ref=archived.ref,
                            sha256=archived.digest,
                            path=archived.path,
                            chars=archived.chars,
                            bytes=archived.bytes,
                            summary_chars=len(archived.summary),
                            tokens_before=archived.token_estimate_before,
                            tokens_after=archived.token_estimate_after,
                            tokens_saved=archived.saved_tokens,
                            token_source="estimate/chars-4",
                        )
        self._note(
            "tool.end",
            name=outcome.name,
            duration_ms=outcome.duration_ms,
            ok=bool(outcome.ok),
            decision=str(outcome.decision or ""),
            note=str(outcome.error or ""),
        )
        transcript.event(
            "tool.call",
            step=step,
            call_index=call_index,
            call_id=outcome.call_id,
            name=outcome.name,
            arguments=outcome.arguments,
            decision=outcome.decision,
            policy_reasons=outcome.policy_reasons,
            executed=outcome.executed,
            ok=outcome.ok,
            error=outcome.error,
            duration_ms=outcome.duration_ms,
            parallel=bool(parallel),
            result_sha256=outcome.digest,
            result_preview=preview,
            result_truncated=truncated,
        )
        return outcome


__all__ = [
    "AgentLoop",
    "AgentLoopError",
    "AgentRunResult",
    "AgentTool",
    "BOUND_REASONS",
    "LoopLimits",
    "REASON_CANCELLED",
    "REASON_EMPTY_ANSWER",
    "REASON_MAX_STEPS",
    "REASON_TOKEN_BUDGET",
    "STATUS_ANSWERED",
    "STATUS_FAILED",
    "STATUS_INCOMPLETE",
    "SYSTEM_PROMPT",
    "ToolOutcome",
    "ToolSet",
    "Transcript",
    "agent_state_dir",
    "builtin_tools",
    "definition_for_spec",
    "function_definition",
    "input_schema_for_spec",
    "input_schema_from_sample",
    "input_schema_from_source",
    "list_runs",
    "new_run_id",
    "read_transcript",
    "registry_tools",
    "run_path",
    "runs_dir",
    "schema_for_annotation",
    "schema_for_value",
    "tool_set_for",
    "tools_array",
    "transcript_history",
    "wire_tool_name",
]
