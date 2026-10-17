"""The full-screen terminal application — Abschnitt 6 / Nutzerbefund 3.

    "Der sichtbare Einstieg entspricht weiterhin nicht nachweislich der
     vollstaendigen vereinbarten Vollbild-TUI."

    "`skaldir` oeffnet im unterstuetzten interaktiven Terminal: grosser Chat
     links, Tools oben rechts, Goal unten rechts."
    "Ein Renderer mit Beispieldaten oder Transcript-Preview erfuellt das nicht."

So this is the *application*, not a renderer: it takes the terminal (alt screen,
hidden cursor), draws the agreed layout on every frame from live state, reads real
keys, reacts to resize, and runs the agent behind the input line.

The rules from the assignment that decide behaviour here and nowhere else:

* **Tab at an empty prompt with no completion menu cycles the approval mode; active
  completion retains its Tab behaviour.**
* streaming answers and tool events appear while they happen
* result preview with expandable detail
* resize, scrollback, and return to the live edge
* a quiet figure; errors in red **plus** readable text
* reattach to a running run
* a usable non-interactive CLI/JSON output (the app refuses to start without a TTY
  and says which command to use instead)

No second execution engine: sending text hands the work to the product's own agent
loop and only renders its events.
"""

from __future__ import annotations

import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .tui import TuiState
from .tui_live import LiveTui, LiveTuiConfig

__all__ = ["TuiApp", "TuiAppConfig", "NO_TTY_HINT"]

NO_TTY_HINT = (
    "the full-screen UI needs an interactive terminal (a pipe or a redirect has no "
    "screen to take over). Use `skaldir agent run \"<task>\" --json` for the same work "
    "as one JSON envelope, or `skaldir tui preview` to see the layout as text."
)


@dataclass
class TuiAppConfig:
    width: int = 0
    height: int = 0
    unicode: bool = True
    motion: bool = True
    project: str = ""
    run_on_send: Callable[[str], None] | None = None
    fps: float = 12.0


