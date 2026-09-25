"""The compaction engine — port of ``src/compact.ts`` and ``src/request.ts``.

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

import asyncio
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .errors import StateFittingError
from .request import build_jev_request, noul_answer, parse_jev_response
from .state import collect_tool_calls, estimate_tokens, fit_state
from .types import (
    DEFAULT_GOAL,
    DEFAULT_KEEP_THRESHOLD,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MAX_REQUEST_TOKENS,
    DEFAULT_MAX_STATE_TOKENS,
    DEFAULT_PRESERVE_RECENT_MESSAGES,
    DEFAULT_TRUNCATE_HEAD_CHARS,
    AsyncJevAsker,
    CallAnswer,
    CallDecision,
    CompactOptions,
    CompactResult,
    CompactStats,
    FittedState,
    JevAsker,
    Message,
    ResolvedCompactOptions,
    ToolCall,
    ToolResult,
    ToolUse,
    answers_of,
    block_text_of,
    is_error_of,
    message_role,
    message_text,
    option_value,
    tool_input_of,
    tool_name_of,
    tool_results_of,
    tool_text_of,
    tool_use_id_of,
    tool_uses_of,
    wire_json,
)

DEFAULT_OPTIONS = ResolvedCompactOptions(
    goal=DEFAULT_GOAL,
    keep_threshold=DEFAULT_KEEP_THRESHOLD,
    preserve_recent_messages=DEFAULT_PRESERVE_RECENT_MESSAGES,
    max_state_tokens=DEFAULT_MAX_STATE_TOKENS,
    max_request_tokens=DEFAULT_MAX_REQUEST_TOKENS,
    truncate_head_chars=DEFAULT_TRUNCATE_HEAD_CHARS,
    max_concurrency=DEFAULT_MAX_CONCURRENCY,
)

#: Tokens the request envelope (``model``, key names) adds around state and questions.
REQUEST_OVERHEAD_TOKENS = 20

__all__ = [
    "DEFAULT_OPTIONS",
    "REQUEST_OVERHEAD_TOKENS",
    "apply_decisions",
    "ask_batch",
    "ask_batch_async",
    "batch_calls",
    "build_jev_request",
    "collect_tool_calls",
    "compact",
    "compact_async",
    "decide_call",
    "estimate_tokens",
    "fit_state",
    "keep_threshold",
    "message_chars",
    "noul_answer",
    "parse_jev_response",
    "questions_for",
    "reduction_ratio",
    "resolve_options",
    "run_batches",
    "truncated_result_text",
]


def _finite(value: Any, fallback: float) -> float:
    """``finite(value, fallback)`` — non-numbers, NaN and infinite fall back."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(fallback)
    if not math.isfinite(float(value)):
        return float(fallback)
    return float(value)


def resolve_options(options: Any = None) -> ResolvedCompactOptions:
    """Fill in the upstream defaults; non-finite values and missing fields fall back."""
    return ResolvedCompactOptions(
        goal=option_value(options, "goal", DEFAULT_OPTIONS.goal) or DEFAULT_OPTIONS.goal,
        keep_threshold=_finite(
            option_value(options, "keep_threshold"), DEFAULT_OPTIONS.keep_threshold
        ),
        preserve_recent_messages=max(
            0,
            math.floor(
                _finite(
                    option_value(options, "preserve_recent_messages"),
                    DEFAULT_OPTIONS.preserve_recent_messages,
                )
            ),
        ),
        max_state_tokens=max(
            1,
            int(
                _finite(option_value(options, "max_state_tokens"), DEFAULT_OPTIONS.max_state_tokens)
            ),
        ),
        max_request_tokens=max(
            1,
            int(
                _finite(
                    option_value(options, "max_request_tokens"),
                    DEFAULT_OPTIONS.max_request_tokens,
                )
            ),
        ),
        truncate_head_chars=max(
            0,
            math.floor(
                _finite(
                    option_value(options, "truncate_head_chars"),
                    DEFAULT_OPTIONS.truncate_head_chars,
                )
            ),
        ),
        max_concurrency=max(
            0,
            math.floor(
                _finite(option_value(options, "max_concurrency"), DEFAULT_OPTIONS.max_concurrency)
            ),
        ),
    )


