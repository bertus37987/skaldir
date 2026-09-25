"""Bounded-rate streaming: the render side of a stream that arrives per token.

A model (or a chatty child process) hands us thousands of small chunks. Writing each one
of them to a terminal costs one ``write(2)`` per chunk, so the *terminal* becomes the
slowest consumer in the pipeline and the process burns CPU on syscalls nobody can see —
the user cannot read 200 updates a second anyway. This module is the leaky bucket between
the two: it accepts every chunk, remembers all of them, and **renders at most once per
interval**.

Design transferred from **Cline** (Apache-2.0 — credited in ``THIRD_PARTY_NOTICES.md``),
read at ``sdk/packages/core/src/extensions/tools/executors/bash.ts`` in the upstream tree
(clone studied 2026-09-18, commit ``3a4a14a``):

===========  =========================================================================
Cline        ``createCommandProgressEmitter`` — a per-stream pending buffer, a bounded
             pending tail (``maxPendingChars``), one update per
             ``COMMAND_PROGRESS_FLUSH_INTERVAL_MS`` (48 ms), an ``unref()``-ed timer,
             dropped-character accounting, and a final flush on stop. Its purpose there
             is the same: a noisy child process must not create an unbounded queue of
             UI/websocket events.
             ``createRollingCollector`` — head kept verbatim, tail rolling, so a huge
             stream costs a fixed amount of memory instead of growing without bound.
===========  =========================================================================

Two deliberate differences on a terminal, both measured (``MVP/53-cline-perf.md``):

* **The first chunk is rendered immediately.** Cline's flush timer also delays the first
  update by up to one interval; on a terminal the first byte *is* the perceived latency,
  so :class:`Coalescer` renders the first chunk at once and only coalesces from the second
  one. ``Enter → first byte`` therefore does not move when coalescing is switched on.
* **The flush is time-driven, not timer-driven.** The shell writes from a synchronous
  handler with no event loop to run a ``setTimeout`` callback, so :class:`Coalescer`
  checks the clock on every ``write`` (and exposes :meth:`Coalescer.maybe_flush`). A host
  that *has* a loop can instead pass ``schedule=`` and tick it; both shapes share one
  state machine and one metric set.

Nothing here prints, and nothing here owns policy: a caller hands in a stream and gets a
stream back. The metrics are the point — ``chunks_in``, ``renders``, ``renders_per_s`` and
``cpu_ms`` are what turn "it feels instant" into a number.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .env import getenv

__all__ = [
    "DEFAULT_HZ",
    "LockedStream",
    "same_terminal",
    "DEFAULT_MAX_BUFFER",
    "MAX_HZ",
    "MIN_HZ",
    "STALL_S",
    "Activity",
    "BatchTracker",
    "Coalescer",
    "RollingBuffer",
    "StreamMetrics",
    "ToolBatch",
    "WIRING_SNIPPET",
    "coalescing_stream",
    "contract_entries",
    "install_contract_entries",
    "plan_tool_batches",
    "register_cli",
    "run_stream",
]

#: One render per this many seconds. Cline coalesces into one UI update per 48 ms
#: (``COMMAND_PROGRESS_FLUSH_INTERVAL_MS``); a terminal is smooth at 30 Hz and the eye
#: cannot follow more.
DEFAULT_HZ = 30.0
MIN_HZ = 1.0
MAX_HZ = 240.0

#: A render's ceiling on buffered characters — a burst is bounded by bytes as well as by
#: time, so a single 1 MB chunk cannot be held hostage by the interval.
DEFAULT_MAX_BUFFER = 8192

#: A stream that produced nothing for this long is *stalled*, and the activity line says
#: so instead of spinning. A spinner that keeps turning while nothing arrives is a lie.
STALL_S = 2.0

#: The frames an activity mark cycles through: the product's glyph set, ASCII fallback.
FRAMES_RICH = ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷")
FRAMES_PLAIN = ("|", "/", "-", "\\")


def _now() -> float:
    return time.monotonic()


def _hz(value: Any, *, default: float = DEFAULT_HZ) -> float:
    """A render rate in Hz, clamped to something a terminal can survive."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number <= 0:  # NaN or non-positive
        return default
    return max(MIN_HZ, min(MAX_HZ, number))


# --------------------------------------------------------------------------- #
# the metrics — the reason this module exists
# --------------------------------------------------------------------------- #


@dataclass
class StreamMetrics:
    """What one coalesced stream really did. Every field is measured, none is derived.

    ``first_render_ms``/``last_render_ms`` are measured from ``started`` (the caller's
    "go" — for the shell, the moment the command was submitted). ``cpu_ms`` is
    ``time.process_time()`` (this process's user + system CPU, I/O wait excluded), so a
    render count that falls while the wall clock stays flat shows up as saved CPU and not
    as a looser definition of "faster".
    """

    chunks_in: int = 0
    chars_in: int = 0
    renders: int = 0
    chars_rendered: int = 0
    first_render_ms: int = -1
    last_render_ms: int = -1
    elapsed_ms: int = 0
    cpu_ms: int = 0
    interval_ms: int = 0
    max_buffer: int = 0

    @property
    def renders_per_s(self) -> float:
        elapsed = self.elapsed_ms / 1000.0
        return round(self.renders / elapsed, 1) if elapsed > 0 else 0.0

    @property
    def chunks_per_render(self) -> float:
        return round(self.chunks_in / self.renders, 1) if self.renders else 0.0

    @property
    def bytes_per_render(self) -> int:
        return int(self.chars_rendered / self.renders) if self.renders else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunks_in": self.chunks_in,
            "chars_in": self.chars_in,
            "renders": self.renders,
            "chars_rendered": self.chars_rendered,
            "chunks_per_render": self.chunks_per_render,
            "bytes_per_render": self.bytes_per_render,
            "renders_per_s": self.renders_per_s,
            "first_render_ms": self.first_render_ms,
            "last_render_ms": self.last_render_ms,
            "elapsed_ms": self.elapsed_ms,
            "cpu_ms": self.cpu_ms,
            "interval_ms": self.interval_ms,
            "max_buffer": self.max_buffer,
        }