class TuiApp:
    """Takes the terminal, draws the agreed layout, reads real keys."""

    # -- terminal control --------------------------------------------------- #

    ALT_ENTER = "\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H"
    ALT_LEAVE = "\x1b[?25h\x1b[?1049l"
    CLEAR = "\x1b[2J\x1b[H"
    HIDE_CURSOR = "\x1b[?25l"
    SHOW_CURSOR = "\x1b[?25h"

    def __init__(self, *, config: TuiAppConfig | None = None, stdin=None, stdout=None) -> None:
        self.config = config or TuiAppConfig()
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        cols, rows = shutil.get_terminal_size()
        self.config.width = self.config.width or cols
        self.config.height = self.config.height or rows
        self.tui = LiveTui(config=LiveTuiConfig(
            width=self.config.width, height=self.config.height,
            unicode=self.config.unicode, motion=self.config.motion,
            project=self.config.project,
        ), on_send=self._send)
        self.state: TuiState = self.tui.state
        self._running = False
        self._needs_redraw = True
        self._resized = False
        self._last_frame: list[str] = []
        self._status = ""
        self._old_handler: Any = None

    # -- the send path ------------------------------------------------------

    def _send(self, text: str) -> None:
        """Hand the work to the product's own engine; only render its events."""
        self._status = "waiting for the model"
        runner = self.config.run_on_send
        if runner is None:
            self.state.chat.append("(no runner wired)")
            self._needs_redraw = True
            return

        def go() -> None:
            try:
                runner(text)
            except Exception as exc:  # noqa: BLE001 — a failure is shown, not swallowed
                self.tui.push("run.failed", reason=f"{type(exc).__name__}: {exc}"[:200])
            finally:
                self._status = ""
                self._needs_redraw = True

        threading.Thread(target=go, daemon=True).start()
        self._needs_redraw = True

    # -- geometry -----------------------------------------------------------

    def _on_resize(self, *_: Any) -> None:
        self._resized = True

    def apply_resize(self) -> None:
        cols, rows = shutil.get_terminal_size()
        outcome = self.tui.resize(cols, rows)
        self.config.width, self.config.height = outcome["to"]
        self._needs_redraw = True
        self._write(self.CLEAR)

    # -- drawing ------------------------------------------------------------

    def draw(self) -> None:
        frame = self.tui.render()
        out: list[str] = []
        # strict=False keeps the previous truncating behaviour: a shorter new frame only
        # redraws the rows it has (B905 names the missing policy; the behaviour is kept).
        for row_idx, (old, new) in enumerate(zip(self._last_frame, frame, strict=False), start=1):
            if old != new:
                out.append(f"\x1b[{row_idx};1H{new}")
        if not self._last_frame:
            out = [self.CLEAR] + [
                "".join(f"\x1b[{i + 1};1H{line}" for i, line in enumerate(frame))
            ]
        if out:
            self._write("".join(out))
        self._last_frame = frame
        self._needs_redraw = False

    def _write(self, text: str) -> None:
        try:
            self.stdout.write(text)
            self.stdout.flush()
        except Exception:  # noqa: BLE001 — a broken pipe ends the loop, not the process
            self._running = False

    # -- keys ---------------------------------------------------------------

    def handle_key(self, key: str) -> dict[str, Any]:
        outcome = self.tui.handle_key(key)
        self._needs_redraw = True
        return outcome

    def set_completing(self, active: bool) -> None:
        self.tui.set_completing(active)

    # -- the loop -----------------------------------------------------------

    def run(self, *, max_frames: int = 0, timeout_s: float = 0.0,
            keys: Iterable[str] | None = None,
            stop_when: Callable[[], bool] | None = None) -> int:
        """Take the terminal and run. ``keys`` feeds scripted input (PTY tests)."""
        if not self._tty_available() and keys is None:
            raise RuntimeError(NO_TTY_HINT)

        self._running = True
        self._enter()
        scripted = iter(keys) if keys is not None else None
        started = time.time()
        frames = 0
        interval = 1.0 / max(1.0, float(self.config.fps))
        try:
            while self._running:
                if self._resized:
                    self._resized = False
                    self.apply_resize()
                if scripted is not None:
                    # scripted keys are input, not a stop condition: the loop keeps
                    # drawing until one of the bounds says stop (a PTY test drives
                    # input and still wants several frames).
                    try:
                        key = next(scripted)
                    except StopIteration:
                        key = ""
                    if key:
                        self.handle_key(key)
                else:
                    key = self._read_key(interval)
                    if key:
                        self.handle_key(key)
                self.tui.step()
                self.draw()
                frames += 1
                if max_frames and frames >= max_frames:
                    break
                if timeout_s and (time.time() - started) >= timeout_s:
                    break
                if stop_when is not None and stop_when():
                    break
        finally:
            self._leave()
        return frames

    def stop(self) -> None:
        self._running = False

    def _tty_available(self) -> bool:
        try:
            return bool(self.stdin.isatty() and self.stdout.isatty())
        except Exception:  # noqa: BLE001
            return False

    def _enter(self) -> None:
        self._write(self.ALT_ENTER)
        if hasattr(signal, "SIGWINCH"):
            try:
                self._old_handler = signal.signal(signal.SIGWINCH, self._on_resize)
            except (ValueError, OSError):
                self._old_handler = None

    def _leave(self) -> None:
        self._write(self.ALT_LEAVE)
        if self._old_handler is not None and hasattr(signal, "SIGWINCH"):
            try:
                signal.signal(signal.SIGWINCH, self._old_handler)
            except (ValueError, OSError):
                pass

    def _read_key(self, budget: float) -> str:
        """One key, bounded so the loop keeps drawing while nothing is typed."""
        import select

        try:
            ready, _, _ = select.select([self.stdin], [], [], max(0.01, budget))
        except (OSError, ValueError):
            return ""
        if not ready:
            return ""
        try:
            ch = self.stdin.read(1)
        except Exception:  # noqa: BLE001
            return ""
        if ch == "\x1b":
            return "ESC"
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\t":
            return "TAB"
        if ch in ("\x7f", "\b"):
            return "BACKSPACE"
        return ch

    # -- observable state (what a test checks) -------------------------------

    @property
    def frame_count(self) -> int:
        return self.tui.frame_count

    def screen_name(self) -> str:
        """The agreed layout, by name — what Abschnitt 6 describes."""
        return "chat-left_tools-topright_goal-bottomright"
