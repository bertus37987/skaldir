"""Jev compaction engine — Python port of ``fast-jev-compaction`` (Jev, decision 6).

Ported from `tamaratran/fast-jev-compaction <https://github.com/tamaratran/fast-jev-compaction>`_
(Claude Code plugin 0.3.0 / npm package 0.2.0, commit
``e3f262a7f4d42bd8dd32ced30d26176f7cb545b0``), **MIT License, Copyright (c) 2025**.
The full upstream license text ships as ``UPSTREAM_LICENSE`` in this package; the
ported modules carry the notice in their header.

Why a port: the upstream is npm/TypeScript/ESM for Node >= 18, Skaldir is Python,
and the algorithm is a few hundred lines of dependency-free functions. The port
runs in-process, is testable offline and needs no Node at runtime. The MIT license
permits the derivative work; the only obligation is this attribution.

What the engine does
--------------------

Instead of rewriting old turns into a lossy summary, every tool call outside the
pinned window is judged on its own and then kept, shortened or removed:

* pinned: the first message and the newest ``preserve_recent_messages`` (default 6)
  are never touched;
* ``keepResult >= keepThreshold`` (default 0.5) -> keep call and result verbatim;
* else ``keepCall >= keepThreshold`` -> keep the call, truncate the result to its
  first ``truncate_head_chars`` characters plus a note;
* else -> remove call and result together.
* User and assistant text is never rewritten or removed; untouched messages are
  returned as the very objects they came in as. Nothing is summarized.

The asker is injected (:class:`~skaldir.jevcomp.types.JevAsker`): production passes
an adapter over Skaldir's own LLM provider (no API key lives in this package and no
HTTP client is ported), tests and shadow runs pass a fixture evaluator from
:mod:`skaldir.jevcomp.fixture`.

Failure model — the caller falls back
-------------------------------------

The engine raises instead of guessing: a provider failure
(:class:`~skaldir.jevcomp.errors.JevRequestError`), a malformed answer
(:class:`~skaldir.jevcomp.errors.JevAnswerError`), a history that cannot be fitted
or a batch that leaves no room for its questions
(:class:`~skaldir.jevcomp.errors.StateFittingError`). The caller then keeps the
original transcript or uses its built-in summary — which is exactly how the
upstream Claude Code hook behaves.

Ported function map (upstream -> here)
--------------------------------------

==============================  ==========================================
``estimateTokens``              ``state.estimate_tokens``
``truncate``                    ``state.truncate``
``isPinned``                    ``state.is_pinned``
``collectToolCalls``            ``state.collect_tool_calls``
``goalFromMessages``            ``state.goal_from_messages``
``fitState``                    ``state.fit_state``
``STATE_CONTEXT``               ``state.STATE_CONTEXT``
``resolveOptions``              ``compact.resolve_options``
``DEFAULT_OPTIONS``             ``compact.DEFAULT_OPTIONS``
``questionsFor``                ``compact.questions_for``
``batchCalls``                  ``compact.batch_calls``
``decideCall``                  ``compact.decide_call``
``applyDecisions``              ``compact.apply_decisions``
``messageChars``                ``compact.message_chars``
``reductionRatio``              ``compact.reduction_ratio``
``compact``                     ``compact.compact`` / ``compact.compact_async``
``buildJevRequest``             ``request.build_jev_request``
``parseJevResponse``            ``request.parse_jev_response``
``noulAnswer``                  ``request.noul_answer``
``AskBatch`` (module private)   ``compact.ask_batch`` / ``ask_batch_async``
==============================  ==========================================

Deviations from upstream (every one deliberate)
-----------------------------------------------

1. **Async -> sync, plus an async twin.** Upstream is async and fires all batches
   through ``Promise.all``. Skaldir's provider interface is synchronous, so
   :func:`compact.compact` is synchronous and runs the batches concurrently in a
   ``ThreadPoolExecutor`` (``max_concurrency``, upstream behaviour = 0, all at
   once; 1 = sequential). :func:`compact.compact_async` with an ``AsyncJevAsker``
   is the ``asyncio.gather`` twin for an async provider. Results are identical;
   only the threading changes.
2. **No HTTP client, no ``compactMessages``, no ``JevClient``, no API key.**
   Skaldir injects its provider; the wire helpers (``build_jev_request``,
   ``parse_jev_response``, ``noul_answer``) are ported so an adapter does not
   have to guess the TypeSafe body shape. ``compactMessages`` (which builds a
   ``JevClient``) is intentionally absent — use ``compact(messages, asker,
   options)``.
3. **Synchronous ``ask`` instead of a ``Promise``.** Same contract, one call per
   batch; a failure must raise.
4. **Typed errors.** Upstream throws plain ``Error``; the port raises
   ``JevCompactionError`` subclasses carrying the identical message text
   (``"history too large for Jev (~N tokens after truncation, limit M)"``,
   ``"state leaves no room for questions (~N of M tokens)"``,
   ``"Invalid Jev answer for <name>"``, ``"Jev returned malformed JSON"``,
   ``"Jev response is missing answers"``, ``"Jev request failed (<status>): ..."``)
   so the fallback path can act without string matching.
5. **Dataclasses instead of interfaces.** ``Message``/``ToolUse``/``ToolResult``
   are dataclasses, and foreign shapes are accepted: a message may be a mapping or
   an object with ``toolUses``/``tool_uses``, ``toolResults``/``tool_results``,
   ``isError``/``is_error``. Untouched messages still come back as the *same
   objects* (upstream's identity behaviour); rebuilt ones are ``Message``. A
   rebuilt message normalises a missing ``text`` to ``""``.
6. **Wire keys stay upstream.** The state object, the questions, the stats
   (``state.to_wire()``, ``stats.to_wire()``) use the upstream spelling
   (``tool_calls``, ``isError``, ``camelCase`` stats) so they can be handed to a
   JSON encoder unchanged. Python attribute names are snake_case.
7. **Lengths count Python code points, upstream counts UTF-16 code units.** For
   every text without astral characters (all realistic transcripts) the numbers are
   identical; an emoji in a transcript can shift a length or a token estimate by
   one per astral character. The token regex is otherwise byte-identical
   (``[0-9]`` instead of JavaScript's ASCII-only ``\\d``).
8. **JSON is ``json.dumps(separators=(",", ":"), ensure_ascii=False)``** to match
   ``JSON.stringify`` (compact, no non-ASCII escaping) — this matters because the
   state size estimate is taken from the serialised form. A ``None`` tool input
   maps to JavaScript's ``undefined`` (``input_text`` -> ``[unserializable input]``,
   ``message_chars`` -> flat 20), as upstream's try/catch does.
9. **``options`` may be ``None``, a dict, a dataclass or a ``ResolvedCompactOptions``**,
   and its keys are accepted snake_case or camelCase. Missing/non-finite values
   resolve to the upstream defaults exactly (``preserve_recent_messages`` floored
   at 0, ``truncate_head_chars`` floored at 0, token limits floored at 1).
10. **Port-only extras:** ``max_concurrency``, the fixture askers in
    :mod:`skaldir.jevcomp.fixture`, ``estimate_tokens`` re-exported from the package,
    and ``CompactResult.reduction_ratio()`` as a method next to the function.

Verified against the original with the Node oracle test
(``tests/test_jevcomp_oracle.py``): the same fixture transcript produces the same
call ids, pinning, fitting stage, token counts, decisions and stats in Node and in
Python.

Example
-------

.. code-block:: python

    from skaldir.jevcomp import Message, ToolResult, ToolUse, compact
    from skaldir.jevcomp.fixture import keep_calls_drop_results

    transcript = [
        Message(role="user", text="Never edit src/generated. Fix the failing test."),
        Message(
            role="assistant",
            tool_uses=[ToolUse("toolu_1", "Read", {"file_path": "src/a.ts"})],
        ),
        Message(role="user", tool_results=[ToolResult("toolu_1", "…file…")]),
    ]
    result = compact(transcript, keep_calls_drop_results(), {"preserve_recent_messages": 1})
    assert result.stats.state_stage  # e.g. "full"
"""