class Coalescer(io.TextIOBase):
    """A write-through stream that renders at a bounded rate and loses nothing.

    ``write`` accepts every chunk and *records* all of them; what is bounded is how often
    the pending text reaches ``primary``. Byte order and byte content are unchanged — only
    the number of ``write`` calls on ``primary`` is — so a redirect or a transcript is
    indistinguishable from an uncoalesced run.

    ``sink`` (optional) is an extra stream that always receives every chunk immediately,
    uncoalesced: that is how the shell keeps its session record exact while the terminal
    is paced.
    """

    def __init__(
        self,
        primary: Any,
        *,
        hz: float = DEFAULT_HZ,
        max_buffer: int = DEFAULT_MAX_BUFFER,
        sink: Any = None,
        clock: Callable[[], float] = _now,
        schedule: Callable[[float, Callable[[], None]], Any] | None = None,
        immediate_first: bool = True,
        before_render: Callable[[StreamMetrics], None] | None = None,
    ) -> None:
        self._primary = primary
        self._sink = sink
        self._clock = clock
        self._schedule = schedule
        self._immediate_first = bool(immediate_first)
        self._before_render = before_render
        self._interval = 1.0 / _hz(hz)
        self._max_buffer = max(1, int(max_buffer))
        self._pending: list[str] = []
        self._pending_chars = 0
        self._last_render_at: float | None = None
        self._finished_at: float | None = None
        self._started: float | None = None
        self._closed = False
        self._timer: Any = None
        self._cpu_at_start: float | None = None
        self._cpu_at_end: float | None = None
        self.metrics = StreamMetrics(interval_ms=int(self._interval * 1000),
                                     max_buffer=self._max_buffer)
        #: True when the last write was followed by the buffer being left pending
        self.deferred = False

    # -- identity of the wrapped stream --------------------------------------- #

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return bool(getattr(self._primary, "isatty", lambda: False)())

    def fileno(self) -> int:  # pragma: no cover — only when something really needs it
        return self._primary.fileno()

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return str(getattr(self._primary, "encoding", "utf-8"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)

    # -- the write path ------------------------------------------------------- #

    @property
    def pending(self) -> int:
        """Characters waiting for the next render."""
        return self._pending_chars

    def write(self, text: str) -> int:  # type: ignore[override]
        text = str(text)
        if not text:
            return 0
        if self._started is None:
            self._started = self._clock()
            self._cpu_at_start = time.process_time()
        if self._sink is not None:
            try:
                self._sink.write(text)
            except Exception:  # noqa: BLE001 — the record must never break the stream
                pass
        self._pending.append(text)
        self._pending_chars += len(text)
        self.metrics.chunks_in += 1
        self.metrics.chars_in += len(text)

        if self._closed:
            return len(text)
        if self._immediate_first and self.metrics.renders == 0:
            self._render()
        elif self._pending_chars >= self._max_buffer:
            self._render()
        else:
            self.deferred = not self.maybe_flush()
        return len(text)

    def maybe_flush(self, now: float | None = None) -> bool:
        """Render the pending text **only if** the interval has elapsed. Returns whether
        a render happened — the one entry point a polling host needs."""
        if self._closed or not self._pending:
            return False
        moment = self._clock() if now is None else float(now)
        if self._last_render_at is not None and moment - self._last_render_at < self._interval:
            return False
        self._render()
        return True

    def flush(self) -> None:
        """The IO-protocol flush: pass it through **without** breaking the render bound.

        This is the method a producer calls after every chunk (``sys.stdout.flush()`` does,
        and so does a ``print``), so rendering here would put one write on the terminal per
        token — exactly the thing this class exists to prevent. What a flush *means* is
        "do not hold bytes back indefinitely", and :meth:`maybe_flush` is that promise: the
        pending text goes out as soon as the interval allows. The tail is never at risk
        because :meth:`drain` (and :meth:`close`) render unconditionally, and the shell
        drains both streams when the command ends.
        """
        self.maybe_flush()
        for stream in (self._sink, self._primary):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:  # noqa: BLE001 — a closed terminal is not the stream's fault
                pass

    def drain(self) -> None:
        """Render everything pending **now**, bound or no bound — the tail path.

        Called at the end of a command (and by :meth:`close`): the render bound is a pacing
        rule, never a licence to swallow the last bytes of an answer.
        """
        self._cancel_timer()
        self._render()
        for stream in (self._sink, self._primary):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        """Drain the tail and stop accepting renders. The primary stream stays open."""
        if self._closed:
            return
        self.drain()
        self._closed = True
        self.metrics.elapsed_ms = self._elapsed_ms()
        if self._cpu_at_start is not None:
            self._cpu_at_end = time.process_time()
            self.metrics.cpu_ms = int((self._cpu_at_end - self._cpu_at_start) * 1000)

    # -- internals ------------------------------------------------------------ #

    def _elapsed_ms(self) -> int:
        if self._started is None:
            return 0
        moment = self._finished_at if self._finished_at is not None else self._clock()
        return max(0, int((moment - self._started) * 1000))

    def _render(self) -> None:
        if not self._pending:
            return
        text = "".join(self._pending)
        self._pending = []
        self._pending_chars = 0
        self._cancel_timer()
        moment = self._clock()
        self._last_render_at = moment
        self._finished_at = moment
        if self._before_render is not None:
            # called immediately BEFORE the render reaches the primary: the hook a live
            # indicator uses to erase its own line so the two never interleave
            try:
                self._before_render(self.metrics)
            except Exception:  # noqa: BLE001 — an observer never breaks the stream
                pass
        try:
            self._primary.write(text)
        except Exception:  # noqa: BLE001 — a closed terminal is not the stream's fault
            pass
        try:
            self._primary.flush()
        except Exception:  # noqa: BLE001
            pass
        self.metrics.renders += 1
        self.metrics.chars_rendered += len(text)
        if self._started is not None:
            offset = max(0, int((moment - self._started) * 1000))
            if self.metrics.first_render_ms < 0:
                self.metrics.first_render_ms = offset
            self.metrics.last_render_ms = offset
        self._arm_timer()

    def _arm_timer(self) -> None:
        """Only for a host that supplies a scheduler; the shell does not (it polls)."""
        if self._schedule is None or self._closed or self._timer is not None:
            return
        if self._last_render_at is None:
            return

        def fire() -> None:
            self._timer = None
            self.maybe_flush()

        try:
            self._timer = self._schedule(self._interval, fire)
        except Exception:  # noqa: BLE001 — no scheduler, no timer, nothing lost
            self._timer = None

    def _cancel_timer(self) -> None:
        if self._timer is None:
            return
        cancel = getattr(self._timer, "cancel", None)
        self._timer = None
        if callable(cancel):
            try:
                cancel()
            except Exception:  # noqa: BLE001
                pass


class LockedStream:
    """A stream whose writes are serialized with every other writer of the same target.

    A terminal has one cursor. Two threads that both write to it — the command's output and
    a live indicator redrawing its line — will eventually write into each other, and the
    measured result is not a cosmetic artefact: with the shell's activity thread running,
    four of 240 streamed answer lines lost their first byte (``MVP/53-cline-perf.md`` §6).
    Wrapping the terminal in one lock, shared with the indicator, is the whole fix: the
    bytes are then written one writer at a time and the stream is intact.
    """

    def __init__(self, primary: Any, lock: Any) -> None:
        self._primary = primary
        self._lock = lock

    def write(self, text: str) -> int:
        with self._lock:
            return int(self._primary.write(text))

    def flush(self) -> None:
        with self._lock:
            flush = getattr(self._primary, "flush", None)
            if callable(flush):
                flush()

    def isatty(self) -> bool:
        return bool(getattr(self._primary, "isatty", lambda: False)())

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:  # pragma: no cover — only when something really needs it
        return self._primary.fileno()

    @property
    def encoding(self) -> str:
        return str(getattr(self._primary, "encoding", None) or "utf-8")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)