def questions_for(call: ToolCall) -> dict[str, Any]:
    """The two ``noul`` questions asked about one call: keep the call, keep its result."""
    return {
        f"call_{call.id}": {
            "type": "noul",
            "instructions": (
                f"Tool call {call.id} ({call.tool}) should stay in the history: knowing this call "
                f"was made, with its input, still matters for what the assistant does next"
            ),
        },
        f"result_{call.id}": {
            "type": "noul",
            "instructions": (
                f"The full output of tool call {call.id} ({call.tool}, {call.result_chars} chars) "
                f"should stay in the history verbatim: the assistant still needs its contents and "
                f"re-running the tool would not do"
            ),
        },
    }


def questions_tokens(call: ToolCall) -> int:
    """Estimated tokens of the two questions of one call."""
    return estimate_tokens(wire_json(questions_for(call)))


def batch_calls(
    calls: list[ToolCall], state_tokens: int, options: Any = None
) -> list[list[ToolCall]]:
    """Split the candidate calls into batches that fit one request.

    The complete state travels with every batch, so the budget left for the
    questions is ``max_request_tokens - state_tokens - REQUEST_OVERHEAD_TOKENS``.
    Raises :class:`~skaldir.jevcomp.errors.StateFittingError` when a single question
    does not fit.
    """
    max_request_tokens = int(
        _finite(option_value(options, "max_request_tokens"), DEFAULT_OPTIONS.max_request_tokens)
    )
    budget = float(max_request_tokens - state_tokens - REQUEST_OVERHEAD_TOKENS)
    batches: list[list[ToolCall]] = []
    current: list[ToolCall] = []
    current_tokens = 0.0
    for call in calls:
        tokens = float(questions_tokens(call))
        if current and current_tokens + tokens > budget:
            batches.append(current)
            current = []
            current_tokens = 0.0
        if not current and tokens > budget:
            raise StateFittingError(
                f"state leaves no room for questions (~{state_tokens} of "
                f"{max_request_tokens} tokens)"
            )
        current.append(call)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


def keep_threshold(options: Any = None) -> float:
    """The threshold from an options object, a mapping, or a bare number."""
    if isinstance(options, (int, float)) and not isinstance(options, bool):
        return float(options)
    return _finite(option_value(options, "keep_threshold"), DEFAULT_OPTIONS.keep_threshold)


def decide_call(call: ToolCall, answer: CallAnswer | Any, options: Any = None) -> CallDecision:
    """keep | drop_result | drop_call for one call, in the upstream precedence.

    ``pinned`` wins over everything; then ``keepResult >= threshold`` keeps call
    and result; then ``keepCall >= threshold`` keeps the call and shortens the
    result; otherwise call and result go.
    """
    threshold = keep_threshold(options)
    decision = CallDecision(
        id=call.id,
        tool=call.tool,
        keep_call=float(option_value(answer, "keep_call") or 0.0),
        keep_result=float(option_value(answer, "keep_result") or 0.0),
        action="keep",
        reason="kept",
    )
    if call.pinned:
        decision.action, decision.reason = "keep", "pinned"
        return decision
    if decision.keep_result >= threshold:
        decision.action, decision.reason = "keep", "kept"
        return decision
    if decision.keep_call >= threshold:
        decision.action, decision.reason = "drop_result", "result_dropped"
        return decision
    decision.action, decision.reason = "drop_call", "call_dropped"
    return decision


def _ask_questions(batch: list[ToolCall]) -> dict[str, Any]:
    questions: dict[str, Any] = {}
    for call in batch:
        questions.update(questions_for(call))
    return questions


def _answers_for(batch: list[ToolCall], response: Any) -> dict[str, CallAnswer]:
    answers = answers_of(response)
    return {
        call.id: CallAnswer(
            keep_call=noul_answer(answers, f"call_{call.id}"),
            keep_result=noul_answer(answers, f"result_{call.id}"),
        )
        for call in batch
    }


def ask_batch(asker: JevAsker, state: Any, batch: list[ToolCall]) -> dict[str, CallAnswer]:
    """Ask one batch of questions and map the answers back to their calls.

    A malformed answer raises :class:`~skaldir.jevcomp.errors.JevAnswerError`, so the
    caller falls back instead of compacting on guesses.
    """
    return _answers_for(batch, asker.ask(state, _ask_questions(batch)))


