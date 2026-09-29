"""Feeds the TUI renderer from real run and tool events.

GOAL §4: "Vorhandenen Renderer an echte Run-/Tool-Events anschließen. Vorschau und
aufklappbare Details, funktionierender Live-Cursor/Reattach, Resize und Scrollback."

:mod:`skaldir.tui` is a pure renderer over :class:`~skaldir.tui.TuiState`; nothing fed that
state from a real run. This is that feeder. It is deliberately *read-only*: it never runs a
tool and never starts a runtime, so "keine zweite Runtime im UI" holds by construction.

Two details matter more than they look:

* **Reattach must not double anything.** ``TuiState.cursor`` is a sequence cursor over the
  event stream. Re-reading the same events must be a no-op, not a second copy — a call
  appended twice on every refresh is the classic reattach bug (``03-TUI-ENTSCHEIDUNGEN.md``
  §4 says so directly: "duplicates never double a call").
* **Scrolling away is not a silent stop.** While the reader is not at the live edge, new
  events are *counted*, not drawn over the view. The user sees "… N new events — return to
  live" instead of a list that moves under them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .tui import CallRow, CallState, GoalPanel, TuiState

__all__ = ["TuiEventFeed", "apply_events", "state_from_transcript"]

#: transcript ``kind`` → the call state it puts the row into
#: transcript ``kind`` → the call state it puts the row into. The vocabulary is the one
#: ``agent_loop`` actually writes; unknown kinds stay ``UNKNOWN`` rather than being guessed
#: into a success (``CallState`` docstring: "done never implies fachlich success").
_CALL_STATE = {
    "tool.queued": CallState.QUEUED,
    "tool.start": CallState.RUNNING,
    "tool.started": CallState.RUNNING,
    "tool.running": CallState.RUNNING,
    "tool.result": CallState.DONE,
    "tool.ok": CallState.DONE,
    "tool.done": CallState.DONE,
    "tool.failed": CallState.FAILED,
    "tool.error": CallState.FAILED,
    "tool.cancelled": CallState.CANCELLED,
    "tool.dropped": CallState.CANCELLED,
    "tool.blocked": CallState.UNKNOWN,
    "tool.skipped": CallState.UNKNOWN,
}


def _read_events(path: str | Path) -> list[dict[str, Any]]:
    """Read a run transcript. An unreadable file is an empty stream, not a crash."""
    events: list[dict[str, Any]] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return events
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def apply_events(state: TuiState, events: Iterable[Mapping[str, Any]]) -> TuiState:
    """Apply events to a state in place. Only events *after* the cursor are applied."""
    for event in events:
        seq = int(event.get("seq", 0) or 0)
        if seq and seq <= state.cursor:
            continue                      # reattach: never apply the same event twice
        kind = str(event.get("kind", ""))

        if kind == "run.started":
            state.run_id = str(event.get("run_id", state.run_id) or state.run_id)
            task = str(event.get("task", "") or event.get("goal", "") or "")
            if task:
                state.goal = GoalPanel(
                    title=task,
                    phase=str(event.get("phase", "") or ""),
                    run_state="running",
                )

        elif kind in ("assistant.message", "model.answer", "answer"):
            text = str(event.get("text") or event.get("content") or "")
            if text:
                state.chat.append(text)

        elif kind in ("user.message", "user"):
            text = str(event.get("text") or event.get("content") or "")
            if text:
                state.chat.append(f"> {text}")

        # ORDER MATTERS: the finishing kinds must be matched before the generic
        # ``tool.`` branch. Otherwise every result is read as a *new* call and the tools
        # pane grows one row per event — exactly the duplication this module exists to
        # prevent (measured before the reorder, not assumed).
        elif kind in ("tool.result", "tool.ok", "tool.done"):
            _finish_call(state, event, CallState.DONE)

        elif kind in ("tool.failed", "tool.error"):
            _finish_call(state, event, CallState.FAILED)

        elif kind in ("tool.cancelled", "tool.dropped"):
            _finish_call(state, event, CallState.CANCELLED)

        elif kind.startswith("tool."):
            state.calls.append(_call_row(event))

        elif kind == "run.error":
            state.blocked = str(event.get("error") or event.get("reason") or "")[:300]

        elif kind == "usage":
            if event.get("tokens") is not None:
                state.ctx = str(event.get("tokens"))
            if event.get("tok_per_s") is not None:
                state.tok_per_s = f"{event['tok_per_s']:.1f}"
            if event.get("cost_usd") is not None:
                # no "$" here: the status line already writes one, and a doubled symbol
                # is the kind of detail that makes a UI look unmeasured
                state.cost = f"{event['cost_usd']:.4f}"

        if seq:
            state.cursor = max(state.cursor, seq)
    return state


def _call_row(event: Mapping[str, Any]) -> CallRow:
    kind = str(event.get("kind", ""))
    state = _CALL_STATE.get(kind, CallState.QUEUED)
    args = event.get("args") if isinstance(event.get("args"), Mapping) else {}
    return CallRow(
        call_id=str(event.get("call_id") or event.get("id") or event.get("tool", "")),
        label=str(event.get("tool") or event.get("name") or ""),
        target=str(event.get("target") or event.get("path") or ""),
        state=state,
        duration_s=event.get("duration_s"),
        preview=str(event.get("preview") or event.get("detail") or "")[:200],
        args=dict(args),
        details=str(event.get("details") or ""),
        exit_code=event.get("exit_code"),
        error=str(event.get("error") or ""),
        parent=str(event.get("batch") or event.get("parent") or ""),
    )


def _finish_call(state: TuiState, event: Mapping[str, Any], final: CallState) -> None:
    """Move an existing row to its final state instead of adding a second row."""
    call_id = str(event.get("call_id") or event.get("id") or "")
    for row in reversed(state.calls):
        if call_id and row.call_id == call_id:
            row.state = final
            row.duration_s = event.get("duration_s", row.duration_s)
            row.preview = str(event.get("preview") or row.preview)[:200]
            row.error = str(event.get("error") or row.error)
            if event.get("exit_code") is not None:
                row.exit_code = event.get("exit_code")
            # The expandable detail ("Ergebnisvorschau und aufklappbare Details"):
            # it arrived on the RESULT, and the result goes through here — without
            # carrying it, the row kept only the one-line preview and the detail was
            # lost (measured: `details` stayed empty after a tool.result).
            if event.get("details") is not None:
                row.details = str(event.get("details"))
            if event.get("args") is not None and isinstance(event.get("args"), Mapping):
                row.args.update(dict(event["args"]))
            return
    # a result without a matching start still has to be visible
    kind = {
        CallState.DONE: "tool.result",
        CallState.FAILED: "tool.failed",
        CallState.CANCELLED: "tool.cancelled",
    }.get(final, "tool.result")
    state.calls.append(_call_row({**dict(event), "kind": kind}))


class TuiEventFeed:
    """Keeps one :class:`TuiState` in step with a run transcript.

    ``feed()`` is idempotent over already-seen events, so a renderer may call it on every
    frame without duplicating anything.
    """

    def __init__(
        self, transcript: str | Path | None = None, *, state: TuiState | None = None
    ) -> None:

        self.transcript = Path(transcript) if transcript else None
        self.state = state or TuiState()
        self._started_at = time.time()

    # -- reading -----------------------------------------------------------

    def feed(self, events: Iterable[Mapping[str, Any]] | None = None) -> TuiState:
        if events is None:
            events = _read_events(self.transcript) if self.transcript else []
        incoming = list(events)
        if self.state.at_live_edge:
            apply_events(self.state, incoming)
            self.state.new_events = 0
        else:
            # not at the live edge: the view must not move under the reader. New events
            # are counted and applied only when the reader returns to live.
            self.count_new(incoming)
        self.state.elapsed_s = time.time() - self._started_at
        return self.state

    # -- the live edge (reattach / scrollback) ------------------------------

    def scroll_away(self) -> None:
        """The reader moved back in history: stop drawing over the view."""
        self.state.at_live_edge = False

    def return_to_live(self) -> int:
        """Jump to the newest events: apply everything that piled up. Returns how many."""
        waiting = self.state.new_events
        self.state.at_live_edge = True
        self.state.new_events = 0
        if self.transcript:
            apply_events(self.state, _read_events(self.transcript))
        return waiting

    def count_new(self, events: Iterable[Mapping[str, Any]]) -> int:
        """Events beyond the cursor, while the reader is not at the live edge."""
        unseen = [e for e in events if int(e.get("seq", 0) or 0) > self.state.cursor]
        self.state.new_events += len(unseen)
        return len(unseen)


def state_from_transcript(path: str | Path, *, state: TuiState | None = None) -> TuiState:
    """Build a complete state from a transcript in one go (the reattach path)."""
    return TuiEventFeed(path, state=state).feed()