def same_terminal(first: Any, second: Any) -> bool:
    """True when two streams are the same *terminal*, so writes must keep one order.

    ``stdout`` and ``stderr`` are separate file objects that may still address one tty.
    Coalescing them independently breaks the program's own write order at the end of a
    command — measured on a real pty: the replay's stderr facts line landed *inside* the
    last stdout line, because stdout's tail was still inside its interval. One terminal
    has one cursor, so when the two descriptors address the same device they get one
    coalescer and one order. Two different destinations (a tty plus a redirected file)
    keep their own, because then nobody is reading them as one stream.
    """
    if first is None or second is None:
        return False
    if first is second:
        return True
    try:
        import os
        import stat as stat_module

        left = os.fstat(first.fileno())
        right = os.fstat(second.fileno())
    except (AttributeError, OSError, ValueError):
        return False
    if not (stat_module.S_ISCHR(left.st_mode) and stat_module.S_ISCHR(right.st_mode)):
        return False
    return (left.st_rdev, left.st_ino) == (right.st_rdev, right.st_ino)


def coalescing_stream(
    primary: Any,
    *,
    enabled: bool = True,
    hz: float = DEFAULT_HZ,
    max_buffer: int = DEFAULT_MAX_BUFFER,
    sink: Any = None,
) -> Any:
    """``primary`` wrapped in a :class:`Coalescer`, or ``primary`` itself when disabled.

    One call site for "should this stream be paced?" so a caller cannot half-enable it.
    """
    if not enabled:
        return primary
    return Coalescer(primary, hz=hz, max_buffer=max_buffer, sink=sink)


# --------------------------------------------------------------------------- #
# bounded memory: the same trick Cline's rolling collector uses
# --------------------------------------------------------------------------- #


@dataclass
class RollingBuffer:
    """A bounded text buffer: the head verbatim plus a rolling tail.

    Cline's ``createRollingCollector`` keeps the first half of the budget verbatim and
    rolls the rest, so *the latest output always survives* and the memory cost is fixed.
    The same shape here, with the drop count carried so a truncated record says so
    instead of quietly lying about its length.
    """

    max_chars: int = DEFAULT_MAX_BUFFER
    head: str = ""
    tail: str = ""
    total_chars: int = 0
    dropped: int = 0

    def __post_init__(self) -> None:
        self.max_chars = max(2, int(self.max_chars))

    @property
    def _head_limit(self) -> int:
        return max(1, (self.max_chars + 1) // 2)

    @property
    def _tail_limit(self) -> int:
        return max(1, self.max_chars - self._head_limit)

    def append(self, text: str) -> None:
        text = str(text)
        if not text:
            return
        self.total_chars += len(text)
        room = self._head_limit - len(self.head)
        if room > 0:
            self.head += text[:room]
            text = text[room:]
        if text:
            self.tail = (self.tail + text)[-self._tail_limit:]
        self.dropped = max(0, self.total_chars - len(self.head) - len(self.tail))

    def value(self) -> str:
        return self.head + self.tail

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.value(),
            "total_chars": self.total_chars,
            "dropped_chars": self.dropped,
            "truncated": self.dropped > 0,
            "max_chars": self.max_chars,
        }


# --------------------------------------------------------------------------- #
# parallel tool calls: Cline's adjacency rule
# --------------------------------------------------------------------------- #


@dataclass
class ToolBatch:
    """One group of tool calls that may overlap, and the mode that put them there."""

    mode: str
    names: tuple[str, ...]
    indexes: tuple[int, ...]

    @property
    def parallel(self) -> bool:
        return self.mode == "parallel"

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "names": list(self.names), "indexes": list(self.indexes)}


def plan_tool_batches(
    calls: Sequence[Any],
    *,
    default: str = "sequential",
) -> list[ToolBatch]:
    """Group tool calls into the batches a runner may execute concurrently.

    The rule is Cline's (``sdk/packages/agents/src/agent-runtime.ts``, ``executeToolCalls``):
    **only adjacent parallel calls overlap.** A tool declares its own ``executionMode``
    and the run's default fills in for the rest; a sequential call is a barrier — the
    group before it must finish, and it must finish before the group after it. That is
    what makes "run these three reads at once" safe without allowing a write to overtake a
    read it depends on.

    ``calls`` is any sequence of items carrying a name and an optional declared mode —
    ``str``, a mapping with ``name``/``execution_mode``, or an object with those
    attributes. The caller keeps ownership of the calls; this returns indexes.
    """

    def mode_of(item: Any) -> str:
        declared = ""
        if isinstance(item, str):
            declared = ""
        elif isinstance(item, dict):
            declared = str(item.get("execution_mode") or item.get("executionMode") or "")
        else:
            declared = str(
                getattr(item, "execution_mode", "") or getattr(item, "executionMode", "") or ""
            )
        declared = declared.strip().lower()
        if declared not in {"parallel", "sequential"}:
            declared = "parallel" if str(default).lower() == "parallel" else "sequential"
        return declared

    names = [_name_of(item) for item in calls]
    modes = [mode_of(item) for item in calls]

    batches: list[ToolBatch] = []
    index = 0
    while index < len(modes):
        if "parallel" not in modes[index]:
            batches.append(ToolBatch("sequential", (names[index],), (index,)))
            index += 1
            continue
        start = index
        while index < len(modes) and modes[index] == "parallel":
            index += 1
        batches.append(
            ToolBatch("parallel", tuple(names[start:index]), tuple(range(start, index)))
        )
    return batches