async def ask_batch_async(
    asker: AsyncJevAsker, state: Any, batch: list[ToolCall]
) -> dict[str, CallAnswer]:
    """The async twin of :func:`ask_batch`."""
    return _answers_for(batch, await asker.ask(state, _ask_questions(batch)))


def run_batches(
    asker: JevAsker, state: Any, batches: list[list[ToolCall]], max_concurrency: int = 0
) -> list[dict[str, CallAnswer]]:
    """Ask every batch, concurrently by default (upstream ``Promise.all``).

    ``max_concurrency <= 0`` asks all batches at once, ``1`` one after the other.
    A failure of any batch propagates, so the caller can fall back to its own
    compaction path.
    """
    if not batches:
        return []
    if max_concurrency == 1 or len(batches) == 1:
        return [ask_batch(asker, state, batch) for batch in batches]
    workers = len(batches) if max_concurrency <= 0 else min(max_concurrency, len(batches))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(ask_batch, asker, state, batch) for batch in batches]
        return [future.result() for future in futures]


def truncated_result_text(text: str, is_error: bool, head_chars: int) -> str:
    """The head of a dropped result plus the one-line note upstream writes."""
    if len(text) <= head_chars + 120:
        return text
    head = f"{text[:head_chars]}\n" if head_chars > 0 else ""
    suffix = " (error)" if is_error else ""
    return (
        f"{head}[fast-jev-compaction truncated {len(text) - head_chars} chars of this tool result"
        f"{suffix}; re-run the tool if needed]"
    )


def _input_chars(tool_input: Any) -> int:
    """``JSON.stringify(input).length``; ``undefined`` and cycles cost a flat 20."""
    if tool_input is None:
        return 20
    try:
        return len(wire_json(tool_input))
    except (TypeError, ValueError):
        return 20


def message_chars(message: Any) -> int:
    """Characters of text, tool input and tool output a message holds."""
    total = len(message_text(message))
    for block in tool_uses_of(message):
        total += _input_chars(tool_input_of(block))
    for block in tool_results_of(message):
        total += len(block_text_of(block))
    return total


def apply_decisions(
    messages: list[Any],
    decisions: list[CallDecision],
    calls: list[ToolCall],
    head_chars: int,
) -> list[Any]:
    """Rebuild the conversation from the decisions.

    A dropped call disappears together with its result; a dropped result keeps a
    bounded head and a note. A message that loses all its content is removed;
    untouched messages are returned as the same objects they came in as, and no
    kept text is ever rewritten.
    """
    by_id = {call.id: call for call in calls}
    actions: dict[str, str] = {}
    for decision in decisions:
        call = by_id.get(decision.id)
        if call is not None and decision.action != "keep":
            actions[call.tool_use_id] = decision.action

    kept: list[Any] = []
    for message in messages:
        uses = tool_uses_of(message)
        results = tool_results_of(message)
        touched = any(tool_use_id_of(block) in actions for block in uses) or any(
            tool_use_id_of(block) in actions for block in results
        )
        if not touched:
            kept.append(message)
            continue

        new_uses: list[Any] = []
        dropped_call = False
        for block in uses:
            action = actions.get(tool_use_id_of(block))
            if action == "drop_call":
                dropped_call = True
                continue
            if action != "drop_result":
                new_uses.append(block)
                continue
            original = tool_text_of(block)
            text = truncated_result_text(original, is_error_of(block), head_chars)
            if original == text:
                new_uses.append(block)
            else:
                new_uses.append(
                    ToolUse(
                        tool_use_id=tool_use_id_of(block),
                        tool=tool_name_of(block),
                        input=tool_input_of(block),
                        text=text,
                        is_error=is_error_of(block),
                    )
                )

        new_results: list[Any] = []
        for block in results:
            action = actions.get(tool_use_id_of(block))
            if action == "drop_call":
                dropped_call = True
                continue
            if action != "drop_result":
                new_results.append(block)
                continue
            original = block_text_of(block)
            text = truncated_result_text(original, is_error_of(block), head_chars)
            if text == original:
                new_results.append(block)
            else:
                new_results.append(
                    ToolResult(
                        tool_use_id=tool_use_id_of(block),
                        text=text,
                        is_error=is_error_of(block),
                    )
                )

        unchanged = (
            not dropped_call
            and len(new_uses) == len(uses)
            and all(new is old for new, old in zip(new_uses, uses, strict=True))
            and len(new_results) == len(results)
            and all(new is old for new, old in zip(new_results, results, strict=True))
        )
        if unchanged:
            kept.append(message)
            continue
        if message_text(message).strip() == "" and not new_uses and not new_results:
            continue
        kept.append(
            Message(
                role=message_role(message),
                text=message_text(message),
                tool_uses=new_uses,
                tool_results=new_results,
            )
        )
    return kept


