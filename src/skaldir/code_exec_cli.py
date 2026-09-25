"""``skaldir code …`` — programmatic tool calling (H8) from the CLI.

Two verbs over :mod:`skaldir.code_exec`:

===========================  =========================================================
``code run --file|--stdin``  run a snippet in the sandbox: stdout, stderr, the
                             structured result (last expression or ``result``) and
                             every tool call it made (with duration and outcome)
``code explain``             the sandbox policy: limits, the allowed tools and why,
                             the credential scrub, the kill rule and the limits that
                             are *not* claimed
===========================  =========================================================

Exit codes are the product CLI's vocabulary (``cli_contract``):

    0   ok            the snippet ran and reported a result — *including* a snippet
                      that raised (`status: "error"` and its traceback are the
                      answer), and one that died silently (`status: "no_result"`)
    1   usage         neither/both of ``--file``/``--stdin``, or argparse refused
    2   verification  with ``--fail-on-error``: the snippet raised or reported nothing
    3   blocked       the sandbox cannot be honoured (the core toolset is missing, no
                      tool is allowed, the workspace is not a directory)
    5   not found     ``--file`` does not exist
    7   internal      an envelope problem
    8   unknown       the snippet hit the deadline and its process group was killed
                      (``status: "timeout"``, ``killed: true``)

Like ``tools-core call``, a *measured* failure is not this command's failure: the
snippet's own status travels in ``data.status``. ``--fail-on-error`` is the opt-in
that turns a raising snippet into exit 2 for a script.

**Integration (already wired in ``cli.py``).** The top-level name ``code`` is *shared*
with the repo-map block (``code.map`` / ``code.search`` / ``code.symbols`` / …), so the
group is created once and both verb sets attach to it. ``register_cli`` is
**create-or-extend**: it creates the group when it is absent and otherwise attaches
``run``/``explain`` to the group that is already there — it never returns early and
drops them. The wiring in ``cli.build_parser()`` reads::

    from .code_exec_cli import register_cli as _register_code_exec
    from .codectx_cli import register_cli as _register_codectx

    _code_group = _register_codectx(sub)          # create-or-extend: creates it
    _register_code_exec(sub)                      # …or extends the one that exists

``register_cli`` also installs its own ``cli_contract.COMMANDS`` entries through
``cli_shared.install_contract_entries`` (the same mechanism ``act_cli`` / ``host_cli`` /
``tools_core.cli`` use), so nothing has to be pasted into ``cli_contract.py``.
:func:`proposed_contract_entries` returns the identical objects for a reviewer who
prefers the literal table. Both paths are idempotent — calling either twice is a no-op,
which is what keeps the "conflicting subparser" trap from coming back.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import code_exec as CE
from .cli_shared import (
    dump,
    envelope_mode,
    product_contract,
    runtime_dir,
    share_common_flags,
    text_mode,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))
EXIT_UNKNOWN = int(getattr(_CONTRACT, "EXIT_UNKNOWN", 8))

_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")

#: True while ``python -m skaldir.code_exec_cli`` drives the verbs itself (no product
#: CLI envelope around them): then *this* module reports a refusal itself, because
#: nothing else will.
_STANDALONE_MODE = False
_JSON_REQUESTED = False


# --------------------------------------------------------------- envelope glue


def _publish_code_report(
    report: dict,
    *,
    evidence: list[dict],
    warnings: list[str],
    next_commands: list[tuple[str, str]],
) -> None:
    """Hand the report to the product envelope; a missing envelope is not an error.

    Each item is guarded on its own, so one malformed evidence record cannot cost the
    command its payload — the envelope is a reporting channel, not the result.
    """
    contract = _CONTRACT
    if contract is None or not hasattr(contract, "set_data"):
        return
    try:
        contract.set_data(report)
    except Exception:  # noqa: BLE001 — a payload must not be lost to an envelope bug
        return
    for item in evidence:
        try:
            kind = str(item.get("kind") or "code")
            contract.add_evidence(
                str(item.get("ref") or ""),
                kind if kind in _EVIDENCE_KINDS else "code",
                str(item.get("note") or ""),
            )
        except Exception:  # noqa: BLE001
            continue
    try:
        for note in warnings:
            contract.warn(str(note))
        for command, why in next_commands:
            contract.next_action(str(command), str(why))
    except Exception:  # noqa: BLE001
        return


def _refuse_code_run(
    code: int,
    message: str,
    *,
    hint: str = "",
    next_command: str = "",
    reason: str = "",
    details: dict | None = None,
) -> int:
    """Exit through the contract's own refusal helpers, or print to stderr standalone.

    Standalone (``python -m skaldir.code_exec_cli``) there is no envelope to carry the
    refusal, so the reason goes to stderr — and with ``--json`` one JSON document to
    stdout, because a machine reader must not be handed an empty stream.
    """
    if _STANDALONE_MODE:
        sys.stderr.write(f"{message}\n")
        if _JSON_REQUESTED:
            dump(
                {
                    "ok": False,
                    "exit_code": int(code),
                    "error": message,
                    "hint": hint,
                    "reason": reason,
                    "details": details or {},
                }
            )
    contract = _CONTRACT
    helper = ""
    if code == EXIT_BLOCKED:
        helper = "blocked"
    elif code == EXIT_NOT_FOUND:
        helper = "not_found"
    elif code == EXIT_VERIFICATION_FAILED:
        helper = "verification_failed"
    elif code == EXIT_USAGE:
        helper = "usage_error"
    if contract is not None and helper and hasattr(contract, helper):
        call = getattr(contract, helper)
        if helper == "blocked":
            return int(
                call(message, hint=hint, next_command=next_command, reason=reason, details=details)
            )
        if helper == "usage_error":
            return int(call(message, hint=hint))
        return int(call(message, hint=hint, details=details))
    return int(code)


# --------------------------------------------------------------------- render


def _note_code_failure(code: int, message: str, *, hint: str = "") -> None:
    """Record a non-zero verdict's *reason* so the envelope's error is not just a code."""
    contract = _CONTRACT
    if contract is None or not hasattr(contract, "fail"):
        return
    try:
        contract.fail(int(code), message, hint=hint)
    except Exception:  # noqa: BLE001 — a reporting detail must not change the verdict
        return


def _render_code_text(report: dict) -> str:
    """The human view of one run (stdout is the JSON document in the machine view)."""
    lines = [
        f"status: {report.get('status')}  ({report.get('duration_ms')} ms, "
        f"exit code {report.get('exit_code')})"
    ]
    if report.get("reason"):
        lines.append(f"  {report['reason']}")
    budget = report.get("tool_budget") or {}
    lines.append(
        f"tool calls: {budget.get('used', 0)}/{budget.get('max', 0)} "
        f"(refused {budget.get('refused', 0)})"
    )
    for call in report.get("tool_calls") or []:
        outcome = call.get("refused_by") or call.get("error_kind")
        mark = "ok" if call.get("ok") else f"refused:{outcome}"
        lines.append(
            f"  {call.get('index')}. {call.get('tool'):<14} {mark:<10} "
            f"{call.get('duration_ms')} ms  [{call.get('policy_classification') or '-'}] "
            f"{call.get('result_bytes')} B"
        )
    if report.get("value_source") != "none":
        lines.append(f"result ({report.get('value_source')}):")
        lines.append(_indent(_jsonish(report.get("value"))))
    elif report.get("value_repr"):
        lines.append(f"result (not JSON-serializable): {str(report.get('value_repr'))[:400]}")
    for stream in ("stdout", "stderr"):
        text = str(report.get(stream) or "")
        if text.strip():
            head = f"{stream}"
            if report.get(f"{stream}_truncated"):
                head += f" (truncated from {report.get(f'{stream}_total_bytes')} bytes)"
            lines.append(f"{head}:")
            lines.append(_indent(text.rstrip("\n")))
    if report.get("error_message"):
        lines.append(f"error: {report.get('error_type')}: {report.get('error_message')}")
        if report.get("traceback"):
            lines.append(_indent(str(report["traceback"])))
    for note in report.get("warnings") or []:
        lines.append(f"warning: {note}")
    if report.get("workspace_digest_before") and not report.get("workspace_unchanged"):
        lines.append(
            "note: the workspace digest changed during the run "
            f"({report.get('workspace_digest_before', '')[:12]} → "
            f"{report.get('workspace_digest_after', '')[:12]}) — a tool call wrote, "
            "see tool_calls"
        )
    return "\n".join(lines)


def _jsonish(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in str(text).splitlines())


# ------------------------------------------------------------------ arguments


def _add_run_arguments(parser: Any) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", default="", help="snippet file to run in the sandbox")
    source.add_argument(
        "--stdin", action="store_true", help="read the snippet from standard input"
    )
    parser.add_argument(
        "--workspace", default="", help="the sandbox's working directory (default: the cwd)"
    )
    parser.add_argument("--runtime", default="", help="the product runtime directory")
    parser.add_argument(
        "--timeout", type=int, default=CE.DEFAULT_TIMEOUT_S,
        help=f"wall clock in seconds (1..{CE.MAX_TIMEOUT_S}) before the process group is "
        "killed with SIGKILL",
    )
    parser.add_argument(
        "--max-output", type=int, default=CE.DEFAULT_MAX_OUTPUT_BYTES,
        help="cap for each of stdout/stderr (head+tail, with a marker)",
    )
    parser.add_argument(
        "--max-tool-calls", type=int, default=CE.DEFAULT_MAX_TOOL_CALLS,
        help="how many tool calls the snippet may attempt (a refusal counts too)",
    )
    parser.add_argument(
        "--max-result", type=int, default=CE.DEFAULT_MAX_RESULT_BYTES,
        help="cap for the structured result value",
    )
    parser.add_argument(
        "--allow-tool", action="append", default=[], metavar="NAME",
        help="restrict the sandbox to this tool (repeatable; default: the whole core set)",
    )
    parser.add_argument(
        "--deny-tool", action="append", default=[], metavar="NAME",
        help="take this tool out of the sandbox (repeatable)",
    )
    parser.add_argument(
        "--allow-network", action="store_true",
        help="declare network access for the snippet (web_fetch / web_search); "
        "off by default",
    )
    parser.add_argument(
        "--no-policy-gate", action="store_true",
        help="skip the pre-execution tool-call policy for this run (not recommended: "
        "the call then keeps only the tool's own checks)",
    )
    parser.add_argument(
        "--require-registered", action="store_true",
        help="refuse a tool the runtime registry has no row for",
    )
    parser.add_argument(
        "--allow-site-packages", action="store_true",
        help="WIDENS the sandbox: put site-packages back on the child's sys.path, so the "
        "snippet can import installed packages — including skaldir itself, which would let "
        "it call a tool without this run's policy, budget or evidence",
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the temporary run directory"
    )
    parser.add_argument(
        "--fail-on-error", action="store_true",
        help="exit 2 when the snippet raised or reported no result",
    )
    parser.add_argument(
        "--text", action="store_true", help="human-readable output instead of the JSON report"
    )


def _add_explain_arguments(parser: Any) -> None:
    parser.add_argument("--workspace", default="", help="the workspace the policy would use")
    parser.add_argument("--runtime", default="", help="the product runtime directory")
    parser.add_argument(
        "--timeout", type=int, default=CE.DEFAULT_TIMEOUT_S,
        help="the timeout the policy report should describe",
    )
    parser.add_argument("--max-tool-calls", type=int, default=CE.DEFAULT_MAX_TOOL_CALLS)
    parser.add_argument("--max-output", type=int, default=CE.DEFAULT_MAX_OUTPUT_BYTES)
    parser.add_argument("--max-result", type=int, default=CE.DEFAULT_MAX_RESULT_BYTES)
    parser.add_argument("--allow-tool", action="append", default=[], metavar="NAME")
    parser.add_argument("--deny-tool", action="append", default=[], metavar="NAME")
    parser.add_argument("--allow-network", action="store_true", help="show the policy with network")
    parser.add_argument(
        "--allow-site-packages", action="store_true",
        help="describe the widened policy (site-packages on the child's sys.path)",
    )
    parser.add_argument(
        "--runner", action="store_true", help="also print the child runner's source"
    )
    parser.add_argument(
        "--text", action="store_true", help="human-readable output instead of the JSON report"
    )


# ----------------------------------------------------------------------- verbs


def _workspace_of(args: argparse.Namespace) -> Path:
    """``--workspace``, else the toolset's declared workspace, else the cwd."""
    given = str(getattr(args, "workspace", "") or "").strip()
    if given:
        return Path(given).expanduser()
    try:
        from .env import getenv

        declared = getenv("TOOL_WORKSPACE") or ""
    except Exception:  # noqa: BLE001
        declared = ""
    return Path(declared).expanduser() if declared else Path.cwd()