def _name_of(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("name") or item.get("tool") or "")
    return str(getattr(item, "name", "") or getattr(item, "tool", "") or "")


@dataclass
class BatchTracker:
    """One live line for a group of concurrently running tool calls.

    Sequential tools each need their own line; N parallel tools are *one* line that is
    re-rendered in place, which is Cline's "⌁ Read 6 files" shape and the reason a wave of
    parallel work does not scroll the transcript away. ``renders`` counts only the ticks
    where something actually changed, so the saving is measurable rather than asserted.
    """

    total: int = 0
    states: dict[str, str] = field(default_factory=dict)
    renders: int = 0
    ticks: int = 0

    def begin(self, names: Iterable[str]) -> None:
        self.states = {str(name): "running" for name in names}
        self.total = len(self.states)

    def set(self, name: str, state: str) -> bool:
        """Record a state change; returns True when the rendered line really changed."""
        self.ticks += 1
        previous = self.states.get(str(name))
        if previous == state or previous is None:
            return False
        self.states[str(name)] = state
        self.renders += 1
        return True

    @property
    def done(self) -> int:
        return sum(1 for state in self.states.values() if state != "running")

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "done": self.done,
            "renders": self.renders,
            "ticks": self.ticks,
            "suppressed": max(0, self.ticks - self.renders),
        }


# --------------------------------------------------------------------------- #
# the activity indicator — it ticks from real data
# --------------------------------------------------------------------------- #


class Activity:
    """A one-line activity indicator whose facts come from a real source.

    ``source`` is a callable returning a mapping of *measured* values (chunks, renders,
    bytes, elapsed). Two honesty rules are enforced here, not left to the caller:

    * **the frame only advances when the source moved.** A tick that carries no new fact
      returns the same line and is counted as suppressed — the indicator cannot animate a
      busy state that does not exist;
    * **a stalled stream is labelled stalled** after :data:`STALL_S`, instead of spinning
      forever under a "working" label.
    """

    def __init__(
        self,
        source: Callable[[], dict[str, Any]],
        *,
        label: str = "working",
        frames: Sequence[str] = FRAMES_RICH,
        stall_s: float = STALL_S,
        clock: Callable[[], float] = _now,
    ) -> None:
        self._source = source
        self._clock = clock
        self.label = label
        self.frames = tuple(frames) or FRAMES_RICH
        self.stall_s = float(stall_s)
        self.ticks = 0
        self.renders = 0
        self._frame = 0
        self._facts: dict[str, Any] = {}
        self._changed_at = self._clock()

    def frame(self) -> str:
        return self.frames[self._frame % len(self.frames)]

    def stalled(self) -> bool:
        return (self._clock() - self._changed_at) >= self.stall_s

    def tick(self) -> tuple[bool, dict[str, Any]]:
        """Poll the real source. Returns ``(changed, facts)`` — ``changed`` False means
        the caller must not write anything at all."""
        self.ticks += 1
        facts = dict(self._source() or {})
        moved = facts != self._facts
        if moved:
            self._facts = facts
            self._frame += 1
            self._changed_at = self._clock()
            self.renders += 1
        return moved, dict(facts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ticks": self.ticks,
            "renders": self.renders,
            "suppressed": max(0, self.ticks - self.renders),
            "stalled": self.stalled(),
            "facts": dict(self._facts),
        }


# --------------------------------------------------------------------------- #
# running an iterable of chunks through the bound
# --------------------------------------------------------------------------- #


def run_stream(
    writer: Any,
    chunks: Iterable[str],
    *,
    hz: float = DEFAULT_HZ,
    max_buffer: int = DEFAULT_MAX_BUFFER,
    sink: Any = None,
    pace: Callable[[int], None] | None = None,
) -> StreamMetrics:
    """Push ``chunks`` through a fresh :class:`Coalescer` on ``writer``; return its metrics.

    ``pace`` is called with the chunk index *before* the chunk is written — a measurement
    harness uses it to reproduce a real token rate instead of a tight loop, which is the
    difference between measuring the coalescer and measuring the CPU.
    """
    stream = Coalescer(writer, hz=hz, max_buffer=max_buffer, sink=sink)
    index = 0
    try:
        for chunk in chunks:
            if pace is not None:
                pace(index)
            stream.write(chunk)
            index += 1
    finally:
        stream.close()
    return stream.metrics


# --------------------------------------------------------------------------- #
# CLI group: ``skaldir stream …``
# --------------------------------------------------------------------------- #

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_INTERNAL = 7

LAYER = "PERSIST"

DEFAULT_CHUNKS = 600
DEFAULT_CHUNK_BYTES = 8
#: A fast but real token rate: ~300 chunks/s is a quick model, and the run stays ~2 s.
DEFAULT_CHUNK_RATE = 300.0


def _text_chunks(text: str, size: int) -> list[str]:
    return [text[index:index + size] for index in range(0, len(text), size)]


def _sample_text(chunks: int, chunk_bytes: int) -> str:
    """A deterministic answer-shaped sample — same bytes for every run of a measurement."""
    words = ("agent", "runtime", "stream", "render", "coalesce", "budget", "verify", "skaldir")
    total = max(1, chunks * chunk_bytes)
    out: list[str] = []
    length = 0
    index = 0
    while length < total:
        piece = words[index % len(words)] + " "
        out.append(piece)
        length += len(piece)
        index += 1
    return "".join(out)[:total]


