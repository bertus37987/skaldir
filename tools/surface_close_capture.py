"""SURFACE-CLOSE proof: the one picker, both framed paths, and the fallback.

This is the *closing* capture set. It drives the product's own entry point
(``.venv/bin/skaldir``) on a real 80x24 pty (``TIOCSWINSZ``), types at it after waiting for
the product's **own words** (never a sleep), and saves the raw byte stream, a virtual-screen
replay and one ``summary.json``.

Scenarios
    s1-model-widget         ``skaldir model`` -> the shared widget (``skaldir.picker.select``):
                            header with its row count, <= 10 rows, the ``… N more`` row, the
                            key hint; arrows move, typing narrows, Esc cancels
    s2-model-widget-ctrlc   ``Ctrl-C`` at the same question: the *question* is cancelled and
                            the process is not killed (exit 3, nothing written)
    s3-setup-widget         ``skaldir setup`` -> the provider question through the widget
    s4-plain-dumb           ``TERM=dumb`` on a real terminal: the numbered fallback, 0 escapes
    s5-plain-pipe           stdin a pipe: nothing is asked at all (headless)
    s6-chat-sessions-ctrlc  the real chat: ``/sessions`` -> the framed picker -> ``Ctrl-C``
                            cancels the pick only, the session and the frame survive
    s7-chat-model-ctrlc     the real chat: ``/model`` -> ``select_in_frame`` -> ``Ctrl-C``
                            cancels the pick only, the session and the frame survive
    s8-surface              the taught five lines, ``unregistered == []``, the inventory

Usage:  .venv/bin/python tools/surface_close_capture.py [outdir]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from setup_capture import ENTRY, Proof, Session, capture, clean_env  # noqa: E402

DOCS = Path.home() / "Documents" / "Hermes_Skaldir_Agent_Docs" / "MVP"
DEFAULT_OUT = DOCS / "artifacts-89-cli-surface"

TITLES = ["alpha the first session", "bravo the second session",
          "charlie the third session", "delta the fourth session",
          "echo the fifth session", "foxtrot the sixth session",
          "golf the seventh session", "hotel the eighth session",
          "india the ninth session", "juliet the tenth session",
          "kilo the eleventh session", "lima the twelfth session"]


def seed(home: Path) -> list[str]:
    """Real sessions, through the real command (never a store poke)."""
    ids: list[str] = []
    for title in TITLES:
        proc = subprocess.run(
            [str(ENTRY), "session", "new", "--title", title, "--json"],
            cwd=str(REPO), env=clean_env({"SKALDIR_HOME": str(home)}),
            capture_output=True, text=True, timeout=120,
        )
        try:
            payload = json.loads(proc.stdout)
            created = ((payload.get("data") or {}).get("session") or {})
            ids.append(str(created.get("session_id") or ""))
        except ValueError:
            ids.append("")
    return [item for item in ids if item]


def run(proof: Proof, home: Path) -> None:
    skaldir = str(ENTRY)
    env = {"SKALDIR_HOME": str(home)}

    # -- 1. the shared widget, on a real terminal ------------------------------- #
    payload = capture(
        proof, "s1-model-widget", [skaldir, "model"],
        [
            ("which key", ""),              # the role list is up
            ("", "\x1b[B"),                 # ↓ moves the pointer
            ("", "\r"),                     # select the LLM provider role
            ("provider", ""),               # the 16 catalog rows, base URLs and all
            ("", "\x1b[B\x1b[B"),           # ↓↓
            ("", "deep"),                   # type to narrow: the list becomes DeepSeek
            ("", "\x1b"),                   # Esc: cancel the question
        ],
        env_extra=env,
        note="`skaldir model` asks through `skaldir.picker.select`: the header counts the "
             "rows, at most 10 rows are drawn with a `… N more` row under them, ↑/↓ move, "
             "typing narrows the list to the first match, and Esc cancels the question",
    )
    body = (proof.outdir / "s1-model-widget.raw").read_bytes().decode("utf-8", "replace")
    payload["widget_header"] = "(16)" in body
    payload["more_row"] = "… 6 more" in body
    payload["typed_filter_narrowed"] = 'filter "deep"' in body and "6 DeepSeek" in body
    payload["cancelled_nothing_written"] = payload["exit"] == 3

    # -- 2. Ctrl-C at the same question ----------------------------------------- #
    payload = capture(
        proof, "s2-model-widget-ctrlc", [skaldir, "model"],
        [
            ("which key", ""),
            ("", "\r"),
            ("provider", ""),
            ("", "\x03"),                  # ^C
        ],
        env_extra=env,
        note="Ctrl-C inside the shared widget cancels the *question*: the process is not "
             "killed (exit 3 = blocked, refused: nothing was written), and the terminal is "
             "restored",
    )
    payload["cancelled-the-question"] = payload["exit"] == 3
    rows = [
        line for line in payload["screen"].splitlines()
        if re.match(r"^[ ❯]\s*\d+ \S", line)
    ]
    payload["rows_drawn_at_once"] = len(rows)
    payload["more_row"] = "… 6 more" in payload["screen"]

    # -- 3. skaldir setup asks through the same widget -------------------------- #
    payload = capture(
        proof, "s3-setup-widget", [skaldir, "setup"],
        [
            ("provider", ""),
            ("", "\x03"),
        ],
        env_extra=env,
        note="`skaldir setup`'s provider question is the same widget: the catalog rows with "
             "their base URLs, and Ctrl-C cancels the question without writing anything",
    )
    payload["cancelled_nothing_written"] = payload["exit"] == 3

    # -- 4. the plain fallback on a real terminal ------------------------------- #
    payload = capture(
        proof, "s4-plain-dumb", [skaldir, "model"],
        [
            ("which key", ""),
            ("", "1\r"),
            ("provider", ""),
            ("", "1\r"),
            ("model", ""),
            ("", "q\r"),                   # a non-number: the model question is left empty
        ],
        env_extra={**env, "TERM": "dumb"},
        note="TERM=dumb: the same rows as lines, the answer read as a line — zero escape "
             "bytes and zero non-ASCII from the selector",
    )
    payload["zero_escapes"] = payload["esc_bytes"] == 0
    plain = (proof.outdir / "s4-plain-dumb.raw").read_bytes().decode("utf-8", "replace")
    payload["plain_rows"] = "  1 OpenAI" in plain
    payload["endpoint_on_the_line"] = "https://api.openai.com/v1" in plain
    payload["no_mark_no_block"] = "\x1b[2K" not in plain and "❯" not in plain

    # -- 5. a pipe asks nothing ------------------------------------------------- #
    proc = subprocess.run(
        [skaldir, "setup", "--json"], cwd=str(REPO), input="",
        env=clean_env(env), capture_output=True, text=True, timeout=180,
    )
    (proof.outdir / "s5-pipe-headless.txt").write_text(
        proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8"
    )
    proof.record("s5-plain-pipe", {
        "exit": proc.returncode,
        "asked_a_question": "provider (number" in proc.stdout + proc.stderr
                            or "pick a number" in proc.stdout + proc.stderr,
        "esc": proc.stdout.count("\x1b") + proc.stderr.count("\x1b"),
        "head": (proc.stdout + proc.stderr)[:400],
    })

    # -- 6. the chat's framed picker: /sessions, then Ctrl-C -------------------- #
    session = Session([skaldir, "chat", "--no-probe"], env_extra=env)
    session.until("─", seconds=30)
    session.type_line("/sessions")
    session.until("the twelfth session", seconds=10)
    opened = bytes(session.log)
    session.send("\x03")                       # ^C inside the picker
    session.until("nothing selected (ctrl-c)", seconds=8)
    after = bytes(session.log)
    # the session is still a session: it answers another command, then leaves cleanly
    session.type_line("/sessions")
    alive = session.until("the twelfth session", seconds=10)
    session.send("\x1b")
    code = session.close(quit_keys="/quit\r")
    whole = bytes(session.log)
    proof.save("s6-chat-sessions-ctrlc", whole,
               note="the real chat: /sessions opens the framed picker; Ctrl-C cancels the "
                    "pick (`nothing selected (ctrl-c)`), the frame is redrawn and the "
                    "session answers /sessions again before /quit")
    proof.record("s6-chat-sessions-ctrlc", {
        "exit": code,
        "picker_opened": b"the twelfth session" in opened and "❯".encode() in opened,
        "ctrl_c_cancelled_the_pick": b"nothing selected (ctrl-c)" in after,
        "session_alive_after_ctrl_c": bool(alive) and code == 0,
        "tail": whole.decode("utf-8", "replace")[-1200:],
    })

    # -- 7. the setup path's framed picker: /model in the chat, then Ctrl-C ------ #
    session = Session([skaldir, "chat", "--no-probe"], env_extra=env)
    session.until("─", seconds=30)
    session.type_line("/model")
    session.until("which key", seconds=10)
    opened = bytes(session.log)
    session.send("\x03")
    session.until("nothing selected", seconds=8)
    after = bytes(session.log)
    session.type_line("/sessions")
    alive = session.until("the twelfth session", seconds=10)
    session.send("\x1b")
    code = session.close(quit_keys="/quit\r")
    whole = bytes(session.log)
    proof.save("s7-chat-model-ctrlc", whole,
               note="/model inside the chat drives `model_picker.select_in_frame`; Ctrl-C "
                    "cancels the pick (the run is not killed), the frame comes back and the "
                    "session answers /sessions again before /quit")
    proof.record("s7-chat-model-ctrlc", {
        "exit": code,
        "picker_opened": b"which key" in opened,
        "ctrl_c_cancelled_the_pick": b"nothing selected" in after,
        "session_alive_after_ctrl_c": bool(alive) and code == 0,
        "tail": whole.decode("utf-8", "replace")[-1200:],
    })

    # -- 8. the surface itself -------------------------------------------------- #
    help_proc = subprocess.run([skaldir, "-h"], cwd=str(REPO), env=clean_env(env),
                               capture_output=True, text=True, timeout=120)
    dump = subprocess.run([skaldir, "contract", "dump", "--json"], cwd=str(REPO),
                          env=clean_env(env), capture_output=True, text=True, timeout=180)
    (proof.outdir / "s8-help.txt").write_text(help_proc.stdout, encoding="utf-8")
    (proof.outdir / "s8-contract.json").write_text(dump.stdout, encoding="utf-8")
    data: dict[str, Any] = {}
    try:
        data = (json.loads(dump.stdout).get("data") or {})
    except ValueError:
        data = {}
    taught: list[str] = []
    lines = help_proc.stdout.splitlines()
    start = next((index for index, line in enumerate(lines) if "a session is taught" in line), -1)
    if start >= 0:
        for line in lines[start + 1:]:
            if not line.startswith("    ") or not line.strip():
                break
            taught.append(line)
    root_count = data.get("root_commands")
    leaf_count = data.get("leaf_commands")
    if not isinstance(root_count, int):
        root_count = len(root_count or [])
    if not isinstance(leaf_count, int):
        leaf_count = len(leaf_count or [])
    proof.record("s8-surface", {
        "help_exit": help_proc.returncode,
        "taught_lines": len(taught),
        "taught": taught,
        "roots": root_count,
        "leaves": leaf_count,
        "unregistered": data.get("unregistered"),
        "slice_path": str(DEFAULT_OUT),
    })


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    home = Path("/tmp/surface-close-home")
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)
    proof = Proof(outdir)
    ids = seed(home)
    proof.record("seeded", {"count": len(ids), "ids": ids[:3]})
    run(proof, home)
    proof.finish()
    summary = json.dumps(proof.results, indent=1, ensure_ascii=False, default=str)
    print(summary[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