def _policy_of(args: argparse.Namespace) -> CE.CodeExecPolicy:
    runtime = Path(str(getattr(args, "runtime", "") or "")) if getattr(args, "runtime", "") else (
        runtime_dir(args)
    )
    return CE.CodeExecPolicy(
        workspace=_workspace_of(args),
        runtime=runtime,
        timeout_s=int(getattr(args, "timeout", CE.DEFAULT_TIMEOUT_S)),
        max_output_bytes=int(getattr(args, "max_output", CE.DEFAULT_MAX_OUTPUT_BYTES)),
        max_tool_calls=int(getattr(args, "max_tool_calls", CE.DEFAULT_MAX_TOOL_CALLS)),
        max_result_bytes=int(getattr(args, "max_result", CE.DEFAULT_MAX_RESULT_BYTES)),
        allow_network=bool(getattr(args, "allow_network", False)),
        allow_tools=tuple(getattr(args, "allow_tool", []) or ()),
        deny_tools=tuple(getattr(args, "deny_tool", []) or ()),
        policy_gate=not bool(getattr(args, "no_policy_gate", False)),
        require_registered=bool(getattr(args, "require_registered", False)),
        site_packages=bool(getattr(args, "allow_site_packages", False)),
        keep_run_dir=bool(getattr(args, "keep", False)),
    ).normalized()