def _pace(started: float, index: int, rate: float) -> None:
    """Hold chunk ``index`` back until its slot on a real ``rate``-chunks/s timeline.

    A tight loop measures the coalescer against an input no model produces; pacing at a
    token-like rate is what makes ``elapsed``, ``renders_per_s`` and ``cpu_ms`` comparable
    to a real answer. Reading the clock once per chunk is the only added cost, and it is
    the same cost in both arms of a comparison.
    """
    if rate <= 0:
        return
    due = started + (index + 1) / rate
    remaining = due - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def _measure(*, chunks: int, chunk_bytes: int, hz: float, max_buffer: int,
             coalesce: bool, chunk_rate: float = 0.0) -> dict[str, Any]:
    """Run one measurement in-process against a count-only sink.

    The sink counts ``write`` calls instead of echoing bytes: the render count is the
    quantity under test, and a real terminal is not needed to count it (the PTY proof is
    ``tools/gv_stream_perf.py``). The input sequence, the pace and the sink are identical
    in both arms, so the only difference between them is the coalescer.
    """
    produced = _sample_text(chunks, chunk_bytes)
    pieces = _text_chunks(produced, chunk_bytes)

    class _Counter(io.StringIO):
        writes = 0

        def write(self, text: str) -> int:  # type: ignore[override]
            _Counter.writes += 1
            return super().write(text)

    sink = _Counter()
    _Counter.writes = 0
    cpu_at_start = time.process_time()
    started = _now()
    stream = Coalescer(sink, hz=hz, max_buffer=max_buffer) if coalesce else None
    target = stream if stream is not None else sink
    for index, piece in enumerate(pieces):
        _pace(started, index, chunk_rate)
        target.write(piece)
    if stream is not None:
        stream.close()
    wall_ms = max(0, int((_now() - started) * 1000))
    cpu_ms = max(0, int((time.process_time() - cpu_at_start) * 1000))
    text = sink.getvalue()
    if stream is not None:
        metrics = stream.metrics
        metrics.elapsed_ms = wall_ms
        metrics.cpu_ms = cpu_ms
    else:
        metrics = StreamMetrics(
            chunks_in=len(pieces),
            chars_in=len(produced),
            renders=_Counter.writes,
            chars_rendered=len(text),
            elapsed_ms=wall_ms,
            cpu_ms=cpu_ms,
            interval_ms=0,
            max_buffer=0,
        )
    payload = metrics.as_dict()
    payload.update(
        {
            "coalesced": bool(coalesce),
            "hz": hz if coalesce else None,
            "pacing": f"{chunk_rate:g} chunks/s" if chunk_rate > 0 else "none (tight loop)",
            "bytes_exact": text == produced,
            "cpu_method": "time.process_time() of this process (user+system, I/O wait excluded)",
            "wall_method": "time.monotonic() around the write loop, one clock read per chunk",
        }
    )
    return payload


def _render_bound(hz: float, elapsed_ms: int, chars_in: int, max_buffer: int) -> int:
    """The most renders a correct coalescer may have produced in this run.

    Two independent causes for a render, plus slack: the interval elapsing (``hz``), and
    the pending buffer reaching ``max_buffer`` — a burst is bounded by bytes as well as by
    time, so a tight loop legitimately renders more often than ``hz`` says. The first
    chunk always renders, hence the ``+ 2``.
    """
    timed = hz * (elapsed_ms / 1000.0)
    by_bytes = chars_in / max(1, max_buffer)
    return int(timed + by_bytes) + 2


def _compare(*, chunks: int, chunk_bytes: int, hz: float, max_buffer: int,
             chunk_rate: float = 0.0) -> dict[str, Any]:
    before = _measure(chunks=chunks, chunk_bytes=chunk_bytes, hz=hz,
                      max_buffer=max_buffer, coalesce=False, chunk_rate=chunk_rate)
    after = _measure(chunks=chunks, chunk_bytes=chunk_bytes, hz=hz,
                     max_buffer=max_buffer, coalesce=True, chunk_rate=chunk_rate)
    if not (before["bytes_exact"] and after["bytes_exact"]):
        raise RuntimeError("a measurement run did not reproduce its input bytes")
    saved_cpu = before["cpu_ms"] - after["cpu_ms"]
    payload = {
        "chunks": chunks,
        "chunk_bytes": chunk_bytes,
        "chunk_rate": chunk_rate,
        "hz": hz,
        "max_buffer": max_buffer,
        "before": before,
        "after": after,
        "renders_saved": before["renders"] - after["renders"],
        "cpu_saved_ms": saved_cpu,
        "suppression_ratio": round(before["renders"] / after["renders"], 1)
        if after["renders"]
        else None,
        "byte_identical": before["bytes_exact"] and after["bytes_exact"],
        "method": "the same chunk sequence at the same pace through the same sink, "
        "coalescing off then on; only the coalescer differs between the arms",
    }
    return payload


def _cmd_selfcheck(args: argparse.Namespace) -> int:
    hz = _hz(getattr(args, "hz", DEFAULT_HZ))
    chunks = max(2, int(getattr(args, "chunks", DEFAULT_CHUNKS) or DEFAULT_CHUNKS))
    chunk_rate = max(0.0, float(getattr(args, "chunk_rate", DEFAULT_CHUNK_RATE) or 0.0))
    max_buffer = max(1, int(getattr(args, "max_buffer", DEFAULT_MAX_BUFFER) or 8))
    payload = _compare(chunks=chunks, chunk_bytes=DEFAULT_CHUNK_BYTES, hz=hz,
                       max_buffer=max_buffer, chunk_rate=chunk_rate)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    before = payload["before"]
    after = payload["after"]
    bound = _render_bound(hz, after["elapsed_ms"], after["chars_in"], max_buffer)
    check(
        "byte-identical output",
        bool(payload["byte_identical"]),
        "the coalesced and uncoalesced runs produced the same bytes in the same order",
    )
    check(
        "render rate stays under the requested bound",
        after["renders"] <= bound,
        f"{after['renders']} renders <= {bound} allowed "
        f"({hz:g} Hz x {after['elapsed_ms']} ms + "
        f"{after['chars_in']}/{max_buffer} bytes-forced + 2)",
    )
    check(
        "coalescing suppresses renders",
        after["renders"] < before["renders"],
        f"{before['renders']} -> {after['renders']} renders "
        f"({payload['suppression_ratio']}x fewer)",
    )
    check(
        "no bytes are dropped",
        after["chars_in"] == after["chars_rendered"],
        f"{after['chars_in']} chars in, {after['chars_rendered']} rendered",
    )
    check(
        "the first chunk is rendered immediately",
        after["first_render_ms"] <= max(1, int(1000.0 / hz) + 1),
        f"first render at {after['first_render_ms']} ms "
        f"(one interval is {1000.0 / hz:.0f} ms)",
    )
    report = {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "checks_passed": sum(1 for item in checks if item["ok"]),
        "checks_total": len(checks),
        "measurement": payload,
        "source": "src/skaldir/streaming.py:Coalescer",
        "reference": "Cline COMMAND_PROGRESS_FLUSH_INTERVAL_MS=48 ms, bash.ts (Apache-2.0)",
    }
    if not report["ok"]:
        return _emit(args, report, EXIT_BLOCKED, "a streaming invariant did not hold")
    return _emit(args, report, EXIT_OK, "")


