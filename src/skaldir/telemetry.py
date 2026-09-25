"""Telemetry: duration, tokens/second, context usage, cost — labelled, honest, exportable.

A run's transcript (``<state_dir>/runs/<run-id>.jsonl``) already records every fact
about the run: the provider it used, each request body verbatim, each answer with its
finish reason and the usage the provider reported, each tool call with its duration, and
the final ``run.finished`` with the totals. This module is the *reader* that turns those
facts into the standard display a normal coding agent shows: how long the run took, how
fast it generated, how full the context is, and what it cost.

Four rules, and they are the whole point of the module:

1. **Every number carries its source.** A provider-reported token count is labelled
   ``provider``; anything we computed is labelled ``estimate`` *and names the method*
   (``chars/4``, the loop's own fallback estimator — no tokenizer is bundled). There is
   no path that returns a number without saying where it came from.
2. **A missing number is ``null`` with a reason, never a plausible-looking fake.** An
   unpriced run reports ``cost.usd: null`` with the declaration that would fix it —
   never ``0.0``, which reads as "free". ``record.nulls`` is the index of every such
   field, so a consumer can find them without trusting prose.
3. **Tokens/second is measured over a named window.** The window is the sum of the
   ``model.response.duration_ms`` values (the loop's monotonic clock around the provider
   call, streaming included) of the very turns whose completion tokens are counted, and
   the method string says so. A zero-length window yields ``null``, not a division.
4. **The record lands next to the transcript.** ``build_record`` derives it, ``save``
   writes ``<runs>/<run-id>.telemetry.json``, and :func:`show_record` serves it back —
   so ``skaldir telemetry show <run-id>`` works after the fact, and a run whose
   transcript was rotated away still reports what was recorded (labelled ``persisted``).

Surfaces:

* :func:`footer_line` — the one-line shell footer: ``model · ctx 34% · 1.2k tok/s · 02:14``
  (an estimated number is written with a leading ``~``, an unavailable one as ``n/a``).
* :func:`turn_line` — the per-turn line a shell prints after each agent turn.
* :class:`TelemetryWatcher` / :func:`follow` — the live reader: it tails the transcript
  file while the run writes it, so the footer and the turn lines appear *during* a run
  without the loop knowing anything about telemetry.
* ``skaldir telemetry show|live|export`` (:mod:`skaldir.telemetry_cli`).

Nothing here writes into a run; the only file this module creates is the record beside
the transcript, and only when the caller asks for it.
"""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import env as _env

__all__ = [
    "DEFAULT_CONTEXT_BUDGET",
    "DEFAULT_TRIGGER_FRACTION",
    "ENV_PRICE_PER_1K",
    "LEGACY_FIELD_NOTE",
    "TELEMETRY_SCHEMA_VERSION",
    "TelemetryError",
    "TelemetryRecord",
    "TelemetryWatcher",
    "ToolSpan",
    "Turn",
    "build_record",
    "build_record_from_events",
    "estimate_chars4",
    "export_text",
    "follow",
    "footer_line",
    "format_duration",
    "format_tokens_short",
    "iter_runs",
    "load_record",
    "read_events",
    "record_from_dict",
    "record_path_for",
    "resolve_budget",
    "resolve_price",
    "resolve_run_path",
    "resolve_trigger_fraction",
    "save_record",
    "shell_footer",
    "show_record",
    "turn_line",
]

#: Bumped when the record's shape changes in a way a consumer must notice.
TELEMETRY_SCHEMA_VERSION = "1"

#: The sentence that must ride with every estimate this module prints.
LEGACY_FIELD_NOTE = (
    "token counts are an estimate when the provider reported none: chars/4 over the exact "
    "request body the loop sent (its own fallback estimator — no tokenizer is bundled); "
    "every number here says which of the two it is"
)

#: Same numbers ``skaldir.context_manager`` uses, restated so this module can run
#: standalone; the importable copy wins whenever it is there (see :func:`_context_home`).
DEFAULT_CONTEXT_BUDGET = 128_000
DEFAULT_TRIGGER_FRACTION = 0.75

#: A price the operator declares for this model, in USD per 1000 tokens of any kind.
#: ``SKALDIR_TELEMETRY_PRICE_PER_1K`` (legacy ``GODSV_…``) — read through
#: :mod:`skaldir.env`, never from ``os.environ``.
ENV_PRICE_PER_1K = "TELEMETRY_PRICE_PER_1K"

#: Environment names owned by the compaction layer; read here, never re-invented.
ENV_CONTEXT_BUDGET = "CONTEXT_BUDGET"
ENV_TRIGGER_FRACTION = "CONTEXT_TRIGGER_FRACTION"

EXIT_BLOCKED = 3
EXIT_NOT_FOUND = 5
EXIT_INTERNAL = 7

NUMBER_SOURCES = (
    "provider",
    "estimate",
    "declared",
    "default",
    "env",
    "flag",
    "transcript",
)


class TelemetryError(RuntimeError):
    """This record cannot be built (with the exit code the CLI should use)."""

    def __init__(self, message: str, *, code: str = "internal") -> None:
        super().__init__(message)
        self.code = str(code)


# --------------------------------------------------------------------------- #
# small honest formatters                                                      #
# --------------------------------------------------------------------------- #


