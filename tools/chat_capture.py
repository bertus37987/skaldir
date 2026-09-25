"""Drive the real Skaldir chat screen in a real PTY and save raw captures.

This is the CHAT-1 proof harness. It starts the actual ``python -m skaldir.chatbar shell``
process on a pty whose winsize it sets itself, types at it, resizes it, and writes the raw
byte log to a file. Nothing here fakes the screen: the captures are the bytes the process
wrote to its terminal.

Usage:
    .venv/bin/python tools/chat_capture.py <outdir>
"""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


class Session:
    """A real chat session on a real controlling terminal."""

    def __init__(self, rt: Path, width: int, height: int, *, env_extra: dict | None = None):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        env = dict(os.environ)
        for gone in ("HERMES_HOME", "GODSV_HOME", "SKALDIR_HOME", "PYTHONPATH"):
            env.pop(gone, None)
        env["PYTHONPATH"] = str(SRC)
        env["PYTHONUNBUFFERED"] = "1"
        env["TERM"] = "dumb"
        env["SKALDIR_HOME"] = str(rt)
        env["SKALDIR_CHATBAR"] = "1"
        env["TMPDIR"] = "/tmp"
        if env_extra:
            env.update(env_extra)
        program = (
            "import fcntl, runpy, sys, termios;"
            "fcntl.ioctl(0, getattr(termios, 'TIOCSCTTY', 0x540E), 0);"
            "sys.argv = ['skaldir.chatbar', 'shell', '--no-probe', '--no-activity'];"
            "runpy.run_module('skaldir.chatbar', run_name='__main__')"
        )
        self.child = subprocess.Popen(
            [sys.executable, "-c", program],
            cwd=str(REPO), env=env, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
        )
        os.close(slave)
        self.master = master
        self.log = bytearray()

    # -- reading / writing -------------------------------------------------- #

    def drain(self, seconds: float = 0.6) -> bytes:
        """Read whatever the child has written for ``seconds``; append to the log."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.1)
            except OSError:
                break
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    break
                raise
            if not chunk:
                break
            self.log += chunk
        return bytes(self.log)

    def until(self, needle: bytes, seconds: float = 12.0) -> bool:
        """Drain until ``needle`` shows up in the log (or the deadline passes)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if needle in self.log:
                return True
            self.drain(0.2)
        return needle in self.log

    def send(self, text: str, *, settle: float = 0.7) -> None:
        os.write(self.master, text.encode())
        self.drain(settle)

    def type_line(self, text: str, *, settle: float = 1.2) -> None:
        self.send(text, settle=0.25)
        self.send("\r", settle=settle)

    def resize(self, width: int, height: int, *, settle: float = 1.0) -> None:
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        os.kill(self.child.pid, signal.SIGWINCH)
        self.drain(settle)

    def close(self) -> None:
        try:
            os.write(self.master, b"/quit\r")
            self.drain(1.5)
        except OSError:
            pass
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
        try:
            os.close(self.master)
        except OSError:
            pass


def write_capture(outdir: Path, name: str, data: bytes, *, note: str = "") -> Path:
    """The raw byte log, plus a decoded sidecar so the picture can be read as text."""
    outdir.mkdir(parents=True, exist_ok=True)
    raw = outdir / name
    raw.write_bytes(data)
    text = data.decode("utf-8", "replace")
    (outdir / (name + ".txt")).write_text(text, encoding="utf-8")
    if note:
        (outdir / (name + ".note")).write_text(note + "\n", encoding="utf-8")
    return raw


def screen_of(data: bytes) -> str:
    """A readable rendering: the raw bytes with escapes shown as markers."""
    text = data.decode("utf-8", "replace")
    return (text.replace("\x1b", "<ESC>").replace("\r", "")
                .replace("\x08", "<BS>"))


