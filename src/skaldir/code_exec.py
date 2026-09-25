"""Programmatic tool calling (H8): a sandboxed code runner with the tool API inside.

The agent writes a snippet instead of making one tool call at a time. The snippet
runs in an **isolated child process** and reaches the registered tools through a
small host API on a socket pair — ``skaldir.call("read_file", path="a.txt")`` — so a
snippet can loop, filter and compose tool usage in code.

    import skaldir

    hits = skaldir.call("search_files", pattern="TODO", path="src", limit=5)
    first = hits["output"]["matches"][0]["path"] if hits["output"].get("matches") else ""
    counts = skaldir.call("terminal", command=f"wc -l {first}") if first else {}
    result = {"file": first, "wc": (counts.get("output") or {}).get("stdout", "").strip()}

The last expression of a snippet (or a global ``result``) becomes the structured
return value; stdout, stderr and every tool call it made come back with it.

Why a separate process and a host-side tool API
------------------------------------------------
The child never gets the tool implementations. Each call is a line of JSON over a
socket pair (the child gets the fd number as its last argument); the **host** answers
it in this process by
routing it through :func:`skaldir.tools_core.registry.call` — the one execution seam
every other surface uses — after the pre-execution policy
(:func:`skaldir.guard.judge_tool_call`) and the run's own budget have allowed it. So:

* a snippet cannot bypass, patch or inspect the permission checks: it never holds them;
* every composed call keeps the evidence a normal call has (duration, outcome, the
  arguments/result digests) and is reported back in ``tool_calls``;
* a refused call is a *result document* the snippet can inspect (``ok: False``,
  ``error_kind``, ``error``), exactly like a refused call anywhere else.

Safety policy (the ``code explain`` payload is generated from this module)
-------------------------------------------------------------------------
``always``
    separate process group (``start_new_session=True``) killed with
    ``SIGKILL`` on the whole group when the run exceeds ``timeout_s``; working
    directory is the sandbox workspace; stdout/stderr capped head+tail with a
    marker that says how much was removed; the whole run has one wall clock.
``credentials``
    the child environment is the toolset's allowlist scrub
    (:func:`skaldir.tools_core.sandbox.child_environment`): a name matching
    ``key|token|secret|passwd|password|credential|auth|cookie|session|bearer`` is
    dropped and only *named* in the policy report, never valued. ``PYTHONPATH`` /
    ``PYTHONSTARTUP`` / ``NODE_OPTIONS`` are dropped by design and the interpreter
    runs with ``-I`` (no ``PYTHONPATH``, no user site-packages, no ``PYTHON*`` env).
    There are **no ambient credentials** in the child.
``filesystem``
    the tools address the workspace root only (``tools_core`` resolves every
    candidate path against the declared root and refuses ``..``, ``~/``, an
    absolute escape and a symlink out of it). The runner itself writes nothing.
    A snippet that *wants* a write uses ``write_file``/``patch``, so the write is
    a recorded, permission-checked tool call instead of an invisible side effect.
``imports``
    the interpreter runs ``-I -u -S``: no ``PYTHONPATH``/``PYTHON*`` env, no user
    site, no script directory on ``sys.path``, **no site-packages** — so the child
    cannot ``import skaldir`` and call a tool directly, bypassing the host's policy,
    budget and evidence. In the child ``skaldir`` *is* the host API (the only
    meaning it has there); stdlib only. ``site_packages`` (``--allow-site-packages``)
    widens exactly that and is documented as widening it.
``network``
    refused unless declared: ``web_fetch`` / ``web_search`` are dropped from the
    allowlist unless ``allow_network`` (``code run --allow-network``). Honest
    limit: ``terminal`` can still reach the network; its own isolation (bubblewrap
    ``--unshare-net`` where available) is what constrains that, not this policy.
``tools``
    an allowlist computed from the core toolset's own contracts
    (``allow_tools`` ∩ not ``deny_tools``, network tools only when declared).
    Anything else is refused with ``refused_by: "allowlist"``.
``budget``
    ``max_tool_calls`` counts every attempt (a refusal counts: a snippet cannot
    retry its way around the cap); over the cap the call is refused with
    ``error_kind: "budget"`` and the run reports ``tool_budget``. ``timeout_s``
    bounds the wall clock, ``max_output_bytes`` the streams, ``max_result_bytes``
    the structured value (head+tail with a marker, spilled under
    ``<runtime>/tool_output/`` when it really was oversized).
``not attempted`` (honest limits, never claimed)
    no seccomp/landlock filesystem jail of this process; ``bwrap`` isolation is the
    toolset's, per tool call, not this runner's; a snippet that starts a daemon
    with ``setsid`` leaves the process group, so the timeout kill is not a complete
    containment guarantee; ``-S`` closes the easy ``import skaldir`` bypass, but a
    snippet that deliberately re-adds the product's own directory to ``sys.path``
    could still import it — a real jail is a container, not a flag.

Everything above is a declaration this module enforces in code; ``code explain``
prints the same policy from the same objects.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_MAX_RESULT_BYTES",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_TIMEOUT_S",
    "MAX_TIMEOUT_S",
    "NETWORK_TOOLS",
    "STATUS_ERROR",
    "STATUS_NO_RESULT",
    "STATUS_OK",
    "STATUS_REFUSED",
    "STATUS_TIMEOUT",
    "CodeExecPolicy",
    "CodeResult",
    "ToolCallRecord",
    "host_inventory",
    "policy_report",
    "run_code",
    "runner_source",
    "workspace_digest",
]

#: Default wall clock for one snippet.
DEFAULT_TIMEOUT_S = 10
#: Hard ceiling for ``timeout_s`` — a snippet is not a long-running job.
MAX_TIMEOUT_S = 120
#: Default cap for each of stdout / stderr (head+tail, marker in the text).
DEFAULT_MAX_OUTPUT_BYTES = 32_000
#: Ceiling for that cap.
MAX_OUTPUT_BYTES = 1_000_000
#: Default number of tool calls one run may attempt (refusals included).
DEFAULT_MAX_TOOL_CALLS = 25
#: Ceiling for that budget.
MAX_TOOL_CALLS = 500
#: Default cap for the structured return value.
DEFAULT_MAX_RESULT_BYTES = 64_000

#: Tools that reach the network; dropped from the allowlist unless declared.
NETWORK_TOOLS: tuple[str, ...] = ("web_fetch", "web_search")

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_NO_RESULT = "no_result"
STATUS_REFUSED = "refused"

RUNNER_FILENAME = "skaldir_code_runner.py"
SNIPPET_FILENAME = "snippet.py"
#: A marker in the child's environment, so a process can tell it is a snippet.
SANDBOX_MARKER = "CODE_SANDBOX"


# --------------------------------------------------------------------- policy


@dataclass(frozen=True)
class CodeExecPolicy:
    """Everything the sandbox is allowed to do, declared in one object."""

    workspace: Path = field(default_factory=Path.cwd)
    runtime: Path | None = None
    timeout_s: int = DEFAULT_TIMEOUT_S
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES
    allow_network: bool = False
    allow_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()
    policy_gate: bool = True
    require_registered: bool = False
    site_packages: bool = False
    interpreter: str = ""
    keep_run_dir: bool = False

    def normalized(self) -> CodeExecPolicy:
        """Clamp the numeric limits and resolve the defaults (never raises)."""
        timeout = _clamp(self.timeout_s, 1, MAX_TIMEOUT_S, DEFAULT_TIMEOUT_S)
        return CodeExecPolicy(
            workspace=Path(self.workspace).expanduser(),
            runtime=Path(self.runtime).expanduser() if self.runtime else None,
            timeout_s=timeout,
            max_output_bytes=_clamp(
                self.max_output_bytes, 1_000, MAX_OUTPUT_BYTES, DEFAULT_MAX_OUTPUT_BYTES
            ),
            max_tool_calls=_clamp(
                self.max_tool_calls, 0, MAX_TOOL_CALLS, DEFAULT_MAX_TOOL_CALLS
            ),
            max_result_bytes=_clamp(self.max_result_bytes, 1_000, MAX_OUTPUT_BYTES,
                                    DEFAULT_MAX_RESULT_BYTES),
            allow_network=bool(self.allow_network),
            allow_tools=tuple(str(item) for item in self.allow_tools),
            deny_tools=tuple(str(item) for item in self.deny_tools),
            policy_gate=bool(self.policy_gate),
            require_registered=bool(self.require_registered),
            site_packages=bool(self.site_packages),
            interpreter=str(self.interpreter) or _default_interpreter(),
            keep_run_dir=bool(self.keep_run_dir),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "runtime": str(self.runtime) if self.runtime else "",
            "timeout_s": int(self.timeout_s),
            "max_timeout_s": MAX_TIMEOUT_S,
            "max_output_bytes": int(self.max_output_bytes),
            "max_tool_calls": int(self.max_tool_calls),
            "max_result_bytes": int(self.max_result_bytes),
            "allow_network": bool(self.allow_network),
            "allow_tools": list(self.allow_tools),
            "deny_tools": list(self.deny_tools),
            "policy_gate": bool(self.policy_gate),
            "require_registered": bool(self.require_registered),
            "site_packages": bool(self.site_packages),
            "interpreter": self.interpreter,
            "keep_run_dir": bool(self.keep_run_dir),
        }


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    return max(low, min(high, number))


def _default_interpreter() -> str:
    """The interpreter the child runs: this process's, or the system one."""
    return sys.executable or shutil.which("python3") or "python3"