def _cmd_measure(args: argparse.Namespace) -> int:
    hz = _hz(getattr(args, "hz", DEFAULT_HZ))
    chunks = max(1, int(getattr(args, "chunks", DEFAULT_CHUNKS) or DEFAULT_CHUNKS))
    chunk_bytes = max(1, int(getattr(args, "chunk_bytes", DEFAULT_CHUNK_BYTES) or 8))
    max_buffer = max(1, int(getattr(args, "max_buffer", DEFAULT_MAX_BUFFER) or 8))
    chunk_rate = max(0.0, float(getattr(args, "chunk_rate", DEFAULT_CHUNK_RATE) or 0.0))
    payload = _compare(chunks=chunks, chunk_bytes=chunk_bytes, hz=hz, max_buffer=max_buffer,
                       chunk_rate=chunk_rate)
    return _emit(args, payload, EXIT_OK, "")


def _cmd_replay(args: argparse.Namespace) -> int:
    """Replay a real file as a chunked stream, one chunk per write.

    This is the measurement's *source*, and it says so: the bytes come from a real file on
    disk, the chunk boundaries and the rate are declared on the command line, and the
    payload names both. Nothing here coalesces — the pacing under test belongs to the
    consumer (the shell's :class:`Coalescer`), so replaying through a pipe and replaying
    through the shell measure exactly the same input.
    """
    source = str(getattr(args, "source", "") or "")
    data, origin = _replay_source(source)
    limit = max(0, int(getattr(args, "limit", 0) or 0))
    if limit:
        data = data[:limit]
    chunk_bytes = max(1, int(getattr(args, "chunk_bytes", DEFAULT_CHUNK_BYTES) or 8))
    rate = max(0.0, float(getattr(args, "rate", DEFAULT_CHUNK_RATE) or 0.0))
    pieces = _text_chunks(data, chunk_bytes)
    envelope = _envelope_mode() or _json_mode(args)
    started = _now()
    cpu_at_start = time.process_time()
    written = 0
    if not envelope:
        # stdout carries the answer *and only* the answer, so `| wc -c` means something
        try:
            for index, piece in enumerate(pieces):
                _pace(started, index, rate)
                sys.stdout.write(piece)
                sys.stdout.flush()
                written += len(piece)
        except BrokenPipeError:  # a closed pipe is a cancellation, not a crash
            written = 0
    wall_ms = max(0, int((_now() - started) * 1000))
    cpu_ms = max(0, int((time.process_time() - cpu_at_start) * 1000))
    payload = {
        "source": origin,
        "chunks": 0 if envelope else len(pieces),
        "chunk_bytes": chunk_bytes,
        "chars": written,
        "answer_streamed": not envelope,
        "rate": rate,
        "pacing": "declared (a benchmark, not a model)",
        "wall_ms": wall_ms,
        "cpu_ms": cpu_ms,
        "cpu_method": "time.process_time() of this process (user+system)",
        "wall_method": "time.monotonic() over the write loop",
    }
    if _envelope_mode() or _json_mode(args):
        return _emit(args, payload, EXIT_OK, "")
    # the answer already went to stdout; the facts go to stderr so the answer stays a
    # clean stream (a reader can `wc -c` it and get a number that means something)
    sys.stderr.write(
        f"replayed {written} chars as {len(pieces)} chunks at {rate:g}/s "
        f"in {wall_ms} ms ({cpu_ms} ms cpu)\n"
    )
    contract = _contract()
    if contract is not None:
        try:
            contract.set_data(payload)
            contract.add_evidence("src/skaldir/streaming.py:_cmd_replay", "run",
                                  f"streamed {written} chars as {len(pieces)} chunks")
        except Exception:  # noqa: BLE001
            pass
    return EXIT_OK


def _replay_source(source: str) -> tuple[str, str]:
    """The bytes to replay and an honest name for where they came from."""
    if source:
        path = Path(source).expanduser()
        try:
            return path.read_text(encoding="utf-8", errors="replace"), f"file:{path}"
        except OSError as exc:
            return (
                _sample_text(DEFAULT_CHUNKS, DEFAULT_CHUNK_BYTES),
                f"builtin:sample (could not read {path}: {exc.__class__.__name__})",
            )
    for candidate in (Path("README.md"), Path("AGENTS.md")):
        try:
            if candidate.is_file():
                return (
                    candidate.read_text(encoding="utf-8", errors="replace"),
                    f"file:{candidate}",
                )
        except OSError:  # noqa: PERF203 — try the next candidate
            continue
    return _sample_text(DEFAULT_CHUNKS, DEFAULT_CHUNK_BYTES), "builtin:sample"


def _cmd_policy(args: argparse.Namespace) -> int:
    declared = list(getattr(args, "tool", None) or [])
    if not declared:
        return _emit(
            args,
            {
                "error": "no tool declared",
                "usage": "--tool NAME[:parallel|:sequential] (repeatable)",
                "example": "stream policy --tool search:parallel --tool edit:sequential",
            },
            EXIT_USAGE,
            "stream policy needs at least one --tool",
        )
    calls: list[dict[str, str]] = []
    for item in declared:
        name, _, declared_mode = str(item).partition(":")
        mode = declared_mode.strip().lower()
        if mode not in {"parallel", "sequential"}:
            mode = ""
        calls.append({"name": name.strip(), "execution_mode": mode})
    default = str(getattr(args, "default", "sequential") or "sequential").lower()
    if default not in {"parallel", "sequential"}:
        default = "sequential"
    batches = plan_tool_batches(calls, default=default)
    payload = {
        "default": default,
        "calls": calls,
        "batches": [batch.as_dict() for batch in batches],
        "parallel_batches": sum(1 for batch in batches if batch.parallel),
        "barriers": sum(1 for batch in batches if not batch.parallel),
        "rule": "only adjacent parallel calls overlap; a sequential call is a barrier",
        "reference": "Cline agent-runtime.ts executeToolCalls() (Apache-2.0)",
    }
    return _emit(args, payload, EXIT_OK, "")