def _snippet_source(args: argparse.Namespace) -> tuple[str, str]:
    """The snippet text and where it came from (``("", reason)`` when unreadable)."""
    if bool(getattr(args, "stdin", False)):
        try:
            return sys.stdin.read(), "<stdin>"
        except OSError as exc:
            return "", f"standard input could not be read: {type(exc).__name__}: {exc}"
    path = Path(str(getattr(args, "file", "") or ""))
    try:
        return path.read_text(encoding="utf-8"), str(path)
    except FileNotFoundError:
        return "", f"missing:{path}"
    except OSError as exc:
        return "", f"unreadable:{path}: {type(exc).__name__}: {exc}"


def _run_evidence(report: dict, origin: str) -> list[dict]:
    """The evidence a produced run stands on: the child argv and every tool call."""
    evidence = [
        {
            "ref": " ".join(report.get("argv") or []),
            "kind": "run",
            "note": f"snippet {origin} in an isolated process (cwd "
            f"{(report.get('policy') or {}).get('workspace')}, "
            f"group {report.get('process_group')}, {report.get('duration_ms')} ms)",
        },
        {
            "ref": str((report.get("policy") or {}).get("workspace") or ""),
            "kind": "file",
            "note": f"workspace digest {str(report.get('workspace_digest_before'))[:12]} → "
            f"{str(report.get('workspace_digest_after'))[:12]} "
            f"({'unchanged' if report.get('workspace_unchanged') else 'changed by a tool call'})",
        },
    ]
    for call in report.get("tool_calls") or []:
        outcome = "ok" if call.get("ok") else f"{call.get('refused_by') or call.get('error_kind')}"
        evidence.append(
            {
                "ref": f"code.run tool#{call.get('index')} {call.get('tool')}",
                "kind": "run",
                "note": f"{outcome}, {call.get('duration_ms')} ms, "
                f"[{call.get('policy_classification') or '-'}] "
                f"args {str(call.get('arguments_sha256'))[:12]}",
            }
        )
    return evidence