def estimate_chars4(text: str) -> int:
    """The loop's own fallback estimate (``skaldir.llm.estimate_tokens``), reused."""
    try:
        from .llm import estimate_tokens
    except Exception:  # noqa: BLE001 — the same arithmetic, stated identically
        return max(1, len(text) // 4)
    return int(estimate_tokens(text))


def format_duration(duration_ms: int | float | None) -> str:
    """``02:14`` / ``1:02:14`` — ``null`` becomes ``n/a``, never ``00:00``."""
    if duration_ms is None:
        return "n/a"
    total = max(0, int(round(float(duration_ms) / 1000.0)))
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_tokens_short(value: int | float | None) -> str:
    """``1234`` → ``1.2k``; ``None`` → ``n/a``."""
    if value is None:
        return "n/a"
    number = float(value)
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if abs(number) >= 1000:
        return f"{number / 1000:.1f}k"
    return f"{number:.0f}"


def format_percent(value: float | None) -> str:
    """``33.6`` → ``34%``; a small non-zero value is ``<1%``, never ``0%``."""
    if value is None:
        return "n/a"
    if 0 < float(value) < 1:
        return "<1%"
    return f"{float(value):.0f}%"


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _mark(source: str) -> str:
    """``~`` in front of a number that is not the provider's own measurement."""
    return "~" if str(source).startswith("estimate") else ""


def _number(value: Any, source: str, method: str = "", reason: str = "") -> dict[str, Any]:
    """One number with its provenance — the only shape this module emits."""
    if value is None:
        return {"value": None, "source": source, "method": method, "reason": reason}
    return {
        "value": value,
        "source": source,
        "method": method,
        "reason": "",
        "estimate": str(source).startswith("estimate"),
    }


# --------------------------------------------------------------------------- #
# reading a transcript                                                         #
# --------------------------------------------------------------------------- #


def read_events(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Every JSONL event of a transcript, plus a warning per unreadable line.

    The file is read whole and line by line: a run killed mid-flight has a complete last
    line only (the loop writes one line per fact, with a newline), and a torn line is
    reported instead of being parsed into a guess.
    """
    file = Path(path).expanduser()
    if not file.is_file():
        raise TelemetryError(f"no transcript at {file}", code="not_found")
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise TelemetryError(f"cannot read {file}: {exc}", code="internal") from exc
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            warnings.append(f"line {number} of {file.name} is not JSON — skipped")
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
        else:
            warnings.append(f"line {number} of {file.name} is not an event object — skipped")
    if not events:
        raise TelemetryError(
            f"{file} holds no events — there is nothing to report", code="blocked"
        )
    return events, warnings


def resolve_run_path(state_dir: str | Path, run_id: str) -> Path:
    """``<state_dir>/runs/<run-id>.jsonl`` — the loop's own path rule, reused."""
    from .agent_loop import AgentLoopError, run_path

    try:
        return run_path(state_dir, run_id)
    except AgentLoopError as exc:
        raise TelemetryError(str(exc), code="not_found") from exc


def record_path_for(transcript: str | Path) -> Path:
    """``<runs>/<run-id>.telemetry.json`` — the record beside the transcript.

    An in-memory record (the live watcher before it knows a path) has no file, so the
    empty path stays empty rather than becoming a name invented from nothing.
    """
    file = Path(str(transcript)).expanduser()
    if not file.name:
        return Path("")
    return file.with_suffix(".telemetry.json")


# --------------------------------------------------------------------------- #
# the record                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class ToolSpan:
    """One model-requested tool call, as the transcript measured it."""

    name: str
    step: int
    duration_ms: int
    ok: bool
    executed: bool
    parallel: bool
    decision: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "step": self.step,
            "duration_ms": int(self.duration_ms),
            "ok": bool(self.ok),
            "executed": bool(self.executed),
            "parallel": bool(self.parallel),
            "decision": self.decision,
        }


@dataclass
class Turn:
    """One model call: its request, its answer, the usage, the wall window, its tools."""

    index: int
    step: int
    at: str = ""
    duration_ms: int | None = None
    finish_reason: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_source: str = "estimate"
    tokens_method: str = LEGACY_FIELD_NOTE
    tokens_per_second: float | None = None
    tokens_per_second_reason: str = ""
    context_percent: float | None = None
    context_used_tokens: int | None = None
    context_source: str = ""
    messages: int | None = None
    tool_calls: tuple[ToolSpan, ...] = ()
    answered: bool = True

    @property
    def tokens_total(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return int(self.prompt_tokens) + int(self.completion_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "step": self.step,
            "at": self.at,
            "answered": bool(self.answered),
            "duration_ms": self.duration_ms,
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_total": self.tokens_total,
            "tokens_source": self.tokens_source,
            "tokens_method": self.tokens_method,
            "tokens_per_second": self.tokens_per_second,
            "tokens_per_second_reason": self.tokens_per_second_reason,
            "context_used_tokens": self.context_used_tokens,
            "context_percent": self.context_percent,
            "context_source": self.context_source,
            "messages": self.messages,
            "tool_call_count": len(self.tool_calls),
            "tool_calls": [span.to_dict() for span in self.tool_calls],
        }


@dataclass
class TelemetryRecord:
    """One run's standard displays, every number with its provenance."""

    run_id: str
    session_id: str = ""
    transcript: str = ""
    record_path: str = ""
    schema_version: str = TELEMETRY_SCHEMA_VERSION
    status: str = ""
    reason: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int | None = None
    duration_source: str = ""
    turns: tuple[Turn, ...] = ()
    tool_calls: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, Any] = field(default_factory=dict)
    tokens_per_second: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    provider: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    #: §94 — the token levers, one by one (savings_block): prompt cache, archived tool
    #: results, compaction, brief-vs-transcript, reasoning tokens.
    savings: dict[str, Any] = field(default_factory=dict)
    workspace: str = ""
    task: str = ""
    estimates: tuple[str, ...] = ()
    nulls: tuple[dict[str, str], ...] = ()
    warnings: tuple[str, ...] = ()
    source: str = "transcript"

    # -- projections ------------------------------------------------------- #

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def model_display(self) -> str:
        if self.model:
            return self.model
        return "model unknown"

    def footer(self) -> str:
        """The one-line footer the shell keeps on screen."""
        return footer_line(self)

    def turn_lines(self) -> list[str]:
        """One line per turn, in order."""
        return [turn_line(turn, self.turn_count) for turn in self.turns]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "transcript": self.transcript,
            "record_path": self.record_path,
            "source": self.source,
            "task": self.task,
            "workspace": self.workspace,
            "status": self.status,
            "reason": self.reason,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "duration": format_duration(self.duration_ms),
            "duration_source": self.duration_source,
            "turns": self.turn_count,
            "turn_rows": [turn.to_dict() for turn in self.turns],
            "tool_calls": dict(self.tool_calls),
            "tokens": dict(self.tokens),
            "tokens_per_second": dict(self.tokens_per_second),
            "context": dict(self.context),
            "model": self.model,
            "provider": dict(self.provider),
            "cost": dict(self.cost),
            "limits": dict(self.limits),
            "savings": dict(self.savings),
            "footer": self.footer(),
            "estimates": list(self.estimates),
            "nulls": [dict(item) for item in self.nulls],
            "warnings": list(self.warnings),
        }

    def lines(self) -> list[str]:
        """The human view: one block a terminal can read top to bottom."""
        out = [
            f"run {self.run_id} · {self.status}"
            + (f" · {self.reason}" if self.reason else "")
            + ("" if self.source == "transcript" else f" · from {self.source}"),
            f"  model    {self.model_display}"
            + (f" · {self.provider.get('provider')}" if self.provider.get("provider") else ""),
            f"  duration {format_duration(self.duration_ms)} ({self.duration_source})",
            f"  turns    {self.turn_count}"
            + f" · tool calls {self.tool_calls.get('count', 0)}"
            f" ({self.tool_calls.get('total_duration_ms', 0)} ms in tools)",
            "  tokens   "
            + _tokens_line(self.tokens.get("prompt"), "prompt")
            + " · "
            + _tokens_line(self.tokens.get("completion"), "completion")
            + " · "
            + _tokens_line(self.tokens.get("total"), "total"),
            f"  speed    {_speed_line(self.tokens_per_second)}",
            f"  context  {_context_line(self.context)}",
            f"  cost     {_cost_line(self.cost)}",
        ]
        for line in _savings_lines(self.savings):
            out.append(line)
        for line in self.turn_lines():
            out.append(f"  {line}")
        if self.estimates:
            out.append(f"  estimates {', '.join(self.estimates)}")
        for item in self.nulls:
            out.append(f"  null     {item['field']}: {item['reason']}")
        for warning in self.warnings:
            out.append(f"  warning  {warning}")
        return out


def _tokens_line(block: dict[str, Any] | None, label: str) -> str:
    block = dict(block or {})
    if block.get("value") is None:
        return f"{label} n/a"
    return f"{label} {_mark(block.get('source', ''))}{block['value']}"


def _speed_line(block: dict[str, Any] | None) -> str:
    block = dict(block or {})
    if block.get("value") is None:
        return f"tokens/s n/a ({block.get('reason') or 'not measurable'})"
    return (
        f"{_mark(block.get('source', ''))}{block['value']:.1f} tokens/s over "
        f"{block.get('window_ms', 0)} ms — {block.get('method', '')}"
    )


def _context_line(block: dict[str, Any] | None) -> str:
    block = dict(block or {})
    if block.get("percent") is None:
        return f"not measurable ({block.get('reason') or 'no budget declared'})"
    return (
        f"{_mark(block.get('used_source', ''))}{block['used_tokens']} of {block['total_tokens']} "
        f"({format_percent(block['percent'])}, {block.get('used_source')}) · compacts at "
        f"{block['trigger_tokens']} ({block['trigger_fraction']:.2f} × {block['total_source']}) "
        f"· {block.get('state')}"
    )


def _cost_line(block: dict[str, Any] | None) -> str:
    block = dict(block or {})
    if block.get("usd") is None:
        return f"null ({block.get('reason') or 'no price declared'})"
    return (
        f"${block['usd']:.6f} at {block['price_per_1k_tokens']} USD/1k tokens "
        f"({block.get('price_source')}, {block.get('tokens_used')} tokens)"
    )


# --------------------------------------------------------------------------- #
# the standard displays                                                        #
# --------------------------------------------------------------------------- #


def footer_line(record: TelemetryRecord) -> str:
    """``model · ctx 34% · 1.2k tok/s · 02:14`` — ``~`` marks an estimate.

    An unavailable part is written as ``n/a`` instead of being dropped: a footer that
    silently omits the speed would read as "not applicable", which is a different claim.
    """
    context = dict(record.context or {})
    speed = dict(record.tokens_per_second or {})
    if context.get("percent") is None:
        context_part = "ctx n/a"
    else:
        context_part = (
            f"ctx {_mark(context.get('used_source', ''))}{format_percent(context['percent'])}"
        )
    if speed.get("value") is None:
        speed_part = "tok/s n/a"
    else:
        speed_part = (
            f"{_mark(speed.get('source', ''))}{format_tokens_short(speed['value'])} tok/s"
        )
    return " · ".join(
        [record.model_display, context_part, speed_part, format_duration(record.duration_ms)]
    )


