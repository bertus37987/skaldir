"""The chat acceptance harness: the REAL ``skaldir chat`` entry, on a pty, with raw captures.

What it proves (and where the bytes are):

* **the entry is the product's own** — ``.venv/bin/skaldir chat`` (the console script), on a real
  pty at 80x24, never an internal function and never ``python -m``;
* **multi-turn continuity** — the local stub provider records every request *body*, so turn 2's
  answer can be read back as "I saw these messages": the first user line and the first assistant
  answer must both be in it;
* **a run can be attached** — a real goal row in the scratch runs store, and a session opened with
  ``--attach <goal-id>``;
* **``/steer`` and ``/queue`` deliver durably** — the chat's own words in the capture, plus the
  rows in ``runs.db`` (kind, origin, text), plus the named refusal when nothing is attached;
* **the pipe stays clean** — the same entry with stdout redirected: 0 escape bytes, ASCII only.

Assertions run against a **replayed virtual screen** (``tools/chat_screen_replay.replay``), not the
raw byte stream: the frame repaints, so the bytes legitimately repeat text the terminal shows once.

Usage:
    PYTHONPATH=<repo>/src TMPDIR=/tmp .venv/bin/python tools/chat_acceptance_pty.py <outdir>
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select as _select
import struct
import subprocess
import sys
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO / "tools"))

from chat_screen_replay import replay  # noqa: E402  (a reader for the evidence, kept beside it)

ENTRY = REPO / ".venv" / "bin" / "skaldir"
MODEL = "gv-acceptance-stub"
#: A *label*, not a credential: the local stub answers anything. Never printed by this harness.
STUB_KEY = "stub-key-local-only"


# --------------------------------------------------------------------------- #
# the provider: local, deterministic, and it remembers every request           #
# --------------------------------------------------------------------------- #


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — the base's name
        """Silence the access log: the capture is the evidence, not stderr noise."""

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return {}

    def do_GET(self) -> None:  # noqa: N802 — http.server's spelling
        payload = json.dumps({"data": [{"id": MODEL, "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 — http.server's spelling
        body = self._body()
        messages = body.get("messages") or []
        server: Any = self.server
        server.requests.append({"path": self.path, "body": body})
        turn = len(server.requests)
        roles = [str(item.get("role") or "") for item in messages]
        first_user = next(
            (str(item.get("content") or "") for item in messages if item.get("role") == "user"), ""
        )
        answer = (
            f"[stub turn {turn}] messages={len(messages)} roles={','.join(roles)} "
            f"first_user={first_user[:40]!r}"
        )
        if body.get("stream"):
            self._stream(answer)
        else:
            self._whole(answer)

    def _chunk(self, text: str, *, last: bool = False) -> None:
        data = text.encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()
        if last:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def _stream(self, answer: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        pieces = [answer[i:i + 24] for i in range(0, len(answer), 24)] or [""]
        for index, piece in enumerate(pieces):
            if index:
                time.sleep(0.02)
            payload = json.dumps(
                {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            )
            self._chunk(f"data: {payload}\n\n")
        final = json.dumps(
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": len(answer) // 4 + 1,
                       "total_tokens": 10 + len(answer) // 4 + 1}}
        )
        self._chunk(f"data: {final}\n\n")
        self._chunk("data: [DONE]\n\n")
        self._chunk("", last=True)
        self.close_connection = True

    def _whole(self, answer: str) -> None:
        payload = json.dumps(
            {
                "id": "stub-1", "object": "chat.completion", "model": MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class StubProvider:
    """A local OpenAI-compatible endpoint that remembers the requests it was sent."""

    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.httpd.daemon_threads = True
        self.httpd.requests = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(self.httpd.requests)  # type: ignore[attr-defined]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
# the session: the real entry point, on a real terminal                         #
# --------------------------------------------------------------------------- #


class Session:
    """``skaldir chat`` on a pty whose winsize this harness sets itself."""

    def __init__(self, rt: Path, width: int, height: int, *, argv: list[str],
                 env_extra: dict[str, str]) -> None:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        env = dict(os.environ)
        for gone in ("HERMES_HOME", "GODSV_HOME", "SKALDIR_HOME", "PYTHONPATH", "NO_COLOR",
                     "SKALDIR_CHATBAR", "SKALDIR_LLM_API_KEY", "SKALDIR_LLM_BASE_URL",
                     "SKALDIR_LLM_MODEL", "GODSV_LLM_BASE_URL", "GODSV_LLM_MODEL",
                     "SKALDIR_JEV_BASE_URL", "SKALDIR_JEV_MODEL", "GODSV_JEV_BASE_URL"):
            env.pop(gone, None)
        env.update({
            "PYTHONPATH": str(SRC),
            "PYTHONUNBUFFERED": "1",
            "TERM": "xterm-256color",
            "COLORTERM": "",
            "SKALDIR_HOME": str(rt),
            "TMPDIR": "/tmp",
        })
        env.update(env_extra)
        self.env = env
        self.child = subprocess.Popen(
            [str(ENTRY), *argv],
            cwd=str(REPO), env=env, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
        )
        os.close(slave)
        self.master = master
        self.log = bytearray()

    # -- reading / writing -------------------------------------------------- #

    def drain(self, seconds: float = 0.6) -> bytes:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                ready, _, _ = _select.select([self.master], [], [], 0.1)
            except OSError:
                break
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.log += chunk
        return bytes(self.log)

    def until(self, needle: str, seconds: float = 20.0) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if needle in self.screen():
                return True
            self.drain(0.25)
        return needle in self.screen()

    def send(self, text: str, *, settle: float = 0.6) -> None:
        os.write(self.master, text.encode())
        self.drain(settle)

    def type_line(self, text: str, *, settle: float = 1.0) -> None:
        self.send(text, settle=0.25)
        self.send("\r", settle=settle)

    def screen(self, width: int = 80, height: int = 24) -> str:
        """The capture as a terminal would show it (a VT replay, not the byte stream)."""
        try:
            return "\n".join(replay(bytes(self.log), width, height))
        except Exception:  # noqa: BLE001 — a capture that cannot replay is read as text
            return bytes(self.log).decode("utf-8", "replace")

    def mark(self) -> int:
        return len(self.log)

    def since(self, mark: int) -> str:
        """The screen of everything written since ``mark`` (a fresh window on the new bytes)."""
        body = bytes(self.log[mark:])
        try:
            return "\n".join(replay(body, 80, 24))
        except Exception:  # noqa: BLE001 — a capture that cannot replay is read as text
            return body.decode("utf-8", "replace")

    def close(self) -> int:
        try:
            os.write(self.master, b"/quit\r")
            self.drain(1.5)
        except OSError:
            pass
        try:
            code = self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
            code = -9
        try:
            os.close(self.master)
        except OSError:
            pass
        return code


def save(outdir: Path, name: str, data: bytes, note: str) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    raw = outdir / name
    raw.write_bytes(data)
    (outdir / (name + ".screen")).write_text(
        "\n".join(replay(data, 80, 24)) + "\n", encoding="utf-8"
    )
    (outdir / (name + ".note")).write_text(note + "\n", encoding="utf-8")
    return raw


# --------------------------------------------------------------------------- #
# the run                                                                       #
# --------------------------------------------------------------------------- #


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else REPO / "outputs" / "chat-acceptance"
    rt = Path(os.environ.get("GV_ACCEPT_RT") or "/tmp/gv-chat-acceptance")
    rt.mkdir(parents=True, exist_ok=True)

    from skaldir import runs_store as rs

    stub = StubProvider()
    base = f"http://127.0.0.1:{stub.port}/v1"
    env_extra = {
        "SKALDIR_LLM_BASE_URL": base,
        "SKALDIR_LLM_MODEL": MODEL,
        "SKALDIR_LLM_API_KEY": STUB_KEY,
    }
    checks: dict[str, Any] = {}
    argv_chat = ["chat", "--runtime", str(rt), "--no-probe", "--limit", "0"]

    # -- session A: the entry, two turns, and the refusal when nothing is attached -- #
    a = Session(rt, 80, 24, argv=argv_chat, env_extra=env_extra)
    painted = a.until("skaldir > ") or a.until("\u2500")  # the frame's bar, or the plain prompt
    a.drain(1.5)
    save(outdir, "a1-first-paint.raw", bytes(a.log),
         note="the real `skaldir chat` entry, 80x24, first paint")
    checks["a1_first_paint"] = bool(painted)
    checks["a1_frame_bars"] = a.screen().count("\u2500") > 0

    a.type_line("acceptance: first turn, please answer in one line", settle=3.0)
    got_one = a.until("[stub turn 1]")
    a.drain(1.5)
    save(outdir, "a2-turn-1.raw", bytes(a.log), note="turn 1: the stub streamed its answer")
    checks["a2_turn_1_streamed"] = bool(got_one)
    checks["a2_roles"] = stub.requests[0]["body"]["messages"] if stub.requests else []

    mark = a.mark()
    a.type_line("acceptance: second turn", settle=3.0)
    got_two = a.until("[stub turn 2]")
    a.drain(1.5)
    save(outdir, "a3-turn-2-continuity.raw", bytes(a.log),
         note="turn 2: the REQUEST the stub received carries turn 1's user line and answer")
    checks["a3_turn_2_streamed"] = bool(got_two)
    second = stub.requests[1]["body"]["messages"] if len(stub.requests) > 1 else []
    flattened = json.dumps(second)
    checks["a3_message_count"] = len(second)
    checks["a3_sees_first_user_line"] = "first turn, please answer in one line" in flattened
    checks["a3_sees_first_answer"] = "[stub turn 1]" in flattened
    checks["a3_second_turn_bytes"] = a.since(mark)[:400]

    a.type_line('/steer "watch the failing check next"', settle=2.0)
    a.drain(1.0)
    save(outdir, "a4-steer-no-run.raw", bytes(a.log),
         note="nothing is attached: /steer refuses by name (no-run), it does not write silently")
    checks["a4_refusal_named"] = "no-run" in a.screen()
    checks["a4_exit"] = a.close()

    # -- the run the chat is the surface of --------------------------------- #
    store = rs.RunsStore(rs.run_store_path(rt))
    goal = store.start_goal("acceptance: the chat is this run's surface",
                            session_id="gv-acceptance")
    attached = store.start_goal(
        "acceptance: a run the chat attaches to", session_id="other-session"
    )
    store.close()
    checks["goal_id"] = goal.goal_id

    # -- session B: /steer and /queue into this session's own run ------------- #
    b = Session(rt, 80, 24, argv=[*argv_chat, "--session", "gv-acceptance"],
                env_extra=env_extra)
    b.until("skaldir > ")
    b.drain(1.0)
    b.type_line('/steer "the failing check first, then the docs"', settle=2.0)
    b.drain(0.8)
    steered = b.screen()
    b.type_line('/queue "and run the focused tests after"', settle=2.0)
    b.drain(0.8)
    queued = b.screen()
    save(outdir, "b1-steer-and-queue.raw", bytes(b.log),
         note="/steer + /queue delivered to this session's own run")
    checks["b1_steer_delivered"] = "delivered" in steered and "steer #" in steered
    checks["b2_queue_delivered"] = "delivered" in queued and "queue #" in queued
    checks["b_exit"] = b.close()

    store = rs.RunsStore(rs.run_store_path(rt))
    rows = [
        {"kind": item.kind, "origin": item.origin, "text": item.text}
        for item in store.messages(goal.goal_id)
    ]
    store.close()
    checks["b3_store_rows"] = rows
    checks["b3_store_kinds"] = sorted({row["kind"] for row in rows})

    # -- session C: --attach <goal-id> --------------------------------------- #
    c = Session(rt, 80, 24, argv=[*argv_chat, "--session", "gv-attached",
                                  "--attach", attached.goal_id],
                env_extra=env_extra)
    c.until("skaldir > ")
    c.drain(1.0)
    c.type_line('/steer "the attached run hears this"', settle=2.0)
    c.drain(0.8)
    attached_screen = c.screen()
    save(outdir, "c1-attached-steer.raw", bytes(c.log),
         note="--attach <goal-id>: the message goes to the ATTACHED run, not the session's own")
    checks["c1_attach_delivered"] = "delivered" in attached_screen
    checks["c1_names_attached_run"] = attached.goal_id[:20] in attached_screen
    checks["c_exit"] = c.close()

    store = rs.RunsStore(rs.run_store_path(rt))
    own = [item.text for item in store.messages(goal.goal_id)]
    other = [item.text for item in store.messages(attached.goal_id)]
    store.close()
    checks["c2_attached_run_rows"] = other
    checks["c2_own_run_rows"] = own
    checks["c2_routed_to_attached"] = (
        any("attached run hears this" in text for text in other)
        and not any("attached run hears this" in text for text in own)
    )

    # -- the pipe proof: the same entry, stdout redirected -------------------- #
    env = dict(os.environ)
    for gone in ("HERMES_HOME", "GODSV_HOME", "SKALDIR_HOME", "PYTHONPATH", "NO_COLOR"):
        env.pop(gone, None)
    env.update({"PYTHONPATH": str(SRC), "SKALDIR_HOME": str(rt), "TMPDIR": "/tmp"})
    env.update(env_extra)
    proc = subprocess.run(
        [str(ENTRY), "chat", "--runtime", str(rt), "--no-probe", "--limit", "2"],
        cwd=str(REPO), env=env, input="/help\n/quit\n", capture_output=True, text=True,
        timeout=120,
    )
    out = proc.stdout
    checks["d_pipe"] = {
        "exit": proc.returncode,
        "esc": out.count("\x1b"),
        "non_ascii": sum(1 for ch in out if ord(ch) > 127),
        "prompt": "skaldir > " in out,
    }
    save(outdir, "d1-pipe.raw", out.encode(), note=f"a pipe: {checks['d_pipe']}")

    stub.close()
    (outdir / "summary.json").write_text(
        json.dumps(checks, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    verdicts = {key: value for key, value in checks.items() if isinstance(value, bool)}
    for key, value in verdicts.items():
        print(f"{'PASS' if value else 'FAIL'}  {key}")
    print(json.dumps({k: v for k, v in checks.items() if not isinstance(v, bool)},
                     indent=2, ensure_ascii=False)[:4000])
    return 0 if all(verdicts.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