# ---------------------------------------------------------------- tool access


def _core_module() -> Any:
    """The core toolset's registry module (the one execution seam), or ``None``.

    Imported by its full path on purpose: ``skaldir.tools_core`` re-exports its
    functions lazily, so the submodule is what carries ``call``/``names``.
    """
    try:
        from .tools_core import registry
    except Exception:  # noqa: BLE001 — reported as a refusal, never invented
        return None
    return registry


def _core_names() -> tuple[str, ...]:
    module = _core_module()
    if module is None:
        return ()
    try:
        return tuple(module.names())
    except Exception:  # noqa: BLE001
        return ()


def _registered_names(runtime: Path | None) -> dict[str, dict[str, Any]]:
    """``name -> registry row`` for the core tools (empty when there is no registry)."""
    module = _core_module()
    if module is None or runtime is None:
        return {}
    try:
        rows = module.registry_view(runtime)
    except Exception:  # noqa: BLE001
        return {}
    return {str(row.get("name")): row for row in rows}


def host_inventory(policy: CodeExecPolicy) -> list[dict[str, Any]]:
    """Every core tool with its declared state: allowed here, or refused and why.

    The allowlist is derived (never hand-written): names come from the toolset's own
    contracts, the network tools need ``allow_network``, and ``allow_tools`` /
    ``deny_tools`` narrow it further. ``registry`` says whether the runtime really
    carries a row for the tool — the sandbox reports that, it does not pretend.
    """
    names = _core_names()
    declared = set(names)
    requested = set(policy.allow_tools)
    denied = set(policy.deny_tools)
    rows = _registered_names(policy.runtime)
    inventory: list[dict[str, Any]] = []
    for name in names:
        reason = ""
        if name in denied:
            reason = "refused by --deny-tool"
        elif requested and name not in requested:
            reason = "not in --allow-tool"
        elif name in NETWORK_TOOLS and not policy.allow_network:
            reason = "network is not declared for this sandbox (--allow-network)"
        registered = bool(rows.get(name, {}).get("registered"))
        if policy.require_registered and not registered:
            reason = reason or "the runtime registry has no row for this tool"
        inventory.append(
            {
                "name": name,
                "allowed": not reason,
                "reason": reason,
                "registered": registered,
                "registry_status": str(rows.get(name, {}).get("status", "")),
            }
        )
    for name in sorted(denied | requested):
        if name not in declared:
            inventory.append(
                {
                    "name": name,
                    "allowed": False,
                    "reason": f"unknown tool: {name} is not in the core toolset",
                    "registered": False,
                    "registry_status": "",
                }
            )
    return inventory


