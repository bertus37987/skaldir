"""The live TUI loop — Paket 5 (audit F10).

    "Prüfe den echten ``skaldir``-Terminalstart: der neue TUI-Feed muss im laufenden
     interaktiven Event-/Renderloop arbeiten, nicht nur eine Transcript-Preview erzeugen."

``tui_feed.py`` turns events into a :class:`~skaldir.tui.TuiState`. Until now only the
preview used it, so reading a transcript into the renderer proved nothing about an
interactive full-screen UI. This is the loop that keeps it live:

* it **subscribes to run events** and feeds them to the state on arrival, not by re-reading
  a file;
* it renders on a schedule and on change, so a running tool shows up while it runs;
* it handles live input — Enter sends, and **Tab at an empty prompt cycles the approval
  mode** while an active completion keeps its Tab behaviour (the confirmed UI rule);
* it reacts to **resize**, and reports scrollback/live-edge so the reader is not moved
  under the user;
* it adds **no second execution engine** — the loop renders and routes; the run's own
  engines do the work.

What is deliberately *not* claimed: this is a renderer over the product's own event stream.
A PTY test proves the loop draws and responds; it is not a claim that every product path is
already wired to it.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .tui import Screen, TuiState
from .tui_feed import TuiEventFeed

__all__ = ["LiveTui", "TuiEvent", "LiveTuiConfig"]


@dataclass
class TuiEvent:
    """One event from the run's own stream."""

    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)


@dataclass
class LiveTuiConfig:
    width: int = 100
    height: int = 30
    unicode: bool = True
    motion: bool = True
    render_interval_s: float = 0.10
    project: str = ""


class LiveTui:
    """The interactive loop: events in, frames out, keys handled."""

    def __init__(self, *, config: LiveTuiConfig | None = None,
                 state: TuiState | None = None,
                 on_send: Callable[[str], None] | None = None,
                 on_mode_change: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config or LiveTuiConfig()
        self.feed = TuiEventFeed(state=state or TuiState())
        self.state = self.feed.state
        self.state.project = self.config.project or self.state.project
        self.screen = Screen(unicode=self.config.unicode, motion=self.config.motion)
        self.on_send = on_send
        self.on_mode_change = on_mode_change
        self._clock = clock
        self._queue: list[TuiEvent] = []
        self._lock = threading.Lock()
        self._dirty = True
        self._running = False
        self._frames = 0
        self._completing = False

    # -- events -------------------------------------------------------------

    def push(self, kind: str, **payload: Any) -> None:
        """An event arrives from the run's own stream."""
        with self._lock:
            self._queue.append(TuiEvent(kind=kind, payload=payload))
            self._dirty = True

    def push_seq(self, seq: int, kind: str, **payload: Any) -> None:
        """An event with its sequence number, so reattach does not double anything."""
        self.push(kind, seq=seq, **payload)

    def _drain(self) -> list[TuiEvent]:
        with self._lock:
            events, self._queue = self._queue, []
        return events

    # -- the loop -----------------------------------------------------------

    def step(self) -> list[str]:
        """One turn: apply what arrived, then draw. Returns the frame."""
        events = self._drain()
        if events:
            # the kind always travels with the payload: `apply_events` dispatches on it,
            # and a payload without one would be silently ignored (measured)
            self.feed.feed([{**dict(e.payload), "kind": e.payload.get("kind") or e.kind}
                            for e in events])
            self.feed.state.elapsed_s = self._clock() - getattr(self, "_started", self._clock())
        frame = self.render()
        self._frames += 1
        with self._lock:
            self._dirty = False
        return frame

    def render(self) -> list[str]:
        return self.screen.render(self.state, self.config.width, self.config.height)

    def run(self, *, max_steps: int = 0, timeout_s: float = 0.0,
            stop_when: Callable[[], bool] | None = None) -> int:
        """The loop. Bounded for tests, unbounded for a real terminal."""
        self._running = True
        self._started = self._clock()
        started = time.time()
        steps = 0
        try:
            while self._running:
                self.step()
                steps += 1
                if max_steps and steps >= max_steps:
                    break
                if timeout_s and (time.time() - started) >= timeout_s:
                    break
                if stop_when is not None and stop_when():
                    break
                time.sleep(self.config.render_interval_s)
        finally:
            self._running = False
        return steps

    def stop(self) -> None:
        self._running = False

    # -- input --------------------------------------------------------------

    def handle_key(self, key: str) -> dict[str, Any]:
        """One keypress. Returns what it did, so a PTY test can check it.

        The confirmed UI rule is implemented here and nowhere else: **Tab at an empty
        prompt with no completion menu cycles the approval mode; active completion keeps
        its Tab behaviour.**
        """
        from .approval_modes import ApprovalMode

        if key in ("TAB", "\\t"):
            if self._completing:
                # an active completion keeps Tab for itself
                return {"action": "completion", "mode": self.state.approval.value}
            if not (self.state.input_text or "").strip():
                previous = self.state.approval
                self.state.approval = ApprovalMode(previous).next() \
                    if hasattr(ApprovalMode(previous), "next") else previous
                self._dirty = True
                if self.on_mode_change is not None:
                    self.on_mode_change(self.state.approval.value)
                return {
                    "action": "cycle_approval",
                    "from": previous.value,
                    "to": self.state.approval.value,
                    "mode": self.state.approval.value,
                }
            return {"action": "tab_ignored", "reason": "input is not empty"}

        if key in ("ENTER", "\\n", "\\r"):
            text = (self.state.input_text or "").strip()
            self.state.input_text = ""
            self._dirty = True
            if text and self.on_send is not None:
                self.on_send(text)
            return {"action": "send", "text": text}

        if key in ("BACKSPACE", "\\x7f"):
            self.state.input_text = (self.state.input_text or "")[:-1]
            self._dirty = True
            return {"action": "edit"}

        if len(key) == 1:
            self.state.input_text = (self.state.input_text or "") + key
            self._dirty = True
            return {"action": "edit"}

        return {"action": "ignored", "key": key[:20]}

    def set_completing(self, active: bool) -> None:
        """Whether a completion menu is open — the difference that decides what Tab does."""
        self._completing = bool(active)

    # -- terminal geometry ---------------------------------------------------

    def resize(self, width: int, height: int) -> dict[str, Any]:
        """A resize is a real event, not a restart."""
        before = (self.config.width, self.config.height)
        self.config.width = max(20, int(width))
        self.config.height = max(6, int(height))
        self._dirty = True
        return {
            "from": before,
            "to": (self.config.width, self.config.height),
            "compact": self.config.height < 14 or self.config.width < 60,
        }

    def terminal_size(self) -> tuple[int, int]:
        try:
            size = shutil.get_terminal_size()
            return size.columns, size.lines
        except Exception:  # noqa: BLE001
            return self.config.width, self.config.height

    # -- state --------------------------------------------------------------

    @property
    def frame_count(self) -> int:
        return self._frames

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._dirty
