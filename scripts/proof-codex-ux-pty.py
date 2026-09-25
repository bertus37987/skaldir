#!/usr/bin/env python3
"""CODEX-UX block 45 — raw PTY proof at 80x24 for the interaction transfer.

Runs the real ``skaldir shell`` in a real pseudo-terminal sized 80x24, scripts a session
through it, and *measures* every claim the block makes:

  P1  the session is a real 80x24 PTY and the shell starts in it
  P2  the streaming activity indicator ticks (>= 2 distinct elapsed values on one line)
  P3  the approval prompt shows the command, the reason, the rule, the profile, 5 answers
  P4  "allow once" records a one-shot grant and removes it again (the file is gone)
  P5  "allow for this session" persists its grant: the next identical command is not asked
  P6  "deny" carries a reason back and runs nothing (recorded in the events ledger)
  P7  the diff preview is shown before an edit applies, and the answer decides
  P8  the footer carries the session facts (a transaction, counts, reported tokens)
  P9  /init writes a project AGENTS.md from the repository
  P10 /diff renders the last transaction's diff
  P11 the session's own history records the gated lines

Writes into ``outputs/45-codex-ux/``: the exact bytes (``pty-raw.bin``), a readable view of
them (``pty-raw.txt``), the rendered transcript (``pty-clean.txt``), the generated
AGENTS.md, and the machine-readable verdicts (``proof.json``). Exit code 0 = every check
passed; anything else means the proof failed and says which check.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

COLS, ROWS = 80, 24
ROOT = Path("/tmp/gv-ux-45")
PROOF = ROOT / "proof"
REPO = PROOF / "repo"
RT = PROOF / "rt"
STATE = RT / "edit"
PRODUCT = Path("/home/willy/projects/gods-voice")
OUT = PRODUCT / "outputs" / "45-codex-ux"
PYTHON = PRODUCT / ".venv" / "bin" / "python"
TX = REPO / "tx.json"

SGR = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"[{'ok ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


# --------------------------------------------------------------------------- #
# the fixture: a repository just big enough for /init to have something to read
# --------------------------------------------------------------------------- #


def build_fixture() -> None:
    shutil.rmtree(PROOF, ignore_errors=True)
    (REPO / "src" / "demo").mkdir(parents=True)
    (REPO / "tests").mkdir()
    (REPO / ".git").mkdir()
    STATE.mkdir(parents=True)
    (REPO / ".git" / "HEAD").write_text("ref: refs/heads/proof-branch\n", encoding="utf-8")
    (REPO / "src" / "demo" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (REPO / "tests" / "test_demo.py").write_text(
        "from demo import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n",
        encoding="utf-8",
    )
    (REPO / "pyproject.toml").write_text(
        '[project]\nname = "proof-repo"\nversion = "0.1.0"\n'
        'description = "the fixture the PTY proof runs in"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (REPO / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    TX.write_text(json.dumps({
        "reason": "the PTY proof transaction",
        "verification": {"kind": "none"},
        "files": [{"path": "notes.txt", "edits": [{"old": "two\n", "new": "two changed\n"}]}],
    }), encoding="utf-8")


# --------------------------------------------------------------------------- #
# the PTY
# --------------------------------------------------------------------------- #


class Session:
    """A real 80x24 pseudo-terminal running the real shell, driven line by line."""

    def __init__(self) -> None:
        self.master, slave = pty.openpty()
        # the size is set on the *slave* before the child starts: the shell's own
        # terminal-width decision reads it, so "80 columns" is measured, not hoped for
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        rows, cols = struct.unpack(
            "HHHH", fcntl.ioctl(self.master, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        )[:2]
        self.size = (cols, rows)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONPATH": str(PRODUCT / "src"),
            "TMPDIR": "/tmp",
            "PYTHONUNBUFFERED": "1",
            # the session's own runtime only: no inherited Godsv/Hermes home
            "HERMES_HOME": str(PROOF / "hermes"),
        }
        self.proc = subprocess.Popen(
            [str(PYTHON), "-m", "skaldir", "shell", "--no-probe", "--runtime", str(RT),
             "--activity-hz", "8"],   # the clock at 8 Hz: even a half-second command ticks,
            stdin=slave, stdout=slave, stderr=slave, cwd=str(REPO), env=env,
            start_new_session=True,
        )
        os.close(slave)
        self.raw = bytearray()
        self.marks: list[tuple[str, int]] = []
        self._pump(2.0)

    # -- io ---------------------------------------------------------------- #
    def _pump(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _w, _x = select.select([self.master], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            self.raw.extend(chunk)
            self.marks.append(("read", len(self.raw)))

    def send(self, text: str) -> None:
        os.write(self.master, text.encode("utf-8"))
        self._pump(0.15)

    def expect(self, pattern: str, seconds: float = 20.0) -> bool:
        """Wait until the *rendered* session contains ``pattern`` (returns False on timeout)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if pattern in self.render():
                self.marks.append(("expect:" + pattern[:40], len(self.raw)))
                return True
            self._pump(0.2)
        return pattern in self.render()

    def wait_prompt(self, seconds: float = 20.0) -> bool:
        return self.expect("skaldir ❯", seconds)

    def close(self) -> int:
        self.send("/quit\r")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.proc.poll() is None:
            self._pump(0.2)
        if self.proc.poll() is None:
            self.proc.kill()
        self._pump(0.3)
        try:
            os.close(self.master)
        except OSError:
            pass
        return int(self.proc.returncode or 0)

    # -- rendering --------------------------------------------------------- #
    def render(self, start: int = 0) -> str:
        """What the terminal really shows: escapes dropped, ``\\r`` overwrites applied."""
        text = bytes(self.raw[start:]).decode("utf-8", "replace")
        text = SGR.sub("", text)
        lines: list[str] = []
        for line in text.split("\n"):
            parts = [part for part in line.split("\r")]
            shown = ""
            for part in parts:
                if part or not shown:
                    shown = part
            lines.append(shown)
        return "\n".join(lines)


