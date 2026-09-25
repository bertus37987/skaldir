"""Data model of the Jev compaction engine.

Ported from ``fast-jev-compaction`` (Claude Code plugin 0.3.0 / npm package 0.2.0,
commit ``e3f262a7f4d42bd8dd32ced30d26176f7cb545b0``), MIT License,
Copyright (c) 2025 — https://github.com/tamaratran/fast-jev-compaction

See ``UPSTREAM_LICENSE`` next to this file for the full upstream license text and
``skaldir.jevcomp`` for the package overview and the list of ported deviations.

Every name on the wire keeps the upstream spelling (``tool_use_id``, ``isError``,
``tool_calls``, ...): the state object built here is the state the upstream sends,
so a provider adapter can hand it to ``json.dumps`` unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["user", "assistant"]

#: Upstream ``DEFAULT_OPTIONS`` (``compact.ts``). Kept here so that the fitting
#: code (``state.py``) and the option resolver (``compact.py``) share one source.
DEFAULT_GOAL = ""
DEFAULT_KEEP_THRESHOLD = 0.5
DEFAULT_PRESERVE_RECENT_MESSAGES = 6
DEFAULT_MAX_STATE_TOKENS = 25_000
DEFAULT_MAX_REQUEST_TOKENS = 30_000
DEFAULT_TRUNCATE_HEAD_CHARS = 300
#: Port-only option (upstream runs every batch at once via ``Promise.all``;
#: ``0`` means "all batches at once", the faithful behaviour).
DEFAULT_MAX_CONCURRENCY = 0


def wire_json(value: Any) -> str:
    """``JSON.stringify`` for the wire: compact separators, no ASCII escaping.

    Upstream token estimation and the HTTP body both go through
    ``JSON.stringify``; Python's default ``json.dumps`` would add spaces and
    escape non-ASCII characters, which changes the estimated token counts.
    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _value(source: Any, *names: str) -> Any:
    """First present field of ``source`` (mapping key or attribute), else ``None``.

    Accepts both the upstream camelCase spelling and the Python snake_case one so
    a transcript coming from the CLI, the plugin or a captured session can be
    passed in unchanged.
    """
    if source is None:
        return None
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _as_sequence(source: Any) -> list[Any]:
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        return list(source)
    return []


def message_role(message: Any) -> str:
    role = _value(message, "role")
    return role if isinstance(role, str) else "user"


def message_text(message: Any) -> str:
    text = _value(message, "text")
    return "" if text is None else str(text)


def tool_uses_of(message: Any) -> list[Any]:
    return _as_sequence(_value(message, "tool_uses", "toolUses"))


def tool_results_of(message: Any) -> list[Any]:
    return _as_sequence(_value(message, "tool_results", "toolResults"))


def tool_use_id_of(block: Any) -> str:
    value = _value(block, "tool_use_id", "toolUseId")
    return "" if value is None else str(value)


def tool_name_of(block: Any) -> str:
    value = _value(block, "tool")
    return "" if value is None else str(value)


def tool_input_of(block: Any) -> Any:
    return _value(block, "input")


def tool_text_of(block: Any) -> str:
    """``block.text ?? ''`` — a missing/``None`` text is the empty string."""
    value = _value(block, "text")
    return "" if value is None else str(value)


def block_text_of(block: Any) -> str:
    """``block.text`` of a tool result; a missing text is the empty string."""
    value = _value(block, "text")
    return "" if value is None else str(value)


def is_error_of(block: Any) -> bool:
    return bool(_value(block, "is_error", "isError"))


@dataclass
class ToolUse:
    """A ``tool_use`` block of an assistant message."""

    tool_use_id: str
    tool: str
    input: Any = None
    text: str | None = None
    is_error: bool = False

    @classmethod
    def from_any(cls, block: Any) -> ToolUse:
        """Coerce a foreign block shape (dict or object, camelCase or snake_case)."""
        if isinstance(block, cls):
            return block
        return cls(
            tool_use_id=tool_use_id_of(block),
            tool=tool_name_of(block),
            input=tool_input_of(block),
            text=None if _value(block, "text") is None else tool_text_of(block),
            is_error=is_error_of(block),
        )

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {
            "tool_use_id": self.tool_use_id,
            "tool": self.tool,
            "input": self.input,
        }
        if self.text is not None:
            wire["text"] = self.text
        if self.is_error:
            wire["isError"] = True
        return wire


