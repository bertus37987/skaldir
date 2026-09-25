"""Live wiring for the Jev compaction port — budget accounting, trigger, preserved set, fallback.

The port (:mod:`skaldir.jevcomp`, a Python port of ``fast-jev-compaction``) decides
per tool call whether it stays verbatim, keeps only the head of its result, or goes.
Until now nothing called it during a run: ``src/skaldir/jev/**`` *models* a
``context_compaction`` decision, but the algorithm that rebuilds a transcript had no
live caller. This module is that caller, and it owns the five things the port
deliberately does not:

1. **Token accounting per run** — exact character counts and provider-reported usage
   are *measured*; every token count is an **estimate** and is labelled as one. The
   port's estimator (:func:`skaldir.jevcomp.estimate_tokens`, calibrated against real
   Jev usage) and a plain ``chars/4`` are both reported, each with its method name.
   No number here is ever presented as the provider's own unless the provider said it.
2. **The automatic trigger** — the conversation is measured before every request and
   compaction runs when it crosses ``budget_tokens × trigger_fraction`` (default
   128 000 × 0.75). Configuration comes from ``--budget`` / ``--trigger-fraction``,
   then ``SKALDIR_CONTEXT_BUDGET`` / ``SKALDIR_CONTEXT_TRIGGER_FRACTION``, then the
   defaults; the report always names which one supplied the value.
3. **The preserved set and its verification** — the system prompt, the goal, the
   criteria, the current user turn and the last tool result are recorded *before* the
   compaction by sha256, and re-checked *after* it in the same relative order. The
   pinning window is widened when the configured one would not cover them, and if the
   check ever fails the compaction is **refused** and the original history is kept.
4. **The fallback** — the port raises instead of guessing (a provider failure, a
   malformed answer, a history that cannot be fitted). Any such failure is caught, the
   uncompacted history is used, and the run says so (``fallback: true`` plus the typed
   error text) instead of pretending it compacted.
5. **A dry run and a usage report** — ``skaldir context compact --dry-run`` prints
   exactly what *would* be dropped (per call) and what would stay verbatim;
   ``skaldir context usage`` reports the budget state of a real transcript.

The live seam is :func:`attach` / :class:`CompactingClient`: it wraps the client a
running loop already holds, so compaction happens on the exact request the loop is
about to send, while the loop's own bookkeeping (and its transcript) keep the full
history. No edit to ``agent_loop.py`` is needed::

    from skaldir.context_manager import attach
    attach(loop, goal=task)          # loop.client is now compaction-aware

What this module does NOT do: it never rewrites a kept message into a summary (the
port only keeps, shortens or removes), it never touches the port itself, it never
invents a token count, and it never compacts when no asker is configured — an asker
that cannot be built is reported as blocked, not faked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import cli_shared as shared
from . import env
from .jevcomp import (
    CompactOptions,
    JevAnswerError,
    JevAsker,
    JevCompactionError,
    JevRequestError,
    JevResponse,
    Message,
    ToolCall,
    ToolResult,
    ToolUse,
    collect_tool_calls,
    compact,
    estimate_tokens,
    message_chars,
    resolve_options,
    wire_json,
)

__all__ = [
    "ACCOUNTING_ESTIMATE_NOTE",
    "DEFAULT_BUDGET_TOKENS",
    "DEFAULT_TRIGGER_FRACTION",
    "AskerUnavailable",
    "CompactingClient",
    "ContextError",
    "ContextManager",
    "ContextReport",
    "HistoryError",
    "Preserved",
    "PreservedCheck",
    "ProviderJevAsker",
    "TranscriptView",
    "TriggerDecision",
    "accounting_for",
    "attach",
    "build_parser",
    "build_provider_asker",
    "contract_entries",
    "fixture_asker",
    "load_history",
    "main",
    "message_digest",
    "preserved_items",
    "register_cli",
    "register_contract_entries",
    "verify_preserved",
    "wrap_client",
]

# --------------------------------------------------------------------------- #
# configuration                                                                 #
# --------------------------------------------------------------------------- #

#: A declared context budget in tokens. Not a measurement of anything: the number the
#: operator says the model has (128k is the common floor of the models we target).
DEFAULT_BUDGET_TOKENS = 128_000

#: Compact once this share of the budget is *estimated* (or measured) to be in use.
#: Above the halfway mark a long run tends to be one tool result away from the ceiling.
DEFAULT_TRIGGER_FRACTION = 0.75

#: The one sentence that must ride with every token number this module produces.
ACCOUNTING_ESTIMATE_NOTE = (
    "token counts are estimates (no tokenizer is bundled): the jevcomp estimator and "
    "chars/4 are reported side by side, each with its method; only 'measured' carries "
    "provider-reported values"
)

ENV_BUDGET = "CONTEXT_BUDGET"
ENV_TRIGGER_FRACTION = "CONTEXT_TRIGGER_FRACTION"
ENV_COMPACTION = "CONTEXT_COMPACTION"
ENV_PRESERVE_RECENT = "CONTEXT_PRESERVE_RECENT"
ENV_TARGET = "CONTEXT_TARGET"
ENV_MODE = "CONTEXT_COMPACTION_MODE"

#: The compaction modes (owner requirement). ``jev`` is today's behaviour — the
#: trigger fires at the budget fraction and Jev judges the per-call plan. ``always``
#: runs the same existing trigger machinery but fires deterministically on every
#: ``maybe_compact`` call, so compaction works when Jev is off; ``off`` disables.
COMPACTION_MODES = ("jev", "always", "off")
DEFAULT_COMPACTION_MODE = "jev"

#: The port's own defaults, re-stated here so a report can say "default" instead of
#: "whatever the port resolves to today".
PORT_DEFAULTS = resolve_options(None)


class ContextError(RuntimeError):
    """Base of this module's failures (all of them are reported, never swallowed)."""


class HistoryError(ContextError):
    """The history to measure or compact could not be read as messages."""

    def __init__(self, message: str, *, code: str = "usage") -> None:
        super().__init__(message)
        self.code = code


class AskerUnavailable(ContextError):
    """No asker could be built — e.g. the live provider is not configured."""


# --------------------------------------------------------------------------- #
# transcript shapes: the loop's messages <-> the port's messages                #
# --------------------------------------------------------------------------- #


def _mapping(message: Any) -> Mapping[str, Any] | None:
    return message if isinstance(message, Mapping) else None


def _loop_tool_calls(message: Any) -> list[Any]:
    """The ``tool_calls`` array of an OpenAI/loop-shaped message (never a port message)."""
    if isinstance(message, Message):
        return []
    data = _mapping(message)
    return list((data or {}).get("tool_calls") or [])


def _loop_tool_call_id(message: Any) -> str:
    if isinstance(message, Message):
        return ""
    data = _mapping(message)
    value = (data or {}).get("tool_call_id")
    return "" if value is None else str(value)


def _content_text(message: Any) -> str:
    """The text a *loop-shaped* message carries (``content``, which the port calls ``text``)."""
    data = _mapping(message)
    if data is None:
        return ""
    content = data.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return wire_json(content) if isinstance(content, (Mapping, list)) else str(content)


def _raw_arguments(call: Any) -> str:
    """The raw JSON argument string of a loop tool call (kept byte-exact when it exists)."""
    data = _mapping(call) or {}
    function = data.get("function")
    if isinstance(function, Mapping):
        raw = function.get("arguments")
        if isinstance(raw, str) and raw.strip():
            return raw
    return ""


def _arguments_value(call: Any) -> Any:
    raw = _raw_arguments(call)
    if not raw:
        function = (_mapping(call) or {}).get("function")
        value = function.get("arguments") if isinstance(function, Mapping) else None
        return value
    try:
        return json.loads(raw)
    except ValueError:
        return raw


class TranscriptView:
    """One loop transcript as the port sees it, with the originals kept for identity.

    The port reads ``text`` / ``toolUses`` / ``toolResults``; a live loop speaks
    ``content`` / ``tool_calls`` / ``tool_call_id``. Nothing is lost in either
    direction: a message the port did not touch is handed back as **the exact object
    it came in as**, and a message the port rebuilt is rebuilt in the loop's own
    shape (with the original raw argument string and the original tool name restored).
    """

    def __init__(self, messages: Sequence[Any]) -> None:
        self.originals: list[Any] = list(messages)
        self._origin: dict[int, Any] = {}
        self._arguments: dict[str, str] = {}
        self._tool_names: dict[str, str] = {}
        self.port: list[Message] = [self._to_port(message) for message in self.originals]
        for original, converted in zip(self.originals, self.port, strict=True):
            self._origin[id(converted)] = original
            data = _mapping(original) or {}
            name = str(data.get("name") or "")
            call_id = _loop_tool_call_id(original) or ""
            if name and call_id:
                self._tool_names[call_id] = name
            for call in _loop_tool_calls(original):
                raw = _raw_arguments(call)
                key = str((_mapping(call) or {}).get("id") or "")
                if key and raw:
                    self._arguments[key] = raw

    # -- forward ------------------------------------------------------------ #

    def _to_port(self, message: Any) -> Message:
        if isinstance(message, Message):
            return message
        if _loop_tool_calls(message) or _loop_tool_call_id(message):
            return self._openai_to_port(message)
        converted = Message.from_any(message)
        if converted.text == "" and _content_text(message):
            return Message(
                role=converted.role,
                text=_content_text(message),
                tool_uses=converted.tool_uses,
                tool_results=converted.tool_results,
            )
        return converted

    def _openai_to_port(self, message: Any) -> Message:
        data = _mapping(message) or {}
        uses = [
            ToolUse(
                tool_use_id=str((_mapping(call) or {}).get("id") or ""),
                tool=str(((_mapping(call) or {}).get("function") or {}).get("name") or ""),
                input=_arguments_value(call),
                text=None,
            )
            for call in _loop_tool_calls(message)
        ]
        call_id = _loop_tool_call_id(message)
        results = (
            [ToolResult(tool_use_id=call_id, text=_content_text(message))]
            if call_id
            else []
        )
        return Message(
            role=str(data.get("role") or "user"),
            text="" if uses or results else _content_text(message),
            tool_uses=uses,
            tool_results=results,
        )

    # -- back --------------------------------------------------------------- #

    def back(self, kept: Sequence[Any]) -> list[Any]:
        """The compacted messages in the loop's shape (untouched ones by identity)."""
        out: list[Any] = []
        for item in kept:
            original = self._origin.get(id(item))
            if original is not None:
                out.append(original)
            else:
                rebuilt = self._port_to_loop(item)
                if rebuilt is not None:
                    out.append(rebuilt)
        return out

    def _port_to_loop(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, Message):
            return dict(_mapping(message) or {"role": "user", "content": str(message)})
        role = message.role
        uses = list(message.tool_uses)
        results = list(message.tool_results)
        if uses:
            return {
                "role": role,
                "content": message.text,
                "tool_calls": [
                    {
                        "id": block.tool_use_id,
                        "type": "function",
                        "function": {
                            "name": block.tool or "",
                            "arguments": self._arguments.get(block.tool_use_id)
                            or _argument_text(block.input),
                        },
                    }
                    for block in uses
                ],
            }
        if results:
            block = results[0]
            return {
                "role": role,
                "tool_call_id": block.tool_use_id,
                "name": self._tool_names.get(block.tool_use_id, ""),
                "content": block.text,
            }
        return {"role": role, "content": message.text}