def _emit(args: argparse.Namespace, payload: dict[str, Any], code: int, message: str) -> int:
    """One result: fill the envelope's data/evidence, and print only when standalone.

    Inside the product CLI the driver owns stdout (it redirects a handler's writes into the
    envelope) — :func:`skaldir.cli_shared.envelope_mode` says which case this is, so the
    group never prints a second JSON document next to the driver's.
    """
    payload = dict(payload)
    payload.setdefault("ok", code == EXIT_OK)
    if message:
        payload["message"] = message
    contract = _contract()
    if contract is not None:
        try:
            contract.set_data(payload)
            contract.add_evidence(
                "src/skaldir/streaming.py:Coalescer", "code",
                "bounded-rate render of a streamed answer",
            )
            measured = payload.get("chunks")
            if measured is None:
                measured = (payload.get("measurement") or {}).get("chunks_in", 0)
            contract.add_evidence(
                "src/skaldir/streaming.py:run_stream", "run",
                f"measured {measured} chunks end to end",
            )
        except Exception:  # noqa: BLE001 — a contract that refuses still gets the payload
            pass
    if _envelope_mode():
        return code
    if _json_mode(args):
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(_human(payload) + "\n")
    return code


def _envelope_mode() -> bool:
    """True while the product driver runs us and prints the envelope itself."""
    try:
        from .cli_shared import envelope_mode

        return bool(envelope_mode())
    except Exception:  # noqa: BLE001 — standalone use is the normal case
        return False


def _json_mode(args: argparse.Namespace) -> bool:
    if bool(getattr(args, "json", False)):
        return True
    return str(getenv("SKALDIR_FORMAT", "") or "").lower() == "json"


def _human(payload: dict[str, Any]) -> str:
    if "before" in payload and "after" in payload:
        rows = [
            f"chunks        {payload['chunks']} x {payload['chunk_bytes']} bytes",
            f"hz            {payload['hz']:g}",
            f"renders       {payload['before']['renders']} -> {payload['after']['renders']}",
            f"renders/s     {payload['before']['renders_per_s']} -> "
            f"{payload['after']['renders_per_s']}",
            f"cpu ms        {payload['before']['cpu_ms']} -> {payload['after']['cpu_ms']}",
            f"bytes equal   {payload['byte_identical']}",
        ]
        return "\n".join(rows)
    if "batches" in payload:
        lines = [f"default {payload['default']}"]
        for index, batch in enumerate(payload["batches"], 1):
            lines.append(f"{index:>2}  {batch['mode']:<10} {', '.join(batch['names'])}")
        return "\n".join(lines)
    if "checks" in payload:
        lines = [
            f"[{'PASS' if item['ok'] else 'FAIL'}] {item['check']}: {item['detail']}"
            for item in payload["checks"]
        ]
        lines.append(f"{payload['checks_passed']}/{payload['checks_total']} checks passed")
        return "\n".join(lines)
    return json.dumps(payload, indent=2, sort_keys=True)


def _contract() -> Any:
    try:
        from . import cli_contract

        return cli_contract
    except Exception:  # noqa: BLE001 — a build without the contract still runs
        return None


def _spec(**kwargs: Any) -> Any:
    """A ``CommandSpec`` when the contract has one, else a plain dict the dump can read."""
    contract = _contract()
    spec = getattr(contract, "CommandSpec", None) if contract is not None else None
    if spec is None:
        return dict(kwargs)
    return spec(
        kwargs["layer"],
        tuple(kwargs["evidence"]),
        kwargs["description"],
        exit_codes=tuple(kwargs["exit_codes"]),
        mutates=bool(kwargs["mutates"]),
        data_schema=kwargs.get("data_schema"),
        notes=kwargs.get("notes", ""),
    )