def cmd_run(args: argparse.Namespace) -> int:
    """``skaldir code run`` — one sandboxed snippet, with its result and its calls."""
    source, origin = _snippet_source(args)
    if origin.startswith("missing:"):
        return _refuse_code_run(
            EXIT_NOT_FOUND,
            f"no such snippet file: {origin[len('missing:') :]}",
            hint="pass an existing --file, or --stdin to read the snippet from the pipe",
        )
    if origin.startswith("unreadable:"):
        return _refuse_code_run(EXIT_USAGE, origin[len("unreadable:") :], hint="check the path")
    if not source.strip():
        return _refuse_code_run(
            EXIT_USAGE,
            f"the snippet from {origin} is empty",
            hint="a snippet needs at least one statement",
        )
    policy = _policy_of(args)
    result = CE.run_code(source, policy=policy)
    report = result.to_dict()
    report["origin"] = origin
    if result.status == CE.STATUS_REFUSED:
        return _refuse_code_run(
            EXIT_BLOCKED,
            result.reason or "the sandbox could not honour this run",
            hint="fix what the reason names, then retry",
            next_command="skaldir code explain",
            reason="the sandbox policy and the allowed tool list are printed there",
            details={"policy": report.get("policy")},
        )
    if text_mode(args):
        print(_render_code_text(report))
    elif not envelope_mode():
        # inside the product driver the envelope carries the payload; printing it here
        # too would put a second copy on stderr.
        dump(report)
    warnings = [str(item) for item in report.get("warnings") or []]
    _publish_code_report(
        report,
        evidence=_run_evidence(report, origin),
        warnings=warnings,
        next_commands=[
            ("skaldir code explain", "the policy this run was executed under"),
            ("skaldir tools-core list", "the contracts of the tools the snippet could call"),
        ],
    )
    if result.status == CE.STATUS_TIMEOUT:
        _note_code_failure(
            EXIT_UNKNOWN,
            result.reason,
            hint="raise --timeout, or make the snippet terminate; the process group was killed",
        )
        return EXIT_UNKNOWN
    if bool(getattr(args, "fail_on_error", False)) and result.status != CE.STATUS_OK:
        _note_code_failure(
            EXIT_VERIFICATION_FAILED,
            f"the snippet did not produce a result (status {result.status})"
            + (f": {result.error_type}: {result.error_message}" if result.error_type else ""),
            hint="the run's report is in data (status, traceback, tool calls)",
        )
        return EXIT_VERIFICATION_FAILED
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    """``skaldir code explain`` — the sandbox policy, generated from the same objects."""
    policy = _policy_of(args)
    report = CE.policy_report(
        workspace=policy.workspace,
        runtime=policy.runtime,
        timeout_s=policy.timeout_s,
        max_output_bytes=policy.max_output_bytes,
        max_tool_calls=policy.max_tool_calls,
        max_result_bytes=policy.max_result_bytes,
        allow_network=policy.allow_network,
        allow_tools=policy.allow_tools,
        deny_tools=policy.deny_tools,
        policy_gate=policy.policy_gate,
        require_registered=policy.require_registered,
        site_packages=policy.site_packages,
    )
    if bool(getattr(args, "runner", False)):
        report["runner_source"] = CE.runner_source()
    if text_mode(args):
        print(_render_policy_text(report))
    elif not envelope_mode():
        dump(report)
    allowed = (report.get("tools") or {}).get("allowed") or []
    _publish_code_report(
        report,
        evidence=[
            {
                "ref": "src/skaldir/code_exec.py policy_report",
                "kind": "code",
                "note": f"{len(allowed)} tool(s) allowed, "
                f"{len((report.get('tools') or {}).get('refused') or [])} refused",
            },
            {
                "ref": "src/skaldir/tools_core/sandbox.py child_environment",
                "kind": "code",
                "note": "the credential scrub this policy declares (names only, never values)",
            },
        ],
        warnings=[],
        next_commands=[
            ("skaldir code run --stdin", "run a snippet under exactly this policy"),
            ("skaldir tools-core policy", "the per-tool permissions behind the allowlist"),
        ],
    )
    return EXIT_OK


