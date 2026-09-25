"""The typed model adapter for :func:`skaldir.code_exec.run_code` — CORE-06.

The audit (F02) measured it precisely: the Python runner works (``ok=true, value=10``,
stdout correct, errors/budget/timeout covered by tests) and is offered by **no** model
toolset. So the engine exists and the *adapter* is missing — which the assignment calls an
implementation gap even when the engine works on its own.

What this is and is not:

* **It is a thin adapter.** Execution is delegated to ``code_exec.run_code``. There is no
  second Python interpreter here — building one would be exactly the "second runner" the
  assignment forbids.
* **It is context-bound.** Workspace, runtime, run id and policy come from the caller and
  are passed through, so two concurrent runs cannot share a cwd by accident (F03).
* **It is honest about isolation.** ``code_exec`` is not an OS sandbox — the runner
  documents that it has no filesystem cage, and ``-I``/``-S`` plus a cwd are not one. The
  tool says so in its own result instead of implying containment.
* **It reports the failure shape.** ``ok``, ``value``, ``stdout``, ``stderr``, an error
  name, a traceback, artefact references and the duration — so a caller can tell "the code
  raised" from "the tool failed" from "the budget ran out".
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

__all__ = ["run_code_tool", "CODE_TOOL_NAME"]

CODE_TOOL_NAME = "run_code"

#: What the model sees. Deliberately small: source, language, and the budgets that
#: actually change behaviour. Everything else is the run's own context.
CODE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "minLength": 1,
            "description": "the code to run (Python in this release)",
        },
        "language": {
            "type": "string",
            "enum": ["python"],
            "default": "python",
            "description": "only python is executed here; other runtimes go through the terminal tool",
        },
        "timeout_s": {
            "type": "number",
            "minimum": 1,
            "maximum": 120,
            "description": "wall-clock budget for this run (the runner caps at 120s)",
        },
        "max_output_bytes": {
            "type": "integer",
            "minimum": 256,
            "maximum": 200000,
            "description": "cap on returned stdout/stderr",
        },
    },
    "required": ["source"],
}


#: ``CodeResult`` field -> the name this tool reports. Measured against the real result
#: object rather than guessed: the runner says ``error_type``/``traceback_text``, not
#: ``error``/``traceback``.
_FIELD_MAP = (
    ("value", "value"),
    ("value_repr", "value_repr"),
    ("value_source", "value_source"),
    ("stdout", "stdout"),
    ("stderr", "stderr"),
    ("status", "status"),
    ("reason", "reason"),
    ("error_type", "error"),
    ("error_message", "error_message"),
    ("traceback_text", "traceback"),
    ("exit_code", "exit_code"),
    ("timed_out_after_s", "timed_out_after_s"),
    ("killed", "killed"),
    ("tool_calls", "tool_calls"),
    ("warnings", "warnings"),
    ("stdout_truncated", "stdout_truncated"),
    ("stderr_truncated", "stderr_truncated"),
    ("workspace_digest_before", "workspace_digest_before"),
    ("workspace_digest_after", "workspace_digest_after"),
)


def _result(payload: Mapping[str, Any], *, started: float, **extra: Any) -> dict[str, Any]:
    """Normalise a CodeResult into the shape the agent transcript expects.

    A raised exception becomes ``ok=false`` **with** the error name and the traceback, so a
    caller can tell "the code raised" from "the tool failed" from "the budget ran out" —
    which is what makes the failure handling real instead of a boolean.
    """
    out: dict[str, Any] = {
        "ok": bool(payload.get("ok")),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }
    for src, dst in _FIELD_MAP:
        if src in payload:
            out[dst] = payload[src]
    # a non-zero command exit must not read as success in the verification path
    if out.get("exit_code") not in (None, 0):
        out["ok"] = False
    out.update(extra)
    return out


def run_code_tool(
    *,
    workspace: str | Path,
    runtime: str | Path | None = None,
    run_id: str = "",
    policy: Any = None,
) -> Any:
    """Build the ``run_code`` agent tool, bound to one run's context.

    ``workspace``/``runtime``/``run_id``/``policy`` are captured **here**, at build time,
    so every call through this tool uses the run's own context. That is the F03 fix at the
    tool layer: a handler that closes over the wrong globals is what lost the workspace.
    """
    from .agent_loop import AgentTool

    ws = Path(workspace).expanduser().resolve()
    rt = Path(runtime).expanduser().resolve() if runtime else None

    def handler(source: str = "", language: str = "python", timeout_s: float | None = None,
                max_output_bytes: int | None = None, **_ignored: Any) -> dict[str, Any]:
        started = time.monotonic()
        code = (source or "").strip()
        if not code:
            return {"ok": False, "error": "usage", "reason": "no source given"}

        lang = (language or "python").strip().lower()
        if lang not in ("python", "py"):
            return {
                "ok": False,
                "error": "unsupported_language",
                "reason": (f"{lang!r} is not executed by this tool — run other runtimes "
                           "through the terminal tool"),
            }

        from . import code_exec

        overrides: dict[str, Any] = {}
        if timeout_s is not None:
            overrides["timeout_s"] = float(timeout_s)
        if max_output_bytes is not None:
            overrides["max_output_bytes"] = int(max_output_bytes)

        try:
            result = code_exec.run_code(
                code,
                policy=policy,
                workspace=ws,
                runtime=rt,
                **overrides,
            )
        except TypeError:
            # an older/looser signature: retry without the optional budgets rather than
            # fail on a keyword the runner does not know
            result = code_exec.run_code(code, policy=policy, workspace=ws, runtime=rt)
        except Exception as exc:  # noqa: BLE001 — a tool error is data, not a crash
            return {
                "ok": False,
                "error": type(exc).__name__,
                "reason": str(exc)[:400],
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }

        payload = result if isinstance(result, Mapping) else {
            "ok": getattr(result, "ok", False),
            **{src: getattr(result, src, None) for src, _ in _FIELD_MAP},
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        out = _result(payload, started=started)
        out["workspace"] = str(ws)
        out["run_id"] = run_id
        out["isolation"] = (
            "none — direct Python runs on the host workspace. This is NOT an OS "
            "sandbox: the runner has no filesystem cage and `-I`/`-S` plus a cwd do "
            "not provide one. Effects outside `skaldir.call` are not automatically "
            "individually approved."
        )
        return out

    return AgentTool(
        name=CODE_TOOL_NAME,
        description=(
            "Run Python code in the run's own workspace and return the value of a "
            "variable named `result`, plus stdout/stderr. Use it for calculations and "
            "data work instead of writing a script and shelling out. Failures are "
            "returned with the error name and traceback — a raised exception is data, "
            "not a crash. Not an OS sandbox."
        ),
        input_schema=CODE_INPUT_SCHEMA,
        handler=handler,
        parallel_safe=False,     # it can write to the workspace; run it alone
        mutating=True,
        source="builtin",
        source_name="skaldir.code_exec",
    )