from __future__ import annotations

from .compact import (
    DEFAULT_OPTIONS,
    REQUEST_OVERHEAD_TOKENS,
    apply_decisions,
    ask_batch,
    ask_batch_async,
    batch_calls,
    compact,
    compact_async,
    decide_call,
    keep_threshold,
    message_chars,
    questions_for,
    reduction_ratio,
    resolve_options,
    run_batches,
    truncated_result_text,
)
from .errors import (
    JevAnswerError,
    JevCompactionError,
    JevRequestError,
    StateFittingError,
)
from .fixture import (
    NoulAsker,
    ReplayAsker,
    constant_asker,
    keep_calls_drop_results,
    noul_answers,
    noul_asker,
    replay_asker,
)
from .request import (
    DEFAULT_MODEL,
    SYSTEM_ONE_URL,
    build_jev_request,
    noul_answer,
    parse_jev_response,
)
from .state import (
    INPUT_CHARS,
    STATE_CONTEXT,
    TEXT_HEAD,
    TEXT_TAIL,
    abridge,
    calls_by_message,
    collect_tool_calls,
    compact_call,
    estimate_tokens,
    fit_state,
    goal_from_messages,
    history_entries,
    is_pinned,
    merge_call_runs,
    truncate,
)
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
    CompactionState,
    CompactOptions,
    CompactResult,
    CompactStats,
    FittedState,
    HistoryEntry,
    HistoryToolCall,
    JevAsker,
    JevRequest,
    JevResponse,
    Message,
    ResolvedCompactOptions,
    ToolCall,
    ToolResult,
    ToolUse,
    option_value,
    wire_json,
)

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_GOAL",
    "DEFAULT_KEEP_THRESHOLD",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_MAX_REQUEST_TOKENS",
    "DEFAULT_MAX_STATE_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_OPTIONS",
    "DEFAULT_PRESERVE_RECENT_MESSAGES",
    "DEFAULT_TRUNCATE_HEAD_CHARS",
    "INPUT_CHARS",
    "REQUEST_OVERHEAD_TOKENS",
    "STATE_CONTEXT",
    "SYSTEM_ONE_URL",
    "TEXT_HEAD",
    "TEXT_TAIL",
    "AsyncJevAsker",
    "CallAnswer",
    "CallDecision",
    "CompactOptions",
    "CompactResult",
    "CompactStats",
    "CompactionState",
    "FittedState",
    "HistoryEntry",
    "HistoryToolCall",
    "JevAnswerError",
    "JevAsker",
    "JevCompactionError",
    "JevRequest",
    "JevRequestError",
    "JevResponse",
    "Message",
    "NoulAsker",
    "ReplayAsker",
    "ResolvedCompactOptions",
    "StateFittingError",
    "ToolCall",
    "ToolResult",
    "ToolUse",
    "abridge",
    "apply_decisions",
    "ask_batch",
    "ask_batch_async",
    "batch_calls",
    "build_jev_request",
    "calls_by_message",
    "collect_tool_calls",
    "compact",
    "compact_async",
    "compact_call",
    "constant_asker",
    "decide_call",
    "estimate_tokens",
    "fit_state",
    "goal_from_messages",
    "history_entries",
    "is_pinned",
    "keep_calls_drop_results",
    "keep_threshold",
    "message_chars",
    "merge_call_runs",
    "noul_answer",
    "noul_answers",
    "noul_asker",
    "option_value",
    "parse_jev_response",
    "questions_for",
    "reduction_ratio",
    "replay_asker",
    "resolve_options",
    "run_batches",
    "truncate",
    "truncated_result_text",
    "wire_json",
]