@dataclass
class ToolResult:
    """A ``tool_result`` block of a user message."""

    tool_use_id: str
    text: str
    is_error: bool = False

    @classmethod
    def from_any(cls, block: Any) -> ToolResult:
        """Coerce a foreign block shape (dict or object, camelCase or snake_case)."""
        if isinstance(block, cls):
            return block
        return cls(
            tool_use_id=tool_use_id_of(block),
            text=block_text_of(block),
            is_error=is_error_of(block),
        )

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"tool_use_id": self.tool_use_id, "text": self.text}
        if self.is_error:
            wire["isError"] = True
        return wire


@dataclass
class Message:
    """One transcript message; the shape is the upstream ``Message`` interface.

    Deliberately a subset of a session transcript, so a captured transcript can
    be passed in as is. ``skaldir.jevcomp`` accepts foreign message shapes too
    (mappings or objects, camelCase or snake_case); everything it has to rebuild
    comes back as this dataclass, while untouched messages are returned as the
    exact objects they came in as.
    """

    role: str
    text: str = ""
    tool_uses: list[Any] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)

    @classmethod
    def from_any(cls, message: Any) -> Message:
        """Coerce a foreign message shape into this dataclass (no-op if it is one).

        Nested tool blocks are coerced as well, so a message built here always
        carries :class:`ToolUse` / :class:`ToolResult` objects.
        """
        if isinstance(message, cls):
            return message
        return cls(
            role=message_role(message),
            text=message_text(message),
            tool_uses=[ToolUse.from_any(block) for block in tool_uses_of(message)],
            tool_results=[ToolResult.from_any(block) for block in tool_results_of(message)],
        )

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {
            "role": self.role,
            "text": self.text,
            "toolUses": [_block_wire(block) for block in self.tool_uses],
        }
        if self.tool_results:
            wire["toolResults"] = [_block_wire(block) for block in self.tool_results]
        return wire


def _block_wire(block: Any) -> Any:
    """A tool block in its wire form (dataclasses convert, foreign blocks pass through)."""
    to_wire = getattr(block, "to_wire", None)
    return to_wire() if callable(to_wire) else block


@dataclass
class ToolCall:
    """A tool call paired with its result by ``tool_use_id``."""

    id: str
    tool_use_id: str
    tool: str
    input: Any
    call_index: int
    result_index: int
    result_chars: int
    is_error: bool
    pinned: bool


@dataclass
class CallAnswer:
    """Jev's probabilities for one call: keep the call, keep its result verbatim."""

    keep_call: float
    keep_result: float


CallAction = Literal["keep", "drop_result", "drop_call"]
DecisionReason = Literal["pinned", "kept", "result_dropped", "call_dropped"]


@dataclass
class CallDecision:
    id: str
    tool: str
    keep_call: float
    keep_result: float
    action: CallAction
    reason: DecisionReason


@dataclass
class HistoryToolCall:
    id: str
    tool: str
    input: str
    result: str

    def to_wire(self) -> dict[str, Any]:
        return {"id": self.id, "tool": self.tool, "input": self.input, "result": self.result}


@dataclass
class HistoryEntry:
    """One history entry of the state Jev sees (upstream ``HistoryEntry``)."""

    i: int
    role: str
    text: str
    #: Structured per call, or one compact line per call once the state had to shrink.
    tool_calls: list[HistoryToolCall] | list[str] | None = None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"i": self.i, "role": self.role, "text": self.text}
        if self.tool_calls is not None:
            wire["tool_calls"] = [
                call if isinstance(call, str) else call.to_wire() for call in self.tool_calls
            ]
        return wire


@dataclass
class CompactionState:
    """The state sent with every Jev request: the whole history, results omitted."""

    context: str
    goal: str
    history: list[HistoryEntry]

    def to_wire(self) -> dict[str, Any]:
        return {
            "context": self.context,
            "goal": self.goal,
            "history": [entry.to_wire() for entry in self.history],
        }


@dataclass
class FittedState:
    state: CompactionState
    tokens: int
    #: Which fitting stage produced the state, for diagnostics.
    stage: str


@dataclass
class CompactOptions:
    """Compaction options; ``None`` on a field means "use the upstream default"."""

    #: Ongoing task description; defaults to the last few user prompts.
    goal: str | None = None
    #: Minimum keep probability for a call or result to stay. Default 0.5.
    keep_threshold: float | None = None
    #: Newest messages never touched (the first message is always kept). Default 6.
    preserve_recent_messages: int | None = None
    #: Estimated token ceiling for the state. Default 25 000.
    max_state_tokens: int | None = None
    #: Estimated token ceiling for state plus one batch of questions. Default 30 000.
    max_request_tokens: int | None = None
    #: Characters of a dropped tool result to retain. Default 300.
    truncate_head_chars: int | None = None
    #: Port-only: batches asked at the same time. 0 = all at once (upstream
    #: ``Promise.all``), 1 = one after the other.
    max_concurrency: int | None = None