def turn_line(turn: Turn, total: int) -> str:
    """One turn, as a shell prints it after each agent turn."""
    parts = [f"turn {turn.index}/{total}" if total else f"turn {turn.index}"]
    if turn.duration_ms is not None:
        parts.append(f"{int(turn.duration_ms)} ms")
    if turn.completion_tokens is None:
        parts.append("out n/a")
    else:
        parts.append(
            f"out {_mark(turn.tokens_source)}{turn.completion_tokens} tok "
            f"({turn.tokens_source})"
        )
    if turn.tokens_per_second is None:
        parts.append("tok/s n/a")
    else:
        parts.append(
            f"{_mark(turn.tokens_source)}{format_tokens_short(turn.tokens_per_second)} tok/s"
        )
    if turn.tool_calls:
        names = ", ".join(span.name for span in turn.tool_calls)
        parts.append(f"{len(turn.tool_calls)} tool call(s) [{names}]")
    else:
        parts.append("no tool call")
    if turn.context_percent is not None:
        parts.append(
            f"ctx {_mark(turn.context_source)}{format_percent(turn.context_percent)}"
        )
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# settings: budget, trigger, price — each with the name of what declared it     #
# --------------------------------------------------------------------------- #


def _context_home() -> Any:
    """``skaldir.context_manager`` when importable — the one home of the budget numbers."""
    try:
        from . import context_manager
    except Exception:  # noqa: BLE001 — telemetry must stay importable on its own
        return None
    return context_manager


def _settings_from_env() -> dict[str, Any]:
    """The budget, the trigger fraction and the price, with their declaring name."""
    home = _context_home()
    defaults = {
        "budget": int(getattr(home, "DEFAULT_BUDGET_TOKENS", DEFAULT_CONTEXT_BUDGET)),
        "fraction": float(getattr(home, "DEFAULT_TRIGGER_FRACTION", DEFAULT_TRIGGER_FRACTION)),
        "budget_env": str(getattr(home, "ENV_BUDGET", ENV_CONTEXT_BUDGET)),
        "fraction_env": str(getattr(home, "ENV_TRIGGER_FRACTION", ENV_TRIGGER_FRACTION)),
    }
    return defaults


def _positive_int(raw: Any) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _fraction(raw: Any) -> float | None:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= 1 else None


def resolve_budget(explicit: int | None = None) -> tuple[int, str]:
    """``(tokens, source)`` — ``flag`` > the layer's env var > the declared default."""
    settings = _settings_from_env()
    given = _positive_int(explicit)
    if given is not None:
        return given, "flag:--context-budget"
    token = settings["budget_env"]
    name = _env.source(token)
    if name:
        value = _positive_int(_env.getenv(token))
        if value is not None:
            return value, f"env:{name}"
    return int(settings["budget"]), "default"


def resolve_trigger_fraction(explicit: float | None = None) -> tuple[float, str]:
    """``(fraction, source)`` — same precedence as the budget, same wording."""
    settings = _settings_from_env()
    given = _fraction(explicit)
    if given is not None:
        return given, "flag:--trigger-fraction"
    token = settings["fraction_env"]
    name = _env.source(token)
    if name:
        value = _fraction(_env.getenv(token))
        if value is not None:
            return value, f"env:{name}"
    return float(settings["fraction"]), "default"


def resolve_price(explicit: float | None = None) -> tuple[float | None, str]:
    """``(usd_per_1k, source)`` — ``None`` when nobody declared a price.

    The order is: the flag, then ``SKALDIR_TELEMETRY_PRICE_PER_1K``, then the price the
    *run itself* declared (``agent run --price-per-1k-tokens``, recorded in the
    transcript). A transcript price is applied by the caller, because only the caller
    holds the transcript; this function handles the two operator-declared sources.
    """
    value = _fractionless(explicit)
    if value is not None:
        return value, "flag:--price-per-1k-tokens"
    name = _env.source(ENV_PRICE_PER_1K)
    if name:
        from_env = _fractionless(_env.getenv(ENV_PRICE_PER_1K))
        if from_env is not None:
            return from_env, f"env:{name}"
    return None, ""