def _allowed_names(inventory: list[dict[str, Any]]) -> set[str]:
    return {str(row["name"]) for row in inventory if row["allowed"]}


# --------------------------------------------------------------------- result


@dataclass
class ToolCallRecord:
    """One tool call a snippet made, with its duration and outcome."""

    index: int
    tool: str
    ok: bool = False
    error_kind: str = ""
    error: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    refused: bool = False
    refused_by: str = ""
    policy_classification: str = ""
    policy_reasons: list[str] = field(default_factory=list)
    arguments_sha256: str = ""
    result_sha256: str = ""
    result_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "tool": self.tool,
            "ok": bool(self.ok),
            "error_kind": self.error_kind,
            "error": self.error,
            "exit_code": int(self.exit_code),
            "duration_ms": int(self.duration_ms),
            "refused": bool(self.refused),
            "refused_by": self.refused_by,
            "policy_classification": self.policy_classification,
            "policy_reasons": list(self.policy_reasons),
            "arguments_sha256": self.arguments_sha256,
            "result_sha256": self.result_sha256,
            "result_bytes": int(self.result_bytes),
        }


@dataclass
class CodeResult:
    """The whole outcome of one snippet run."""

    status: str = STATUS_REFUSED
    ok: bool = False
    reason: str = ""
    value: Any = None
    value_source: str = "none"
    value_serializable: bool = True
    value_repr: str = ""
    result_truncated: bool = False
    result_bytes: int = 0
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0
    error_type: str = ""
    error_message: str = ""
    traceback_text: str = ""
    exit_code: int | None = None
    killed: bool = False
    killed_signal: str = ""
    duration_ms: int = 0
    timed_out_after_s: int = 0
    process_group: int = 0
    run_dir: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_budget: dict[str, Any] = field(default_factory=dict)
    workspace_digest_before: str = ""
    workspace_digest_after: str = ""
    argv: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": bool(self.ok),
            "reason": self.reason,
            "value": self.value,
            "value_source": self.value_source,
            "value_serializable": bool(self.value_serializable),
            "value_repr": self.value_repr,
            "result_truncated": bool(self.result_truncated),
            "result_bytes": int(self.result_bytes),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": bool(self.stdout_truncated),
            "stderr_truncated": bool(self.stderr_truncated),
            "stdout_total_bytes": int(self.stdout_total_bytes),
            "stderr_total_bytes": int(self.stderr_total_bytes),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "traceback": self.traceback_text,
            "exit_code": self.exit_code,
            "killed": bool(self.killed),
            "killed_signal": self.killed_signal,
            "duration_ms": int(self.duration_ms),
            "timed_out_after_s": int(self.timed_out_after_s),
            "process_group": int(self.process_group),
            "run_dir": self.run_dir,
            "tool_calls": [record.to_dict() for record in self.tool_calls],
            "tool_budget": dict(self.tool_budget),
            "workspace_digest_before": self.workspace_digest_before,
            "workspace_digest_after": self.workspace_digest_after,
            "workspace_unchanged": self.workspace_digest_before == self.workspace_digest_after,
            "argv": list(self.argv),
            "warnings": list(self.warnings),
            "policy": dict(self.policy),
        }


# ------------------------------------------------------------ workspace digest


def workspace_digest(
    root: str | Path, *, max_files: int = 4000, max_file_bytes: int = 65_536
) -> dict[str, Any]:
    """A digest of the workspace tree — the "did the run change anything" answer.

    Content is hashed (size + bytes), so an edit to an already-dirty file is visible;
    the walk is bounded and reports when it was cut, because an unchecked tree must
    never be reported as unchanged.
    """
    base = Path(root)
    digest = hashlib.sha256()
    files = 0
    truncated = False
    if not base.is_dir():
        return {"root": str(base), "sha256": "", "files": 0, "truncated": True,
                "error": "not a directory"}
    for path in sorted(base.rglob("*")):
        if any(part in {".git", ".venv", "__pycache__", "node_modules"} for part in path.parts):
            continue
        try:
            if path.is_symlink():
                digest.update(b"L" + path.name.encode())
                continue
            if not path.is_file():
                continue
            stat = path.stat()
            digest.update(path.relative_to(base).as_posix().encode("utf-8", "replace"))
            digest.update(str(stat.st_size).encode())
            with path.open("rb") as handle:
                digest.update(handle.read(max_file_bytes))
        except OSError:
            continue
        files += 1
        if files >= max_files:
            truncated = True
            break
    return {
        "root": str(base),
        "sha256": digest.hexdigest(),
        "files": files,
        "truncated": truncated,
    }


# ------------------------------------------------------------- tool channel


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _sha256_of(payload: Any) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _truncation_rule(max_bytes: int, *, spill: bool) -> Any:
    from .tools_core.contract import TruncationRule

    return TruncationRule(max_bytes=int(max_bytes), spill=bool(spill))


def _truncate_text(text: str, *, limit: int, label: str, runtime: Path | None) -> dict[str, Any]:
    """Head+tail truncation with the toolset's own rule (one implementation)."""
    from .tools_core.sandbox import truncate_bytes

    return truncate_bytes(
        text.encode("utf-8"),
        _truncation_rule(limit, spill=runtime is not None),
        label=label,
        runtime=runtime,
        spill_name=label,
    )


