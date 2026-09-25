"""Owner-facing proof: the setup/model selectors on a REAL 80x24 terminal.

This drives the product's own entry point (``.venv/bin/skaldir``) under a real pty whose
window size is set with ``TIOCSWINSZ``, types at it, and saves the raw byte stream plus a
virtual-screen replay of every scenario. Nothing here is a mock of the terminal: the captures
are the bytes the process wrote.

Scenarios
    p1-role-selector        the two roles as a list, arrows, Esc
    p2-provider-selector    16 providers, base URL pre-filled, type-to-narrow
    p3-model-selector       the catalog's own model list, typed override allowed
    p4-masked-key-setup     the whole two-key flow against two local stub endpoints
    p5-check                ``--check`` from a fresh process
    p6-plain-color          NO_COLOR on a terminal: the same rows as lines
    p7-plain-dumb           TERM=dumb on a terminal
    p8-plain-stderr-file    a real tty for input, stderr to a file: lines, no escapes
    p9-pipe-headless        stdin a pipe: nothing is asked, the missing flags are named
    p10-exit-codes          no key / wrong key / success

Usage:  .venv/bin/python tools/setup_capture.py [outdir]
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import shutil
import stat
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from test_setup_flow import StubEndpoint  # noqa: E402 — the stub the suite already trusts

ENTRY = REPO / ".venv" / "bin" / "skaldir"
#: The key typed in the captures. Synthetic, never a real credential, and reported by LENGTH
#: only — the same rule the rest of this project follows.
TYPED_KEY = "sk-stub-" + "a1b2c3d4e5f6g7h8i9j0k1l2"

#: 80x24, the size the owner asked for.
WIDTH, HEIGHT = 80, 24

#: The capture-time delays: generous enough that a slow machine still produces a full screen.
SETTLE = 0.45
STEP = 0.25


# --------------------------------------------------------------------------- #
# the terminal                                                                  #
# --------------------------------------------------------------------------- #


class Session:
    """A real process on a real controlling terminal, at a size this file chooses."""

    def __init__(
        self,
        argv: list[str],
        *,
        width: int = WIDTH,
        height: int = HEIGHT,
        env_extra: dict[str, str] | None = None,
        stderr_to: Path | None = None,
    ) -> None:
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
        env = clean_env(env_extra)
        err: Any = slave
        self.err_file = None
        self.err_path = None
        self.err_buf = bytearray()
        self.err_mark = 0
        if stderr_to is not None:
            self.err_path = Path(stderr_to)
            self.err_file = open(self.err_path, "wb")  # noqa: SIM115 — closed in close()
            err = self.err_file.fileno()
        self.child = subprocess.Popen(
            argv,
            cwd=str(REPO),
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=err,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        self.master = master
        self.log = bytearray()
        self.mark = 0

    # -- reading / writing -------------------------------------------------- #

    def drain(self, seconds: float = SETTLE) -> bytes:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.05)
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

    def until(self, needle: str, seconds: float = 12.0) -> bool:
        """Wait for ``needle`` in what was printed **since the last send**.

        Both streams count: when stderr is a file (the half-pipe case) the questions are
        *not* on the terminal, and a script that only watched the terminal would type at a
        prompt that has not been asked yet.
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if needle in self._fresh().decode("utf-8", "replace"):
                return True
            self.drain(0.15)
        return needle in self._fresh().decode("utf-8", "replace")

    def _fresh(self) -> bytes:
        """Everything printed after the last send, from the terminal **and** the error file."""
        self._read_err()
        return bytes(self.log[self.mark :]) + bytes(self.err_buf[self.err_mark :])

    def _read_err(self) -> None:
        if self.err_path is None:
            return
        try:
            with open(self.err_path, "rb") as handle:
                handle.seek(len(self.err_buf))
                self.err_buf += handle.read()
        except OSError:
            return

    def send(self, keys: str, *, settle: float = SETTLE) -> None:
        # the marks are taken **before** the keys: everything the product prints from here on
        # is "new", so the next ``until`` cannot match a prompt that was already on the screen
        self.mark = len(self.log)
        self._read_err()
        self.err_mark = len(self.err_buf)
        os.write(self.master, keys.encode())
        self.drain(settle)

    def type_line(self, text: str) -> None:
        self.send(text, settle=STEP)
        self.send("\r")

    def close(self, *, quit_keys: str = "\x04") -> int:
        """End the session: ``Ctrl-D`` (a clean EOF) if it is still running, then its code."""
        try:
            if self.child.poll() is None:
                os.write(self.master, quit_keys.encode())
                self.drain(0.4)
        except OSError:
            pass
        code = self.child.wait(timeout=20)
        if self.err_file is not None:
            self.err_file.close()
        return code


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A clean environment: no leaked keys, no stale homes, a terminal that can be driven."""
    env = dict(os.environ)
    for gone in (
        "HERMES_HOME",
        "GODSV_HOME",
        "SKALDIR_HOME",
        "SKALDIR_LLM_CONFIG",
        "SKALDIR_SETUP_ENV_FILE",
        "SKALDIR_SETUP_LLM_API_KEY",
        "SKALDIR_SETUP_JEV_API_KEY",
        "SKALDIR_SETUP_LLM_PROVIDER",
        "SKALDIR_SETUP_JEV_PROVIDER",
        "SKALDIR_LLM_MODEL",
        "SKALDIR_LLM_BASE_URL",
        "SKALDIR_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "LLAMA_API_KEY",
        "VLLM_API_KEY",
        "NO_COLOR",
        "SKALDIR_PLAIN",
    ):
        env.pop(gone, None)
    env["TERM"] = "xterm-256color"
    env["LANG"] = "C.UTF-8"
    env["LC_ALL"] = "C.UTF-8"
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONUNBUFFERED"] = "1"
    env["TMPDIR"] = "/tmp"
    if extra:
        env.update(extra)
    return env


# --------------------------------------------------------------------------- #
# a virtual screen: replaying the escape vocabulary the selector emits          #
# --------------------------------------------------------------------------- #

_ESCAPE = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")


class Screen:
    """80x24 of cells, fed the capture: the picture, not the byte stream.

    The vocabulary it understands is exactly what this selector writes — ``\\r``, ``\\n``,
    ``ESC[<n>A``/````ESC[<n>B``, ``ESC[<n>K``, ``ESC[<n>J`` — plus everything a real shell or
    the CLI envelope may add. An escape it does not know is **counted, never silently
    dropped**, and the count is reported with the capture.
    """

    def __init__(self, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.width, self.height = width, height
        self.cells = [[" "] * width for _ in range(height)]
        self.row = self.col = 0
        self.unknown = 0

    def feed(self, data: bytes) -> None:
        text = data.decode("utf-8", "replace")
        index = 0
        while index < len(text):
            char = text[index]
            if char == "\x1b":
                match = _ESCAPE.match(text, index)
                if match is None:
                    self.unknown += 1
                    index += 1
                    continue
                self._escape(match.group(1), match.group(2))
                index = match.end()
                continue
            if char == "\r":
                self.col = 0
            elif char == "\n":
                self._down()
            elif char == "\b":
                self.col = max(0, self.col - 1)
            elif char == "\x07":
                pass
            else:
                self._put(char)
            index += 1

    def _escape(self, params: str, final: str) -> None:
        numbers = [int(part) for part in params.split(";") if part.isdigit()] or [0]
        count = numbers[0]
        if final == "A":
            self.row = max(0, self.row - (count or 1))
        elif final == "B":
            self.row = min(self.height - 1, self.row + (count or 1))
        elif final == "K":
            if count == 2:
                self.cells[self.row] = [" "] * self.width
            else:
                for col in range(self.col, self.width):
                    self.cells[self.row][col] = " "
        elif final == "J":
            self.cells = [[" "] * self.width for _ in range(self.height)]
            self.row = self.col = 0
        elif final == "H":
            row = (numbers[0] or 1) - 1
            col = (numbers[1] if len(numbers) > 1 else 1) - 1
            self.row = max(0, min(self.height - 1, row))
            self.col = max(0, min(self.width - 1, col))
        else:
            self.unknown += 1

    def _down(self) -> None:
        self.row += 1
        if self.row >= self.height:
            self.cells.pop(0)
            self.cells.append([" "] * self.width)
            self.row = self.height - 1

    def _put(self, char: str) -> None:
        if self.col >= self.width:
            self.col = 0
            self._down()
        self.cells[self.row][self.col] = char
        self.col += 1

    def picture(self) -> str:
        return "\n".join("".join(row).rstrip() for row in self.cells)

    def non_blank(self) -> list[str]:
        return [line for line in self.picture().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# capture plumbing                                                              #
# --------------------------------------------------------------------------- #


class Proof:
    """One output directory, one JSON of results, one capture per scenario."""

    def __init__(self, outdir: Path) -> None:
        self.outdir = outdir
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, Any] = {}

    def save(self, name: str, data: bytes, *, screen: Screen | None = None, note: str) -> None:
        (self.outdir / f"{name}.raw").write_bytes(data)
        if screen is not None:
            (self.outdir / f"{name}.screen.txt").write_text(
                screen.picture() + "\n", encoding="utf-8"
            )
        (self.outdir / f"{name}.note").write_text(note + "\n", encoding="utf-8")

    def record(self, name: str, payload: dict[str, Any]) -> None:
        self.results[name] = payload

    def finish(self) -> None:
        (self.outdir / "summary.json").write_text(
            json.dumps(self.results, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )


def capture(
    proof: Proof,
    name: str,
    argv: list[str],
    script: list[tuple[str, str]],
    *,
    note: str,
    env_extra: dict[str, str] | None = None,
    stderr_to: Path | None = None,
    stop_at: str = "",
) -> dict[str, Any]:
    """Run one scenario and save it: the raw bytes, the screen, and what it proved.

    ``script`` is a list of ``(needle, keys)``: wait for ``needle`` (when given), then type
    ``keys``. Waiting on the product's own words — never on a sleep — is what keeps the
    capture honest on a slow machine.
    """
    session = Session(argv, env_extra=env_extra, stderr_to=stderr_to)
    screen = Screen()
    timeline: list[str] = []
    for needle, keys in script:
        if needle and not session.until(needle):
            timeline.append(f"never saw {needle!r}")
            break
        session.send(keys)
        timeline.append(f"{keys!r} after {needle!r}")
    session.drain(SETTLE)
    if stop_at:
        session.until(stop_at, seconds=2.0)
    raw = bytes(session.log)
    screen.feed(raw)
    code = session.close()
    proof.save(name, raw, screen=screen, note=note)
    payload = {
        "exit": code,
        "bytes": len(raw),
        "esc_bytes": raw.count(b"\x1b"),
        "non_ascii": sum(1 for byte in raw if byte > 127),
        "unknown_escapes": screen.unknown,
        "timeline": timeline,
        "screen": screen.picture(),
    }
    proof.record(name, payload)
    return payload


# --------------------------------------------------------------------------- #
# the scenarios                                                                 #
# --------------------------------------------------------------------------- #


def selector_captures(proof: Proof, home: Path) -> None:
    """p1-p3: the three selectors, each at 80x24, each left with Esc."""
    model = [str(ENTRY), "model", "--config", str(home / "config.yaml")]

    # p1 — the role selector
    capture(
        proof,
        "p1-role-selector",
        model,
        [("which key", "\x1b[B"), ("TypeSafe", "\x1b")],
        env_extra={"SKALDIR_HOME": str(home)},
        note="the two roles as a selection list: ↓ moved the pointer to row 2, Esc left "
             "without choosing (the question is answered, the run is not killed)",
    )

    # p2 — the provider selector, then type-to-narrow
    capture(
        proof,
        "p2-provider-selector",
        model,
        [
            ("which key", "\r"),           # take the default role (the LLM provider)
            ("provider", "\x1b[B\x1b[B"),  # move in the 16-row list
            ("Anthropic", "deep"),         # type to narrow
            ("DeepSeek", "\x1b"),
        ],
        env_extra={"SKALDIR_HOME": str(home)},
        note="the 16 catalog rows with their base URLs; ↓↓ then typing 'deep' narrowed the "
             "list to DeepSeek; Esc left without choosing",
    )

    # p3 — the model selector
    capture(
        proof,
        "p3-model-selector",
        model,
        [
            ("which key", "\r"),
            ("provider", "deep\r"),
            ("model", "\x1b[B"),           # the catalog's own list: chat, reasoner
            ("reasoner", "\x1b"),
        ],
        env_extra={"SKALDIR_HOME": str(home)},
        note="the provider named by typing its id, then the catalog's own model list "
             "(deepseek-chat / deepseek-reasoner) with the pointer moved to row 2",
    )


def setup_captures(proof: Proof, home: Path) -> None:
    """p4-p5: the whole two-key flow against two real local endpoints, then ``--check``."""
    config = home / "config.yaml"
    env_file = home / ".env"
    argv = [str(ENTRY), "setup", "--config", str(config), "--runtime", str(home),
            "--format", "text"]
    answers: list[tuple[str, str]] = [
        ("provider (number or id)", "13\r"),  # llama.cpp, the row that names the stub's URL
        ("endpoint", "\r"),                  # the endpoint the row pre-filled
        ("model", "stub-llama\r"),           # a local server names its own model
        ("the key", TYPED_KEY + "\r"),       # masked
        ("TypeSafe", "14\r"),                # vLLM, the second stub's row
        ("endpoint", "\r"),
        ("model", "stub-jev\r"),
        ("the key", TYPED_KEY + "\r"),
    ]
    payload = capture(
        proof,
        "p4-masked-key-setup",
        argv,
        answers,
        note="the two-key flow at 80x24 against two local stub endpoints: the provider list, "
             "the pre-filled endpoint, the model, and the key typed at the masked prompt",
        env_extra={"SKALDIR_HOME": str(home)},
        stop_at="what happens next",
    )
    payload["typed_key_in_capture"] = TYPED_KEY in bytes(
        (proof.outdir / "p4-masked-key-setup.raw").read_bytes()
    ).decode("utf-8", "replace")

    if env_file.exists():
        body = env_file.read_text(encoding="utf-8")
        payload["env_file"] = {
            "mode": f"{stat.S_IMODE(env_file.stat().st_mode):04o}",
            "names": [
                line.split("=", 1)[0]
                for line in body.splitlines()
                if "=" in line and not line.strip().startswith("#")
            ],
            "value_lengths": {
                line.split("=", 1)[0]: len(line.split("=", 1)[1])
                for line in body.splitlines()
                if "=" in line and not line.strip().startswith("#")
            },
            "typed_key_is_in_the_file": TYPED_KEY in body,
        }
    payload["config_written"] = config.exists()
    if config.exists():
        payload["config_names_no_value"] = TYPED_KEY not in config.read_text(encoding="utf-8")

    # p5 — ``--check`` from a FRESH process: re-validate, write nothing
    before = {
        "config": config.read_bytes() if config.exists() else b"",
        "env": env_file.read_bytes() if env_file.exists() else b"",
    }
    proc = subprocess.run(
        [str(ENTRY), "setup", "--check", "--config", str(config), "--runtime", str(home), "--json"],
        cwd=str(REPO),
        env=clean_env({"SKALDIR_HOME": str(home)}),
        capture_output=True,
        text=True,
        timeout=180,
    )
    after = {
        "config": config.read_bytes() if config.exists() else b"",
        "env": env_file.read_bytes() if env_file.exists() else b"",
    }
    try:
        document = json.loads(proc.stdout).get("data") or {}
    except ValueError:
        document = {}
    proof.record(
        "p5-check",
        {
            "exit": proc.returncode,
            "ok": bool(document.get("ok")),
            "model_echo": {
                role: ((document.get("roles") or {}).get(role) or {})
                .get("measure", {})
                .get("model_echo")
                for role in ("llm", "typesafe")
            },
            "measured_ok": {
                role: bool(((document.get("roles") or {}).get(role) or {}).get("ok"))
                for role in ("llm", "typesafe")
            },
            "credential_kinds": {
                role: ((document.get("key_sources") or {}).get(role) or {}).get("source")
                or ((document.get("key_sources") or {}).get(role) or {}).get("kind")
                for role in ("llm", "typesafe")
            },
            "wrote_nothing": before == after,
            "verdict": document.get("verdict", ""),
        },
    )


def plain_captures(proof: Proof, home: Path) -> None:
    """p6-p8: the fallback — NO_COLOR, TERM=dumb, and stderr to a file with a real tty.

    Every one of them picks row **13** (``llama.cpp``, the row whose pre-filled URL is the
    stub), accepts the endpoint the row supplied, and then ends at the model prompt with an
    EOF: the list was drawn as lines, the answers were read as lines, and the run refuses
    rather than inventing a model — nothing is written.
    """
    config = home / "plain" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    script = [
        ("which key", "\r"),              # Enter takes the pre-filled role (llm)
        ("provider (number or id)", "13\r"),  # a row number, on a list printed as lines
        ("endpoint", "\r"),               # the URL the row carried
        ("model", "\x04"),                # EOF: no model id is invented on the server's behalf
    ]

    capture(
        proof,
        "p6-plain-color",
        [str(ENTRY), "model", "--config", str(config)],
        script,
        note="NO_COLOR on a real terminal: the same rows printed as lines, the answer read "
             "as a line — no selection list and no cursor addressing",
        env_extra={"NO_COLOR": "1"},
    )
    capture(
        proof,
        "p7-plain-dumb",
        [str(ENTRY), "model", "--config", str(config)],
        script,
        note="TERM=dumb: no cursor addressing, so the questions are plain lines",
        env_extra={"TERM": "dumb"},
    )
    err_path = proof.outdir / "p8-plain-stderr-file.stderr.txt"
    capture(
        proof,
        "p8-plain-stderr-file",
        [str(ENTRY), "model", "--config", str(config)],
        script,
        note="stdin is a real terminal, stderr is a FILE: the rows go to the file as plain "
             "lines (the half-pipe case: `skaldir setup 2>log`)",
        stderr_to=err_path,
    )
    if err_path.exists():
        body = err_path.read_text(encoding="utf-8")
        proof.results["p8-plain-stderr-file"]["stderr_file"] = {
            "esc_bytes": body.count("\x1b"),
            "lines": len([line for line in body.splitlines() if line.strip()]),
            "shows_the_rows": "http://127.0.0.1:8080/v1" in body,
            "shows_the_label": "llama.cpp (local server) (local)" in body,
            "is_ascii_control_free": "\x1b[2K" not in body,
        }


def exit_code_captures(proof: Proof, home: Path) -> None:
    """p9-p10: a real pipe (nothing is asked) and the three exit codes.

    Runs **last** and against the two 401 endpoints, so "a wrong key" is measured on the same
    shape of endpoint the success case used — the only difference is what the server answers.
    """
    # p9 — stdin is a pipe: no question may be asked, and the missing flags are named
    proc = subprocess.run(
        [
            str(ENTRY), "setup", "--config", str(home / "pipe.yaml"),
            "--runtime", str(home), "--json",
        ],
        cwd=str(REPO),
        env=clean_env({"SKALDIR_HOME": str(home)}),
        input="",
        capture_output=True,
        text=True,
        timeout=120,
    )
    document: dict[str, Any] = {}
    try:
        document = json.loads(proc.stdout).get("data") or {}
    except ValueError:
        pass
    asked = "provider (number or id)" in proc.stderr or "which key" in proc.stderr
    proof.save(
        "p9-pipe-headless",
        (proc.stdout + proc.stderr).encode(),
        note="stdin is a pipe: `skaldir setup` asks nothing and names the flag it is missing",
    )
    proof.record(
        "p9-pipe-headless",
        {
            "exit": proc.returncode,
            "asked_a_question": asked,
            "stdout_esc_bytes": proc.stdout.count("\x1b"),
            "stdout_non_ascii": sum(1 for char in proc.stdout if ord(char) > 127),
            "missing_flags": {
                role: str(((document.get("roles") or {}).get(role) or {}).get("problem") or "")
                for role in ("llm", "typesafe")
            },
            "verdict": document.get("verdict", ""),
        },
    )

    # p10a — no key anywhere: the honest refusal, nothing written
    no_key_config = home / "no-key.yaml"
    model_nokey = subprocess.run(
        [str(ENTRY), "model", "--role", "llm", "--provider", "deepseek", "--no-input",
         "--config", str(no_key_config), "--json"],
        cwd=str(REPO), env=clean_env({"SKALDIR_HOME": str(home)}),
        capture_output=True, text=True, timeout=120,
    )
    setup_nokey = subprocess.run(
        [str(ENTRY), "setup", "--yes", "--config", str(no_key_config), "--runtime", str(home),
         "--json"],
        cwd=str(REPO), env=clean_env({"SKALDIR_HOME": str(home)}),
        capture_output=True, text=True, timeout=120,
    )

    # p10b — a wrong key: both endpoints answer 401, so both probes fail and the config is
    # never written (the key itself is kept in 0600 — that is the documented split)
    wrong = home / "wrong-key.yaml"
    wrong_env = home / "wrong.env"
    wrong_proc = subprocess.run(
        [str(ENTRY), "setup",
         "--llm-provider", "llamacpp", "--llm-model", "stub-llama", "--llm-key-stdin",
         "--jev-provider", "vllm", "--jev-model", "stub-jev", "--jev-key-stdin",
         "--config", str(wrong), "--env-file", str(wrong_env), "--runtime", str(home), "--json"],
        cwd=str(REPO), env=clean_env({"SKALDIR_HOME": str(home)}),
        input=f"{TYPED_KEY}\n{TYPED_KEY}\n", capture_output=True, text=True, timeout=300,
    )
    try:
        wrong_doc = json.loads(wrong_proc.stdout).get("data") or {}
    except ValueError:
        wrong_doc = {}
    success = proof.results.get("p4-masked-key-setup", {})

    proof.record(
        "p10-exit-codes",
        {
            "no_key": {
                "model_exit": model_nokey.returncode,
                "model_wrote": no_key_config.exists(),
                "setup_exit": setup_nokey.returncode,
                "error_code": (
                    json.loads(setup_nokey.stdout or "{}").get("error") or {}
                ).get("code"),
            },
            "wrong_key": {
                "exit": wrong_proc.returncode,
                "wrote": bool(wrong_doc.get("wrote")),
                "config_file_exists": wrong.exists(),
                "env_file": (wrong_doc.get("env_write") or {}).get("path"),
                "env_file_names": (wrong_doc.get("env_write") or {}).get("appended"),
                "ok": bool(wrong_doc.get("ok")),
                "verdict": wrong_doc.get("verdict", ""),
                "error_code": (
                    json.loads(wrong_proc.stdout or "{}").get("error") or {}
                ).get("code"),
            },
            "success": {
                "exit": success.get("exit"),
                "config_written": success.get("config_written"),
                "env_file": success.get("env_file"),
            },
        },
    )


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else REPO / "outputs" / "setup-interactive"
    home = Path("/tmp/skaldir-setup-interactive")
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)

    proof = Proof(outdir)
    # One process at a time, and the port is handed over between the stubs: the 401 endpoints
    # come last, so "a wrong key" is measured on the same shape of endpoint as the success case
    selector_captures(proof, home)      # needs no endpoint at all
    good = StubEndpoint(model="stub-llama", host="127.0.0.1", port=8080)
    jev = StubEndpoint(model="stub-jev", host="127.0.0.1", port=8000)
    try:
        setup_captures(proof, home)
        plain_captures(proof, home)
    finally:
        good.close()
        jev.close()
    time.sleep(0.3)
    refusing = StubEndpoint(model="stub-llama", host="127.0.0.1", port=8080, status=401)
    refusing_jev = StubEndpoint(model="stub-jev", host="127.0.0.1", port=8000, status=401)
    try:
        exit_code_captures(proof, home)
    finally:
        refusing.close()
        refusing_jev.close()
        proof.finish()

    print(
        json.dumps(
            {
                key: {k: v for k, v in value.items() if k != "screen"}
                for key, value in proof.results.items()
            },
            indent=2,
            default=str,
        )
    )
    print(f"\ncaptures in {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
