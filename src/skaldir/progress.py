"""Live progress for a running command: the immediate line, the per-event line, the pacing.

The problem this solves is measured, not assumed (``MVP/57-perceived-speed.md``): the agent
loop prints *nothing at all* until the whole run is over. In a real terminal the first visible
byte was therefore the completion — 1.3 s with a 300 ms stub, minutes with a real model — so a
user cannot tell a working loop from a frozen terminal. This module is the small piece that
puts the first line on screen *before any provider is resolved* and keeps a bounded trickle of
real events afterwards.

Four rules, each one a measured finding rather than a style choice:

1. **The first line is written immediately and is never rate-limited.** Felt latency is
   decided by the first byte, so the acknowledgment line is written before the provider is
   even resolved (see :meth:`begin`). Pacing would give that away for nothing.
2. **One line per real event** — a model round, a tool call, a tool result. Nothing here
   animates a state that does not exist: there is no spinner thread that runs when the loop
   is idle.
3. **The render rate is bounded** (:data:`DEFAULT_MIN_INTERVAL_MS`). A burst of events (five
   parallel tool calls, a retry storm) is folded into one line with the number of folded
   events named, instead of a wall of text the user cannot read. The count of writes is the
   bound; nothing is lost from the record (the transcript keeps every event).
4. **The silence is bounded and labelled.** While a model call or a tool is open, at most one
   line per :data:`DEFAULT_KEEPALIVE_S` — and once :data:`skaldir.streaming.STALL_S` has passed
   with no *real* event the line says so instead of pretending to be busy. From Cline's own
   rule: an indicator that keeps spinning while nothing arrives is a lie.

The rules are the ones :mod:`skaldir.streaming` established for the render side of a chunk
stream (first render immediate, one render per interval, folded accounting, a stall threshold,
in-place rewriting only when the line really changed) — they are applied here to *discrete*
events instead of a byte stream, which is why the gate is local while
:meth:`skaldir.streaming.Coalescer.maybe_flush` and :func:`skaldir.render.live_line` are used
as they are.

Progress is a **human line and goes to stderr**, like every other human line in ``--json``
mode, so the one JSON envelope on stdout stays byte-clean for a pipe, the API and a test.

Nothing in this module imports a provider or a loop: :class:`RunProgress` is a renderer, and
:meth:`RunProgress.event` is the one callable an owner (the agent loop) feeds.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import render
from . import theme as theme_module
from .streaming import STALL_S

__all__ = [
    "DEFAULT_KEEPALIVE_S",
    "DEFAULT_MIN_INTERVAL_MS",
    "RunProgress",
    "for_args",
]

#: At most one rendered line per interval — ~16 lines/s is already more than a human reads,
#: and a terminal that writes faster than it draws is the noise the audit measured.
DEFAULT_MIN_INTERVAL_MS = 60

#: While something is open (a model call, a tool), at most one keep-alive line per this many
#: seconds. Never more: a keep-alive that arrives like a metronome is decoration.
DEFAULT_KEEPALIVE_S = 2.0

#: How much of a tool argument line is shown (a command, a path) — clipped, never truncated
#: into a lie: the clip marker is explicit.
ARGUMENT_CHARS = 72


def _arguments_preview(arguments: Mapping[str, Any] | None) -> str:
    """The one thing worth showing from a tool call: what it is going to touch."""
    if not arguments:
        return ""
    for key in ("command", "path", "url", "query", "task", "pattern", "file", "cmd"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    try:
        return json.dumps(dict(arguments), sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover — a caller's exotic mapping
        return ""


def _folded_summary(labels: Sequence[str], limit: int = 3) -> str:
    """The names of the folded events, deduplicated, shortest useful form.

    A collapse that only says *how many* events were dropped throws away what a user
    needs to know ("was that the test run or the patch?"); Cline's own transcript collapses
    a wave of parallel calls into one line that still names them. Three names is the point
    where the line stops being a summary and becomes the wall of text it replaced.
    """
    seen: list[str] = []
    for label in labels:
        name = str(label).strip()
        if name and name not in seen:
            seen.append(name)
    if not seen:
        return ""
    shown = ", ".join(seen[:limit])
    return shown if len(seen) <= limit else f"{shown}, +{len(seen) - limit} more"


def _stream_fact_text(facts: Mapping[str, Any] | None) -> str:
    """``· 128 deltas · ~32 tok (est. chars/4)`` — the measured facts of a live stream.

    Only what was measured: the delta count and the character count come from the provider
    client, the token number is an estimate and says which estimator produced it. No facts
    at all ⇒ ``""``, so a line never grows a placeholder.
    """
    if not facts:
        return ""
    bits: list[str] = []
    deltas = int(facts.get("deltas") or 0)
    chars = int(facts.get("chars") or 0)
    tokens = facts.get("tokens_estimate")
    if deltas:
        bits.append(f"{deltas} deltas")
    if chars:
        bits.append(f"{chars} chars")
    if tokens:
        method = str(facts.get("token_method") or "chars/4")
        bits.append(f"~{int(tokens)} tok (est. {method})")
    return "".join(f" · {bit}" for bit in bits)


def _clip(text: str, limit: int = ARGUMENT_CHARS) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


@dataclass
class _Timeline:
    """What the renderer actually wrote — the audit reads this, not a claim."""

    entries: list[tuple[float, str]] = field(default_factory=list)
    suppressed: int = 0
    folded: int = 0
    keepalives: int = 0
    stalled_lines: int = 0

    def longest_silence_ms(self) -> float:
        worst = 0.0
        previous = 0.0
        for at, _ in self.entries:
            worst = max(worst, at - previous)
            previous = at
        return round(worst, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rendered": len(self.entries),
            "suppressed": self.suppressed,
            "folded": self.folded,
            "keepalives": self.keepalives,
            "stalled_lines": self.stalled_lines,
            "longest_silence_ms": self.longest_silence_ms(),
        }


class RunProgress:
    """The progress renderer of one command run.

    Parameters
    ----------
    stream
        Where human lines go. Defaults to ``sys.stderr`` — never stdout, because the
        envelope owns stdout in ``--json`` mode.
    enabled
        ``False`` turns every method into a counter (no write, no thread). The tests and
        the audit rely on the counters being exact in both states.
    min_interval_ms
        The render bound (rule 3).
    keepalive_s
        The keep-alive bound (rule 4). ``0`` disables it.
    clock
        Injected for tests: pacing is asserted without sleeping.
    """

    def __init__(
        self,
        stream: Any = None,
        *,
        enabled: bool = True,
        theme: theme_module.Theme | None = None,
        min_interval_ms: int = DEFAULT_MIN_INTERVAL_MS,
        keepalive_s: float = DEFAULT_KEEPALIVE_S,
        clock: Callable[[], float] = time.monotonic,
        keepalive_thread: bool = True,
        label: str = "working",
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = bool(enabled)
        self.min_interval_ms = max(0, int(min_interval_ms))
        self.keepalive_s = max(0.0, float(keepalive_s))
        self._clock = clock
        self._theme: Any = theme
        self._label = str(label)
        self._started_at: float | None = None
        self._last_render_at: float | None = None
        self._pending: list[str] = []
        self._folded_labels: list[str] = []
        self._open_since: float | None = None
        self._open_kind = ""
        self._open_step = 0
        self._live_active = False
        self._live = ""
        #: Set while a *streamed* answer is being written to stdout. Nothing here is drawn
        #: in that state: the answer is the feedback, and a second writer on the same
        #: terminal can split a line (measured — MVP/53-cline-perf.md §6). The line comes
        #: back the moment the flow stops (``model.response``/``tool.*``).
        self._content_flowing = False
        #: What the stream has really delivered, when the loop reports it (measured).
        self._stream_facts: dict[str, Any] = {}
        self._closed = False
        self._lock = threading.Lock()
        self.timeline = _Timeline()
        self._last_event_at: float | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.keepalive_thread = bool(keepalive_thread)
        if self.enabled and self.keepalive_thread and self.keepalive_s > 0:
            self._thread = threading.Thread(
                target=self._keepalive_loop, name="skaldir-progress", daemon=True
            )
            self._thread.start()

    # -- theme --------------------------------------------------------------- #

    @property
    def theme(self) -> theme_module.Theme:
        if self._theme is None:
            try:
                self._theme = theme_module.get()
            except Exception:  # noqa: BLE001 — a renderer must never break a command
                self._theme = theme_module.Theme()
        return self._theme

    # -- the write path ------------------------------------------------------ #

    def _write(self, line: str) -> None:
        """One line, flushed. Never raises: a broken pipe is not the loop's problem."""
        try:
            self.stream.write(line + "\n")
            self.stream.flush()
        except Exception:  # noqa: BLE001 — a closed terminal is not a command failure
            return

    def _emit(self, line: str, *, immediate: bool = False, label: str = "") -> str:
        """Rate-bounded write. Returns the line that reached the terminal (``""`` = none).

        Events that arrive inside the interval are **folded**: the next line carries how
        many were collapsed **and which ones** (:func:`_folded_summary`), so the user still
        learns what happened — that is the difference between a summary and a silence.
        """
        with self._lock:
            return self._emit_locked(line, immediate=immediate, label=label)

    def _emit_locked(self, line: str, *, immediate: bool = False, label: str = "") -> str:
        if self._started_at is None:
            self._started_at = self._clock()
        now = self._clock()
        self._last_event_at = now
        if not self.enabled:
            self.timeline.suppressed += 1
            return ""
        if not immediate and self._last_render_at is not None:
            since_ms = (now - self._last_render_at) * 1000
            if since_ms < self.min_interval_ms:
                self._pending.append(line)
                if label:
                    self._folded_labels.append(label)
                self.timeline.suppressed += 1
                return ""
        text = line
        if self._pending:
            self.timeline.folded += len(self._pending)
            extra = len(self._pending)
            marker = self.theme.glyph("sep")
            names = _folded_summary(self._folded_labels)
            suffix = f" {marker} +{extra} event{'s' if extra != 1 else ''}"
            if names:
                suffix += f": {names}"
            text = f"{line}{self.theme.style(suffix, 'muted')}"
            self._pending.clear()
            self._folded_labels.clear()
        if self._live_active:
            # a keep-alive line owns the current terminal row (rich mode rewrites in
            # place); a real line must start on a fresh row or it would overwrite it
            self._write("")
            self._live_active = False
        self._last_render_at = now
        self._write(text)
        self.timeline.entries.append((round((now - self._started_at) * 1000, 2), text))
        return text

    # -- facts for the keep-alive ------------------------------------------- #

    def _waiting(self) -> float:
        """Seconds the current wait has been open (0 when nothing is open)."""
        if self._open_since is None:
            return 0.0
        return max(0.0, self._clock() - self._open_since)

    def _quiet_for(self) -> float:
        """Seconds since the last *real* event — what "stalled" is measured against."""
        reference = self._last_event_at if self._last_event_at is not None else self._started_at
        if reference is None:
            return 0.0
        return max(0.0, self._clock() - reference)

    def _keepalive_loop(self) -> None:
        # Waiting on the event (not sleeping) is what keeps close() instant: the renderer
        # must never add its own poll interval to a command's wall clock.
        while not self._stop.wait(0.1):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — a keep-alive must never kill a run
                continue

    def tick(self) -> str:
        """One keep-alive opportunity (the thread calls this; tests call it directly).

        Writes **only** when a wait is open and its bound has passed, and never more than
        once per :data:`DEFAULT_KEEPALIVE_S`. Returns the line (``""`` = nothing written).
        """
        with self._lock:
            return self._tick_locked()

    def _tick_locked(self) -> str:
        if self._closed or self._open_since is None or not self.enabled:
            return ""
        if self._content_flowing:
            # Tokens are reaching the terminal right now. An in-place rewrite of a second
            # line on the same screen is how a marker loses its first byte; the answer is
            # its own liveness indicator, so this stays silent until the flow stops.
            return ""
        now = self._clock()
        if self._last_render_at is not None and now - self._last_render_at < self.keepalive_s:
            return ""
        waited = self._waiting()
        quiet = self._quiet_for()
        stalled = quiet >= STALL_S
        step = f" · step {self._open_step}" if self._open_step else ""
        streamed = _stream_fact_text(self._stream_facts)
        if stalled:
            line = render.status_line(
                "warn", f"still working · no new event for {quiet:.0f} s{step}",
                theme=self.theme, indent=2,
            )
        else:
            line = render.status_line(
                "running", f"still working · {waited:.1f} s{step}{streamed}",
                theme=self.theme, indent=2,
            )
        rendered = render.live_line(line, previous=self._live, theme=self.theme, final=False)
        self._live = line
        self._live_active = bool(rendered) and not self.theme.plain
        if not rendered:
            return ""
        self._last_render_at = now
        self.timeline.keepalives += 1
        if stalled:
            self.timeline.stalled_lines += 1
        try:
            self.stream.write(rendered if not self.theme.plain else rendered.rstrip("\n") + "\n")
            self.stream.flush()
        except Exception:  # noqa: BLE001
            return ""
        if self._started_at is not None:
            self.timeline.entries.append(
                (round((self._clock() - self._started_at) * 1000, 2), line)
            )
        return line

    # -- the events ---------------------------------------------------------- #

    def _open(self, kind: str, *, step: int = 0) -> None:
        self._open_since = self._clock()
        self._open_kind = kind
        self._open_step = int(step or 0)
        self._live = ""
        # a new wait means no content is flowing yet; the stream facts of the previous
        # call were already consumed by its answer line
        self._content_flowing = False

    def _close_open(self) -> None:
        self._open_since = None
        self._open_kind = ""
        self._open_step = 0
        self._live = ""
        # the flow ends with the wait: from here on the progress line may be drawn again
        self._content_flowing = False

    def begin(self, action: str, **facts: Any) -> str:
        """The immediate acknowledgment line — written **before** anything is resolved.

        This is rule 1 and the single biggest felt improvement of the whole audit: the
        first visible byte of ``agent run`` moves from "when the model finished" to "as
        soon as the process has its arguments".
        """
        bits = [self.theme.style("working", "muted"), action]
        for key, value in facts.items():
            if value in (None, "", 0):
                continue
            bits.append(f"{key} {value}" if key in ("run", "model", "workspace") else str(value))
        line = render.status_line("running", " · ".join(str(bit) for bit in bits),
                                  theme=self.theme, indent=2)
        self._emit(line, immediate=True)
        self._open(action)
        return line

    def event(self, kind: str, fields: Mapping[str, Any] | None = None, **extra: Any) -> str:
        """The one callable an owner feeds (``AgentLoop(progress=progress.event)``).

        Accepts both shapes on purpose: ``event("tool.end", {"name": "x"})`` — what the
        agent loop sends, one mapping — and ``event("tool.end", name="x")``, which reads
        better in a test. The loop's protocol is the mapping one.
        """
        data = dict(fields or {})
        data.update(extra)
        fields = data
        if kind == "model.request":
            return self.model_call(
                int(fields.get("step") or 0),
                messages=int(fields.get("messages") or 0),
                tool_names=fields.get("tool_names") or (),
                model=str(fields.get("model") or ""),
            )
        if kind == "model.response":
            return self.model_answer(
                int(fields.get("step") or 0),
                duration_ms=int(fields.get("duration_ms") or 0),
                tool_calls=int(fields.get("tool_calls") or 0),
                text_chars=int(fields.get("text_chars") or 0),
                streaming=fields.get("streaming") or None,
            )
        if kind == "tool.start":
            return self.tool(
                str(fields.get("name") or ""),
                arguments=fields.get("arguments") or {},
                index=int(fields.get("index") or 0),
                total=int(fields.get("total") or 0),
            )
        if kind == "tool.end":
            return self.tool_done(
                str(fields.get("name") or ""),
                duration_ms=int(fields.get("duration_ms") or 0),
                ok=bool(fields.get("ok")),
                decision=str(fields.get("decision") or ""),
                note=str(fields.get("note") or ""),
            )
        if kind == "model.tokens":
            return self.model_tokens(
                int(fields.get("step") or 0),
                deltas=int(fields.get("deltas") or 0),
                chars=int(fields.get("chars") or 0),
                tokens_estimate=fields.get("tokens_estimate"),
            )
        if kind == "note":
            return self.note(str(fields.get("text") or ""))
        return self.note(f"{kind} {fields}" if fields else kind)

    def model_call(self, step: int, *, messages: int = 0, tool_names: Sequence[str] = (),
                   model: str = "") -> str:
        """A model round is open — the wait the user feels most."""
        self._open("waiting for the model", step=step)
        facts: list[str] = []
        if step:
            facts.append(f"step {int(step)}")
        if messages:
            facts.append(f"{int(messages)} messages")
        if tool_names:
            facts.append(f"{len(tool_names)} tools")
        if model:
            facts.append(str(model))
        text = " · ".join(facts)
        return self._emit(
            render.status_line("running", f"model call {'· ' + text if text else ''}".strip(),
                               theme=self.theme, indent=2, muted=True),
            label="model call",
        )

    def model_tokens(self, step: int, *, deltas: int = 0, chars: int = 0,
                     tokens_estimate: Any = None) -> str:
        """Tokens are arriving: record the facts, draw **nothing**.

        Writing a line here would put a second writer on a terminal that is receiving the
        answer itself — that is the interference this module's own §6 measured. What it
        does instead is keep the *facts* (`deltas`/`chars`/`tokens_estimate`) so the wait
        line and the answer line can state the real numbers the moment the flow stops.
        """
        self._content_flowing = True
        self._stream_facts = {
            "step": int(step or 0),
            "deltas": int(deltas or 0),
            "chars": int(chars or 0),
            "tokens_estimate": tokens_estimate,
            "token_method": "chars/4",
        }
        return ""

    def model_answer(self, step: int, *, duration_ms: int = 0, tool_calls: int = 0,
                     text_chars: int = 0, streaming: Mapping[str, Any] | None = None) -> str:
        """The model answered: report what really came back, then close the wait.

        ``streaming`` is the loop's own measured report for this call (deltas, characters,
        first-content stamp, and whether anything reached the sink). It is used **instead
        of** the live facts when given: one event carries its own numbers. A stream that
        did not happen is still a fact and is named (`unstreamed`), never dropped.
        """
        self._close_open()
        facts = dict(streaming) if streaming is not None else dict(self._stream_facts)
        self._stream_facts = {}
        bits = [f"answer {duration_ms} ms"]
        if tool_calls:
            bits.append(f"{int(tool_calls)} tool call{'s' if int(tool_calls) != 1 else ''}")
        if text_chars:
            bits.append(f"{int(text_chars)} chars")
        first = facts.get("first_content_ms")
        if first is not None:
            bits.append(f"first token {float(first) / 1000.0:.2f} s")
        elif facts and not facts.get("streamed"):
            bits.append("unstreamed")
        bits.extend(
            bit.strip() for bit in _stream_fact_text(facts).split("·") if bit.strip()
        )
        text = " · ".join(bits)
        mark = "running" if tool_calls else "ok"
        return self._emit(
            render.status_line(mark, text, theme=self.theme, indent=2, muted=True),
            label="answer",
        )

    def tool(self, name: str, *, arguments: Mapping[str, Any] | None = None, index: int = 0,
             total: int = 0) -> str:
        """One tool call is about to run (rule 2: one line per real event)."""
        self._open(f"{name} running")
        preview = _clip(_arguments_preview(arguments))
        label = f"{name} {preview}".strip()
        if total:
            line = render.step_line(index or 1, total, label, status="running", theme=self.theme)
        else:
            line = render.status_line("running", label, theme=self.theme, indent=2)
        return self._emit(line, label=name)

    def tool_done(self, name: str, *, duration_ms: int = 0, ok: bool = True,
                  decision: str = "", note: str = "") -> str:
        """One tool finished: its own duration and verdict, never a guess."""
        self._close_open()
        text = f"{name} · {int(duration_ms)} ms"
        # "allowed" is the normal case and saying it on every line is noise; a refusal or
        # an approval-gated call is the one thing a user must not have to guess at
        if decision and str(decision).lower() not in ("allowed", "allow", "ok", "safe"):
            text += f" · {decision}"
        if note:
            text += f" · {_clip(note, 60)}"
        return self._emit(
            render.status_line("ok" if ok else "fail", text, theme=self.theme, indent=2,
                               muted=True),
            label=name,
        )

    def note(self, text: str) -> str:
        """One free line (still rate-bounded — a note is not exempt from rule 3)."""
        return self._emit(render.status_line("info", str(text), theme=self.theme, indent=2,
                                             muted=True))

    def settle(self, verdict: str, text: str = "") -> str:
        """The run's own verdict as the last progress line (the result block follows)."""
        self._close_open()
        status = {"answered": "ok", "failed": "fail", "incomplete": "blocked"}.get(
            str(verdict), "info"
        )
        body = text or str(verdict)
        # the run's own verdict is the one line that is never folded away: it is the last
        # thing a human reads, and a dropped verdict reads as a hang
        return self._emit(
            render.status_line(status, body, theme=self.theme, indent=2), immediate=True
        )

    def close(self) -> dict[str, Any]:
        """Flush what is pending, stop the keep-alive thread, return the counters.

        The thread is joined **outside** the lock: it needs that lock for its own tick,
        and joining while holding it is the deadlock that turns a fast command into a
        hanging one.
        """
        with self._lock:
            if self._closed:
                return self.timeline.as_dict()
            if self._pending and self.enabled:
                # a final flush: the freshest folded line is written with its count, so a
                # burst right before the end still reaches the terminal exactly once
                last = self._pending[-1]
                self._emit_locked(last, immediate=True, label=last)
                self._pending.clear()
            elif self._pending:
                self.timeline.folded += len(self._pending)
                self._pending.clear()
            self._closed = True
            self._close_open()
            thread = self._thread
            self._thread = None
            self._stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        try:
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass
        return self.timeline.as_dict()

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "min_interval_ms": self.min_interval_ms,
            "keepalive_s": self.keepalive_s,
            **self.timeline.as_dict(),
        }


def for_args(args: Any, *, stream: Any = None) -> RunProgress:
    """Build the renderer for one CLI invocation.

    Precedence: ``--no-progress`` wins, then ``--progress``, then ``--quiet``, then "is
    stderr a terminal". A pipe, a redirect or a captured stream therefore gets **no**
    progress lines at all — which is what keeps the ``--json`` envelope and the API's own
    stdout honest.
    """
    out = stream if stream is not None else sys.stderr
    quiet = bool(getattr(args, "quiet", False))
    explicit = getattr(args, "progress", None)
    if explicit is None:
        enabled = (not quiet) and bool(getattr(out, "isatty", lambda: False)())
    else:
        enabled = bool(explicit) and not quiet
    chosen: Any = None
    chooser = getattr(theme_module, "for_args", None)
    if callable(chooser):
        try:
            chosen = chooser(args)
        except Exception:  # noqa: BLE001 — a missing flag is not a rendering failure
            chosen = None
    if chosen is None:
        try:
            chosen = theme_module.detect(stream=out)
        except Exception:  # noqa: BLE001
            chosen = None
    return RunProgress(out, enabled=enabled, theme=chosen)