def _render_policy_text(report: dict) -> str:
    limits = report.get("limits") or {}
    tools = report.get("tools") or {}
    lines = [
        f"sandbox workspace: {(report.get('policy') or {}).get('workspace')}",
        f"limits: {limits.get('wall_clock_s')}s wall clock · "
        f"{limits.get('tool_calls')} tool calls · "
        f"{limits.get('stdout_bytes')} B per stream · "
        f"{limits.get('result_bytes')} B result",
        f"kill: {limits.get('kill')}",
        f"allowed tools ({len(tools.get('allowed') or [])}): "
        + ", ".join(tools.get("allowed") or []),
    ]
    for row in tools.get("refused") or []:
        lines.append(f"  refused: {row.get('name')} — {row.get('reason')}")
    lines.append(f"call path: {report.get('call_path')}")
    for note in report.get("limits_not_claimed") or []:
        lines.append(f"not claimed: {note}")
    return "\n".join(lines)


# ------------------------------------------------------------ parser + wiring


def _add_code_verbs(sub: Any) -> Any:
    """Add the two ``code`` verbs to a subparsers handle (one definition)."""
    p_run = sub.add_parser(
        "run",
        help="run a snippet in the sandbox with the tool API inside "
        "(skaldir.call('read_file', path=...))",
    )
    _add_run_arguments(p_run)
    p_run.set_defaults(func=cmd_run, command_path="code.run")

    p_explain = sub.add_parser("explain", help="the sandbox policy: limits, allowlist, proof")
    _add_explain_arguments(p_explain)
    p_explain.set_defaults(func=cmd_explain, command_path="code.explain")

    for parser in (p_run, p_explain):
        share_common_flags(parser)
    return sub