def _fractionless(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# --------------------------------------------------------------------------- #
# building the record                                                          #
# --------------------------------------------------------------------------- #


def _events_of(events: Sequence[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [event for event in events if str(event.get("kind") or "") == kind]


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage_of(event: dict[str, Any]) -> tuple[dict[str, int], bool]:
    """``(usage, estimated)`` from one ``model.response``/``run.finished`` event.

    The loop's own ``usage_estimated`` flag is the authority, not the presence of a
    ``usage`` mapping: a run whose usage is an estimate must stay an estimate here even
    if some other layer filled the mapping with numbers.
    """
    raw = event.get("usage")
    mapping = {
        str(key): int(value)
        for key, value in dict(raw or {}).items()
        if _int_or_none(value) is not None
    }
    estimated = bool(event.get("usage_estimated", False))
    return mapping, estimated


def _turn_from(
    *,
    index: int,
    step: int,
    request: dict[str, Any] | None,
    response: dict[str, Any] | None,
    calls: Sequence[dict[str, Any]],
    budget: int,
) -> Turn:
    """One turn, built from the events that describe it (never from a re-run)."""
    turn = Turn(index=index, step=step)
    body = dict((request or {}).get("body") or {})
    messages = body.get("messages")
    turn.messages = len(messages) if isinstance(messages, list) else None
    turn.at = str((response or request or {}).get("at") or "")
    turn.answered = response is not None
    if response is None:
        turn.tokens_per_second_reason = (
            "the transcript holds a request for this step with no answer — the step was "
            "interrupted before the model replied"
        )
    turn.duration_ms = _int_or_none((response or {}).get("duration_ms"))
    turn.finish_reason = str((response or {}).get("finish_reason") or "")

    usage, estimated = _usage_of(response or {})
    if usage and not estimated:
        turn.tokens_source = "provider"
        turn.tokens_method = "reported by the provider in this answer's `usage`"
        turn.prompt_tokens = int(usage.get("prompt_tokens", 0))
        turn.completion_tokens = int(usage.get("completion_tokens", 0))
    elif response is None:
        turn.tokens_source = "estimate"
        turn.tokens_method = "no answer was recorded for this step — nothing to count"
    else:
        turn.tokens_source = "estimate"
        turn.tokens_method = LEGACY_FIELD_NOTE
        request_text = _json_text(messages if isinstance(messages, list) else body)
        turn.prompt_tokens = estimate_chars4(request_text)
        answer_text = str((response or {}).get("text") or "") + "".join(
            _json_text(call.get("arguments_raw") or "")
            for call in ((response or {}).get("tool_calls") or [])
        )
        turn.completion_tokens = estimate_chars4(answer_text) if answer_text else 0

    if turn.prompt_tokens is not None:
        turn.context_used_tokens = int(turn.prompt_tokens)
        turn.context_source = turn.tokens_source
        turn.context_percent = 100.0 * float(turn.prompt_tokens) / float(budget or 1)

    window_ms = int(turn.duration_ms or 0)
    if turn.completion_tokens is None or not window_ms:
        if not turn.answered:
            turn.tokens_per_second_reason = (
                "the transcript holds a request for this step with no answer — the step was "
                "interrupted before the model replied"
            )
        elif not window_ms:
            turn.tokens_per_second_reason = (
                "the transcript records no measurable generation window for this turn"
            )
        else:
            turn.tokens_per_second_reason = "no completion token count for this turn"
    else:
        turn.tokens_per_second = float(turn.completion_tokens) / (window_ms / 1000.0)

    turn.tool_calls = tuple(
        ToolSpan(
            name=str(call.get("name") or ""),
            step=_int_or_none(call.get("step")) or step,
            duration_ms=int(_int_or_none(call.get("duration_ms")) or 0),
            ok=bool(call.get("ok", False)),
            executed=bool(call.get("executed", False)),
            parallel=bool(call.get("parallel", False)),
            decision=str(call.get("decision") or ""),
        )
        for call in calls
    )
    return turn


def _turns(events: Sequence[dict[str, Any]], budget: int) -> list[Turn]:
    """Pair each ``model.request`` with its ``model.response``, in step order."""
    requests = {
        _int_or_none(item.get("step")): item
        for item in _events_of(events, "model.request")
    }
    responses = {
        _int_or_none(item.get("step")): item
        for item in _events_of(events, "model.response")
    }
    calls: dict[int, list[dict[str, Any]]] = {}
    for call in _events_of(events, "tool.call"):
        calls.setdefault(_int_or_none(call.get("step")) or 0, []).append(call)
    steps = sorted({step for step in [*requests, *responses] if step})
    return [
        _turn_from(
            index=index,
            step=step,
            request=requests.get(step),
            response=responses.get(step),
            calls=calls.get(step, []),
            budget=budget,
        )
        for index, step in enumerate(steps, start=1)
    ]


def _sum_tokens(turns: Sequence[Turn], key: str) -> tuple[int | None, str]:
    values = [getattr(turn, key) for turn in turns]
    known = [value for value in values if value is not None]
    if not known:
        return None, "estimate"
    source = "provider" if turns and all(turn.tokens_source == "provider" for turn in turns) else (
        "estimate"
    )
    return sum(int(value) for value in known), source


def _tokens_block(turns: Sequence[Turn]) -> dict[str, Any]:
    prompt, prompt_source = _sum_tokens(turns, "prompt_tokens")
    completion, completion_source = _sum_tokens(turns, "completion_tokens")
    total = None if prompt is None or completion is None else prompt + completion
    method = (
        "summed over the run's turns; each turn's number is the provider's own when it "
        "reported `usage`, otherwise chars/4 over the exact request body / the answer text"
    )
    return {
        "prompt": _number(prompt, prompt_source, method),
        "completion": _number(completion, completion_source, method),
        "total": _number(total, completion_source, method),
        "note": LEGACY_FIELD_NOTE,
    }


def _tokens_per_second(turns: Sequence[Turn]) -> dict[str, Any]:
    """Measured over the generation window — the turns' own model-call wall time."""
    window_ms = 0
    counted = 0
    tokens = 0
    estimated = False
    for turn in turns:
        if turn.tokens_per_second is None:
            continue
        window_ms += int(turn.duration_ms or 0)
        tokens += int(turn.completion_tokens or 0)
        counted += 1
        estimated = estimated or turn.tokens_source != "provider"
    method = (
        "sum(completion tokens) / sum(model.response.duration_ms of those turns) — each "
        "duration is the loop's monotonic clock around the provider call, streaming included"
    )
    if not counted or window_ms <= 0:
        return {
            "value": None,
            "source": "estimate",
            "reason": (
                "no turn has both a completion token count and a non-zero generation "
                "window — a rate would be invented"
            ),
            "method": method,
            "turns_measured": 0,
            "window_ms": 0,
            "completion_tokens": None,
        }
    source = "estimate" if estimated else "provider"
    return {
        "value": tokens / (window_ms / 1000.0),
        "source": source,
        "reason": "",
        "method": method,
        "turns_measured": counted,
        "window_ms": window_ms,
        "completion_tokens": tokens,
    }


def _context_block(
    turns: Sequence[Turn],
    last_messages: Any,
    *,
    budget: int,
    budget_source: str,
    fraction: float,
    fraction_source: str,
    provider_usage: dict[str, int],
) -> dict[str, Any]:
    """Used/total/percent plus the point at which compaction would fire.

    The reading is the **last answered turn's** prompt — i.e. how full the context was on the
    most recent request the model actually answered. Reading the newest *request* instead
    would report a request that has not been answered yet, and (because a pending request has
    no provider count) it would switch estimator mid-run: measured `78%` on the estimate
    scale, then `40%` on the turn's own count, for the same context. A footer that moves like
    that is noise; a footer that only moves when the context really grows is information.
    """
    threshold = int(budget * fraction)
    used = None
    used_source = ""
    estimate_block: dict[str, Any] = {}
    answered = [turn for turn in turns if turn.answered and turn.context_used_tokens is not None]
    if answered:
        last = answered[-1]
        used = last.context_used_tokens
        used_source = "provider_measured" if last.tokens_source == "provider" else "estimate"
    if used is None and provider_usage:
        used = _int_or_none(provider_usage.get("prompt_tokens"))
        used_source = "provider_measured"
    home = _context_home()
    if home is not None and isinstance(last_messages, list) and last_messages:
        try:
            accounting = home.accounting_for(
                last_messages,
                measured=provider_usage or None,
                budget_tokens=budget,
                trigger_fraction=fraction,
                budget_source=budget_source,
            )
        except Exception as exc:  # noqa: BLE001 — a broken estimate is reported, not hidden
            estimate_block = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            estimated = dict(accounting.get("estimated") or {})
            estimate_block = {
                "jevcomp": estimated.get("tokens"),
                "chars4": estimated.get("tokens_chars4"),
                "method": str(estimated.get("method") or ""),
                "messages": (accounting.get("measured") or {}).get("messages"),
                "chars": (accounting.get("measured") or {}).get("chars"),
            }
            if used is None:
                used = int((accounting.get("budget") or {}).get("used_tokens") or 0) or None
                used_source = str((accounting.get("budget") or {}).get("used_source") or "")
    percent = None
    if used is not None and budget:
        percent = 100.0 * float(used) / float(budget)
    reason = ""
    if used is None:
        reason = (
            "the transcript records no request body to measure — context usage is unknown, "
            "not zero"
        )
    state = "unknown"
    if used is not None:
        state = "over_budget" if used > budget else ("at_trigger" if used >= threshold
                                                     else "below_trigger")
    return {
        "used_tokens": used,
        "used_source": used_source or "unknown",
        "total_tokens": int(budget),
        "total_source": budget_source,
        "trigger_fraction": float(fraction),
        "trigger_fraction_source": fraction_source,
        "trigger_tokens": threshold,
        "remaining_tokens": None if used is None else int(budget) - int(used),
        "percent": percent,
        "percent_display": format_percent(percent),
        "state": state,
        "reason": reason,
        "estimate": estimate_block or None,
        "note": LEGACY_FIELD_NOTE,
    }


def _cost_block(
    tokens_total: int | None,
    *,
    price: float | None,
    price_source: str,
    run_price: float | None,
) -> dict[str, Any]:
    """The cost, or ``null`` with the declaration that would produce one."""
    effective = price if price is not None else run_price
    source = price_source or ("transcript:run.started.limits.price_per_1k_tokens"
                              if run_price is not None else "")
    if effective is None:
        return {
            "usd": None,
            "price_per_1k_tokens": None,
            "price_source": "",
            "tokens_used": tokens_total,
            "reason": (
                "no price was declared for this run: pass --price-per-1k-tokens, set "
                "SKALDIR_TELEMETRY_PRICE_PER_1K, or run with "
                "`agent run --price-per-1k-tokens <rate>` (the run's own declaration is "
                "used when the transcript carries one)"
            ),
            "method": (
                "cost is (total tokens / 1000) × the declared rate — the same arithmetic "
                "the loop uses for its cost budget; no price catalogue is bundled, so "
                "nothing is inferred from the model name"
            ),
        }
    if tokens_total is None:
        return {
            "usd": None,
            "price_per_1k_tokens": float(effective),
            "price_source": source,
            "tokens_used": None,
            "reason": "a price is declared but the run has no token count to apply it to",
            "method": "(total tokens / 1000) × declared rate",
        }
    return {
        "usd": (float(tokens_total) / 1000.0) * float(effective),
        "price_per_1k_tokens": float(effective),
        "price_source": source,
        "tokens_used": int(tokens_total),
        "reason": "",
        "method": (
            "(total tokens / 1000) × the declared rate — the same arithmetic the loop "
            "uses; tokens may be an estimate, in which case the cost is one too"
        ),
    }


def _duration_of(
    header: dict[str, Any], finished: dict[str, Any], last: dict[str, Any]
) -> tuple[int | None, str, str]:
    """``(duration_ms, ended_at, source)`` — measured, or measured to the last event."""
    reported = _int_or_none(finished.get("duration_ms"))
    if finished and reported is not None:
        return reported, str(finished.get("at") or ""), (
            "measured by the loop around the whole run (run.finished.duration_ms)"
        )
    started_at = str(header.get("at") or "")
    ended_at = str(last.get("at") or "")
    if started_at and ended_at:
        try:
            delta = datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
        except ValueError:
            return None, ended_at, "the transcript's timestamps could not be parsed"
        return int(delta.total_seconds() * 1000), ended_at, (
            "measured from run.started to the last recorded event — this run has no "
            "run.finished, so it is still running or was killed"
        )
    return None, ended_at, "the transcript carries no usable timestamps"


def _savings_block(
    events: Sequence[dict[str, Any]],
    *,
    tokens: dict[str, Any],
) -> dict[str, Any]:
    """§94 — the token levers, **visible one by one** instead of as one hopeful total.

    The owner's question was "how can we expand Skaldir's token economy?", and the
    honest answer starts with measuring each mechanism separately, because they are
    not the same kind of saving and they fail in different ways:

    * **prompt-cache hits** — the provider's own counter for tokens it re-read from
      its cache. Provider-reported, never estimated; ``null`` with a reason when the
      backend reports no cache counter at all (a cache we *sent* is not a hit).
    * **archived tool results** — tokens a full tool result *would* have cost minus
      what its id + digest + summary actually cost. Our own ``chars/4`` estimate,
      labelled as such; the artifact itself is byte-identical retrievable.
    * **compaction** — kept / shortened / dropped messages and the tokens that freed.
    * **brief vs transcript** — what a subagent brief cost against the transcript it
      replaced (the brief is the *token* form of a child's work).
    * **reasoning tokens** — billed as completion tokens, produce no content: the
      invisible spend that makes a run look empty while it is burning its budget.
    """
    # -- prompt cache: the provider's own counters ------------------------- #
    cache_hits = 0
    cache_reported = False
    cache_events = 0
    for event in _events_of(events, "model.response"):
        usage = event.get("usage")
        if not isinstance(usage, Mapping):
            continue
        found = False
        for key in (
            "prompt_cache_hit_tokens",
            "cache_read_input_tokens",
            "cached_tokens",
        ):
            value = usage.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cache_hits += int(value)
                found = True
        details = usage.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            value = details.get("cached_tokens")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cache_hits += int(value)
                found = True
        if found:
            cache_reported = True
            cache_events += 1
    if cache_reported:
        prompt_block: dict[str, Any] = {
            "value": cache_hits,
            "source": "provider",
            "turns_with_a_cache_counter": cache_events,
            "note": "the provider's own cache-hit counter, summed over the run",
        }
    else:
        prompt_block = {
            "value": None,
            "source": "unavailable",
            "turns_with_a_cache_counter": 0,
            "reason": (
                "no prompt-cache counter came back in any response — this backend does "
                "not report one, so a hit is not claimed"
            ),
        }

    # -- archived tool results --------------------------------------------- #
    archived = _events_of(events, "tool_archived")
    before = sum(int(item.get("tokens_before") or 0) for item in archived)
    after = sum(int(item.get("tokens_after") or 0) for item in archived)
    archive_block = {
        "results": len(archived),
        "tokens_before": before if archived else None,
        "tokens_after": after if archived else None,
        "tokens_saved": (before - after) if archived else None,
        "source": "estimate/chars-4" if archived else "unavailable",
        "reason": (
            ""
            if archived
            else "no tool result was oversized enough to be archived on this run"
        ),
        "artifacts": [
            {
                "ref": str(item.get("ref") or ""),
                "sha256": str(item.get("sha256") or ""),
                "chars": int(item.get("chars") or 0),
                "name": str(item.get("name") or ""),
                "source_label": str(item.get("source") or ""),
            }
            for item in archived
        ],
    }

    # -- compaction: kept / shortened / dropped ---------------------------- #
    kept = shortened = dropped = removed = 0
    freed = 0
    compactions = 0
    for event in events:
        kind = str(event.get("kind") or "")
        if kind not in ("context_compaction", "compaction", "context_report"):
            continue
        report = event.get("report") if isinstance(event.get("report"), Mapping) else event
        if not isinstance(report, Mapping):
            continue
        compactions += 1
        kept += len(report.get("kept_verbatim") or [])
        shortened += len(report.get("shortened") or [])
        dropped += len(report.get("dropped") or [])
        removed += len(report.get("removed") or [])
        freed_block = report.get("freed")
        if isinstance(freed_block, Mapping):
            for value in freed_block.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    freed += int(value)
                    break
    compaction_block = {
        "events": compactions,
        "kept": kept,
        "shortened": shortened,
        "dropped": dropped,
        "removed": removed,
        "tokens_freed": freed if compactions else None,
        "source": "estimate/chars-4" if compactions else "unavailable",
        "reason": "" if compactions else "no compaction fired on this run",
    }

    # -- brief vs transcript ------------------------------------------------ #
    brief_chars = transcript_chars = 0
    briefs = 0
    for event in events:
        kind = str(event.get("kind") or "")
        if kind == "subagent_brief_created":
            briefs += 1
            brief_chars += int(event.get("chars") or len(str(event.get("brief") or "")))
        elif kind == "subagent_transcript":
            transcript_chars += int(event.get("chars") or 0)
    brief_block = {
        "briefs": briefs,
        "brief_chars": brief_chars if briefs else None,
        "transcript_chars": transcript_chars if briefs else None,
        "chars_avoided": (
            (transcript_chars - brief_chars) if briefs and transcript_chars else None
        ),
        "tokens_saved": (
            max(0, (transcript_chars - brief_chars)) // 4 if briefs and transcript_chars else None
        ),
        "source": "estimate/chars-4" if briefs else "unavailable",
        "reason": "" if briefs else "no subagent brief was recorded on this run",
    }

    # -- reasoning tokens: billed, invisible, the worst kind of waste ------- #
    reasoning = 0
    reasoning_seen = False
    empty_answers_with_reasoning = 0
    for event in _events_of(events, "model.response"):
        usage = event.get("usage")
        if not isinstance(usage, Mapping):
            continue
        value = usage.get("reasoning_tokens")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            reasoning += int(value)
            reasoning_seen = True
            if int(value) > 0 and not str(event.get("text") or "").strip():
                empty_answers_with_reasoning += 1
    completion_value = (tokens.get("completion") or {}).get("value")
    reasoning_block = {
        "value": reasoning if reasoning_seen else None,
        "source": "provider" if reasoning_seen else "unavailable",
        "share_of_completion": (
            round(reasoning / completion_value, 4)
            if reasoning_seen and isinstance(completion_value, int) and completion_value > 0
            else None
        ),
        "reasoning_only_answers": empty_answers_with_reasoning,
        "reason": (
            ""
            if reasoning_seen
            else "the provider reported no reasoning-token counter — reported as unknown, "
            "not as zero"
        ),
        "note": (
            "reasoning tokens are billed like completion tokens and produce no content: "
            "an answer with reasoning and no text is budget spent with nothing to show"
        ),
    }

    return {
        "prompt_cache": prompt_block,
        "archived_tool_results": archive_block,
        "compaction": compaction_block,
        "brief_vs_transcript": brief_block,
        "reasoning": reasoning_block,
        "lever_order": [
            "archived_tool_results",
            "compaction",
            "brief_vs_transcript",
            "prompt_cache",
            "reasoning",
        ],
        "note": (
            "each lever is reported on its own: a sum of different kinds of saving "
            "would hide which one is not working"
        ),
    }


def _savings_lines(savings: dict[str, Any]) -> list[str]:
    """§94 — one line per token lever, each with its source, or its reason for absence."""
    if not savings:
        return []
    order = list(savings.get("lever_order") or [])
    blocks = {
        name: savings.get(name) for name in order if isinstance(savings.get(name), Mapping)
    }
    if not blocks:
        return []

    def value_of(name: str) -> str:
        block = blocks.get(name) or {}
        value = block.get("value", block.get("tokens_saved"))
        if value is None:
            return "n/a"
        return format_tokens_short(value)

    lines = [
        "  savings  "
        + " · ".join(f"{name.replace('_', '-')} {value_of(name)}" for name in blocks),
    ]
    archive = blocks.get("archived_tool_results") or {}
    if archive.get("results"):
        lines.append(
            f"    archive  {archive['results']} result(s) "
            f"{format_tokens_short(archive.get('tokens_before'))} → "
            f"{format_tokens_short(archive.get('tokens_after'))} tok "
            f"({archive.get('source')})"
        )
    compaction = blocks.get("compaction") or {}
    if compaction.get("events"):
        lines.append(
            f"    compact  kept {compaction.get('kept')} · "
            f"shortened {compaction.get('shortened')} · dropped {compaction.get('dropped')} "
            f"· freed {format_tokens_short(compaction.get('tokens_freed'))} tok"
        )
    reasoning = blocks.get("reasoning") or {}
    if reasoning.get("value") is not None:
        share = reasoning.get("share_of_completion")
        extra = f" ({share:.0%} of completion)" if isinstance(share, float) else ""
        lines.append(
            f"    reason   {format_tokens_short(reasoning['value'])} tok{extra} · "
            f"{reasoning.get('reasoning_only_answers', 0)} answer(s) with no content"
        )
    for name, block in blocks.items():
        if not isinstance(block, Mapping):
            continue
        if block.get("value") is None and block.get("reason"):
            lines.append(f"    {name.replace('_', '-')}  unavailable: {block['reason']}")
    return lines


def build_record(
    transcript: str | Path,
    *,
    run_id: str = "",
    session_id: str = "",
    context_budget: int | None = None,
    trigger_fraction: float | None = None,
    price_per_1k_tokens: float | None = None,
) -> TelemetryRecord:
    """Derive the standard displays for one run from its transcript.

    Read-only: nothing about the run is re-executed, and every number is either the
    provider's own or ours *labelled as ours*. Raises :class:`TelemetryError` when there
    is nothing to read (``code="not_found"``) or nothing to report (``code="blocked"``).
    """
    events, warnings = read_events(transcript)
    return build_record_from_events(
        events,
        warnings=warnings,
        transcript=str(Path(transcript).expanduser()),
        run_id=run_id,
        session_id=session_id,
        context_budget=context_budget,
        trigger_fraction=trigger_fraction,
        price_per_1k_tokens=price_per_1k_tokens,
    )


def build_record_from_events(
    events: Sequence[dict[str, Any]],
    *,
    warnings: Sequence[str] = (),
    transcript: str = "",
    run_id: str = "",
    session_id: str = "",
    context_budget: int | None = None,
    trigger_fraction: float | None = None,
    price_per_1k_tokens: float | None = None,
) -> TelemetryRecord:
    """The same record, from events already in memory (the live watcher's path).

    Kept separate from :func:`build_record` so the live reader never re-reads or
    re-parses a file it is already tailing — the numbers a live footer shows are the
    same numbers the final record carries.
    """
    warnings = list(warnings)
    path = str(transcript)
    header = next((item for item in events if item.get("kind") == "run.started"), {})
    finished = next(
        (item for item in reversed(events) if item.get("kind") == "run.finished"), {}
    )
    budget, budget_source = resolve_budget(context_budget)
    fraction, fraction_source = resolve_trigger_fraction(trigger_fraction)
    turns = _turns(events, budget)
    requests = _events_of(events, "model.request")
    last_messages = dict((requests[-1].get("body") if requests else {}) or {}).get("messages")
    responses = _events_of(events, "model.response")
    last_usage: dict[str, int] = {}
    if responses:
        mapped, estimated = _usage_of(responses[-1])
        last_usage = {} if estimated else mapped

    tokens = _tokens_block(turns)
    speed = _tokens_per_second(turns)
    context = _context_block(
        turns,
        last_messages,
        budget=budget,
        budget_source=budget_source,
        fraction=fraction,
        fraction_source=fraction_source,
        provider_usage=last_usage,
    )
    duration_ms, ended_at, duration_source = _duration_of(header, finished, events[-1])
    provider_note = dict(header.get("provider") or {})
    model = str(provider_note.get("model") or "").strip() or None
    limits = dict(header.get("limits") or {})
    run_price = _fractionless(limits.get("price_per_1k_tokens"))
    price, price_source = resolve_price(price_per_1k_tokens)
    cost = _cost_block(
        tokens["total"].get("value"), price=price, price_source=price_source, run_price=run_price
    )
    if cost.get("usd") is not None and tokens["total"].get("source", "").startswith("estimate"):
        cost["estimate"] = True
        cost["method"] += " (the token total is an estimate, so this cost is one too)"

    spans = [span for turn in turns for span in turn.tool_calls]
    savings = _savings_block(events, tokens=tokens)
    tool_durations = [span.duration_ms for span in spans]
    tool_block = {
        "count": len(spans),
        "executed": sum(1 for span in spans if span.executed),
        "failed": sum(1 for span in spans if not span.ok),
        "parallel": sum(1 for span in spans if span.parallel),
        "total_duration_ms": sum(tool_durations),
        "max_duration_ms": max(tool_durations) if tool_durations else None,
        "mean_duration_ms": (
            round(sum(tool_durations) / len(tool_durations), 1) if tool_durations else None
        ),
        "turns_with_calls": sum(1 for turn in turns if turn.tool_calls),
        "calls": [span.to_dict() for span in spans],
        "note": (
            "each duration is the tool's own wall time as the loop measured it; a call "
            "refused by policy is recorded with executed=false and duration 0"
        ),
    }

    record = TelemetryRecord(
        run_id=str(run_id or header.get("run_id") or Path(path).stem or "run-unknown"),
        session_id=str(
            session_id or run_id or header.get("run_id") or Path(path).stem or "run-unknown"
        ),
        transcript=path,
        record_path=str(record_path_for(path)),
        status=str(finished.get("status") or ("running" if not finished else "")),
        reason=str(finished.get("reason") or ""),
        started_at=str(header.get("at") or ""),
        ended_at=ended_at,
        duration_ms=duration_ms,
        duration_source=duration_source,
        turns=tuple(turns),
        tool_calls=tool_block,
        tokens=tokens,
        tokens_per_second=speed,
        context=context,
        model=model,
        provider=provider_note,
        cost=cost,
        limits=limits,
        savings=savings,
        workspace=str(header.get("workspace") or ""),
        task=str(header.get("task") or ""),
        warnings=tuple(warnings),
    )
    if not finished:
        record.warnings = (*record.warnings, (
            f"{record.transcript} has no `run.finished` event — the numbers below are what "
            "the run recorded up to the last event"
        ))
    if model is None:
        record.warnings = (*record.warnings, (
            "the transcript carries no provider note — the model is unknown, not empty"
        ))
    record.estimates, record.nulls = _honesty_index(record)
    return record


def _honesty_index(record: TelemetryRecord) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """The two indexes a consumer can act on: what is estimated, and what is missing."""
    estimates: list[str] = []
    for name in ("prompt", "completion", "total"):
        block = dict((record.tokens or {}).get(name) or {})
        if block.get("value") is not None and str(block.get("source")).startswith("estimate"):
            estimates.append(f"tokens.{name} (estimate)")
    speed = dict(record.tokens_per_second or {})
    if speed.get("value") is not None and str(speed.get("source")).startswith("estimate"):
        estimates.append("tokens_per_second (estimate: the completion count is one)")
    context = dict(record.context or {})
    if str(context.get("used_source") or "").startswith("estimate"):
        estimates.append("context.used_tokens (estimate)")
    cost = dict(record.cost or {})
    if cost.get("estimate") is True:
        estimates.append("cost.usd (derived from an estimated token total)")

    nulls: list[dict[str, str]] = []
    if record.duration_ms is None:
        nulls.append({"field": "duration_ms", "reason": record.duration_source})
    if record.model is None:
        nulls.append({
            "field": "model",
            "reason": "the transcript records no provider note",
        })
    if cost.get("usd") is None:
        nulls.append({"field": "cost.usd", "reason": str(cost.get("reason") or "")})
    if speed.get("value") is None:
        nulls.append({
            "field": "tokens_per_second",
            "reason": str(speed.get("reason") or "not measurable"),
        })
    if context.get("percent") is None:
        nulls.append({
            "field": "context.percent",
            "reason": str(context.get("reason") or "not measurable"),
        })
    for turn in record.turns:
        if turn.tokens_per_second is None:
            nulls.append({
                "field": f"turns[{turn.index}].tokens_per_second",
                "reason": turn.tokens_per_second_reason or "not measurable",
            })
    return tuple(estimates), tuple(nulls)


# --------------------------------------------------------------------------- #
# persistence                                                                  #
# --------------------------------------------------------------------------- #


def save_record(record: TelemetryRecord, path: str | Path | None = None) -> Path:
    """Write the record beside the transcript (``<run-id>.telemetry.json``).

    Refuses when there is no file name to write to (an in-memory record of a run whose
    transcript was never on disc) — a record must not be filed under an invented name.
    """
    target = Path(path).expanduser() if path else Path(str(record.record_path)).expanduser()
    if not target.name:
        raise TelemetryError(
            "this record has no transcript path, so there is nowhere to file it — give "
            "--out <file> or build the record from a transcript on disc"
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = record.to_dict()
        payload["record_path"] = str(target)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise TelemetryError(f"cannot write the telemetry record {target}: {exc}") from exc
    return target


def load_record(path: str | Path) -> TelemetryRecord:
    """Read a persisted record back as data (no re-derivation)."""
    file = Path(path).expanduser()
    if not file.is_file():
        raise TelemetryError(f"no telemetry record at {file}", code="not_found")
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TelemetryError(f"cannot read the telemetry record {file}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TelemetryError(f"{file} does not hold a telemetry record", code="blocked")
    return record_from_dict(payload)


def record_from_dict(payload: dict[str, Any]) -> TelemetryRecord:
    """Rebuild the record object from its JSON form (used by ``show`` after a rotation)."""
    turns = tuple(
        Turn(
            index=int(item.get("index") or 0),
            step=int(item.get("step") or 0),
            at=str(item.get("at") or ""),
            duration_ms=item.get("duration_ms"),
            finish_reason=str(item.get("finish_reason") or ""),
            prompt_tokens=item.get("prompt_tokens"),
            completion_tokens=item.get("completion_tokens"),
            tokens_source=str(item.get("tokens_source") or "estimate"),
            tokens_method=str(item.get("tokens_method") or ""),
            tokens_per_second=item.get("tokens_per_second"),
            tokens_per_second_reason=str(item.get("tokens_per_second_reason") or ""),
            context_percent=item.get("context_percent"),
            context_used_tokens=item.get("context_used_tokens"),
            context_source=str(item.get("context_source") or ""),
            messages=item.get("messages"),
            answered=bool(item.get("answered", True)),
            tool_calls=tuple(
                ToolSpan(
                    name=str(span.get("name") or ""),
                    step=int(span.get("step") or 0),
                    duration_ms=int(span.get("duration_ms") or 0),
                    ok=bool(span.get("ok")),
                    executed=bool(span.get("executed")),
                    parallel=bool(span.get("parallel")),
                    decision=str(span.get("decision") or ""),
                )
                for span in (item.get("tool_calls") or [])
            ),
        )
        for item in (payload.get("turn_rows") or [])
    )
    return TelemetryRecord(
        run_id=str(payload.get("run_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        transcript=str(payload.get("transcript") or ""),
        record_path=str(payload.get("record_path") or ""),
        schema_version=str(payload.get("schema_version") or TELEMETRY_SCHEMA_VERSION),
        status=str(payload.get("status") or ""),
        reason=str(payload.get("reason") or ""),
        started_at=str(payload.get("started_at") or ""),
        ended_at=str(payload.get("ended_at") or ""),
        duration_ms=payload.get("duration_ms"),
        duration_source=str(payload.get("duration_source") or ""),
        turns=turns,
        tool_calls=dict(payload.get("tool_calls") or {}),
        tokens=dict(payload.get("tokens") or {}),
        tokens_per_second=dict(payload.get("tokens_per_second") or {}),
        context=dict(payload.get("context") or {}),
        model=payload.get("model"),
        provider=dict(payload.get("provider") or {}),
        cost=dict(payload.get("cost") or {}),
        limits=dict(payload.get("limits") or {}),
        savings=dict(payload.get("savings") or {}),
        workspace=str(payload.get("workspace") or ""),
        task=str(payload.get("task") or ""),
        estimates=tuple(str(item) for item in (payload.get("estimates") or [])),
        nulls=tuple(dict(item) for item in (payload.get("nulls") or [])),
        warnings=tuple(str(item) for item in (payload.get("warnings") or [])),
        source="persisted record",
    )


def show_record(
    *,
    transcript: str | Path = "",
    state_dir: str | Path = "",
    run_id: str = "",
    persist: bool = True,
    **build_kwargs: Any,
) -> tuple[TelemetryRecord, list[str]]:
    """The record for a run, rebuilt from the transcript or served from disc.

    Returns ``(record, notes)``. The transcript is the source of truth and always wins;
    the persisted record is only used when the transcript is gone, and the record then
    says so in ``source`` — a rotated transcript must not turn into a silent re-run.
    """
    notes: list[str] = []
    path: Path | None = None
    if transcript:
        path = Path(transcript).expanduser()
    elif run_id:
        path = resolve_run_path(state_dir or ".", run_id)
    if path is None:
        raise TelemetryError(
            "give a run: --run-id <id> --state-dir <dir>, or --transcript <file>",
            code="not_found",
        )
    if path.is_file():
        record = build_record(path, run_id=run_id, **build_kwargs)
        if persist:
            try:
                saved = save_record(record)
            except TelemetryError as exc:  # noqa: BLE001 — a report that cannot be filed
                notes.append(str(exc))
            else:
                notes.append(f"record written to {saved}")
        return record, notes
    fallback = record_path_for(path)
    if fallback.is_file():
        record = load_record(fallback)
        notes.append(
            f"the transcript {path} is gone — this is the record filed beside it "
            f"({fallback}); it was not re-derived"
        )
        return record, notes
    raise TelemetryError(
        f"no run '{run_id or path.stem}': neither {path} nor {fallback} exists",
        code="not_found",
    )


# --------------------------------------------------------------------------- #
# export                                                                       #
# --------------------------------------------------------------------------- #


def export_text(record: TelemetryRecord, *, fmt: str = "json") -> str:
    """The record as one exportable document: ``json`` | ``jsonl`` | ``csv``.

    ``jsonl`` is one line per turn plus a final ``record`` line — the shape a log
    collector wants. ``csv`` carries the run's own row first (``row=run``), so a column
    consumer sees the totals and the per-turn rows in one file.
    """
    kind = str(fmt or "json").strip().lower()
    payload = record.to_dict()
    if kind in ("json", ""):
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if kind == "jsonl":
        lines = [json.dumps({"row": "turn", **turn.to_dict()}, ensure_ascii=False, sort_keys=True)
                 for turn in record.turns]
        lines.append(json.dumps({"row": "run", **payload}, ensure_ascii=False, sort_keys=True))
        return "".join(f"{line}\n" for line in lines)
    if kind == "csv":
        buffer = io.StringIO()
        columns = [
            "row", "run_id", "index", "step", "duration_ms", "prompt_tokens",
            "completion_tokens", "tokens_total", "tokens_source", "tokens_per_second",
            "context_percent", "context_used_tokens", "tool_call_count", "at",
        ]
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(_csv_run_row(record))
        for turn in record.turns:
            writer.writerow(_csv_turn_row(record, turn))
        return buffer.getvalue()
    raise TelemetryError(f"unknown export format {fmt!r}: use json, jsonl or csv", code="usage")


def _csv_run_row(record: TelemetryRecord) -> dict[str, Any]:
    speed = dict(record.tokens_per_second or {})
    context = dict(record.context or {})
    return {
        "row": "run",
        "run_id": record.run_id,
        "duration_ms": "" if record.duration_ms is None else int(record.duration_ms),
        "prompt_tokens": (record.tokens.get("prompt") or {}).get("value"),
        "completion_tokens": (record.tokens.get("completion") or {}).get("value"),
        "tokens_total": (record.tokens.get("total") or {}).get("value"),
        "tokens_source": (record.tokens.get("total") or {}).get("source", ""),
        "tokens_per_second": "" if speed.get("value") is None else f"{speed['value']:.3f}",
        "context_percent": "" if context.get("percent") is None else f"{context['percent']:.2f}",
        "context_used_tokens": context.get("used_tokens", ""),
        "tool_call_count": record.tool_calls.get("count", 0),
    }


def _csv_turn_row(record: TelemetryRecord, turn: Turn) -> dict[str, Any]:
    return {
        "row": "turn",
        "run_id": record.run_id,
        "index": turn.index,
        "step": turn.step,
        "duration_ms": "" if turn.duration_ms is None else int(turn.duration_ms),
        "prompt_tokens": turn.prompt_tokens,
        "completion_tokens": turn.completion_tokens,
        "tokens_total": turn.tokens_total,
        "tokens_source": turn.tokens_source,
        "tokens_per_second": (
            "" if turn.tokens_per_second is None else f"{turn.tokens_per_second:.3f}"
        ),
        "context_percent": (
            "" if turn.context_percent is None else f"{turn.context_percent:.2f}"
        ),
        "context_used_tokens": turn.context_used_tokens,
        "tool_call_count": len(turn.tool_calls),
        "at": turn.at,
    }


# --------------------------------------------------------------------------- #
# the live reader                                                              #
# --------------------------------------------------------------------------- #


class TelemetryWatcher:
    """Tail a run transcript and turn it into footer and per-turn lines as it grows.

    The loop writes one complete JSONL line per fact, so a reader that keeps its byte
    offset and only accepts newline-terminated lines can follow a live run without
    touching it. Every line is derived from events the run *already* recorded — nothing
    is estimated from the outside, so the live view and the final record agree.
    """

    def __init__(
        self,
        transcript: str | Path,
        *,
        context_budget: int | None = None,
        trigger_fraction: float | None = None,
        price_per_1k_tokens: float | None = None,
        run_id: str = "",
    ) -> None:
        self.path = Path(transcript).expanduser()
        self.run_id = str(run_id or self.path.stem)
        self.options: dict[str, Any] = {
            "context_budget": context_budget,
            "trigger_fraction": trigger_fraction,
            "price_per_1k_tokens": price_per_1k_tokens,
        }
        self.events: list[dict[str, Any]] = []
        self._offset = 0
        self._buffer = b""
        self._turns_seen = 0
        self._finished = False
        self.truncated = False

    # -- reading ----------------------------------------------------------- #

    def poll(self) -> list[dict[str, Any]]:
        """Ingest whatever the run appended since the last poll.

        A run id names exactly one run: the loop *truncates* the transcript when an id is
        reused (recorded in ``run.started.previous_transcript``), so a watcher that finds
        the file shorter than its offset restarts from the top and says so once — following
        a byte offset into a rewritten file would otherwise print nothing at all.
        """
        if not self.path.is_file():
            return []
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        if size < self._offset:
            self.events.clear()
            self._buffer = b""
            self._offset = 0
            self._turns_seen = 0
            self._finished = False
            self.truncated = True
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
        except OSError as exc:
            raise TelemetryError(f"cannot read {self.path}: {exc}") from exc
        self._buffer += chunk
        fresh: list[dict[str, Any]] = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = {"kind": "unreadable", "raw": text[:200]}
            if isinstance(parsed, dict):
                self.events.append(parsed)
                fresh.append(parsed)
        return fresh

    @property
    def finished(self) -> bool:
        return self._finished or any(
            str(event.get("kind") or "") == "run.finished" for event in self.events
        )

    def record(self) -> TelemetryRecord | None:
        """The record as of *now* — ``None`` while the transcript holds no events."""
        if not self.events:
            return None
        try:
            record = _record_from_events(self.events, run_id=self.run_id, **self.options)
        except TelemetryError:
            return None
        record.transcript = str(self.path)
        record.record_path = str(record_path_for(self.path))
        record.source = "transcript" if self.finished else "live"
        return record

    # -- the displays ------------------------------------------------------ #

    def lines(self, fresh: Sequence[dict[str, Any]] | None = None) -> list[str]:
        """The lines to print for the events just read: turn lines, then a footer.

        ``fresh`` is what :meth:`poll` just returned. ``fresh=None`` means "report
        whatever is on disc": the transcript is polled once more (nothing is left to wait
        for) and everything ingested is replayed. A poll that brought no answer and no end
        event prints nothing at all, so following a run cannot fill the screen with
        footers for tool-call bookkeeping.
        """
        if fresh is None:
            self.poll()
            events = list(self.events)
        else:
            events = list(fresh)
        kinds = {str(event.get("kind") or "") for event in events}
        self._finished = self._finished or any(
            str(event.get("kind") or "") == "run.finished" for event in self.events
        )
        out: list[str] = []
        if self.truncated:
            self.truncated = False
            out.append(
                "· the transcript was truncated — this is a new run under the same id, "
                "following it from the top"
            )
        if fresh is not None and not kinds & {"model.response", "run.finished"}:
            return out
        record = self.record()
        if record is None:
            return out
        # a turn line is printed only once its answer is on disc: a request the loop has
        # just written has no usage and no finish reason yet, and reporting it as a turn
        # would be a line about something that has not happened
        answered = [turn for turn in record.turns if turn.answered]
        for turn in answered[self._turns_seen:]:
            out.append(turn_line(turn, len(answered)))
        self._turns_seen = len(answered)
        out.append(record.footer())
        return out

    def tail(self) -> Path:
        """Where the record is filed for this run."""
        return record_path_for(self.path)


def _record_from_events(
    events: Sequence[dict[str, Any]], *, run_id: str = "", **options: Any
) -> TelemetryRecord:
    """Build a record from events already in memory (the live path's builder)."""
    return build_record_from_events(events, run_id=run_id, **options)


def follow(
    transcript: str | Path,
    *,
    run_id: str = "",
    interval_s: float = 0.25,
    timeout_s: float = 0.0,
    context_budget: int | None = None,
    trigger_fraction: float | None = None,
    price_per_1k_tokens: float | None = None,
    on_line: Callable[[str], None] = print,
    stop: Callable[[], bool] | None = None,
) -> tuple[list[str], TelemetryWatcher]:
    """Watch a run write its transcript, emitting turn lines and footers as they appear.

    ``timeout_s=0`` follows until ``run.finished`` (or the stop signal). A timeout that
    fires with the run still open stops the *reading*, and says so — the transcript keeps
    growing and a later ``telemetry show`` reports what was never printed here.
    """
    watcher = TelemetryWatcher(
        transcript,
        context_budget=context_budget,
        trigger_fraction=trigger_fraction,
        price_per_1k_tokens=price_per_1k_tokens,
        run_id=run_id,
    )
    emitted: list[str] = []
    deadline = time.monotonic() + float(timeout_s) if timeout_s else None
    while True:
        fresh = watcher.poll()
        for line in watcher.lines(fresh):
            emitted.append(line)
            on_line(line)
        if watcher.finished:
            break
        if stop is not None and stop():
            break
        if deadline is not None and time.monotonic() >= deadline:
            on_line(
                f"· still running after {int(timeout_s)}s — the transcript keeps growing; "
                f"`telemetry show` will report the rest"
            )
            break
        time.sleep(max(0.02, float(interval_s)))
    return emitted, watcher


def shell_footer(
    *,
    state_dir: str | Path,
    run_id: str,
    context_budget: int | None = None,
    trigger_fraction: float | None = None,
    price_per_1k_tokens: float | None = None,
    persist: bool = True,
) -> str:
    """The shell's one call: the footer line for a finished (or running) run.

    ``skaldir shell`` can print this after an agent turn — one function, no state of its
    own, and it names what it could not measure instead of filling the gap.
    """
    try:
        record, _notes = show_record(
            state_dir=state_dir,
            run_id=run_id,
            persist=persist,
            context_budget=context_budget,
            trigger_fraction=trigger_fraction,
            price_per_1k_tokens=price_per_1k_tokens,
        )
    except TelemetryError as exc:
        return f"telemetry n/a ({exc})"
    return record.footer()


def iter_runs(state_dir: str | Path, *, limit: int = 20) -> Iterable[dict[str, Any]]:
    """Newest-first ``{run_id, transcript, record, status, duration_ms}`` rows."""
    from .agent_loop import list_runs, runs_dir

    for row in list_runs(state_dir, limit=limit):
        transcript = Path(str(row.get("path") or ""))
        yield {
            **row,
            "transcript": str(transcript),
            "record": str(record_path_for(transcript)) if transcript.name else "",
        }
    if not runs_dir(state_dir).is_dir():  # pragma: no cover — an installation with no runs
        return