class _StreamSink:
    """Bounded head+tail accumulation for one child stream (never unbounded)."""

    def __init__(self, *, head_bytes: int, tail_bytes: int) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self._head_bytes = max(1, int(head_bytes))
        self._tail_bytes = max(0, int(tail_bytes))

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._head_bytes - len(self.head)
        if room > 0:
            self.head.extend(chunk[:room])
            chunk = chunk[room:]
        if chunk and self._tail_bytes:
            self.tail.extend(chunk)
            if len(self.tail) > self._tail_bytes:
                del self.tail[: len(self.tail) - self._tail_bytes]

    @property
    def truncated(self) -> bool:
        return self.total > len(self.head) + len(self.tail)

    def text(self, label: str, runtime: Path | None) -> tuple[str, dict[str, Any]]:
        head = bytes(self.head).decode("utf-8", "replace")
        tail = bytes(self.tail).decode("utf-8", "replace")
        if not self.truncated:
            return head + tail, {"label": label, "truncated": False, "total_bytes": self.total,
                                 "spill_path": ""}
        removed = self.total - len(self.head) - len(self.tail)
        marker = (
            f"[TRUNCATED: {label} was {self.total} bytes; {len(self.head)} head + "
            f"{len(self.tail)} tail kept, {removed} removed]"
        )
        return head + f"\n{marker}\n" + tail, {
            "label": label,
            "truncated": True,
            "total_bytes": self.total,
            "removed_bytes": removed,
            "marker": marker,
            "spill_path": "",
        }


class _ToolHost:
    """The host side of the tool channel: policy, budget, execution, evidence.

    Every request is answered in *this* process. The snippet's process holds no tool
    implementation, so it cannot widen its own scope, skip a check or hide a call.
    """

    def __init__(self, policy: CodeExecPolicy, inventory: list[dict[str, Any]]) -> None:
        self.policy = policy
        self.inventory = inventory
        self.allowed = _allowed_names(inventory)
        self.calls: list[ToolCallRecord] = []
        self.by_tool: dict[str, int] = {}

    def remaining(self) -> int:
        return max(0, int(self.policy.max_tool_calls) - len(self.calls))

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Answer one message. ``None`` = nothing to answer (a notification)."""
        kind = str(message.get("type", ""))
        if kind == "inventory":
            return {"type": "inventory", "ok": True, "tools": self.inventory}
        if kind == "tool":
            return self._answer_tool(message)
        return {"type": "error", "ok": False, "error": f"unknown message type: {kind!r}"}

    # -- one tool call -------------------------------------------------------
    def _answer_tool(self, message: dict[str, Any]) -> dict[str, Any]:
        tool = str(message.get("name", "")).strip()
        arguments = message.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        record = ToolCallRecord(
            index=len(self.calls) + 1,
            tool=tool,
            arguments_sha256=_sha256_of(arguments),
        )
        self.calls.append(record)
        self.by_tool[tool] = self.by_tool.get(tool, 0) + 1
        started = time.time()
        document = self._dispatch(record, tool, arguments)
        record.duration_ms = int((time.time() - started) * 1000)
        record.result_bytes = len(_json_bytes(document))
        record.result_sha256 = _sha256_of(document)
        rules = document.get("output")
        if isinstance(rules, dict) and rules.get("warnings"):
            record.policy_reasons = list(record.policy_reasons)
        return {"type": "tool", "ok": True, "id": message.get("id"), "result": document}

    def _dispatch(
        self, record: ToolCallRecord, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if not tool:
            return self._refuse(record, "usage", "a tool call needs a name", "protocol")
        if tool not in self.allowed:
            known = any(row["name"] == tool for row in self.inventory)
            reason = next(
                (row["reason"] for row in self.inventory if row["name"] == tool),
                f"unknown tool: {tool} is not in the core toolset",
            )
            return self._refuse(
                record, "blocked" if known else "not_found", reason,
                "allowlist" if known else "unknown_tool",
            )
        if len(self.calls) > int(self.policy.max_tool_calls):
            return self._refuse(
                record,
                "budget",
                f"tool-call budget exhausted: this run allows "
                f"{self.policy.max_tool_calls} attempt(s) and this is attempt "
                f"{len(self.calls)}",
                "budget",
            )
        if self.policy.policy_gate:
            classification, reasons = _gate(tool, arguments, self.policy.workspace)
            record.policy_classification = classification
            record.policy_reasons = list(reasons)
            if classification and classification != "ALLOW":
                return self._refuse(
                    record,
                    "blocked",
                    f"the pre-execution policy classified this call {classification}; a "
                    "snippet has no approval channel, so the call is refused",
                    f"policy:{classification}",
                    detail={"classification": classification, "reasons": list(reasons)},
                )
        return self._call_tool(record, tool, arguments)

    def _call_tool(self, record: ToolCallRecord, tool: str, arguments: dict[str, Any]) -> dict:
        module = _core_module()
        if module is None:
            return self._refuse(
                record, "blocked", "the core toolset is not available in this build",
                "tools_core_unavailable",
            )
        try:
            result = module.call(
                tool,
                arguments,
                runtime=self.policy.runtime,
                root=self.policy.workspace,
                require_registered=bool(self.policy.require_registered),
            )
        except Exception as exc:  # noqa: BLE001 — a tool crash is a measured refusal
            return self._refuse(
                record, "internal", f"the tool call failed: {type(exc).__name__}: {exc}",
                "tool_error",
            )
        document = result.to_dict()
        record.ok = bool(document.get("ok"))
        record.error_kind = str(document.get("error_kind") or "")
        record.error = str(document.get("error") or "")
        record.exit_code = int(document.get("exit_code") or 0)
        return document

    def _refuse(
        self,
        record: ToolCallRecord,
        kind: str,
        message: str,
        by: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> dict:
        record.refused = True
        record.refused_by = by
        record.ok = False
        record.error_kind = kind
        record.error = message
        from .tools_core.contract import ERROR_EXIT

        return {
            "tool": record.tool,
            "ok": False,
            "error_kind": kind,
            "error": message,
            "exit_code": int(ERROR_EXIT.get(kind, 7)),
            "duration_ms": 0,
            "warnings": [],
            "evidence": [],
            "output": {
                "refused": True,
                "refused_by": by,
                **(detail or {}),
            },
            "refused": True,
            "refused_by": by,
        }

    def report(self) -> dict[str, Any]:
        max_calls = int(self.policy.max_tool_calls)
        return {
            "max": max_calls,
            "used": len(self.calls),
            "remaining": self.remaining(),
            "exhausted": len(self.calls) >= max_calls,
            "refused": sum(1 for record in self.calls if record.refused),
            "by_tool": dict(sorted(self.by_tool.items())),
        }


def _fd_of(obj: Any) -> int:
    """The file descriptor of a selector key's ``fileobj`` (a pipe or an int)."""
    if isinstance(obj, int):
        return obj
    return int(obj.fileno())