# --------------------------------------------------------------------------- #
# accounting (measured vs estimated, never mixed)                                #
# --------------------------------------------------------------------------- #


def _stable_json(value: Any) -> str:
    """One deterministic serialisation, for digests and reports (never for the wire)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def message_digest(message: Any) -> str:
    """sha256 over the whole message (port messages via ``to_wire``)."""
    payload = message.to_wire() if hasattr(message, "to_wire") else message
    return _sha256(_stable_json(payload))


def estimate_for(messages: Sequence[Any]) -> dict[str, Any]:
    """The full estimate block for one transcript — clearly labelled as an estimate.

    ``chars_measured`` is exact (Python counts the characters we hold). Both token
    numbers are estimates: the port's calibrated estimator and the plain ``chars/4``
    the rest of the product uses. The estimate is taken over the port's wire form of
    the transcript, which is what that estimator was calibrated on.
    """
    view = TranscriptView(messages)
    chars = sum(message_chars(item) for item in view.port)
    text = wire_json([item.to_wire() for item in view.port])
    return {
        "messages": len(messages),
        "chars_measured": chars,
        "tokens_estimate_jevcomp": int(estimate_tokens(text)),
        "tokens_estimate_chars4": int(chars // 4),
        "estimate_method": (
            "jevcomp.estimate_tokens over the Jev wire form (ported from upstream; lands "
            "2-18% above the provider's true count on real transcripts) / chars/4"
        ),
        "exact": False,
        "counts": "chars are counted exactly; both token numbers are estimates",
        "note": ACCOUNTING_ESTIMATE_NOTE,
    }


def accounting_for(
    messages: Sequence[Any],
    *,
    measured: Mapping[str, Any] | None = None,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    trigger_fraction: float = DEFAULT_TRIGGER_FRACTION,
    budget_source: str = "default",
) -> dict[str, Any]:
    """Measured facts and labelled estimates for one transcript, plus the budget state.

    The separation is the point: ``measured`` carries only exact counts and what the
    provider itself reported; ``estimated`` carries only our two token estimates, each
    with its method. A caller that mixes them has a bug, not a rounding error.
    """
    estimate = estimate_for(messages)
    measured_block: dict[str, Any] = {
        "messages": estimate["messages"],
        "chars": estimate["chars_measured"],
        "provider": {str(key): int(value) for key, value in dict(measured or {}).items()},
        "provider_reported": bool(measured),
    }
    used = int(measured_block["provider"].get("prompt_tokens", 0) or 0)
    used_source = "provider_measured" if used else "estimate"
    if not used:
        used = int(estimate["tokens_estimate_jevcomp"])
    threshold = int(budget_tokens * trigger_fraction)
    return {
        "measured": measured_block,
        "estimated": {
            "tokens": estimate["tokens_estimate_jevcomp"],
            "tokens_chars4": estimate["tokens_estimate_chars4"],
            "method": estimate["estimate_method"],
            "exact": False,
            "note": ACCOUNTING_ESTIMATE_NOTE,
        },
        "budget": {
            "tokens": int(budget_tokens),
            "source": budget_source,
            "trigger_fraction": float(trigger_fraction),
            "threshold_tokens": threshold,
            "used_tokens": int(used),
            "used_source": used_source,
            "remaining_tokens": int(budget_tokens) - int(used),
            "state": _budget_state(used, threshold, int(budget_tokens)),
        },
    }


def _budget_state(used: int, threshold: int, budget_tokens: int) -> str:
    """``below_trigger`` / ``at_trigger`` / ``over_budget`` — one place, no nesting."""
    if used > budget_tokens:
        return "over_budget"
    return "at_trigger" if used >= threshold else "below_trigger"


def _argument_text(value: Any) -> str:
    """A tool call's input as the text a request carries (never a JSON crash)."""
    return value if isinstance(value, str) else wire_json(value)


# --------------------------------------------------------------------------- #
# the preserved set (hard rule)                                                  #
# --------------------------------------------------------------------------- #

PRESERVED_LABELS = (
    "system_prompt",
    "goal",
    "current_user_turn",
    "last_tool_result",
)


@dataclass(frozen=True)
class Preserved:
    """One item that must survive a compaction, recorded by digest before it runs."""

    label: str
    kind: str  # "message" | "out_of_band"
    index: int
    sha256: str
    chars: int
    note: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "index": self.index,
            "sha256": self.sha256,
            "sha256_short": self.sha256[:16],
            "chars": self.chars,
            "note": self.note,
        }


@dataclass(frozen=True)
class PreservedCheck:
    """The post-compaction verdict for one preserved item."""

    label: str
    kind: str
    sha256: str
    found: bool
    order_ok: bool
    detail: str

    @property
    def ok(self) -> bool:
        return self.found and self.order_ok

    def to_wire(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "sha256": self.sha256,
            "sha256_short": self.sha256[:16],
            "found": self.found,
            "order_ok": self.order_ok,
            "ok": self.ok,
            "detail": self.detail,
        }


def _role_of(message: Any) -> str:
    if isinstance(message, Message):
        return message.role
    return str((_mapping(message) or {}).get("role") or "")


def _has_tool_results(message: Any) -> bool:
    if isinstance(message, Message):
        return bool(message.tool_results)
    data = _mapping(message) or {}
    if data.get("tool_results") or data.get("toolResults"):
        return True
    return bool(_loop_tool_call_id(message))


def preserved_items(
    messages: Sequence[Any],
    *,
    goal: str = "",
    criteria: Iterable[str] = (),
    system_prompt: str = "",
) -> list[Preserved]:
    """The five things a compaction may never drop, recorded before it runs.

    ``system_prompt``, ``goal`` and ``criteria`` are **out of band** when they are not
    in the transcript (a run passes them separately); they are still recorded, because
    a report that claims "nothing important was dropped" has to say where the goal
    lived. The two in-band items — the current user turn and the last tool result —
    carry their index and digest.
    """
    items: list[Preserved] = []
    criteria_list = [str(item) for item in criteria]
    if goal:
        items.append(
            Preserved("goal", "out_of_band", -1, _sha256(goal), len(goal), "the run's goal")
        )
    for position, text in enumerate(criteria_list):
        items.append(
            Preserved(
                f"criterion:{position}",
                "out_of_band",
                -1,
                _sha256(text),
                len(text),
                "an acceptance criterion of the run",
            )
        )

    system_text = ""
    system_index = -1
    for index, message in enumerate(messages):
        if _role_of(message) == "system":
            system_index = index
            system_text = _content_text(message)
            break
    if system_index >= 0:
        items.append(
            Preserved(
                "system_prompt",
                "message",
                system_index,
                message_digest(messages[system_index]),
                len(system_text),
                "messages[0] of the live request",
            )
        )
    elif system_prompt:
        items.append(
            Preserved(
                "system_prompt", "out_of_band", -1, _sha256(system_prompt), len(system_prompt),
                "passed out of band (no system message in this history)",
            )
        )

    user_index = -1
    for index, message in enumerate(messages):
        if _role_of(message) == "user" and not _has_tool_results(message):
            if _content_text(message).strip():
                user_index = index
    if user_index >= 0:
        items.append(
            Preserved(
                "current_user_turn",
                "message",
                user_index,
                message_digest(messages[user_index]),
                len(_content_text(messages[user_index])),
                f"messages[{user_index}] (the newest human turn)",
            )
        )

    result_index = -1
    for index, message in enumerate(messages):
        if _has_tool_results(message):
            result_index = index
    if result_index >= 0:
        items.append(
            Preserved(
                "last_tool_result",
                "message",
                result_index,
                message_digest(messages[result_index]),
                len(_content_text(messages[result_index])),
                f"messages[{result_index}] (the newest tool output)",
            )
        )
    return items


