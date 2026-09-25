"""State building and staged fitting — port of ``src/state.ts``.

Ported from ``fast-jev-compaction`` (Claude Code plugin 0.3.0 / npm package 0.2.0,
commit ``e3f262a7f4d42bd8dd32ced30d26176f7cb545b0``), MIT License,
Copyright (c) 2025 — https://github.com/tamaratran/fast-jev-compaction

MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The full upstream license ships as ``UPSTREAM_LICENSE`` in this package; the
package docstring lists every deviation from the TypeScript original.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .errors import StateFittingError
from .types import (
    DEFAULT_MAX_STATE_TOKENS,
    DEFAULT_PRESERVE_RECENT_MESSAGES,
    CompactionState,
    FittedState,
    HistoryEntry,
    HistoryToolCall,
    ToolCall,
    block_text_of,
    is_error_of,
    message_role,
    message_text,
    option_value,
    tool_input_of,
    tool_name_of,
    tool_results_of,
    tool_use_id_of,
    tool_uses_of,
    wire_json,
)

STATE_CONTEXT = (
    "A coding assistant conversation is being compacted to free context. `history` is the whole "
    "conversation so far, oldest first; tool outputs are replaced by a short `result` note and "
    "long texts may be abridged. Each question asks whether one tool call, or the full output "
    "of that call, still needs to stay in the history verbatim. Whatever is not kept is deleted "
    "permanently, but the assistant can always re-run a tool or re-read a file."
)

#: Successive caps on the serialised tool input included per call.
INPUT_CHARS = (1000, 200, 60)
TEXT_HEAD = 400
TEXT_TAIL = 150

#: ``[A-Za-z]+|\d+|[^\sA-Za-z\d]`` — ``\d`` is ASCII in JavaScript, so ``[0-9]`` here.
TOKEN_PIECES = re.compile(r"[A-Za-z]+|[0-9]+|[^\sA-Za-z0-9]")
WHITESPACE = re.compile(r"\s+")


def estimate_tokens(text: str) -> int:
    """Token estimate without a tokenizer.

    A word costs one token per six letters, a digit run half a token per digit,
    any other symbol nine tenths. Calibrated against the usage Jev reports for
    real transcripts, where it lands 2–18 % above the true count; a plain
    characters-per-token ratio undercounts the JSON-heavy states by up to 40 %.
    """
    tokens = 0.0
    for match in TOKEN_PIECES.finditer(text):
        piece = match.group(0)
        first = ord(piece[0])
        if 48 <= first <= 57:
            tokens += len(piece) / 2
        elif (65 <= first <= 90) or (97 <= first <= 122):
            tokens += 1 + (len(piece) - 1) // 6
        else:
            tokens += 0.9
    return math.ceil(tokens)


def truncate(text: str, limit: int) -> str:
    """``text`` capped at ``limit`` characters, ellipsis included when cut."""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def abridge(text: str, head: int, tail: int) -> str:
    """Head + tail of ``text`` with a note about the omitted middle."""
    if len(text) <= head + tail + 40:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n[… {omitted} chars omitted …]\n{text[len(text) - tail :]}"


def is_pinned(index: int, total: int, preserve_recent_messages: int) -> bool:
    """The first message and the newest ``preserve_recent_messages`` are pinned."""
    return index == 0 or index >= total - preserve_recent_messages


def collect_tool_calls(messages: list[Any], preserve_recent_messages: int) -> list[ToolCall]:
    """Pair every ``tool_use`` with its ``tool_result`` by ``tool_use_id``.

    A call without a result is no candidate (there is nothing to drop yet). The
    short ids (``t1``, ``t2``, ...) are the upstream ones: they appear in the Jev
    state and in the question names.
    """
    results: dict[str, tuple[int, Any]] = {}
    for index, message in enumerate(messages):
        for result in tool_results_of(message):
            results[tool_use_id_of(result)] = (index, result)

    calls: list[ToolCall] = []
    for call_index, message in enumerate(messages):
        for tool in tool_uses_of(message):
            found = results.get(tool_use_id_of(tool))
            if found is None:
                continue
            result_index, result = found
            calls.append(
                ToolCall(
                    id=f"t{len(calls) + 1}",
                    tool_use_id=tool_use_id_of(tool),
                    tool=tool_name_of(tool),
                    input=tool_input_of(tool),
                    call_index=call_index,
                    result_index=result_index,
                    result_chars=len(block_text_of(result)),
                    is_error=is_error_of(result),
                    pinned=(
                        is_pinned(call_index, len(messages), preserve_recent_messages)
                        or is_pinned(result_index, len(messages), preserve_recent_messages)
                    ),
                )
            )
    return calls


def input_text(tool_input: Any, limit: int) -> str:
    """``JSON.stringify(input)`` truncated, or a marker for unserializable input.

    In JavaScript a ``null``/``undefined`` input throws inside ``JSON.stringify``
    *and* on the following ``.length``, so upstream returns the same marker; the
    port mirrors that instead of printing ``null``.
    """
    if tool_input is None:
        return "[unserializable input]"
    try:
        serialized = wire_json(tool_input)
    except (TypeError, ValueError):
        serialized = "[unserializable input]"
    return truncate(serialized, limit)


def result_note(call: ToolCall) -> str:
    return f"{'error' if call.is_error else 'ok'}, {call.result_chars} chars (omitted)"


def compact_call(call: ToolCall) -> str:
    """One call as a single line, for when the structured form is too costly."""
    source = call.input if hasattr(call.input, "items") else {}
    parts: list[str] = []
    for key, value in source.items():
        text = value if isinstance(value, str) else input_text({key: value}, 200)
        parts.append(f"{key}={WHITESPACE.sub(' ', text)}")
    joined = " ".join(parts)
    return (
        f"{call.id} {call.tool} {truncate(joined, INPUT_CHARS[2])} → "
        f"{'error' if call.is_error else 'ok'} {call.result_chars}ch"
    )


def merge_call_runs(history: list[HistoryEntry], pinned: Any) -> list[HistoryEntry]:
    """Fold runs of adjacent call-only entries into one entry each.

    The per-entry envelope is then paid once per run; the call lines keep their
    ids. Entries are copied (upstream spreads them) so the state's entries are
    never mutated in place.
    """

    def foldable(entry: HistoryEntry) -> bool:
        calls = entry.tool_calls
        return (
            not pinned(entry) and len(entry.text) == 0 and bool(calls) and isinstance(calls[0], str)
        )

    merged: list[HistoryEntry] = []
    for entry in history:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and foldable(previous)
            and foldable(entry)
            and previous.role == entry.role
        ):
            previous.tool_calls = [*previous.tool_calls, *entry.tool_calls]  # type: ignore[misc]
            continue
        merged.append(
            HistoryEntry(
                i=entry.i,
                role=entry.role,
                text=entry.text,
                tool_calls=None if entry.tool_calls is None else list(entry.tool_calls),
            )
        )
    return merged


def calls_by_message(calls: list[ToolCall]) -> dict[int, list[ToolCall]]:
    by_message: dict[int, list[ToolCall]] = {}
    for call in calls:
        by_message.setdefault(call.call_index, []).append(call)
    return by_message


def history_entries(
    messages: list[Any], calls: list[ToolCall], input_chars: int
) -> list[HistoryEntry]:
    by_message = calls_by_message(calls)
    entries: list[HistoryEntry] = []
    for i, message in enumerate(messages):
        tool_calls = [
            HistoryToolCall(
                id=call.id,
                tool=call.tool,
                input=input_text(call.input, input_chars),
                result=result_note(call),
            )
            for call in by_message.get(i, [])
        ]
        if message_text(message).strip() == "" and not tool_calls:
            continue
        entries.append(
            HistoryEntry(
                i=i,
                role=message_role(message),
                text=message_text(message),
                tool_calls=tool_calls or None,
            )
        )
    return entries


def goal_from_messages(messages: list[Any]) -> str:
    """The last three user prompts, as the default ``goal``."""
    prompts = [
        truncate(message_text(message), 500)
        for message in messages
        if message_role(message) == "user"
        and message_text(message).strip() != ""
        and not tool_results_of(message)
    ]
    return "\n".join(prompts[-3:])


def _number(options: Any, name: str, default: float) -> float:
    value = option_value(options, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    if not math.isfinite(float(value)):
        return float(default)
    return float(value)


def _number_text(value: float | int) -> str:
    """Format a number the way a JS template literal does (no trailing ``.0``)."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def fit_state(messages: list[Any], calls: list[ToolCall], options: Any = None) -> FittedState:
    """Build the Jev state from the whole conversation and shrink it until it fits.

    Stages, each applied only if the previous one was not enough: tool inputs are
    truncated, then long texts are abridged oldest-first (pinned messages last),
    then old messages collapse to a one-line note, then old tool calls shrink to
    one line each, then old messages that carry no call are left out, then runs of
    old call-only messages are folded into one entry. Raises
    :class:`~skaldir.jevcomp.errors.StateFittingError` when even that is too big.

    ``options`` may be a :class:`~skaldir.jevcomp.types.CompactOptions`, a resolved
    options object or a mapping; only ``goal``, ``max_state_tokens`` and
    ``preserve_recent_messages`` are read.
    """
    max_state_tokens = _number(options, "max_state_tokens", DEFAULT_MAX_STATE_TOKENS)
    preserve_recent = int(
        _number(options, "preserve_recent_messages", DEFAULT_PRESERVE_RECENT_MESSAGES)
    )
    goal = option_value(options, "goal") or goal_from_messages(messages)

    def state_of(history: list[HistoryEntry]) -> CompactionState:
        return CompactionState(context=STATE_CONTEXT, goal=goal, history=history)

    def entry_tokens(entry: HistoryEntry) -> int:
        return estimate_tokens(wire_json(entry.to_wire())) + 1

    base_tokens = estimate_tokens(wire_json(state_of([]).to_wire()))

    def fitted(history: list[HistoryEntry], tokens: int, stage: str) -> FittedState:
        return FittedState(state=state_of(history), tokens=tokens, stage=stage)

    history: list[HistoryEntry] = []
    per_entry: list[int] = []
    tokens = 0

    def rebuild(input_chars: int) -> None:
        nonlocal history, per_entry, tokens
        history = history_entries(messages, calls, input_chars)
        per_entry = [entry_tokens(entry) for entry in history]
        tokens = base_tokens + sum(per_entry)

    def fits() -> bool:
        return tokens <= max_state_tokens

    def shrink(index: int, change: Any) -> None:
        nonlocal tokens
        if index >= len(history):
            return
        entry = history[index]
        change(entry)
        now = entry_tokens(entry)
        tokens += now - per_entry[index]
        per_entry[index] = now

    rebuild(INPUT_CHARS[0])
    if fits():
        return fitted(history, tokens, "full")

    for limit in INPUT_CHARS[1:]:
        rebuild(limit)
        if fits():
            return fitted(history, tokens, f"inputs<={limit}")

    def pinned(entry: HistoryEntry) -> bool:
        return is_pinned(entry.i, len(messages), preserve_recent)

    indices = list(range(len(history)))
    order = [index for index in indices if not pinned(history[index])] + [
        index for index in indices if pinned(history[index])
    ]

    for index in order:
        entry = history[index]
        if len(entry.text) <= TEXT_HEAD + TEXT_TAIL + 40:
            continue
        shrink(index, lambda e: setattr(e, "text", abridge(e.text, TEXT_HEAD, TEXT_TAIL)))
        if fits():
            return fitted(history, tokens, "texts abridged")

    for index in order:
        entry = history[index]
        if pinned(entry) or len(entry.text) == 0:
            continue
        original = (
            len(message_text(messages[entry.i])) if entry.i < len(messages) else len(entry.text)
        )
        shrink(
            index,
            lambda e, original=original: setattr(e, "text", f"[… {original} chars omitted …]"),
        )
        if fits():
            return fitted(history, tokens, "old messages collapsed")

    by_message = calls_by_message(calls)
    for index in order:
        entry = history[index]
        own = by_message.get(entry.i)
        if pinned(entry) or own is None:
            continue
        shrink(index, lambda e, own=own: setattr(e, "tool_calls", [compact_call(c) for c in own]))
        if fits():
            return fitted(history, tokens, "old calls compacted")

    left: set[int] = set()
    for index in order:
        entry = history[index]
        if pinned(entry) or entry.tool_calls:
            continue
        left.add(index)
        tokens -= per_entry[index]
        if fits():
            return fitted(
                [entry for i, entry in enumerate(history) if i not in left],
                tokens,
                "old messages left out",
            )

    history = merge_call_runs([entry for i, entry in enumerate(history) if i not in left], pinned)
    per_entry = [entry_tokens(entry) for entry in history]
    tokens = base_tokens + sum(per_entry)
    if fits():
        return fitted(history, tokens, "old calls merged")

    raise StateFittingError(
        f"history too large for Jev (~{_number_text(tokens)} tokens after truncation, "
        f"limit {_number_text(max_state_tokens)})"
    )