def _ev(ref: str, kind: str, note: str) -> dict[str, str]:
    return {"ref": ref, "kind": kind, "note": note}


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entries for the three ``stream`` verbs."""
    _measurements = {
        "type": "object",
        "properties": {
            "chunks_in": {"type": "integer", "description": "chunks accepted from the source"},
            "chars_in": {"type": "integer", "description": "characters accepted"},
            "renders": {"type": "integer", "description": "writes that reached the terminal"},
            "renders_per_s": {"type": "number", "description": "renders per second, measured"},
            "first_render_ms": {"type": "integer", "description": "ms to the first visible byte"},
            "cpu_ms": {"type": "integer", "description": "process CPU time, user+system"},
            "byte_identical": {"type": "boolean", "description": "input reproduced byte for byte"},
        },
        "required": ["chunks_in", "renders"],
    }
    return {
        "stream.selfcheck": _spec(
            layer=LAYER,
            evidence=(
                _ev("src/skaldir/streaming.py:Coalescer", "code",
                    "accepts every chunk, renders at most once per interval"),
                _ev("src/skaldir/streaming.py:_cmd_selfcheck", "test",
                    "five invariants: byte identity, bound, suppression, no drop, first byte"),
            ),
            description="prove the render bound holds (byte-identical, no dropped bytes)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_INTERNAL),
            mutates=False,
            data_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean", "description": "every invariant held"},
                    "checks": {"type": "array", "description": "one row per invariant",
                               "items": {"type": "object"}},
                    "checks_passed": {"type": "integer", "description": "invariants that held"},
                    "checks_total": {"type": "integer", "description": "invariants evaluated"},
                    "measurement": _measurements,
                },
                "required": ["ok", "checks_passed", "checks_total"],
            },
            notes="read-only, in-process, deterministic: no terminal and no network involved. "
            "A failing invariant exits 3 with the reason, never 0.",
        ),
        "stream.measure": _spec(
            layer=LAYER,
            evidence=(
                _ev("src/skaldir/streaming.py:_measure", "run",
                    "the same chunks through the same sink, coalescing off then on"),
            ),
            description="measure a streamed answer with and without coalescing",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_INTERNAL),
            mutates=False,
            data_schema={
                "type": "object",
                "properties": {
                    "chunks": {"type": "integer", "description": "chunks in the sequence"},
                    "chunk_bytes": {"type": "integer", "description": "bytes per chunk"},
                    "hz": {"type": "number", "description": "requested render rate"},
                    "before": _measurements,
                    "after": _measurements,
                    "renders_saved": {"type": "integer", "description": "renders avoided"},
                    "cpu_saved_ms": {"type": "integer", "description": "CPU milliseconds saved"},
                    "byte_identical": {
                        "type": "boolean",
                        "description": "both arms reproduced the input bytes",
                    },
                },
                "required": ["chunks", "before", "after", "byte_identical"],
            },
            notes="the source is a deterministic synthetic sequence at a tight loop — the upper "
            "bound on render pressure, not a modelled token rate; the payload says so",
        ),
        "stream.policy": _spec(
            layer="COMPOSE",
            evidence=(
                _ev("src/skaldir/streaming.py:plan_tool_batches", "code",
                    "adjacent-parallel grouping with a sequential barrier"),
            ),
            description="the parallel-tool grouping a run would use, call by call",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_INTERNAL),
            mutates=False,
            data_schema={
                "type": "object",
                "properties": {
                    "default": {"type": "string", "description": "mode for undeclared calls"},
                    "calls": {"type": "array", "description": "the calls as they were declared",
                              "items": {"type": "object"}},
                    "batches": {"type": "array", "description": "groups that may overlap",
                                "items": {"type": "object"}},
                    "parallel_batches": {"type": "integer", "description": "groups that overlap"},
                    "barriers": {"type": "integer", "description": "sequential barriers"},
                },
                "required": ["default", "batches"],
            },
            notes="read-only: it reports the grouping, it does not execute a tool call",
        ),
        "stream.replay": _spec(
            layer=LAYER,
            evidence=(
                _ev("src/skaldir/streaming.py:_cmd_replay", "run",
                    "one chunk per write, flushed, at the declared rate"),
            ),
            description="replay a real file as a chunked stream (the measurement's source)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_INTERNAL),
            mutates=False,
            data_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "where the bytes came from"},
                    "chunks": {"type": "integer", "description": "writes performed"},
                    "chunk_bytes": {"type": "integer", "description": "bytes per chunk"},
                    "chars": {"type": "integer", "description": "characters streamed"},
                    "rate": {"type": "number", "description": "declared chunks per second"},
                    "wall_ms": {"type": "integer", "description": "wall time, ms"},
                    "cpu_ms": {"type": "integer", "description": "this process's CPU, ms"},
                },
                "required": ["source", "chunks", "chars"],
            },
            notes="the bytes are a real file; the chunk boundaries and the rate are declared. "
            "It is a benchmark source, not a model: the payload says `pacing: declared`. In "
            "text mode the answer goes to stdout and the facts to stderr, so the stream stays "
            "a clean stream; under --json the answer is captured by the driver and only the "
            "facts travel in the envelope.",
        ),
    }


def install_contract_entries() -> list[str]:
    """Add the missing ``stream.*`` entries to ``cli_contract.COMMANDS`` (never overwrites)."""
    contract = _contract()
    if contract is None:
        return []
    added: list[str] = []
    for path, entry in contract_entries().items():
        if path not in contract.COMMANDS:
            contract.COMMANDS[path] = entry
            added.append(path)
    return added


def _add_verbs(subparsers: Any) -> None:
    from .cli_shared import share_common_flags

    check = subparsers.add_parser(
        "selfcheck",
        help="prove the render bound holds (byte-identical, no dropped bytes)",
    )
    check.add_argument("--chunks", type=int, default=DEFAULT_CHUNKS,
                       help="chunks in the fixture sequence")
    check.add_argument("--hz", type=float, default=DEFAULT_HZ,
                       help="the render rate under test")
    check.add_argument("--chunk-rate", type=float, default=DEFAULT_CHUNK_RATE,
                       help="chunks per second the fixture is paced at (0 = tight loop)")
    check.add_argument("--max-buffer", type=int, default=DEFAULT_MAX_BUFFER,
                       help="force a render at this many buffered characters")
    share_common_flags(check)
    check.set_defaults(func=_cmd_selfcheck)

    measure = subparsers.add_parser(
        "measure", help="measure a streamed answer with and without coalescing"
    )
    measure.add_argument("--chunks", type=int, default=DEFAULT_CHUNKS,
                         help="chunks in the fixture sequence")
    measure.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES,
                         help="bytes per chunk (a token is 3-12 bytes)")
    measure.add_argument("--hz", type=float, default=DEFAULT_HZ,
                         help="renders per second (must be at least 1)")
    measure.add_argument("--max-buffer", type=int, default=DEFAULT_MAX_BUFFER,
                         help="force a render at this many buffered characters")
    measure.add_argument("--chunk-rate", type=float, default=DEFAULT_CHUNK_RATE,
                         help="chunks per second the fixture is paced at (0 = tight loop)")
    share_common_flags(measure)
    measure.set_defaults(func=_cmd_measure)

    policy = subparsers.add_parser(
        "policy", help="the parallel-tool grouping a run would use"
    )
    policy.add_argument("--tool", action="append", default=[], metavar="NAME[:MODE]",
                        help="a call: name with an optional :parallel/:sequential")
    policy.add_argument("--default", default="sequential",
                        help="mode for calls that declare none (parallel|sequential)")
    share_common_flags(policy)
    policy.set_defaults(func=_cmd_policy)

    replay = subparsers.add_parser(
        "replay",
        help="replay a real file as a chunked stream (the measurement's source)",
    )
    replay.add_argument("--source", default="",
                        help="the file to stream (default: README.md, else AGENTS.md)")
    replay.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES,
                        help="bytes per chunk (a token is 3-12 bytes)")
    replay.add_argument("--rate", type=float, default=DEFAULT_CHUNK_RATE,
                        help="chunks per second (0 = as fast as it can write)")
    replay.add_argument("--limit", type=int, default=0,
                        help="stop after this many bytes (0 = the whole file)")
    share_common_flags(replay)
    replay.set_defaults(func=_cmd_replay)


def register_cli(subparsers: Any) -> Any:
    """Add the ``stream`` group (idempotent) and install its contract entries."""
    existing = getattr(subparsers, "choices", {}).get("stream")
    if existing is not None:
        install_contract_entries()
        return existing
    parser = subparsers.add_parser(
        "stream",
        help="bounded-rate streaming: prove the render bound, measure it, plan tool batches",
        description="The render side of a streamed answer: coalescing, its bound, its numbers.",
    )
    verbs = parser.add_subparsers(dest="stream_command", required=True)
    _add_verbs(verbs)
    install_contract_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir stream",
                                     description="bounded-rate streaming")
    verbs = parser.add_subparsers(dest="stream_command", required=True)
    _add_verbs(verbs)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir stream …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


#: Paste into ``src/skaldir/cli.py`` — two lines, right after the root subparsers are
#: created (the same place the other groups are wired). Never edit cli.py from here.
WIRING_SNIPPET = '''\
    # --- CLINE-PERF-1: bounded-rate streaming. Two lines, paste-ready, right after
    #     `_register_jev_cli(sub)` inside build_parser() ---
    from .streaming import register_cli as _register_stream

    _register_stream(sub)
'''