@dataclass
class ResolvedCompactOptions:
    goal: str
    keep_threshold: float
    preserve_recent_messages: int
    max_state_tokens: int
    max_request_tokens: int
    truncate_head_chars: int
    max_concurrency: int


@dataclass
class CompactStats:
    messages_before: int
    messages_after: int
    chars_before: int
    chars_after: int
    calls: int
    kept: int
    results_dropped: int
    calls_dropped: int
    pinned: int
    state_tokens: int
    #: Which fitting stage the state needed; ``""`` when no request was made.
    state_stage: str
    requests: int
    ms: int

    def to_wire(self) -> dict[str, Any]:
        """The upstream ``stats`` object (camelCase keys, for CLI JSON output)."""
        return {
            "messagesBefore": self.messages_before,
            "messagesAfter": self.messages_after,
            "charsBefore": self.chars_before,
            "charsAfter": self.chars_after,
            "calls": self.calls,
            "kept": self.kept,
            "resultsDropped": self.results_dropped,
            "callsDropped": self.calls_dropped,
            "pinned": self.pinned,
            "stateTokens": self.state_tokens,
            "stateStage": self.state_stage,
            "requests": self.requests,
            "ms": self.ms,
        }


@dataclass
class CompactResult:
    #: The compacted transcript; untouched messages are the input objects.
    messages: list[Any]
    decisions: list[CallDecision]
    stats: CompactStats

    def reduction_ratio(self) -> float:
        from .compact import reduction_ratio

        return reduction_ratio(self)


@dataclass
class JevResponse:
    """A Jev answer set (upstream ``JevResponse``)."""

    answers: dict[str, Any]
    model: str | None = None
    usage: dict[str, Any] | None = None
    #: The response body as parsed, for fields this dataclass does not model.
    raw: dict[str, Any] | None = None


@dataclass
class JevRequest:
    """The HTTP request for one Jev call, for any fetch-like transport."""

    url: str
    method: str
    headers: dict[str, str]
    body: str


@runtime_checkable
class JevAsker(Protocol):
    """Anything that can answer Jev questions: our LLM provider adapter, a fixture.

    ``state`` is the JSON-ready state object (upstream ``CompactionState``);
    ``questions`` is a mapping of question name to question object. The returned
    answer set must carry one finite ``noul`` probability per asked question.
    A failure of any kind should raise: the caller then falls back.
    """

    def ask(
        self, state: dict[str, Any], questions: dict[str, Any]
    ) -> JevResponse | Mapping[str, Any]:
        """Answer one batch of questions about the state."""
        ...


@runtime_checkable
class AsyncJevAsker(Protocol):
    """The async twin of :class:`JevAsker`, for an async provider adapter."""

    async def ask(
        self, state: dict[str, Any], questions: dict[str, Any]
    ) -> JevResponse | Mapping[str, Any]:
        """Answer one batch of questions about the state."""
        ...


def snake_to_camel(name: str) -> str:
    """``max_state_tokens`` -> ``maxStateTokens``."""
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def option_value(options: Any, name: str, default: Any = None) -> Any:
    """Read one option from a mapping, a dataclass or any object.

    Accepts the snake_case name (``max_state_tokens``) or the upstream camelCase
    one (``maxStateTokens``). A value of ``None`` means "unset", as in upstream's
    ``options.maxStateTokens ?? DEFAULT``.
    """
    if options is None:
        return default
    if isinstance(options, Mapping):
        if name in options:
            return options[name]
        camel = snake_to_camel(name)
        return options[camel] if camel in options else default
    if hasattr(options, name):
        return getattr(options, name)
    camel = snake_to_camel(name)
    return getattr(options, camel) if hasattr(options, camel) else default


def answers_of(response: JevResponse | Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``answers`` mapping of a Jev response (dataclass or raw body)."""
    if isinstance(response, Mapping):
        answers = response.get("answers")
    else:
        answers = getattr(response, "answers", None)
    if not isinstance(answers, Mapping):
        from .errors import JevAnswerError

        raise JevAnswerError("Jev response is missing answers")
    return answers