def verify_preserved(
    preserved: Sequence[Preserved],
    after: Sequence[Any],
    *,
    goal: str = "",
    criteria: Iterable[str] = (),
    system_prompt: str = "",
) -> list[PreservedCheck]:
    """Check every preserved item against the compacted history.

    An out-of-band item (goal, criteria, a system prompt that lives outside the
    transcript) cannot be touched by a compaction of the transcript, so it is compared
    with the value the caller still holds — saying that out loud beats a green tick with
    no referent. An in-band item must be present **byte-identical** (same digest) and the
    preserved items must appear in the same relative order as before, because a
    compaction removes messages and never reorders them.
    """
    digests = [message_digest(message) for message in after]
    positions: dict[str, list[int]] = {}
    for position, digest in enumerate(digests):
        positions.setdefault(digest, []).append(position)
    criteria_list = [str(item) for item in criteria]
    checks: list[PreservedCheck] = []
    cursor = 0
    ordered = sorted(preserved, key=lambda entry: (entry.kind == "message", entry.index))
    for item in ordered:
        if item.kind == "out_of_band":
            held, label = _held_value(item, goal, criteria_list, system_prompt)
            if held is None:
                checks.append(
                    PreservedCheck(item.label, item.kind, item.sha256, False, False, label)
                )
                continue
            same = _sha256(held) == item.sha256
            checks.append(
                PreservedCheck(
                    item.label,
                    item.kind,
                    item.sha256,
                    same,
                    same,
                    f"out of band: {label}"
                    + (" — byte-identical" if same else " — CHANGED since it was recorded"),
                )
            )
            continue
        where = positions.get(item.sha256, [])
        found = bool(where)
        order_ok = found and max(where) >= cursor
        if found:
            cursor = max(where)
        checks.append(
            PreservedCheck(
                item.label,
                item.kind,
                item.sha256,
                found,
                order_ok,
                "verbatim in the compacted history" if found and order_ok
                else ("present but reordered" if found else "absent from the compacted history"),
            )
        )
    return checks


def _held_value(
    item: Preserved, goal: str, criteria: list[str], system_prompt: str
) -> tuple[str | None, str]:
    """What the caller still holds for an out-of-band item, and what it is."""
    if item.label == "goal":
        return (goal or None, "the run's goal")
    if item.label == "system_prompt":
        return (system_prompt or None, "the system prompt handed to the request")
    if item.label.startswith("criterion:"):
        try:
            position = int(item.label.split(":", 1)[1])
        except ValueError:
            return None, "a criterion nobody holds"
        if 0 <= position < len(criteria):
            return criteria[position], f"criterion #{position}"
        return None, "a criterion nobody holds"
    return None, "an item with no holder"


# --------------------------------------------------------------------------- #
# the trigger and the per-compaction report                                      #
# --------------------------------------------------------------------------- #


@dataclass
class TriggerDecision:
    """Whether compaction should run now, and on what evidence."""

    fire: bool
    reason: str
    budget_tokens: int
    budget_source: str
    trigger_fraction: float
    threshold_tokens: int
    used_tokens: int
    used_source: str
    mode: str = DEFAULT_COMPACTION_MODE

    def to_wire(self) -> dict[str, Any]:
        return {
            "fire": self.fire,
            "reason": self.reason,
            "budget_tokens": self.budget_tokens,
            "budget_source": self.budget_source,
            "trigger_fraction": self.trigger_fraction,
            "threshold_tokens": self.threshold_tokens,
            "used_tokens": self.used_tokens,
            "used_source": self.used_source,
            "mode": self.mode,
            "estimate": True,
        }


@dataclass
class ContextReport:
    """Before/after of one compaction attempt — dropped, shortened, kept verbatim."""

    reason: str
    fired: bool
    applied: bool
    dry_run: bool
    trigger: dict[str, Any] = field(default_factory=dict)
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    freed: dict[str, Any] = field(default_factory=dict)
    preserve: dict[str, Any] = field(default_factory=dict)
    preserved: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    kept_verbatim: list[dict[str, Any]] = field(default_factory=list)
    shortened: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    fallback: bool = False
    refused: bool = False
    error: str = ""
    error_type: str = ""
    ms: int = 0
    notes: list[str] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "fired": self.fired,
            "applied": self.applied,
            "dry_run": self.dry_run,
            "fallback": self.fallback,
            "refused": self.refused,
            "error": self.error,
            "error_type": self.error_type,
            "duration_ms": self.ms,
            "trigger": self.trigger,
            "before": self.before,
            "after": self.after,
            "freed": self.freed,
            "preserve": self.preserve,
            "preserved": self.preserved,
            "dropped": self.dropped,
            "kept_verbatim": self.kept_verbatim,
            "shortened": self.shortened,
            "removed": self.removed,
            "stats": self.stats,
            "notes": self.notes,
        }