def _gate(tool: str, arguments: dict[str, Any], workspace: Path) -> tuple[str, list[str]]:
    """The pre-execution policy a normal call gets — ``("", [])`` when unavailable.

    ``skaldir.toolcall`` is the module that judges a *planned* tool call
    (ALLOW / ASK_APPROVAL / PAUSE_AND_INVESTIGATE / BLOCK) before it runs. A snippet
    has no approval channel, so anything that is not ALLOW is refused here and the
    classification travels back in the call's evidence.
    """
    try:
        from . import toolcall
    except Exception:  # noqa: BLE001 — no policy module here: the tools' own checks stand
        return "", []
    intent = toolcall.ToolIntent(
        tool=tool,
        args=dict(arguments),
        workspace=str(workspace),
        project_root=str(workspace),
    )
    decision = toolcall.judge_tool_call(intent)
    return str(decision.classification), [str(item) for item in decision.reasons]


# ------------------------------------------------------------------- runner


def runner_source() -> str:
    """The child's source: the host API, the snippet execution, the result frame.

    Stdlib only and self-contained on purpose — the child imports no Skaldir module,
    so it cannot reach the tool implementations even by accident.
    """
    return _RUNNER_SOURCE


_RUNNER_SOURCE = '''\
"""Skaldir code runner (child side): the host tool API and the snippet execution.

Runs in its own process with a scrubbed environment. The only way to a tool is the
socket fd in its last argument; the host answers there. ``skaldir`` (and an
importable ``skaldir`` module) is the host API, not the product package.
"""
from __future__ import annotations

import ast
import json
import socket
import sys
import types
import traceback

_MISSING = object()


class ToolChannelError(RuntimeError):
    """The tool channel to the host is broken (not a refused tool call)."""


class HostAPI:
    """Line-delimited JSON-RPC to the host, one request at a time."""

    def __init__(self, channel_file):
        self._channel = channel_file
        self._request_id = 0
        self._inventory = None

    def _exchange(self, message):
        payload = json.dumps(message, ensure_ascii=False, sort_keys=True, default=str)
        self._channel.write((payload + "\\n").encode("utf-8"))
        self._channel.flush()
        line = self._channel.readline()
        if not line:
            raise ToolChannelError("the host closed the tool channel")
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolChannelError(f"the host sent an unreadable answer: {exc}") from exc

    def call(self, name, **arguments):
        """Call one registered tool. Returns its result document (never raises for a refusal)."""
        self._request_id += 1
        reply = self._exchange(
            {"type": "tool", "id": self._request_id, "name": str(name),
             "arguments": dict(arguments)}
        )
        if reply.get("type") == "error":
            raise ToolChannelError(str(reply.get("error") or "the host refused the request"))
        result = reply.get("result")
        if not isinstance(result, dict):
            raise ToolChannelError("the host answered without a result document")
        return result

    def tools(self):
        """The inventory the host declared for this sandbox (name, allowed, reason)."""
        if self._inventory is None:
            reply = self._exchange({"type": "inventory", "id": 0})
            self._inventory = [dict(item) for item in (reply.get("tools") or [])]
        return self._inventory

    def allowed_tools(self):
        return [item["name"] for item in self.tools() if item.get("allowed")]


def _host_module(host):
    module = types.ModuleType("skaldir")
    module.__doc__ = "The sandbox tool API: skaldir.call(name, **arguments)."
    module.call = host.call
    module.tools = host.tools
    module.allowed_tools = host.allowed_tools
    module.ToolChannelError = ToolChannelError
    module.__all__ = ["ToolChannelError", "allowed_tools", "call", "tools"]
    return module


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def main(argv):
    # The tool channel is an inherited file descriptor and nothing else: the number is
    # the last argument, so the child reads no environment variable at all.
    socket_file = socket.socket(fileno=int(argv[2])).makefile("rwb", buffering=0)
    host = HostAPI(socket_file)
    module = _host_module(host)
    # In this process "skaldir" *is* the host API: the product package is not on the
    # path (-I drops PYTHONPATH and the user site), so a snippet cannot import it.
    sys.modules.setdefault("skaldir", module)
    source = _read(argv[1])
    tree = ast.parse(source, filename=argv[1])
    tail = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        tail = tree.body.pop()
        ast.fix_missing_locations(tail)
    namespace = {"__name__": "__skaldir_snippet__", "__file__": argv[1], "skaldir": module}
    try:
        exec(compile(tree, argv[1], "exec"), namespace)
        value, origin = _MISSING, "none"
        if tail is not None:
            value = eval(compile(ast.Expression(tail.value), argv[1], "eval"), namespace)
            origin = "last_expression"
        elif "result" in namespace:
            value, origin = namespace["result"], "result"
    except BaseException as exc:  # the snippet's own failure is the result
        payload = {
            "type": "error",
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": _clean_traceback(),
            },
        }
        _send(socket_file, payload)
        sys.stdout.flush()
        sys.stderr.flush()
        return 1
    _send(socket_file, {"type": "result", "payload": _payload(value, origin)})
    sys.stdout.flush()
    sys.stderr.flush()
    return 0


def _clean_traceback():
    """The snippet's frames only — the runner's own frames are noise for the caller."""
    lines = traceback.format_exc().splitlines()
    kept = [line for line in lines if "skaldir_code_runner.py" not in line]
    return "\\n".join(kept)[:4000]


def _payload(value, origin):
    if value is _MISSING:
        return {"value_source": "none", "value": None, "value_serializable": True,
                "value_repr": ""}
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=_no_default)
        serializable = True
        repr_text = ""
    except (TypeError, ValueError):
        encoded = ""
        serializable = False
        repr_text = repr(value)[:2000]
    return {
        "value_source": origin,
        "value": json.loads(encoded) if serializable else None,
        "value_serializable": serializable,
        "value_repr": repr_text,
    }


def _no_default(_value):
    raise TypeError("not JSON-serializable")


def _send(channel, payload):
    channel.write((json.dumps(payload, ensure_ascii=False, default=str) + "\\n").encode("utf-8"))
    channel.flush()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''


# ------------------------------------------------------------------ execution


def run_code(
    source: str,
    *,
    policy: CodeExecPolicy | None = None,
    workspace: str | Path | None = None,
    runtime: str | Path | None = None,
    **overrides: Any,
) -> CodeResult:
    """Run one snippet in the sandbox and return everything it produced.

    Never raises for a snippet's own failure — a raising snippet, a timeout, a refused
    tool call and a clean run are all *measured* results (``status``). It refuses
    (``status="refused"``) only when the sandbox cannot be honoured at all.
    """
    if policy is None:
        settings = dict(overrides)
        if workspace is not None:
            settings["workspace"] = Path(workspace)
        if runtime is not None:
            settings["runtime"] = Path(runtime)
        policy = CodeExecPolicy(**settings).normalized()
    else:
        policy = policy.normalized()
    result = CodeResult(policy=policy.to_dict())
    if not str(source or "").strip():
        result.reason = "the snippet is empty"
        return result
    if not policy.workspace.is_dir():
        result.reason = f"the workspace is not a directory: {policy.workspace}"
        return result
    module = _core_module()
    if module is None:
        result.reason = "the core toolset (skaldir.tools_core) is not available in this build"
        return result
    if not _core_names():
        result.reason = "the core toolset declares no tools"
        return result
    inventory = host_inventory(policy)
    if not _allowed_names(inventory):
        result.reason = (
            "no tool is allowed in this sandbox — every core tool was refused "
            "(network not declared, --deny-tool, or --allow-tool narrowed it to nothing)"
        )
        return result

    run_dir = Path(tempfile.mkdtemp(prefix="skaldir-code-run-"))
    result.run_dir = str(run_dir)
    try:
        snippet = run_dir / SNIPPET_FILENAME
        snippet.write_text(source, encoding="utf-8")
        runner = run_dir / RUNNER_FILENAME
        runner.write_text(runner_source(), encoding="utf-8")
        _execute(result, policy, inventory, run_dir, runner, snippet)
    except OSError as exc:
        result.status = STATUS_REFUSED
        result.reason = f"the sandbox could not be prepared: {type(exc).__name__}: {exc}"
    finally:
        if not policy.keep_run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)
            result.run_dir = ""
    return result


def _interpreter_argv(
    policy: CodeExecPolicy, runner: Path, snippet: Path, channel_fd: int
) -> list[str]:
    """The child's argv — the interpreter flags *are* part of the sandbox.

    ``-I`` ignores ``PYTHONPATH``/``PYTHON*`` env vars, the user site and the script's
    directory (``-E -s -P``); ``-u`` keeps output visible when a runaway is killed;
    ``-S`` (the default) keeps site-packages off ``sys.path``, which is what makes the
    environment importable-only-by-declaration — with it, a snippet cannot
    ``import skaldir`` and call a tool *directly*, bypassing the host's policy,
    budget and evidence. ``policy.site_packages`` widens exactly that.

    The tool channel's file descriptor is the last argument: the child reads no
    environment variable at all, and the recorded argv shows the channel it used.
    """
    flags = ["-I", "-u"] + ([] if policy.site_packages else ["-S"])
    return [policy.interpreter, *flags, str(runner), str(snippet), str(int(channel_fd))]


def _child_environment(policy: CodeExecPolicy) -> tuple[dict[str, str], dict]:
    """The child's environment: the toolset's allowlist scrub + a sandbox marker."""
    from .env import child_env
    from .tools_core.sandbox import child_environment

    extra = child_env({SANDBOX_MARKER: "1"})
    return child_environment(policy.workspace, extra=extra)


def _execute(
    result: CodeResult,
    policy: CodeExecPolicy,
    inventory: list[dict[str, Any]],
    run_dir: Path,
    runner: Path,
    snippet: Path,
) -> None:
    parent_socket, child_socket = socket.socketpair()
    host = _ToolHost(policy, inventory)
    try:
        env, env_report = _child_environment(policy)
    except Exception as exc:  # noqa: BLE001 — no scrub policy, no sandbox
        parent_socket.close()
        child_socket.close()
        result.status = STATUS_REFUSED
        result.reason = f"the environment policy is unavailable: {type(exc).__name__}: {exc}"
        return
    result.warnings.extend(_env_warnings(env_report))
    result.workspace_digest_before = workspace_digest(policy.workspace)["sha256"]
    argv = _interpreter_argv(policy, runner, snippet, child_socket.fileno())
    result.argv = argv
    started = time.time()
    try:
        process = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            argv,
            cwd=str(policy.workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(child_socket.fileno(),),
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        parent_socket.close()
        child_socket.close()
        result.status = STATUS_REFUSED
        result.reason = f"the interpreter could not be started: {type(exc).__name__}: {exc}"
        return
    finally:
        child_socket.close()
    result.process_group = int(process.pid)
    head = int(policy.max_output_bytes * 0.75)
    tail = int(policy.max_output_bytes - head)
    out_sink, err_sink = _StreamSink(head_bytes=head, tail_bytes=tail), _StreamSink(
        head_bytes=head, tail_bytes=tail
    )
    frame = _pump(
        result, policy, host, parent_socket, process, out_sink, err_sink, deadline=started
    )
    parent_socket.close()
    result.duration_ms = int((time.time() - started) * 1000)
    _finish(result, policy, host, frame, out_sink, err_sink, process)
    result.workspace_digest_after = workspace_digest(policy.workspace)["sha256"]


def _env_warnings(env_report: dict) -> list[str]:
    kept = len(env_report.get("kept") or [])
    dropped = len(env_report.get("dropped_credentials") or [])
    return [
        f"child environment: {kept} variable(s) kept, {dropped} credential-looking name(s) "
        f"dropped by name (values never read) — `skaldir code explain --json` lists them"
    ]


def _pump(
    result: CodeResult,
    policy: CodeExecPolicy,
    host: _ToolHost,
    parent_socket: socket.socket,
    process: subprocess.Popen,
    out_sink: _StreamSink,
    err_sink: _StreamSink,
    *,
    deadline: float,
) -> dict[str, Any] | None:
    """Service tool requests and read output until the child is done or time is up."""
    selector = selectors.DefaultSelector()
    pending = bytearray()
    streams: dict[int, _StreamSink] = {}
    if process.stdout is not None:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        streams[_fd_of(process.stdout)] = out_sink
    if process.stderr is not None:
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        streams[_fd_of(process.stderr)] = err_sink
    selector.register(parent_socket, selectors.EVENT_READ, "channel")
    frame: dict[str, Any] | None = None
    timeout = float(policy.timeout_s)
    try:
        while True:
            remaining = timeout - (time.time() - deadline)
            if remaining <= 0:
                _kill_group(result, process)
                return frame
            for key, _events in selector.select(timeout=min(0.25, max(0.01, remaining))):
                if key.data == "channel":
                    chunk = _read_fd(parent_socket.fileno())
                    if chunk == b"":
                        selector.unregister(parent_socket)
                        continue
                    pending.extend(chunk)
                    while b"\n" in pending:
                        line, _, rest = bytes(pending).partition(b"\n")
                        pending = bytearray(rest)
                        answered, frame = _handle_line(host, line, frame)
                        if answered:
                            _write_all(parent_socket.fileno(), answered)
                    continue
                sink = streams.get(_fd_of(key.fileobj))
                chunk = _read_fd(_fd_of(key.fileobj))
                if sink is not None:
                    sink.feed(chunk)
                if chunk == b"":
                    selector.unregister(key.fileobj)
            if not selector.get_map():
                return frame
    finally:
        selector.close()


def _handle_line(
    host: _ToolHost, line: bytes, frame: dict[str, Any] | None
) -> tuple[bytes, dict[str, Any] | None]:
    text = line.decode("utf-8", "replace").strip()
    if not text:
        return b"", frame
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return _json_bytes({"type": "error", "ok": False, "error": "unreadable request"}) + b"\n", (
            frame
        )
    if not isinstance(message, dict):
        return _json_bytes({"type": "error", "ok": False, "error": "request is not an object"}) + (
            b"\n"
        ), frame
    kind = str(message.get("type", ""))
    if kind in {"result", "error"}:
        return b"", message
    answer = host.handle(message)
    if answer is None:
        return b"", frame
    return _json_bytes(answer) + b"\n", frame


def _finish(
    result: CodeResult,
    policy: CodeExecPolicy,
    host: _ToolHost,
    frame: dict[str, Any] | None,
    out_sink: _StreamSink,
    err_sink: _StreamSink,
    process: subprocess.Popen,
) -> None:
    stdout, out_meta = out_sink.text("stdout", policy.runtime)
    stderr, err_meta = err_sink.text("stderr", policy.runtime)
    result.stdout, result.stderr = stdout, stderr
    result.stdout_truncated = bool(out_meta.get("truncated"))
    result.stderr_truncated = bool(err_meta.get("truncated"))
    result.stdout_total_bytes = int(out_meta.get("total_bytes") or 0)
    result.stderr_total_bytes = int(err_meta.get("total_bytes") or 0)
    result.tool_calls = list(host.calls)
    result.tool_budget = host.report()
    if frame is None:
        code = process.poll()
        if code is None:
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_group(result, process)
                code = process.poll()
        result.exit_code = code
        if result.killed:
            result.status = STATUS_TIMEOUT
            result.timed_out_after_s = int(policy.timeout_s)
            result.reason = (
                f"the snippet was killed after {policy.timeout_s}s "
                f"({result.killed_signal} to the whole process group); it produced no result"
            )
        else:
            result.status = STATUS_NO_RESULT
            result.reason = (
                "the snippet stopped without reporting a result "
                f"(exit code {code}, no result frame on the tool channel)"
            )
        result.warnings.append(result.reason)
        return
    result.exit_code = process.wait(timeout=5) if process.poll() is None else process.poll()
    answer: dict[str, Any] = frame
    raw_payload = answer.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    if str(answer.get("type")) == "error":
        result.status = STATUS_ERROR
        raw_error = answer.get("error")
        error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        result.error_type = str(error.get("type") or "")
        result.error_message = str(error.get("message") or "")
        result.traceback_text = str(error.get("traceback") or "")
        return
    result.status = STATUS_OK
    result.ok = True
    result.value_source = str(payload.get("value_source") or "none")
    result.value_serializable = bool(payload.get("value_serializable", True))
    result.value_repr = str(payload.get("value_repr") or "")
    result.value = payload.get("value")
    _cap_result_value(result, policy)
    if result.tool_budget.get("exhausted"):
        result.warnings.append(
            "the tool-call budget was exhausted during this run — later calls were refused"
        )


def _cap_result_value(result: CodeResult, policy: CodeExecPolicy) -> None:
    encoded = _json_bytes(result.value)
    result.result_bytes = len(encoded)
    if len(encoded) <= int(policy.max_result_bytes):
        return
    capped = _truncate_text(
        encoded.decode("utf-8", "replace"),
        limit=int(policy.max_result_bytes),
        label="result",
        runtime=policy.runtime,
    )
    result.result_truncated = True
    result.value = capped["text"]
    note = (
        f"the structured result was {len(encoded)} bytes and exceeded "
        f"max_result_bytes={policy.max_result_bytes}; the head+tail is returned as text"
    )
    result.warnings.append(note)
    if capped.get("spill_path"):
        result.warnings.append(f"full result: {capped['spill_path']}")


def _kill_group(result: CodeResult, process: subprocess.Popen) -> None:
    """Kill the child's whole process group — a snippet gets one SIGKILL, then a report."""
    result.killed = True
    result.killed_signal = "SIGKILL"
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        result.warnings.append("the child did not reap after SIGKILL")


def _read_fd(fd: int) -> bytes:
    try:
        return os.read(fd, 65536)
    except (BlockingIOError, InterruptedError):
        return b""
    except OSError:
        return b""


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(fd, view)
        except InterruptedError:
            continue
        except OSError:
            return
        view = view[written:]


# ------------------------------------------------------------------- report


def policy_report(
    *,
    workspace: str | Path | None = None,
    runtime: str | Path | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """``code explain``: the sandbox policy, what is allowed, and the honest limits."""
    policy = CodeExecPolicy(
        workspace=Path(workspace) if workspace is not None else Path.cwd(),
        runtime=Path(runtime) if runtime is not None else None,
        **overrides,
    ).normalized()
    inventory = host_inventory(policy)
    report: dict[str, Any] = {
        "policy": policy.to_dict(),
        "limits": {
            "wall_clock_s": policy.timeout_s,
            "tool_calls": policy.max_tool_calls,
            "stdout_bytes": policy.max_output_bytes,
            "stderr_bytes": policy.max_output_bytes,
            "result_bytes": policy.max_result_bytes,
            "kill": "SIGKILL to the whole process group (start_new_session)",
        },
        "tools": {
            "allowed": sorted(_allowed_names(inventory)),
            "refused": [
                {"name": row["name"], "reason": row["reason"]}
                for row in inventory
                if not row["allowed"]
            ],
            "inventory": inventory,
        },
        "environment": {
            "policy": "the core toolset's allowlist scrub (child_environment)",
            "interpreter_flags": ["-I", "-u"] + ([] if policy.site_packages else ["-S"]),
            "imports": "stdlib only" if not policy.site_packages else "stdlib + site-packages",
            "note": "no ambient credentials: a credential-looking name is dropped and only "
            "named; PYTHONPATH/PYTHONSTARTUP/NODE_OPTIONS are dropped by design",
            "scrub": _environment_scrub(policy),
        },
        "filesystem": {
            "workspace": str(policy.workspace),
            "rule": "the tools address the workspace root only (path escapes, '~' and "
            "symlinks out of the root are refused); the runner writes nothing",
            "workspace_digest": workspace_digest(policy.workspace),
        },
        "protocol": {
            "transport": "line-delimited JSON over a socket pair (one extra fd)",
            "api": "skaldir.call(name, **arguments) -> the tool's result document",
            "refusal_shape": "ok=false + error_kind + refused_by; never an exception",
            "value_source": "the last expression, else the global `result`",
        },
        "call_path": "tools_core.registry.call (the one execution seam) + toolcall."
        "judge_tool_call (the pre-execution policy)",
        "isolation": _isolation_note(),
        "limits_not_claimed": [
            "no seccomp/landlock jail of the child process",
            "bubblewrap isolation is the toolset's, per tool call, not this runner's",
            "a snippet that daemonises with setsid leaves the process group",
            "terminal can still reach the network even with --no-network; its own isolation "
            "is what constrains it",
            "site-packages are off the child's sys.path by default (-S), so 'import skaldir' "
            "is the host API and no tool is importable; a snippet that deliberately re-adds "
            "the product's own directory to sys.path could still import it — a real jail "
            "needs a container, and --allow-site-packages widens this on purpose",
        ],
    }
    try:
        from .tools_core.contract import CORE_VERSION

        report["toolset_version"] = CORE_VERSION
    except Exception:  # noqa: BLE001
        report["toolset_version"] = ""
    return report


def _environment_scrub(policy: CodeExecPolicy) -> dict[str, Any]:
    """The names the child would keep/drop (never the values) — from the same policy."""
    try:
        from .tools_core.sandbox import child_environment

        _env, report = child_environment(policy.workspace)
    except Exception as exc:  # noqa: BLE001 — reported, never assumed
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "policy": str(report.get("policy", "")),
        "kept": list(report.get("kept") or []),
        "dropped_credentials": list(report.get("dropped_credentials") or []),
        "dropped_by_design": list(report.get("dropped_by_design") or []),
    }


def _isolation_note() -> dict[str, Any]:
    try:
        from .tools_core.sandbox import isolation_report

        available = isolation_report()
    except Exception as exc:  # noqa: BLE001
        return {"mode": "unknown", "available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "mode": str(available.get("mode", "process")),
        "available": bool(available.get("available")),
        "binary": str(available.get("binary", "")),
        "error": str(available.get("error", "")),
        "note": "per tool call (terminal --isolated), not applied to the snippet process",
    }