def pipe_clean_check() -> dict:
    """A **real session on a pipe**: stdout is a file, so no frame can be drawn.

    This is the honest pipe proof — not ``chat preview`` (which *demonstrates* the frame and
    therefore prints its box-drawing characters on purpose), but an actual shell whose stdout
    is redirected. It has to come out as ``skaldir > `` and nothing else: zero escape bytes,
    zero non-ASCII bytes.
    """
    env = dict(os.environ)
    for gone in ("HERMES_HOME", "GODSV_HOME", "SKALDIR_HOME", "PYTHONPATH"):
        env.pop(gone, None)
    env["PYTHONPATH"] = str(SRC)
    env["SKALDIR_CHATBAR"] = "1"
    env["SKALDIR_HOME"] = "/tmp/chat-ux-pipe-rt"
    env["TMPDIR"] = "/tmp"
    Path("/tmp/chat-ux-pipe-rt").mkdir(parents=True, exist_ok=True)
    script = "/help\n/status\nwhat is this repository?\n/quit\n"
    proc = subprocess.run(
        [sys.executable, "-m", "skaldir.chatbar", "shell", "--no-probe", "--no-activity",
         "--limit", "4"],
        cwd=str(REPO), env=env, input=script, capture_output=True, text=True, timeout=180,
    )
    out = proc.stdout
    esc = out.count("\x1b")
    non_ascii = sum(1 for ch in out if ord(ch) > 127)
    bytes_out = sorted({f"U+{ord(ch):04X}" for ch in out if ord(ch) > 127})
    return {"exit": proc.returncode, "bytes": len(out.encode()), "esc": esc,
            "non_ascii": non_ascii, "non_ascii_chars": bytes_out,
            "prompt": "skaldir > " in out, "stdout": out, "stderr": proc.stderr[-2000:]}


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else REPO / "outputs" / "chat-ux"
    rt = Path("/tmp/chat-ux-rt")
    rt.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {}
    transcript = (REPO / "outputs/long-project-e2e/achievable-20260918T204940/transcripts"
                          "/achievable-B-1-B1-work-full.jsonl")
    # A real model name (the env source the provider layer reads) and a *declared* price, so
    # the band can be shown both ways: with a declaration, and with none (``$ n/a``).
    declared = {"SKALDIR_LLM_MODEL": "gpt-5-codex",
                "SKALDIR_TELEMETRY_PRICE_PER_1K": "0.00055"}

    # C1 — first paint at 80x24
    s = Session(rt, 80, 24, env_extra=declared)
    s.until("\u2500".encode(), seconds=15)
    s.drain(1.2)
    write_capture(outdir, "c1-first-paint-80x24.raw", bytes(s.log),
                  note="first paint, 80x24: logo + band + frame + hint")
    results["c1"] = screen_of(bytes(s.log))

    # C2b — the band fed by a REAL run's telemetry footer
    if transcript.exists():
        s.type_line(f"telemetry live --transcript {transcript} --text", settle=5.0)
        s.drain(1.5)
        write_capture(outdir, "c2b-band-real-data.raw", bytes(s.log),
                      note="a real run's telemetry lines stream past; the band keeps the real "
                           "ctx / tok-s that run reported")
        results["c2b"] = screen_of(bytes(s.log))[-2500:]

    # C3 — a plain-language turn, streamed inside the frame
    s.type_line("how do I add tests to this project?", settle=4.0)
    s.drain(2.5)
    write_capture(outdir, "c3-turn-streaming.raw", bytes(s.log),
                  note="a plain-language turn typed at the prompt; the answer/activity lines "
                       "stream above the frame and the frame is redrawn under them")
    results["c3"] = screen_of(bytes(s.log))[-2000:]

    # C4 — a resize
    s.resize(100, 30)
    s.resize(80, 24)
    write_capture(outdir, "c4-resize.raw", bytes(s.log),
                  note="SIGWINCH 80x24 -> 100x30 -> 80x24: the frame re-measured at both")
    results["c4"] = screen_of(bytes(s.log))[-2500:]

    # C5 — /clear restores the logo
    s.type_line("/clear", settle=2.5)
    s.drain(1.5)
    write_capture(outdir, "c5-clear.raw", bytes(s.log),
                  note="/clear: wipe, logo back, frame redrawn")
    results["c5"] = screen_of(bytes(s.log))[-2500:]
    s.close()

    # C6 — first paint at 120x30, with NO price declared (``$ n/a`` is the honest answer)
    s2 = Session(rt, 120, 30, env_extra={"SKALDIR_LLM_MODEL": "gpt-5-codex"})
    s2.until("\u2500".encode(), seconds=15)
    s2.drain(1.2)
    write_capture(outdir, "c6-first-paint-120x30.raw", bytes(s2.log),
                  note="first paint, 120x30, no price declared: the band says $ n/a")
    results["c6"] = screen_of(bytes(s2.log))[-2000:]
    s2.close()

    # C7 — the pipe check
    pipe = pipe_clean_check()
    write_capture(outdir, "c7-pipe-clean.raw", pipe["stdout"].encode(),
                  note=f"a real shell on a pipe: esc={pipe['esc']} non_ascii={pipe['non_ascii']}"
                       f" prompt={'skaldir > ' in pipe['stdout']}")
    results["c7"] = pipe

    import json

    (outdir / "summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")

    for key in ("c1", "c2b", "c3", "c4", "c5", "c6"):
        print("=" * 78)
        print(f"--- {key} " + "-" * (70 - len(key)))
        print(results[key][-2600:] if isinstance(results[key], str) else results[key])
    print("=" * 78)
    print("--- c7 pipe check:", {k: v for k, v in pipe.items() if k != "stdout"})
    print("-" * 78)
    print(pipe["stdout"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