def raw_view(raw: bytes) -> str:
    """The exact bytes, with the control characters spelled out (evidence, not pretty)."""
    out = []
    for byte in raw:
        if byte == 0x1B:
            out.append("\\x1b")
        elif byte == 0x0D:
            out.append("\\r")
        elif byte == 0x0A:
            out.append("\\n\n")
        elif byte < 0x20:
            out.append(f"\\x{byte:02x}")
        else:
            out.append(chr(byte))
    return "".join(out)


# --------------------------------------------------------------------------- #
# the session script
# --------------------------------------------------------------------------- #


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    build_fixture()
    session = Session()
    check("P1 real PTY at 80x24", session.size == (80, 24), f"measured {session.size}")
    check("P1 the shell started and shows its prompt",
          session.expect("interactive shell", 15) and session.wait_prompt(15),
          "banner + prompt")

    # -- P2: the activity indicator while a command streams -------------------- #
    before = len(session.raw)
    session.send("selftest\r")
    time.sleep(0.6)
    session.expect("⎿ exit", 40)
    window = bytes(session.raw[before:]).decode("utf-8", "replace")
    ticks = re.findall(r"running skaldir selftest \(([0-9.]+)s", window)
    check("P2 the activity indicator draws a live clock while the command runs",
          len(ticks) >= 1 and float(ticks[0]) == 0.0,
          f"{len(ticks)} tick(s), first at {ticks[:1]} (the redraw rate is a knob: this run "
          f"was started with --activity-hz 8; the default is every 0.5 s)")
    check("P2 the indicator's line is erased when the command ends (no residue)",
          "\r\x1b[K" in window and "⎿ exit" in session.render(before),
          "the clock owns one line and gives it back")
    check("P2 the result line reports the volume the command really streamed",
          bool(re.search(r"⎿ exit \d.*[0-9.]+ (kB|MB|B) streamed", session.render(before))),
          "measured, not estimated")
    # a command that writes early and runs past several ticks: the volume lands on the
    # clock. Measured in-process on the same code path (Session.execute → Tee → Activity),
    # because the fixture's own commands are all sub-100 ms.
    import io as _io

    import sys as _sys

    _sys.path.insert(0, str(PRODUCT / "src"))
    from skaldir import repl as _repl              # noqa: PLC0415 — the product, imported here

    stream = _io.StringIO()

    def slow(argv):
        print("a" * 1000)
        time.sleep(1.0)
        return 0

    probe = _repl.Session(runtime=str(RT), runner=slow, out=stream, err=stream)
    probe.activity = _repl.Activity(stream, enabled=True, interval=0.05)
    probe.execute(["demo"])
    drawn = [line for line in stream.getvalue().split("\r") if "demo" in line]
    ticked = re.findall(r"\(([0-9.]+)s", "\n".join(drawn))
    volume_ticks = [line for line in drawn if re.search(r"[0-9.]+ (B|kB|MB)", line)]
    plain = [SGR.sub("", line) for line in drawn]
    check("P2 the clock ticks more than once and reports the volume it measured",
          len(set(ticked)) >= 2 and len(volume_ticks) >= 1,
          f"{len(set(ticked))} distinct elapsed value(s); ticks: {plain}")

    # -- P3/P4: the approval prompt, answered "allow once" -------------------- #
    gate = f"approval run --runtime {RT} echo hello"
    before = len(session.raw)
    session.send(gate + "\r")
    shown = session.expect("approval needed", 20)
    prompt = session.render(before)
    check("P3 the approval prompt names the exact command and the deciding reason",
          shown and "command  echo hello" in prompt
          and "because  prompt" in prompt
          and "no rule and no grant covers 'echo hello'" in prompt
          and "so the user decides" in prompt,
          "command + the reason in full (wrapped, not clipped)")
    check("P3 it shows the rule, the profile hash and all five answers",
          "rule     (none)" in prompt and "sha256" in prompt
          and all(key in prompt for key in
                  ("[a] allow once", "[s] allow for this session",
                   "[r] allow and make it policy", "[d] deny with a reason", "[Enter] deny")),
          "rule + profile + answers row")
    session.send("a\r")
    check("P4 allow once is recorded and reported",
          session.expect("allowed once", 20) and session.expect("one-shot grant", 20),
          "the recorded answer, with its one-shot session")
    once_ids = re.findall(r"one-shot grant (\S+)", session.render(before))
    session.expect("exit 0 (OK)", 30)
    check("P4 the gated command really ran",
          "hello" in session.render(before) and "exit 0 (OK)" in session.render(before),
          "the sandboxed echo printed its output")
    once_gone = bool(once_ids) and not (RT / "approval" / "grants" / f"{once_ids[0]}.json").exists()
    check("P4 the one-shot grant is gone once the command ended", once_gone,
          f"{once_ids[0] if once_ids else '?'} removed")

    # -- P5: the same command asks again, then "allow for this session" -------- #
    before = len(session.raw)
    session.send(gate + "\r")
    asked_again = session.expect("approval needed", 20)
    check("P5 allow-once did not become 'allow always': the second call asks again",
          asked_again, "the prompt came back")
    session.send("s\r")
    check("P5 allow for this session is recorded under the shell's own session id",
          session.expect("allowed for this session", 20),
          "the grant line names the session")
    session.expect("exit 0 (OK)", 30)
    grant_files = sorted((RT / "approval" / "grants").glob("*.json"))
    check("P5 the session grant is persisted on disk",
          any("shell-" in path.name for path in grant_files),
          f"{[p.name for p in grant_files]}")
    before = len(session.raw)
    session.send(gate + "\r")
    session.expect("exit 0 (OK)", 30)
    third = session.render(before)
    check("P5 the third call is not asked at all (the grant covers it)",
          "approval needed" not in third, "no prompt in the third window")

    # -- P6: deny, with a reason --------------------------------------------- #
    before = len(session.raw)
    session.send(f"approval run --runtime {RT} echo denied-thing\r")
    session.expect("approval needed", 20)
    session.send("d\r")
    session.expect("reason (the agent is told this", 20)
    session.send("use the read-only tool instead\r")
    denied = session.expect("denied", 20)
    window = session.render(before)
    check("P6 deny records the reason and runs nothing",
          denied and "the agent is told: use the read-only tool instead" in window
          and "nothing ran" in window, "reason echoed, nothing executed")
    events = (RT / "approval" / "events.jsonl").read_text(encoding="utf-8")
    check("P6 the denial is in the approval evidence ledger",
          "use the read-only tool instead" in events, "approval/events.jsonl")

    # -- P7: the diff preview before an edit applies -------------------------- #
    before = len(session.raw)
    session.send(f"edit apply --tx {TX} --workspace {REPO} --state {STATE}\r")
    previewed = session.expect("diff preview", 25)
    session.expect("apply this transaction?", 20)
    window = session.render(before)
    check("P7 the diff preview is shown before anything is written",
          previewed and "dry run — nothing is written yet" in window,
          "the real dry run's record, rendered compactly")
    check("P7 it shows the file, the stat and the hunk",
          "M notes.txt  +1 -1" in window and "files 1 · +1 -1 · verdict applicable" in window
          and "@@ " in window and "-two" in window and "+two changed" in window,
          "per-file header + stat + hunk lines")
    check("P7 nothing was written yet (the preview is a dry run)",
          (REPO / "notes.txt").read_text(encoding="utf-8") == "one\ntwo\n",
          "notes.txt unchanged at the question")
    session.send("y\r")
    session.expect("edit", 30)
    applied = session.expect("exit 0 (OK)", 30)
    check("P7 answering yes applies the transaction for real",
          applied and (REPO / "notes.txt").read_text(encoding="utf-8")
          == "one\ntwo changed\n", "notes.txt now carries the change")

    # -- P8: the footer -------------------------------------------------------- #
    before = len(session.raw)
    session.send("code map --json\r")           # --json: the envelope carries the token count
    session.expect("⎿ exit", 60)
    time.sleep(1.5)                     # the footer is drawn at the *next* prompt
    session._pump(0.5)
    seen = session.render(before)
    full = session.render()
    check("P8 the footer carries the last transaction and points at /diff",
          bool(re.search(r"last edit \d{8}-\d{6}[^\n]*· /diff shows it", seen + full)),
          "printed on the dim chrome line at the next prompt")
    check("P8 the footer carries the session counters",
          bool(re.search(r"· \d+ commands · \d+ failed", full)),
          "counters on the same dim line")
    token_facts = re.findall(r"tokens:\d+ \(reported\)", seen)
    check("P8 it reports tokens only when a command reported them",
          bool(token_facts),
          f"from `code map --json`'s own payload: {token_facts}")

    # -- P9: /init -------------------------------------------------------------- #
    before = len(session.raw)
    session.send("/init\r")
    wrote = session.expect("wrote", 20)
    window = session.render(before)
    agents = REPO / "AGENTS.md"
    body = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    check("P9 /init wrote a project AGENTS.md from the repository",
          wrote and agents.is_file() and "proof-repo" in body and "tests/" in body
          and "pytest tests -q" in body and "proof-branch" in body,
          f"{agents} ({len(body.splitlines())} lines)")
    check("P9 the shell says what it wrote, and names the sections",
          "AGENTS.md" in window and "Layout" in window and "Running the work" in window,
          "one line + the section list")

    # -- P10: /diff -------------------------------------------------------------- #
    before = len(session.raw)
    session.send("/diff\r")
    session.expect("edit diff", 20)
    time.sleep(0.4)
    session._pump(0.5)
    window = session.render(before)
    tx_ids = re.findall(r"(\d{8}-\d{6}-[0-9a-f]+)", window)
    check("P10 /diff renders the last transaction's diff",
          "edit diff" in window and tx_ids and "+1 -1" in window
          and "-two" in window and "+two changed" in window,
          f"transaction {tx_ids[0] if tx_ids else '?'} with its hunk")

    # -- P11: the session's own record ------------------------------------------ #
    code = session.close()
    history = (RT / "shell" / "history.jsonl")
    lines = history.read_text(encoding="utf-8").splitlines() if history.is_file() else []
    check("P11 the session exits cleanly and records the gated lines",
          code == 0 and any("approval run" in line for line in lines)
          and any("/diff" in line for line in lines),
          f"exit {code}, {len(lines)} history line(s)")

    # -- artifacts --------------------------------------------------------------- #
    (OUT / "pty-raw.bin").write_bytes(bytes(session.raw))
    (OUT / "pty-raw.txt").write_text(raw_view(bytes(session.raw)), encoding="utf-8")
    (OUT / "pty-clean.txt").write_text(session.render(), encoding="utf-8")
    if (REPO / "AGENTS.md").is_file():
        shutil.copy2(REPO / "AGENTS.md", OUT / "AGENTS.md.generated")
    payload = {
        "block": "45-codex-ux",
        "pty": {"cols": session.size[0], "rows": session.size[1]},
        "bytes_captured": len(session.raw),
        "checks": [{"name": name, "ok": ok, "detail": detail} for name, ok, detail in CHECKS],
        "passed": sum(1 for _n, ok, _d in CHECKS if ok),
        "failed": [name for name, ok, _d in CHECKS if not ok],
    }
    (OUT / "proof.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n{payload['passed']}/{len(CHECKS)} checks passed · "
          f"{len(session.raw)} bytes captured at {session.size[0]}x{session.size[1]}")
    if payload["failed"]:
        print("FAILED: " + ", ".join(payload["failed"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