def _action_rows(
    calls: Sequence[ToolCall],
    result: Any,
    before_messages: Sequence[Any],
    after_messages: Sequence[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Per-call rows: what was dropped, what was shortened, what stayed verbatim."""
    decisions = {decision.id: decision for decision in getattr(result, "decisions", [])}
    dropped: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    shortened: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    before_digests = [message_digest(message) for message in before_messages]
    after_digests = {message_digest(message) for message in after_messages}
    for call in calls:
        decision = decisions.get(call.id)
        if decision is None:
            continue
        row = {
            "id": call.id,
            "tool": call.tool,
            "tool_use_id": call.tool_use_id,
            "pinned": bool(call.pinned),
            "result_chars": int(call.result_chars),
            "keep_call": float(getattr(decision, "keep_call", 0.0)),
            "keep_result": float(getattr(decision, "keep_result", 0.0)),
            "action": str(getattr(decision, "action", "")),
            "reason": str(getattr(decision, "reason", "")),
        }
        if row["action"] == "drop_call":
            dropped.append(row)
        elif row["action"] == "drop_result":
            shortened.append(row)
        else:
            kept.append(row)
    keep_ids = {row["tool_use_id"] for row in kept}
    for position, digest in enumerate(before_digests):
        above = [call.tool_use_id for call in calls if call.call_index == position]
        if above and not any(item in keep_ids for item in above) and digest not in after_digests:
            removed.append({"index": position, "tool_use_id": above[0], "digest": digest[:16]})
    return dropped, kept, shortened, removed


# --------------------------------------------------------------------------- #
# the manager                                                                    #
# --------------------------------------------------------------------------- #


class ContextManager:
    """Per-run budget accounting and the automatic compaction trigger.

    One manager per run (or per conversation): it holds the budget, the asker and the
    pinned window, and it records every report it produced. It is deliberately passive
    — it compacts when :meth:`maybe_compact` says the trigger fired, and it never
    changes a message the port did not change.
    """

    def __init__(
        self,
        *,
        budget_tokens: int | None = None,
        trigger_fraction: float | None = None,
        goal: str = "",
        criteria: Iterable[str] = (),
        target: str = "",
        asker: JevAsker | None = None,
        enabled: bool | None = None,
        preserve_recent_messages: int | None = None,
        keep_threshold: float | None = None,
        truncate_head_chars: int | None = None,
        max_state_tokens: int | None = None,
        max_request_tokens: int | None = None,
        max_concurrency: int = 1,
        clock: Any = time.monotonic,
        emit: Any = None,
        mode: str | None = None,
    ) -> None:
        if budget_tokens is None:
            raw = env.getenv(ENV_BUDGET)
            source = env.source(ENV_BUDGET)
            budget_tokens = _positive_int(raw, DEFAULT_BUDGET_TOKENS)
            self.budget_source = source or "default"
        else:
            budget_tokens = _positive_int(budget_tokens, DEFAULT_BUDGET_TOKENS)
            self.budget_source = "--budget"
        self.budget_tokens = int(budget_tokens)

        if trigger_fraction is None:
            raw_fraction = env.getenv(ENV_TRIGGER_FRACTION)
            trigger_fraction = _fraction(raw_fraction, DEFAULT_TRIGGER_FRACTION)
        self.trigger_fraction = _fraction(trigger_fraction, DEFAULT_TRIGGER_FRACTION)

        self.enabled = env.flag(ENV_COMPACTION, True) if enabled is None else bool(enabled)
        self.mode = self._resolve_mode(mode)
        self.goal = str(goal or "")
        self.criteria = tuple(str(item) for item in criteria)
        self.target = str(target or env.getenv(ENV_TARGET, "") or "")
        self.asker = asker
        self.max_concurrency = max(0, int(max_concurrency))
        self._clock = clock
        self.options = CompactOptions(
            goal=self.goal or None,
            keep_threshold=keep_threshold,
            preserve_recent_messages=_preserve_from_env(preserve_recent_messages),
            truncate_head_chars=truncate_head_chars,
            max_state_tokens=max_state_tokens,
            max_request_tokens=max_request_tokens,
            max_concurrency=self.max_concurrency,
        )
        self.reports: list[ContextReport] = []
        self.usage_snapshots: list[dict[str, Any]] = []
        self.last_prompt_tokens = 0
        #: MVP/87 — where a compaction is *published*. ``emit(kind, payload)`` is the same
        #: subscription the Jev façade and the System-One service use, so one emitter serves
        #: every runtime path; ``None`` = the old behaviour (the report stays in this run).
        self.emit = emit
        self._published = 0

    # -- accounting --------------------------------------------------------- #

    def _resolve_mode(self, mode: str | None) -> str:
        """The compaction mode: the explicit argument, else ``$CONTEXT_COMPACTION_MODE``.

        An unknown value is refused loudly (a typo'd mode must not silently mean
        ``jev``), and ``off`` implies ``enabled=False`` so both spellings agree.
        """
        raw = str(mode if mode is not None else env.getenv(ENV_MODE) or "").strip().lower()
        if not raw:
            return DEFAULT_COMPACTION_MODE
        if raw not in COMPACTION_MODES:
            raise ValueError(
                f"unknown compaction mode {raw!r} — one of {', '.join(COMPACTION_MODES)}"
            )
        return raw

    def accounting(self, messages: Sequence[Any]) -> dict[str, Any]:
        """The budget state of a transcript, with its measurement provenance."""
        measured = (
            {"prompt_tokens": self.last_prompt_tokens} if self.last_prompt_tokens else None
        )
        return accounting_for(
            messages,
            measured=measured,
            budget_tokens=self.budget_tokens,
            trigger_fraction=self.trigger_fraction,
            budget_source=self.budget_source,
        )

    def trigger(self, messages: Sequence[Any]) -> TriggerDecision:
        """Fire when the used budget crosses ``budget × fraction``.

        The measured provider count wins when there is one (it is the only number that
        is not an estimate), and the decision says which of the two was used.
        """
        state = self.accounting(messages)["budget"]
        used = int(state["used_tokens"])
        threshold = int(state["threshold_tokens"])
        fire = used >= threshold
        source = str(state["used_source"])
        if self.mode == "off" or not self.enabled:
            reason = (
                "compaction disabled (mode: off)"
                if self.mode == "off"
                else "compaction disabled (SKALDIR_CONTEXT_COMPACTION=0)"
            )
            fire = False
        elif self.mode == "always":
            # owner requirement: compaction without Jev — the deterministic path
            # fires on EVERY maybe_compact call, using the same existing trigger
            # machinery (the threshold numbers below are still reported so the
            # report says which evidence would have fired the jev trigger).
            fire = True
            reason = (
                f"mode always: compacting every call (at trigger would be {used} tokens "
                f"({source}) vs {threshold}, {self.trigger_fraction:.2f} of "
                f"{self.budget_tokens})"
            )
        elif fire:
            reason = (
                f"at trigger: {used} tokens ({source}) >= {threshold} "
                f"({self.trigger_fraction:.2f} of {self.budget_tokens})"
            )
        else:
            reason = (
                f"below trigger: {used} tokens ({source}) < {threshold} "
                f"({self.trigger_fraction:.2f} of {self.budget_tokens})"
            )
        return TriggerDecision(
            fire=fire,
            reason=reason,
            budget_tokens=self.budget_tokens,
            budget_source=self.budget_source,
            trigger_fraction=self.trigger_fraction,
            threshold_tokens=threshold,
            used_tokens=used,
            used_source=source,
            mode=self.mode,
        )

    def note_usage(self, usage: Mapping[str, Any] | None) -> None:
        """Record what the provider itself reported (the only non-estimated numbers)."""
        reported = {
            str(key): int(value) for key, value in dict(usage or {}).items() if _is_int(value)
        }
        prompt = int(reported.get("prompt_tokens", 0) or 0)
        if not reported:
            return
        self.usage_snapshots.append(reported)
        if prompt:
            self.last_prompt_tokens = prompt

    # -- the preserved set --------------------------------------------------- #

    def preserved(self, messages: Sequence[Any]) -> list[Preserved]:
        return preserved_items(
            messages,
            goal=self.goal,
            criteria=self.criteria,
            system_prompt=self.target,
        )

    def preserve_window(
        self, messages: Sequence[Any], items: Sequence[Preserved]
    ) -> dict[str, Any]:
        """The pinning window, widened so every at-risk preserved item is pinned.

        The port pins ``messages[0]`` and the newest ``preserve_recent_messages``; a
        message the port may actually touch is one that carries a tool call or a tool
        result (it removes a message only when nothing is left of it, and never rewrites
        text). So the window is widened to cover exactly the preserved items that carry
        tools — the last tool result in a live run — while a text-only preserved item
        (the system prompt, the human turn) needs no window: the port cannot touch it,
        and the digest check verifies that after the fact anyway.

        Widening for *every* preserved message instead would pin the whole history in an
        autonomous run (the human turn sits at index 1 while tool results pile up), which
        is how a compaction silently becomes a no-op.
        """
        count = len(messages)
        configured = self.options.preserve_recent_messages
        needed = 0
        counted: list[str] = []
        for item in items:
            # messages[0] needs no window: the port pins it unconditionally.
            if item.kind != "message" or item.index <= 0:
                continue
            if not _carries_tools(messages[item.index]):
                continue
            needed = max(needed, count - item.index)
            counted.append(item.label)
        effective = max(int(configured or 0), needed)
        reason = "configured"
        if needed and effective != int(configured or 0):
            reason = "widened to pin the preserved set"
        return {
            "configured": int(configured or 0),
            "effective": int(effective),
            "pinned_from_index": max(0, count - effective),
            "needed_for_preserved": int(needed),
            "counted": counted,
            "reason": reason,
        }

    # -- compaction ---------------------------------------------------------- #

    def compact(
        self, messages: Sequence[Any], *, dry_run: bool = False
    ) -> tuple[list[Any], ContextReport]:
        """Run the ported compactor once; keep the original history on any failure.

        ``dry_run=True`` still runs the compactor (that is the only way to show exactly
        what *would* be dropped) and throws the result away: the caller gets its own
        messages back, unchanged, plus the full plan.
        """
        started = self._clock()
        original = list(messages)
        items = self.preserved(original)
        window = self.preserve_window(original, items)
        trigger = self.trigger(original)
        before = estimate_for(original)
        options = CompactOptions(
            goal=self.options.goal,
            keep_threshold=self.options.keep_threshold,
            preserve_recent_messages=window["effective"],
            truncate_head_chars=self.options.truncate_head_chars,
            max_state_tokens=self.options.max_state_tokens,
            max_request_tokens=self.options.max_request_tokens,
            max_concurrency=self.max_concurrency,
        )
        report = ContextReport(
            reason="compacted" if not dry_run else "dry_run",
            fired=True,
            applied=False,
            dry_run=bool(dry_run),
            trigger=trigger.to_wire(),
            before=before,
            preserve=window,
            # recorded by digest before the run; ``checked`` turns true once a compaction
            # actually went through and the digests were re-verified against the result
            preserved=[dict(item.to_wire(), checked=False) for item in items],
        )
        report.notes.append(ACCOUNTING_ESTIMATE_NOTE)
        if self.asker is None:
            report.reason = "no_asker"
            report.error_type = "AskerUnavailable"
            report.error = (
                "no asker is configured: build one with build_provider_asker() or "
                "fixture_asker(); the history is used uncompacted"
            )
            report.after = before
            report.ms = int((self._clock() - started) * 1000)
            self.reports.append(report)
            return original, report

        view = TranscriptView(original)
        try:
            result = compact(view.port, self.asker, options)
        except JevCompactionError as exc:
            report.reason = "fallback_compactor_error"
            report.error_type = type(exc).__name__
            report.error = str(exc)
            report.fallback = True
            report.after = before
            report.notes.append(
                "the compactor raised, so the run continues with the UNCOMPACTED history "
                "(the upstream design: fail loud, let the caller fall back)"
            )
            report.notes.append(
                "the preserved set was NOT re-checked: no compaction was applied "
                "(preserved_ok is null, not true)"
            )
            report.ms = int((self._clock() - started) * 1000)
            self.reports.append(report)
            return original, report
        except Exception as exc:  # noqa: BLE001 — a compactor bug is reported, never a fake success
            report.reason = "fallback_unexpected_error"
            report.error_type = type(exc).__name__
            report.error = str(exc)
            report.fallback = True
            report.after = before
            report.notes.append(
                "an unexpected error escaped the compactor; the uncompacted history is kept"
            )
            report.notes.append(
                "the preserved set was NOT re-checked: no compaction was applied "
                "(preserved_ok is null, not true)"
            )
            report.ms = int((self._clock() - started) * 1000)
            self.reports.append(report)
            return original, report

        rebuilt = view.back(result.messages)
        checks = verify_preserved(
            items, rebuilt, goal=self.goal, criteria=self.criteria, system_prompt=self.target
        )
        report.preserved = [dict(check.to_wire(), checked=True) for check in checks]
        violations = [check.label for check in checks if not check.ok]
        after = estimate_for(rebuilt)
        report.after = after
        report.stats = result.stats.to_wire()
        calls = collect_tool_calls(view.port, window["effective"])
        dropped, kept, shortened, removed = _action_rows(calls, result, original, rebuilt)
        report.dropped = dropped
        report.kept_verbatim = kept
        report.shortened = shortened
        report.removed = removed
        report.freed = {
            "messages_before": before["messages"],
            "messages_after": after["messages"],
            "messages_removed": before["messages"] - after["messages"],
            "chars_before": before["chars_measured"],
            "chars_after": after["chars_measured"],
            "chars_freed": before["chars_measured"] - after["chars_measured"],
            "tokens_estimate_before": before["tokens_estimate_jevcomp"],
            "tokens_estimate_after": after["tokens_estimate_jevcomp"],
            "tokens_estimate_freed": (
                before["tokens_estimate_jevcomp"] - after["tokens_estimate_jevcomp"]
            ),
            "estimate": True,
            "projected": bool(dry_run),
        }
        if violations:
            report.reason = "refused_preserved_violation"
            report.refused = True
            report.error_type = "PreservedSetViolation"
            report.error = "the compaction would drop a preserved item: " + ", ".join(violations)
            report.notes.append(
                "the compacted history was DISCARDED and the original is used — the hard "
                "rule (system prompt, goal, criteria, current user turn, last tool result) "
                "outranks freeing context"
            )
            report.notes.append(
                "the preserved set WAS re-checked and failed, which is why the result was "
                "thrown away (preserved carries the failing item's ok=false)"
            )
            report.after = before
            report.freed = {}
            report.ms = int((self._clock() - started) * 1000)
            self.reports.append(report)
            return original, report
        if dry_run:
            report.notes.append(
                "dry run: what is listed under 'dropped'/'shortened' is what a real "
                "compaction would remove; nothing was applied and no message changed — "
                "'after' and 'freed' are the PROJECTION for that removal (freed.projected)"
            )
            report.ms = int((self._clock() - started) * 1000)
            self.reports.append(report)
            return original, report
        report.applied = True
        if not int(result.stats.requests):
            report.reason = "no_candidate"
            report.notes.append(
                "the port found no tool call outside the pinning window that it could ask "
                "about, so the history is unchanged (nothing was dropped)"
            )
        report.ms = int((self._clock() - started) * 1000)
        self.reports.append(report)
        return rebuilt, report

    def maybe_compact(
        self, messages: Sequence[Any], *, force: bool = False
    ) -> tuple[list[Any], ContextReport | None]:
        """The live entry point: compact only when the trigger says so.

        Returns the messages to send (the original list when nothing fired) and the
        report, or ``None`` when the trigger did not fire — "nothing happened" is not a
        finding and must not be dressed up as one.
        """
        decision = self.trigger(messages)
        if not (decision.fire or force):
            return list(messages), None
        messages_after, report = self.compact(messages, dry_run=False)
        self.publish(report)
        return messages_after, report

    def publish(self, report: ContextReport | None) -> dict[str, Any] | None:
        """Publish one compaction attempt to the emitter this manager was built with (MVP/87).

        The decision here is the port's per-call plan, produced by whatever asker this run was
        built with (``ProviderJevAsker`` = a real model) — so the payload carries the plan
        counts, the timing and the reason, and **no** Jev decision id: the façade was not the
        asker, and inventing an id would dress a port run up as a façade decision. Nothing here
        can break the run: a raising emitter is swallowed.
        """
        if report is None or not callable(self.emit):
            return None
        self._published += 1
        preserve_ok = all(bool(item.get("ok")) for item in report.preserved) \
            if report.preserved else None
        payload = {
            "record": f"compaction_run#{self._published}",
            "authority": "record",
            "kind": "context_compaction",
            "decision_id": "",
            "decision_id_note": (
                "no Jev decision id: the compaction ran through the jevcomp port with this "
                "run's asker, not through skaldir.jev.decide"
            ),
            "asker": type(self.asker).__name__ if self.asker is not None else "",
            "status": report.reason or ("compacted" if report.applied else "not_applied"),
            "applied": bool(report.applied),
            "dry_run": bool(report.dry_run),
            "fallback": bool(report.fallback),
            "refused": bool(report.refused),
            "latency_ms": int(report.ms),
            "trigger": dict(report.trigger),
            "freed": dict(report.freed),
            "stats": {
                key: report.stats.get(key)
                for key in ("calls", "callsDropped", "resultsDropped", "pinned", "kept",
                            "stateStage", "stateTokens")
                if key in report.stats
            },
            "plan": {
                "removed": len(report.removed),
                "shortened": len(report.shortened),
                "kept_verbatim": len(report.kept_verbatim),
                "dropped": len(report.dropped),
            },
            "preserved_ok": preserve_ok,
            "error_type": str(report.error_type or ""),
        }
        try:
            self.emit("context_compaction", payload)
        except Exception as exc:  # noqa: BLE001 — an emitter must never break a run
            return {"published": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return payload

    def summary(self) -> dict[str, Any]:
        """The run's own record: how often it compacted and what it freed."""
        applied = [report for report in self.reports if report.applied]
        return {
            "enabled": self.enabled,
            "budget_tokens": self.budget_tokens,
            "budget_source": self.budget_source,
            "trigger_fraction": self.trigger_fraction,
            "asker": type(self.asker).__name__ if self.asker is not None else "",
            "compactions": len(applied),
            "attempts": len(self.reports),
            "fallbacks": sum(1 for report in self.reports if report.fallback),
            "refused": sum(1 for report in self.reports if report.refused),
            "chars_freed": sum(int(report.freed.get("chars_freed", 0)) for report in applied),
            "tokens_estimate_freed": sum(
                int(report.freed.get("tokens_estimate_freed", 0)) for report in applied
            ),
            "last_prompt_tokens": self.last_prompt_tokens,
            "estimate_note": ACCOUNTING_ESTIMATE_NOTE,
        }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _carries_tools(message: Any) -> bool:
    """True when the port may touch this message: it carries a call or a result."""
    if isinstance(message, Message):
        return bool(message.tool_uses or message.tool_results)
    return bool(_loop_tool_calls(message) or _loop_tool_call_id(message))


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return number if number > 0 else int(default)


def _fraction(value: Any, default: float) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return float(default)
    if not (0.0 < number <= 1.0):
        return float(default)
    return number


def _preserve_from_env(explicit: int | None) -> int | None:
    if explicit is not None:
        return int(explicit)
    raw = env.getenv(ENV_PRESERVE_RECENT)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# the live seam: a client that compacts the request it is handed                 #
# --------------------------------------------------------------------------- #


class CompactingClient:
    """Wraps a running loop's client so compaction happens on the request it sends.

    The loop keeps its own ``messages`` list and its transcript (the full history is
    what a run must be able to replay); this wrapper only changes what goes out. Every
    delegation other than ``chat`` falls through to the wrapped client with
    ``__getattr__``, so ``_provider_note`` / ``config`` and the rest still work.

    **The two runtime hooks the loop writes through this wrapper are forwarded.** The
    loop does not know it is holding a wrapper: it sets ``client.on_delta`` (the
    content sink that puts text on the terminal before the model finished) and
    ``client.retry_gate`` (the §94 commit point a provider retry must stay behind) on
    *whatever object it was handed*. A wrapper that swallowed those writes would leave
    the real provider with no sink and no gate — the run would silently stop
    streaming and the retry guard would be dead — while every *read* still answered
    from the wrapped client, so nothing would look broken. Both are therefore
    explicit pass-through properties, not ``__getattr__`` accidents: a read
    (``loop._detach_delta_sink``, ``stream_stats``) and a write land on the same
    object.
    """

    #: names the loop writes on its client and that must reach the real one. Anything
    #: else is read-only delegation (see ``__getattr__``): a wrapper that forwarded
    #: arbitrary writes would let a caller mutate the provider by accident.
    FORWARDED_ATTRIBUTES: tuple[str, ...] = ("on_delta", "retry_gate")

    def __init__(
        self,
        client: Any,
        manager: ContextManager,
        *,
        on_report: Any = None,
    ) -> None:
        self.client = client
        self.manager = manager
        self.on_report = on_report
        self.reports: list[ContextReport] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["client"], name)

    # -- the write-through hooks ------------------------------------------- #

    def _forwarded(self, name: str) -> Any:
        try:
            return getattr(self.__dict__["client"], name, None)
        except Exception:  # noqa: BLE001 — a client unreadable on this name reads as unset
            return None

    def _set_forwarded(self, name: str, value: Any) -> None:
        setattr(self.__dict__["client"], name, value)

    @property
    def on_delta(self) -> Any:
        return self._forwarded("on_delta")

    @on_delta.setter
    def on_delta(self, value: Any) -> None:
        self._set_forwarded("on_delta", value)

    @property
    def retry_gate(self) -> Any:
        return self._forwarded("retry_gate")

    @retry_gate.setter
    def retry_gate(self, value: Any) -> None:
        self._set_forwarded("retry_gate", value)

    def hook_report(self) -> dict[str, Any]:
        """Where the loop's two hooks really landed — the auditable form of the above.

        ``streaming_ready`` is what a caller reads instead of guessing from a silent
        report: the sink exists on the object the provider will call it on.
        """
        return {
            "schema": "skaldir.context.client_hooks.v1",
            "client": type(self.__dict__["client"]).__name__,
            "forwarded": list(self.FORWARDED_ATTRIBUTES),
            "on_delta_set": self._forwarded("on_delta") is not None,
            "retry_gate_set": self._forwarded("retry_gate") is not None,
            "streaming_ready": self._forwarded("on_delta") is not None,
        }

    def chat(self, messages: Sequence[Any], **kwargs: Any) -> Any:
        """Compact when the trigger fires, then call the wrapped client as always."""
        outgoing, report = self.manager.maybe_compact(list(messages))
        if report is not None:
            self.reports.append(report)
            if callable(self.on_report):
                self.on_report(report)
        answer = self.client.chat(outgoing, **kwargs)
        self.manager.note_usage(getattr(answer, "usage", None))
        return answer


def wrap_client(client: Any, manager: ContextManager, *, on_report: Any = None) -> CompactingClient:
    """The one-line seam for an existing client."""
    return CompactingClient(client, manager, on_report=on_report)


def attach(
    loop: Any, manager: ContextManager | None = None, **manager_kwargs: Any
) -> CompactingClient:
    """Make a live loop compaction-aware: ``attach(loop, goal=task)``.

    The loop is left untouched apart from its client, so a run keeps its own history,
    bounds and transcript; only the request body shrinks.
    """
    manager = manager if manager is not None else ContextManager(**manager_kwargs)
    wrapper = CompactingClient(getattr(loop, "client", None), manager)
    loop.client = wrapper
    return wrapper


# --------------------------------------------------------------------------- #
# askers: the live provider adapter and the offline fixture                      #
# --------------------------------------------------------------------------- #

ASK_PROMPT_HEADER = (
    "You are Jev, deciding what an assistant's tool history must keep. Answer ONLY with "
    'a JSON object of the form {"answers": {"<question>": {"type": "noul", "noul": <0..1>}}}. '
    "noul is the probability that the thing the question asks about should stay in the "
    "history verbatim. Never rewrite, summarise or explain: answer the questions you are asked."
)


class ProviderJevAsker:
    """Answers the port's questions through Skaldir's own LLM provider.

    The upstream plugin POSTs ``{model, state, questions}`` to TypeSafe and validates the
    ``answers`` object. The port has no HTTP client of its own (by design), so this adapter
    hands the same body to a configured Skaldir client and validates the reply exactly the
    way the port validates a wire response: a malformed body raises, the caller falls back.
    """

    def __init__(
        self, client: Any, *, tier: str = "main", prompt_id: str = "jev_compaction"
    ) -> None:
        self.client = client
        self.tier = str(tier or "main")
        self.prompt_id = str(prompt_id or "jev_compaction")
        #: Which config this asker's client came from: ``godsv.jev`` /
        #: ``godsv.providers.entries.jev`` / ``godsv.llm`` / ``explicit`` / ``not_consulted``.
        #: A compaction decision that cannot say which provider judged it is not evidence.
        self.source = "not_consulted"
        #: ``typesafe`` (the System One endpoint) or ``llm`` (a chat model standing in).
        self.decision_layer = ""
        #: Redacted endpoint/model facts — names only, never a credential.
        self.base_url = ""
        self.model = ""
        self.api_key_env = ""

    def ask(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> JevResponse:
        prompt = self.prompt_for(state, questions)
        text = self.answer_text(prompt)
        return JevResponse(answers=parse_answers(text, questions))

    def prompt_for(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> str:
        """The instruction, the state and the questions — no key, no URL, no secret."""
        body = wire_json({"state": state, "questions": questions})
        return f"{ASK_PROMPT_HEADER}\n\nRequest:\n{body}"

    def answer_text(self, prompt: str) -> str:
        client = self.client
        if hasattr(client, "chat"):
            conversation = [
                {"role": "system", "content": ASK_PROMPT_HEADER},
                {"role": "user", "content": prompt},
            ]
            answer = client.chat(
                conversation, tier=self.tier, tools=None, tool_choice=None
            )
            return str(getattr(answer, "text", "") or "")
        if hasattr(client, "complete"):
            try:
                return str(
                    client.complete(prompt_id=self.prompt_id, prompt=prompt, tier=self.tier) or ""
                )
            except TypeError:
                return str(client.complete(prompt) or "")
        raise JevRequestError(
            "the provider client has neither chat() nor complete() — no asker can be built from it"
        )


def parse_answers(text: str, questions: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a provider answer body; raise instead of guessing (the port's rule)."""
    if not str(text).strip():
        raise JevRequestError("Jev returned an empty answer")
    body = _json_object_in(text)
    answers = body.get("answers")
    if not isinstance(answers, Mapping):
        raise JevRequestError("Jev response is missing answers")
    validated: dict[str, Any] = {}
    for name in questions:
        answer = answers.get(name)
        value = answer.get("noul") if isinstance(answer, Mapping) else getattr(answer, "noul", None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise JevAnswerError(f"Invalid Jev answer for {name}")
        probability = float(value)
        if not 0.0 <= probability <= 1.0:
            raise JevAnswerError(f"Invalid Jev answer for {name}")
        validated[str(name)] = {"type": "noul", "noul": probability}
    return validated


def _json_object_in(text: str) -> dict[str, Any]:
    """The first JSON object in a model answer (fences and prose tolerated)."""
    stripped = str(text).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise JevRequestError("Jev returned malformed JSON")
    try:
        parsed = json.loads(stripped[start : end + 1])
    except ValueError as exc:
        raise JevRequestError("Jev returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise JevRequestError("Jev response is missing answers")
    return parsed


def decision_provider_client(
    *, env: Mapping[str, str] | None = None, config_path: str | Path | None = None
) -> tuple[Any, dict[str, str]]:
    """``(client, provenance)`` for the **decision layer's** provider, or a loud refusal.

    The order is the runtime binding's own, so the compaction judge and the Jev runtime
    cannot disagree about which block decides (``skaldir.jev.runtime.provider_resolution``):

    1. ``godsv.jev`` — the block ``skaldir setup`` writes and validates live;
    2. ``godsv.providers.entries.jev`` — the same provider in the named registry;
    3. ``godsv.llm`` — the general provider, the documented fallback for an installation
       that never ran ``skaldir setup``.

    A decision block that is **present but unusable** (a native TypeSafe endpoint with no
    authorized credential, a credential that is not exported) raises
    :class:`AskerUnavailable` naming it: it is never quietly replaced by the general
    endpoint, because that substitution is how "TypeSafe" turns into whichever chat model
    happens to be configured.
    """
    try:
        from .jev.runtime import provider_resolution
    except Exception as exc:  # noqa: BLE001 — a build without the Jev layer says so
        raise AskerUnavailable(
            f"the Jev runtime binding is not available in this build: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        resolution = provider_resolution(env=env, config_path=config_path)
    except Exception as exc:  # noqa: BLE001 — any resolution failure is a missing asker
        raise AskerUnavailable(f"{type(exc).__name__}: {exc}") from exc
    block = dict(resolution.get("block") or {})
    provenance = {
        "source": str(resolution.get("source") or ""),
        "decision_layer": str(resolution.get("decision_layer") or ""),
        "base_url": str(block.get("base_url") or ""),
        "model": str(block.get("model") or ""),
        "api_key_env": str(block.get("api_key_env") or ""),
    }
    client = resolution.get("client")
    if client is None:
        raise AskerUnavailable(
            f"{provenance['source']}: {resolution.get('problem') or 'no decision provider'}"
        )
    return client, provenance


def build_provider_asker(
    *,
    tier: str = "",
    client: Any = None,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> ProviderJevAsker:
    """The production asker: an adapter over the **decision layer's** provider, or a refusal.

    ``tier=""`` means **the cheapest configured tier**: the keep/shorten/drop questions this
    asker carries are a handful of tokens of judgement, so a block that declares ``small`` (or
    ``mid``) is asked there and only a block with no declared tier falls back to the main
    model. An explicit ``tier`` always wins — a caller that wants the main model still gets it.

    The provider is the one the *Jev role* is configured with (``godsv.jev`` first), not the
    general model's: before this, the credential an owner configured for Jev was read by the
    setup flow alone and compaction was judged by ``godsv.llm``
    (:func:`decision_provider_client` has the order and the blocker rule).
    """
    from .llm_provider import cheap_decision_tier

    if client is not None:
        asker = ProviderJevAsker(client, tier=str(tier or "") or cheap_decision_tier(client))
        asker.source = "explicit"
        return asker
    resolved, provenance = decision_provider_client(env=env, config_path=config_path)
    asker = ProviderJevAsker(resolved, tier=str(tier or "") or cheap_decision_tier(resolved))
    asker.source = provenance["source"]
    asker.decision_layer = provenance["decision_layer"]
    asker.base_url = provenance["base_url"]
    asker.model = provenance["model"]
    asker.api_key_env = provenance["api_key_env"]
    return asker


def fixture_asker(probability: float | None = None) -> JevAsker:
    """An offline asker for dry runs — a *simulation*, never the model's judgement.

    Without a probability it is the port's own test stance: calls are kept (0.9), results
    are dropped (0.1). The CLI labels every result produced this way as simulated.
    """
    from .jevcomp.fixture import keep_calls_drop_results, noul_asker

    if probability is None:
        return keep_calls_drop_results()
    return noul_asker(lambda _name: float(probability))


# --------------------------------------------------------------------------- #
# reading a history (a live run transcript, or a plain message file)             #
# --------------------------------------------------------------------------- #


def load_history(
    *,
    transcript: str | Path = "",
    run_id: str = "",
    state_dir: str | Path = "",
) -> tuple[list[Any], str]:
    """Read a history to measure: a message file, or a real run's transcript.

    A run transcript records the *request bodies* the loop sent, so the newest
    ``model.request`` event is the exact history that went out. If it only recorded a
    digest, that is reported as such (exit 3) instead of guessing the messages back.
    """
    if transcript:
        path = Path(transcript).expanduser()
        if not path.is_file():
            raise HistoryError(f"no transcript at {path}", code="not_found")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HistoryError(f"cannot read {path}: {exc}") from exc
        messages, how = _history_from_text(text)
        if messages is None:
            raise HistoryError(
                f"{path} holds no message list ({how})", code="blocked"
            )
        return messages, f"{path} ({how})"
    if run_id:
        from .agent_loop import AgentLoopError, run_path

        try:
            path = run_path(state_dir or ".", run_id)
        except AgentLoopError as exc:
            raise HistoryError(str(exc)) from exc
        if not path.is_file():
            raise HistoryError(
                f"no run '{run_id}' at {path}", code="not_found"
            )
        return load_history(transcript=str(path))
    raise HistoryError("give a history: --transcript PATH or --run-id ID --state-dir DIR")


def _history_from_text(text: str) -> tuple[list[Any] | None, str]:
    stripped = text.strip()
    if not stripped:
        return None, "empty file"
    try:
        parsed = json.loads(stripped)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        return list(parsed), "a JSON message list"
    if isinstance(parsed, dict):
        if isinstance(parsed.get("messages"), list):
            return list(parsed["messages"]), "a JSON object with 'messages'"
        # a one-line JSONL file is also valid JSON, so the event path is tried next
        messages = _history_from_jsonl(stripped)
        if messages is not None:
            return messages, "a JSONL run transcript"
        return None, "a JSON object without 'messages'"
    messages = _history_from_jsonl(stripped)
    if messages is not None:
        return messages, "a JSONL run transcript"
    return None, "neither a message list nor a run transcript"


def _history_from_jsonl(text: str) -> list[Any] | None:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            return None
        if not isinstance(item, dict):
            return None
        events.append(item)
    if not events:
        return None
    if all("role" in event for event in events):
        return list(events)
    requests = [event for event in events if event.get("kind") == "model.request"]
    if not requests:
        return None
    body = requests[-1].get("body")
    if isinstance(body, Mapping) and isinstance(body.get("messages"), list):
        return list(body["messages"])
    return None


# --------------------------------------------------------------------------- #
# the CLI group: ``skaldir context compact|usage``                               #
# --------------------------------------------------------------------------- #

GROUP_HELP = "context budget of a live run: usage, the compaction trigger, dry runs"

COMPACT_HELP = (
    "judge the tool history of a run per call (keep / shorten / remove) and report what "
    "would change; --dry-run applies nothing"
)
USAGE_HELP = "the budget state of a transcript: measured facts, labelled estimates, trigger"

CONTRACT_EVIDENCE: dict[str, tuple[dict[str, Any], ...]] = {
    "context.compact": (
        {
            "ref": "src/skaldir/jevcomp/compact.py",
            "kind": "code",
            "note": "the ported upstream algorithm this command drives (keep/shorten/remove)",
        },
        {
            "ref": "src/skaldir/context_manager.py",
            "kind": "code",
            "note": "the preserved set, the widened pinning window and the fallback",
        },
    ),
    "context.usage": (
        {
            "ref": "src/skaldir/jevcomp/state.py",
            "kind": "code",
            "note": "estimate_tokens — the ported estimator behind every token number here",
        },
        {
            "ref": "src/skaldir/context_manager.py",
            "kind": "code",
            "note": "accounting_for — measured counts vs labelled estimates",
        },
    ),
}

CONTRACT_ENTRIES: dict[str, Any] = {}

#: Resolved once, like every group resolves its own copy of the product contract.
_CONTRACT = shared.product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_BLOCKED = shared.EXIT_BLOCKED
EXIT_NOT_FOUND = shared.EXIT_NOT_FOUND
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entries for this group (built once, installed on wiring)."""
    global CONTRACT_ENTRIES
    if CONTRACT_ENTRIES:
        return CONTRACT_ENTRIES
    contract = shared.product_contract()
    if contract is None or not hasattr(contract, "CommandSpec"):
        return {}
    spec = contract.CommandSpec
    CONTRACT_ENTRIES = {
        "context.compact": spec(
            "ROUTE",
            CONTRACT_EVIDENCE["context.compact"],
            COMPACT_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "report": {"type": "object"},
                    "dry_run": {"type": "boolean"},
                    "applied": {"type": "boolean"},
                    "fallback": {"type": "boolean"},
                    "preserved_ok": {"type": "boolean"},
                    "messages_before": {"type": "integer"},
                    "messages_after": {"type": "integer"},
                    "tokens_estimate_before": {"type": "integer"},
                    "tokens_estimate_after": {"type": "integer"},
                    "asker": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["report", "dry_run", "applied", "fallback", "preserved_ok"],
            },
            notes=(
                "read-only unless --out is given (then the compacted history is written there, "
                "never into the run state); every token number is an estimate and says so; "
                "exit 3 when no asker can be built or the transcript records only a digest"
            ),
        ),
        "context.usage": spec(
            "ROUTE",
            CONTRACT_EVIDENCE["context.usage"],
            USAGE_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "accounting": {"type": "object"},
                    "trigger": {"type": "object"},
                    "source": {"type": "string"},
                    "compactions": {"type": "array"},
                },
                "required": ["accounting", "trigger", "source"],
            },
            notes=(
                "read-only; 'measured' carries exact counts and provider-reported usage, "
                "'estimated' the two labelled token estimates — never mixed"
            ),
        ),
    }
    return CONTRACT_ENTRIES


def register_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added."""
    contract = shared.product_contract()
    if contract is None:
        return []
    installed = shared.install_contract_entries(contract_entries(), contract)
    try:  # the same group also carries project_context_cli's three verbs when it built it
        from . import project_context_cli

        installed += [
            path
            for path in project_context_cli.register_contract_entries()
            if path not in installed
        ]
    except Exception:  # noqa: BLE001 — that module is optional here
        pass
    return installed


def _verbs_action(group: Any) -> Any:
    """The ``_SubParsersAction`` of the ``context`` group (there is no public API for it)."""
    for action in getattr(group, "_actions", []):
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise ContextError("the 'context' group has no subcommands action")


def _build_group(subparsers: Any) -> Any:
    """Create the ``context`` group — reusing the project-context builder when it exists.

    The group is shared: ``project_context_cli`` owns ``show``/``explain``/``fingerprint``
    and a later wiring may register it first. Building it through that module's own public
    builder keeps both halves in one group whichever side runs first, and its contract
    entries are installed with ours so the group is never half-declared.
    """
    try:
        from .project_context_cli import build_context_parser

        return build_context_parser(subparsers)
    except Exception:  # noqa: BLE001 — standalone builds without that module still work
        return subparsers.add_parser("context", help=GROUP_HELP, description=GROUP_HELP)


def _add_verbs(sub: Any, *, share_flags: bool = False) -> None:
    """Add ``compact`` and ``usage`` (idempotent: an existing verb is never re-added)."""
    existing = getattr(sub, "choices", {}) or {}
    if "compact" not in existing:
        parser = sub.add_parser("compact", help=COMPACT_HELP, description=COMPACT_HELP)
        parser.add_argument("--transcript", default="", help="a message file or a run transcript")
        parser.add_argument("--run-id", dest="run_id", default="", help="a run of this state dir")
        parser.add_argument("--state-dir", dest="state_dir", default="")
        parser.add_argument("--goal", default="", help="the run's goal (preserved verbatim)")
        parser.add_argument(
            "--criterion",
            dest="criteria",
            action="append",
            default=[],
            help="an acceptance criterion to preserve (repeatable)",
        )
        parser.add_argument("--budget", type=int, default=None, help="context budget in tokens")
        parser.add_argument("--trigger-fraction", dest="trigger_fraction", type=float, default=None)
        parser.add_argument("--keep-threshold", dest="keep_threshold", type=float, default=None)
        parser.add_argument("--preserve-recent", dest="preserve_recent", type=int, default=None)
        parser.add_argument("--truncate-head", dest="truncate_head", type=int, default=None)
        parser.add_argument(
            "--asker",
            choices=("fixture", "provider"),
            default="fixture",
            help="fixture = an offline simulation (default); provider = the real model",
        )
        parser.add_argument("--probability", type=float, default=None,
                            help="the fixture asker's flat keep probability (default 0.9/0.1)")
        parser.add_argument("--dry-run", dest="dry_run", action="store_true")
        parser.add_argument("--force", action="store_true", help="compact below the trigger too")
        parser.add_argument("--out", default="", help="write the compacted history here")
        parser.add_argument("--text", action="store_true", help="human output")
        if share_flags:
            shared.share_common_flags(parser)
        parser.set_defaults(func=cmd_compact, command_path="context.compact")
    if "usage" not in existing:
        parser = sub.add_parser("usage", help=USAGE_HELP, description=USAGE_HELP)
        parser.add_argument("--transcript", default="", help="a message file or a run transcript")
        parser.add_argument("--run-id", dest="run_id", default="", help="a run of this state dir")
        parser.add_argument("--state-dir", dest="state_dir", default="")
        parser.add_argument("--goal", default="")
        parser.add_argument("--criterion", dest="criteria", action="append", default=[])
        parser.add_argument("--budget", type=int, default=None, help="context budget in tokens")
        parser.add_argument("--trigger-fraction", dest="trigger_fraction", type=float, default=None)
        parser.add_argument("--preserve-recent", dest="preserve_recent", type=int, default=None)
        parser.add_argument("--history", action="store_true", help="include the last compaction")
        parser.add_argument("--text", action="store_true", help="human output")
        if share_flags:
            shared.share_common_flags(parser)
        parser.set_defaults(func=cmd_usage, command_path="context.usage")


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent, and cooperative: the ``context`` group already carries the
    project-context verbs when that module is present, so this only adds ``compact`` and
    ``usage`` and installs the contract entries for all of them.
    """
    action = shared.as_subparsers(subparsers)
    group = (getattr(action, "choices", None) or {}).get("context")
    if group is None:
        group = _build_group(action)
    _add_verbs(_verbs_action(group))
    register_contract_entries()
    return group


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``python -m``)."""
    contract = shared.product_contract()
    base = (
        contract.ContractArgumentParser
        if contract is not None and hasattr(contract, "ContractArgumentParser")
        else argparse.ArgumentParser
    )
    parser = base(prog="skaldir context", description=GROUP_HELP)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_verbs(sub, share_flags=True)
    if contract is not None and hasattr(contract, "annotate"):
        contract.annotate(parser)
        register_contract_entries()
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir context …`` standalone — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse owns --help and usage errors
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    runner = getattr(_CONTRACT, "run_command", None)
    if runner is not None:
        return int(runner(args, handler))
    try:
        return int(handler(args))
    except BrokenPipeError:  # `… | head` must not traceback
        return EXIT_OK


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def _manager_from_args(
    args: argparse.Namespace, *, asker: JevAsker | None = None
) -> ContextManager:
    """One manager for one invocation — the same configuration the CLI just parsed."""
    return ContextManager(
        budget_tokens=getattr(args, "budget", None),
        trigger_fraction=getattr(args, "trigger_fraction", None),
        goal=str(getattr(args, "goal", "") or ""),
        criteria=list(getattr(args, "criteria", []) or []),
        asker=asker,
        preserve_recent_messages=getattr(args, "preserve_recent", None),
        keep_threshold=getattr(args, "keep_threshold", None),
        truncate_head_chars=getattr(args, "truncate_head", None),
    )


def _asker_from_args(args: argparse.Namespace) -> JevAsker:
    """The asker the caller asked for; a provider that cannot be built is a refusal."""
    which = str(getattr(args, "asker", "fixture") or "fixture")
    if which == "fixture":
        return fixture_asker(getattr(args, "probability", None))
    return build_provider_asker()


def _evidence_for(source: str, report: ContextReport | None = None) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = [
        {"ref": source, "kind": "file", "note": "the history that was read"},
        {
            "ref": "src/skaldir/jevcomp/compact.py",
            "kind": "code",
            "note": "the ported algorithm behind every decision reported here",
        },
    ]
    if report is not None and report.error:
        evidence.append(
            {
                "ref": type(report).__name__,
                "kind": "run",
                "note": f"{report.error_type}: {report.error}",
            }
        )
    return evidence


def _hand_to_envelope(
    payload: dict[str, Any],
    *,
    evidence: Iterable[dict[str, Any]] = (),
    warnings: Iterable[str] = (),
) -> None:
    """Hand the payload and its evidence to the product envelope when one is active."""
    contract = shared.product_contract()
    if contract is None:
        return
    try:
        contract.set_data(payload)
        for item in evidence:
            contract.add_evidence(
                str(item.get("ref", "")),
                str(item.get("kind", "code")),
                str(item.get("note", "")),
            )
        for text in warnings:
            contract.warn(str(text))
    except Exception:  # noqa: BLE001 — an envelope problem must not lose the answer
        return


def _print_usage_text(payload: dict[str, Any]) -> None:
    accounting = payload.get("accounting") or {}
    measured = accounting.get("measured") or {}
    estimated = accounting.get("estimated") or {}
    budget = accounting.get("budget") or {}
    trigger = payload.get("trigger") or {}
    print(f"source: {payload.get('source')}")
    print(f"messages: {measured.get('messages')}   chars (measured): {measured.get('chars')}")
    print(
        f"tokens (estimate, jevcomp): {estimated.get('tokens')}   "
        f"tokens (estimate, chars/4): {estimated.get('tokens_chars4')}"
    )
    if measured.get("provider"):
        print(f"provider-reported: {measured['provider']}")
    print(
        f"budget: {budget.get('tokens')} ({budget.get('source')})   "
        f"trigger at {budget.get('threshold_tokens')} = {budget.get('trigger_fraction')} — "
        f"{budget.get('state')}"
    )
    print(f"would compact: {trigger.get('fire')} — {trigger.get('reason')}")


def _print_report_text(payload: dict[str, Any]) -> None:
    report = payload.get("report") or {}
    trigger = report.get("trigger") or {}
    freed = report.get("freed") or {}
    print(
        f"reason: {report.get('reason')}   applied: {report.get('applied')}   "
        f"dry run: {report.get('dry_run')}"
    )
    print(f"trigger: {trigger.get('reason')}")
    print(f"messages: {freed.get('messages_before')} -> {freed.get('messages_after')}")
    print(
        f"chars (measured): {freed.get('chars_before')} -> {freed.get('chars_after')}   "
        f"tokens (estimate): {freed.get('tokens_estimate_before')} -> "
        f"{freed.get('tokens_estimate_after')}"
    )
    print(f"preserved set ok: {payload.get('preserved_ok')}")
    for verb, rows in (
        ("REMOVE call + result", report.get("dropped") or []),
        ("SHORTEN result to its head", report.get("shortened") or []),
        ("KEEP verbatim", report.get("kept_verbatim") or []),
    ):
        for row in rows:
            print(f"  {str(row.get('id')):<6} {str(row.get('tool'))[:22]:<24} {verb}")
    if report.get("error"):
        print(f"error: {report.get('error_type')}: {report.get('error')}", file=sys.stderr)
    for note in report.get("notes") or []:
        print(f"note: {note}", file=sys.stderr)


def cmd_usage(args: argparse.Namespace) -> int:
    """``context usage`` — the budget state of one transcript. Read-only."""
    try:
        messages, source = load_history(
            transcript=str(getattr(args, "transcript", "") or ""),
            run_id=str(getattr(args, "run_id", "") or ""),
            state_dir=str(getattr(args, "state_dir", "") or ""),
        )
    except HistoryError as exc:
        message = str(exc)
        if exc.code == "not_found":
            return shared.not_found(message)
        if exc.code == "blocked":
            return shared.blocked(
                message, hint="point --transcript at a message list or a run transcript"
            )
        return _usage_fail(message)
    manager = _manager_from_args(args)
    accounting = manager.accounting(messages)
    decision = manager.trigger(messages)
    payload = {
        "accounting": accounting,
        "trigger": decision.to_wire(),
        "source": source,
        "budget_source": manager.budget_source,
        "compactions": (
            [report.to_wire() for report in manager.reports[-1:]]
            if bool(getattr(args, "history", False))
            else []
        ),
        "estimate_note": ACCOUNTING_ESTIMATE_NOTE,
    }
    evidence = _evidence_for(source)
    warnings: list[str] = []
    if decision.fire:
        warnings.append(
            "the estimate is at the trigger: the next request would be compacted "
            "(run: skaldir context compact --dry-run --transcript <file>)"
        )
    _hand_to_envelope(payload, evidence=evidence, warnings=warnings)
    if shared.text_mode(args):
        _print_usage_text(payload)
    elif not shared.envelope_mode():
        shared.dump(payload)
    return EXIT_OK


def _usage_fail(message: str) -> int:
    """Exit 1 through the contract when it has the helper, else by hand."""
    contract = shared.product_contract()
    if contract is not None and hasattr(contract, "usage_error"):
        return int(contract.usage_error(message))
    print(message, file=sys.stderr)
    return EXIT_USAGE


def _estimated_tokens(block: Mapping[str, Any]) -> int | None:
    """The estimate a report block carries, or ``None`` (never a zero standing in for it)."""
    value = dict(block or {}).get("tokens_estimate_jevcomp")
    return int(value) if isinstance(value, int) else None


def _preserved_ok(report: ContextReport | None) -> bool | None:
    """``True``/``False`` when a preserved set was re-checked, ``None`` when it was not.

    A fallback (or a dry run that was refused) never compacted, so there is nothing to
    verify — ``null`` is the honest answer, not ``false`` and not ``true``.
    """
    if report is None or not report.preserved:
        return None
    checked = [item for item in report.preserved if item.get("checked")]
    if not checked:
        return None
    return all(bool(item.get("ok")) for item in checked)


def cmd_compact(args: argparse.Namespace) -> int:
    """``context compact`` — judge every tool call and report what would change."""
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        asker = _asker_from_args(args)
    except AskerUnavailable as exc:
        return shared.blocked(
            f"no live asker: {exc}",
            hint="export SKALDIR_LLM_BASE_URL + SKALDIR_LLM_MODEL (+ SKALDIR_LLM_API_KEY), "
                 "or use --asker fixture for an offline simulation",
            next_command="skaldir provider status",
            reason="no_provider",
        )
    try:
        messages, source = load_history(
            transcript=str(getattr(args, "transcript", "") or ""),
            run_id=str(getattr(args, "run_id", "") or ""),
            state_dir=str(getattr(args, "state_dir", "") or ""),
        )
    except HistoryError as exc:
        message = str(exc)
        if exc.code == "not_found":
            return shared.not_found(message)
        if exc.code == "blocked":
            return shared.blocked(
                message,
                hint="point --transcript at a message list or a run transcript with request bodies",
                reason="no_messages",
            )
        return _usage_fail(message)

    manager = _manager_from_args(args, asker=asker)
    decision = manager.trigger(messages)
    fired = decision.fire
    if fired or bool(getattr(args, "force", False)):
        compacted, report = manager.compact(messages, dry_run=dry_run)
    else:
        compacted, report = list(messages), None
    payload: dict[str, Any] = {
        "source": source,
        "asker": str(getattr(args, "asker", "fixture")),
        "trigger": decision.to_wire(),
        "fired": fired,
        "forced": bool(getattr(args, "force", False)) and not fired,
        "dry_run": dry_run,
        "applied": bool(report.applied) if report is not None else False,
        "fallback": bool(report.fallback) if report is not None else False,
        "preserved_ok": _preserved_ok(report),
        "messages_before": len(messages),
        "messages_after": len(compacted),
        "tokens_estimate_before": _estimated_tokens(report.before) if report else None,
        "tokens_estimate_after": _estimated_tokens(report.after) if report else None,
        "report": report.to_wire() if report is not None else {},
        "manager": manager.summary(),
        "estimate_note": ACCOUNTING_ESTIMATE_NOTE,
    }
    warnings: list[str] = []
    if report is None:
        warnings.append(f"nothing was compacted: {decision.reason} (use --force to compact anyway)")
    if str(getattr(args, "asker", "fixture")) == "fixture":
        warnings.append(
            "asker=fixture: the keep/shorten/remove plan is a SIMULATION from the port's "
            "fixture probabilities, not the model's judgement — pass --asker provider "
            "for real decisions"
        )
    if report is not None and report.fallback:
        warnings.append(
            f"the compactor raised ({report.error_type}: {report.error}) — the run continues "
            "with the uncompacted history"
        )
    if report is not None and report.refused:
        warnings.append(f"compaction refused: {report.error}")
    target = str(getattr(args, "out", "") or "")
    if target and report is not None and report.applied:
        path = Path(target).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(compacted, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        payload["written_to"] = str(path)
        evidence = _evidence_for(source, report) + [
            {"ref": str(path), "kind": "file", "note": "the compacted history written by --out"}
        ]
    else:
        evidence = _evidence_for(source, report)
    _hand_to_envelope(payload, evidence=evidence, warnings=warnings)
    if shared.text_mode(args):
        _print_report_text(payload)
    elif not shared.envelope_mode():
        shared.dump(payload)
    for text in warnings:
        print(f"warning: {text}", file=sys.stderr)
    if report is not None and report.reason == "no_asker":
        return shared.blocked(report.error, hint="pass --asker provider or --asker fixture")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - manual entry
    raise SystemExit(main(sys.argv[1:]))