def proposed_contract_entries() -> dict[str, Any]:
    """The ``COMMANDS`` entries this group installs (identical objects)."""
    if _CONTRACT is None:
        return {}
    spec = _CONTRACT.CommandSpec

    def ref(path: str, note: str = "") -> dict:
        return {"ref": path, "kind": "code", "note": note}

    exec_ref = ref("src/skaldir/code_exec.py:run_code")
    policy_ref = ref("src/skaldir/code_exec.py:policy_report")
    return {
        "code.run": spec(
            "COMPOSE",
            (
                exec_ref,
                ref("src/skaldir/tools_core/registry.py:call"),
                ref("src/skaldir/tools_core/sandbox.py:child_environment"),
            ),
            "run one snippet in a sandboxed process; the tool API inside composes tool "
            "calls and the run returns stdout/stderr, a structured value and every call",
            mutates=True,
            exit_codes=(0, 1, 2, 3, 5, 7, 8),
            data_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "value": {},
                    "value_source": {"type": "string"},
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "tool_calls": {"type": "array"},
                    "tool_budget": {"type": "object"},
                    "workspace_unchanged": {"type": "boolean"},
                },
                "required": ["status", "stdout", "stderr", "tool_calls", "tool_budget"],
            },
            notes="H8: a snippet's tool calls go through tools_core.registry.call plus the "
            "pre-execution policy (toolcall.judge_tool_call) in the host process, each "
            "recorded with duration and outcome; a measured failure (a raising snippet, a "
            "refused call) keeps exit 0 — --fail-on-error turns a non-ok status into 2, and a "
            "timeout kill exits 8",
        ),
        "code.explain": spec(
            "COMPOSE",
            (policy_ref, ref("src/skaldir/tools_core/contract.py:TruncationRule")),
            "the sandbox policy: limits, the allowed tool list and why, the credential "
            "scrub, the kill rule and the limits that are not claimed",
            exit_codes=(0, 1, 3, 7),
            data_schema={"type": "object", "additionalProperties": True},
            notes="read-only: the report is generated from the same objects the runner "
            "enforces, so the documented policy cannot drift from the executed one",
        ),
    }


def _verb_action(parser: Any) -> Any:
    """The subparsers action a group parser already has (or ``None``)."""
    return next(
        (
            item
            for item in getattr(parser, "_actions", [])
            if isinstance(item, argparse._SubParsersAction)
        ),
        None,
    )


def register_cli(subparsers: Any) -> Any:
    """Create the ``code`` group — or extend the one that already exists.

    The top-level name ``code`` is shared with the repo-map block (``code.map``,
    ``code.search``, …), so this must be **create-or-extend**, never
    "return early and drop my verbs": whichever module runs first creates the group and
    the other attaches its verb set to the same command action. Dropping either set
    silently would be a lie in the contract dump.
    """
    from .cli_shared import as_subparsers, install_contract_entries

    sub = as_subparsers(subparsers)
    parent = getattr(sub, "choices", {}).get("code")
    if parent is None:
        parent = sub.add_parser(
            "code",
            help="programmatic tool calling: run a snippet that calls tools in code (H8)",
            description=(
                "The agent writes code instead of one tool call at a time. The snippet runs "
                "in its own process with a scrubbed environment and reaches the registered "
                "tools through skaldir.call(name, **arguments); every call keeps the normal "
                "permission checks, the run's budget and its own evidence. The last "
                "expression of a snippet (or a global `result`) is the structured value."
            ),
        )
    verbs = _verb_action(parent)
    if verbs is None:
        verbs = parent.add_subparsers(dest="code_command", required=True)
    if "run" not in getattr(verbs, "choices", {}):
        _add_code_verbs(verbs)
    for parser in (parent, verbs):
        try:
            share_common_flags(parser)
        except Exception:  # noqa: BLE001 — the flags are a convenience, not the contract
            continue
    install_contract_entries(proposed_contract_entries(), _CONTRACT)
    return parent


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(
        prog="skaldir code", description="programmatic tool calling (sandboxed snippets)"
    )
    verbs = parser.add_subparsers(dest="code_command", required=True)
    _add_code_verbs(verbs)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir code …`` — returns the exit code, never raises for a request."""
    global _JSON_REQUESTED, _STANDALONE_MODE
    invoked = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not invoked:
        parser.print_help()
        return EXIT_USAGE
    _STANDALONE_MODE = True
    _JSON_REQUESTED = "--json" in invoked
    try:
        args = parser.parse_args(invoked)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(args, "func", None)
    return int(handler(args)) if handler is not None else EXIT_USAGE


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_parser",
    "cmd_explain",
    "cmd_run",
    "main",
    "proposed_contract_entries",
    "register_cli",
]


if __name__ == "__main__":  # pragma: no cover — a standalone run, not a product path
    raise SystemExit(main())