def reduction_ratio(result: Any) -> float:
    """Share of the transcript's characters the compaction removed (0.0 when empty)."""
    stats = option_value(result, "stats") or result
    before = option_value(stats, "chars_before")
    after = option_value(stats, "chars_after")
    if not before:
        return 0.0
    return (float(before) - float(after)) / float(before)


def _count(decisions: list[CallDecision], reason: str) -> int:
    return sum(1 for decision in decisions if decision.reason == reason)


def _finish(
    messages: list[Any],
    calls: list[ToolCall],
    answers: dict[str, CallAnswer],
    resolved: ResolvedCompactOptions,
    requests: int,
    fitted: FittedState | None,
    started: float,
) -> CompactResult:
    decisions = [
        decide_call(
            call, answers.get(call.id, CallAnswer(keep_call=1.0, keep_result=1.0)), resolved
        )
        for call in calls
    ]
    kept = apply_decisions(messages, decisions, calls, resolved.truncate_head_chars)
    return CompactResult(
        messages=kept,
        decisions=decisions,
        stats=CompactStats(
            messages_before=len(messages),
            messages_after=len(kept),
            chars_before=sum(message_chars(message) for message in messages),
            chars_after=sum(message_chars(message) for message in kept),
            calls=len(calls),
            kept=_count(decisions, "kept"),
            results_dropped=_count(decisions, "result_dropped"),
            calls_dropped=_count(decisions, "call_dropped"),
            pinned=_count(decisions, "pinned"),
            state_tokens=fitted.tokens if fitted else 0,
            state_stage=fitted.stage if fitted else "",
            requests=requests,
            ms=int((time.perf_counter() - started) * 1000),
        ),
    )


def _plan(
    messages: list[Any], resolved: ResolvedCompactOptions
) -> tuple[list[ToolCall], list[ToolCall]]:
    calls = collect_tool_calls(messages, resolved.preserve_recent_messages)
    return calls, [call for call in calls if not call.pinned]


def compact(
    messages: list[Any], asker: JevAsker, options: CompactOptions | Any = None
) -> CompactResult:
    """Compact a transcript by asking Jev about every tool call outside the pins.

    For every non-pinned tool call the asker gets two questions (keep the call,
    keep its result verbatim). The whole history (results omitted, fitted into
    ``max_state_tokens``) is sent as state with every batch of questions. Raises
    on failure — the caller decides whether to fall back to its own summary.
    """
    started = time.perf_counter()
    resolved = resolve_options(options)
    calls, candidates = _plan(messages, resolved)

    fitted: FittedState | None = None
    request_count = 0
    answers: dict[str, CallAnswer] = {}
    if candidates:
        fitted = fit_state(messages, calls, resolved)
        batches = batch_calls(candidates, fitted.tokens, resolved)
        request_count = len(batches)
        wire_state = fitted.state.to_wire()
        for answered in run_batches(asker, wire_state, batches, resolved.max_concurrency):
            answers.update(answered)
    return _finish(messages, calls, answers, resolved, request_count, fitted, started)


async def compact_async(
    messages: list[Any], asker: AsyncJevAsker, options: CompactOptions | Any = None
) -> CompactResult:
    """The async twin of :func:`compact`, for an async provider adapter.

    Batches run concurrently through ``asyncio.gather`` (the upstream shape).
    """
    started = time.perf_counter()
    resolved = resolve_options(options)
    calls, candidates = _plan(messages, resolved)

    fitted: FittedState | None = None
    request_count = 0
    answers: dict[str, CallAnswer] = {}
    if candidates:
        fitted = fit_state(messages, calls, resolved)
        batches = batch_calls(candidates, fitted.tokens, resolved)
        request_count = len(batches)
        wire_state = fitted.state.to_wire()
        answered = await asyncio.gather(
            *(ask_batch_async(asker, wire_state, batch) for batch in batches)
        )
        for mapping in answered:
            answers.update(mapping)
    return _finish(messages, calls, answers, resolved, request_count, fitted, started)
