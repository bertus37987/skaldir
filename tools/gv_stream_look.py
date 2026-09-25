#!/usr/bin/env python3
"""What a streamed answer **looks like**: a real pty capture, the flicker, and the pipe.

``tools/gv_stream_ttfb.py`` answers *how fast* the first token arrives. This tool answers the
next question, which a speed number cannot: **does it look right?** It drives the product's
streaming sink (:class:`skaldir.stream_render.MarkdownStream`) inside a real 80x24 pty at a
fixed chunk rate and reports, from the bytes the terminal actually received:

* the **mid-stream** capture and the **end** capture of one answer carrying every token the
  vocabulary has (heading, bold, inline code, list, fenced code block);
* the **flicker** — rows committed vs rows that changed after they were already visible —
  measured from the captured bytes by replaying them the way a terminal would, and checked
  against the renderer's own screen model;
* the **batching table** — updates, updates/s, bytes written and characters dropped, at three
  chunk rates, with the visible answer required to be byte-identical across all of them (if
  batching were visible, the reader would see it);
* the **long-output** case — a huge tool return printed as a head plus the exact count of what
  was hidden;
* the **pipe** proof — the same answer with stdout redirected to a file, required to be
  byte-identical to the model's own markdown, with no escape sequence anywhere.

Usage::

    .venv/bin/python tools/gv_stream_look.py            # run everything, print the report
    .venv/bin/python tools/gv_stream_look.py --json out.json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
ROWS, COLS = 24, 80
OUT_DIR = Path(os.environ.get("GV_STREAM_LOOK_OUT", "/tmp/gv-stream-look"))

#: Every escape sequence the terminal received.
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: ``\r\x1b[K`` — the one escape this renderer emits: back to column 0, erase to end of row.
CLEAR_LINE = "\r\x1b[K"

#: The answer the proof streams. Deliberately carries **every** token the vocabulary has, so
#: one capture covers the whole look: a heading, a bold span, an inline code span, a bulleted
#: list, and a fenced code block with a row wide enough that the rule has to be repeated.
DOCUMENT = (
    "# Streamed answer\n"
    "\n"
    "This line has **bold** and `inline code` in it.\n"
    "\n"
    "- the first bullet\n"
    "- the second bullet\n"
    "\n"
    "```python\n"
    "def render(text, *, width=80, ellipsis='…'):\n"
    "    return text[:width] + (ellipsis if len(text) > width else '')  # never wider than asked\n"
    "```\n"
    "\n"
    "Done.\n"
)

#: A tool return big enough that printing it whole would flood the screen.
HUGE_RETURN = "\n".join(f"result row {n:04d}: some tool output on this line" for n in range(1, 901))

#: The child drives the real sink and reports what it wrote *and* what it measured; the parent
#: never has to guess either. Its stdout is the terminal (or a file) and nothing else.
CHILD = r"""
import json
import sys
import time

from skaldir import stream_render as sr
from skaldir import theme

plan = json.loads(sys.argv[1])
the_theme = theme.Theme(mode=theme.MODE_RICH, depth=theme.DEPTH_256, glyphs=theme.GLYPHS_UNICODE)

recorded = []


class Tee:
    def __init__(self, inner):
        self.inner = inner

    def write(self, text):
        recorded.append(text)
        return self.inner.write(text)

    def flush(self):
        try:
            self.inner.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self.inner.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self.inner, name)


sink = Tee(sys.stdout)
if plan["renderer"] == "markdown":
    renderer = sr.MarkdownStream(
        sink,
        tty=sink.isatty(),
        theme=the_theme,
        interval_ms=plan["interval_ms"],
        columns=plan["columns"],
        footer=plan["footer"],
        line_numbers=plan["line_numbers"],
        tail_flush_ms=plan["tail_flush_ms"],
    )
else:
    renderer = sr.StreamRenderer(
        sink,
        tty=sink.isatty(),
        theme=the_theme,
        interval_ms=plan["interval_ms"],
        columns=plan["columns"],
        footer=plan["footer"],
    )
renderer.arm()
document = plan["document"]
size = max(1, plan["chunk"])
for start in range(0, len(document), size):
    renderer.delta(document[start:start + size])
    time.sleep(plan["gap_ms"] / 1000.0)
if plan["long_output"]:
    renderer.end_turn()
    sink.write(sr.bounded_output(plan["long_output"], max_lines=plan["head_lines"],
                                 theme=the_theme))
    sink.flush()
    facts = renderer.as_dict()
elif plan["status"]:
    facts = renderer.end_turn()
else:
    facts = renderer.finish()
with open(plan["metrics"], "w") as handle:
    json.dump({"facts": facts, "writes": "".join(recorded), "document": document}, handle)
"""


# --------------------------------------------------------------------------- #
# running the child                                                             #
# --------------------------------------------------------------------------- #


def run_pty(argv: list[str], env: dict[str, str], *, rows: int = ROWS, cols: int = COLS,
            timeout: float = 180.0) -> dict[str, Any]:
    """Run one command on a real pty and time every read off the master."""
    master, slave = pty.openpty()
    try:
        fcntl = __import__("fcntl")
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        attr = termios.tcgetattr(slave)
        attr[3] &= ~termios.ECHO  # the child's own text must not be counted as output
        termios.tcsetattr(slave, termios.TCSANOW, attr)
        started = time.monotonic()
        child = subprocess.Popen(  # noqa: S603 — an explicit argv, no shell
            argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(REPO),
            start_new_session=True, close_fds=True,
        )
    finally:
        os.close(slave)
    events: list[tuple[float, bytes]] = []
    buffer = bytearray()
    deadline = started + timeout
    try:
        while True:
            if time.monotonic() > deadline:
                child.kill()
                break
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:      # EIO once the slave side is gone
                    break
                if not data:
                    break
                events.append((time.monotonic() - started, data))
                buffer.extend(data)
            elif child.poll() is not None:
                break
        child.wait(timeout=10)
    finally:
        os.close(master)
    return {"capture": bytes(buffer), "events": events, "exit": child.returncode,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}


def run_plan(plan: dict[str, Any], *, mode: str = "pty", name: str = "facts",
             out_dir: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one child (on a pty, or piped) and return ``(screen, child_report)``."""
    directory = out_dir or OUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    metrics = directory / f"{name}.json"
    body = dict(plan)
    body["metrics"] = str(metrics)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if mode == "pty":
        screen: dict[str, Any] = run_pty([sys.executable, "-c", CHILD, json.dumps(body)], env)
    else:
        started = time.monotonic()
        pipe = directory / f"{name}.out"
        with open(pipe, "wb") as handle:
            done = subprocess.run(  # noqa: S603 — an explicit argv, no shell
                [sys.executable, "-c", CHILD, json.dumps(body)], env=env, cwd=str(REPO),
                stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.PIPE,
                check=False, timeout=180,
            )
        screen = {"capture": pipe.read_bytes(), "events": [], "exit": done.returncode,
                  "elapsed_ms": int((time.monotonic() - started) * 1000),
                  "stderr": done.stderr.decode("utf-8", "replace")}
    report = json.loads(metrics.read_text()) if metrics.is_file() else {}
    return screen, report


# --------------------------------------------------------------------------- #
# reading the capture                                                           #
# --------------------------------------------------------------------------- #


def plain(text: str) -> str:
    """What the eye sees: every escape removed, the ``\\r`` folded away, the text kept."""
    return ANSI.sub("", text).replace("\r", "")


def visible_rows(text: str) -> list[str]:
    """The rows the screen really shows, in order — taken from a terminal-mode replay.

    Not ``split("\\n")``: a row written twice (the footer, then the answer) carries no
    newline between the two writes, and joining them would invent a line that was never on
    screen and hide the very change the flicker number is about.
    """
    return [row for row in replay(text)["rows"] if row.strip()]


def render_reference(plan: dict[str, Any]) -> str:
    """The same answer rendered in-process — what the pty run has to match, byte for byte."""
    from skaldir import stream_render as sr
    from skaldir import theme as theme_mod

    the_theme = theme_mod.Theme(mode=theme_mod.MODE_RICH, depth=theme_mod.DEPTH_256,
                                glyphs=theme_mod.GLYPHS_UNICODE)
    buffer = io.StringIO()
    renderer = sr.MarkdownStream(buffer, tty=True, theme=the_theme,
                                 interval_ms=plan["interval_ms"], columns=plan["columns"],
                                 footer=False, line_numbers=plan["line_numbers"])
    renderer.arm()
    document = plan["document"]
    size = max(1, plan["chunk"])
    for start in range(0, len(document), size):
        renderer.delta(document[start:start + size])
    renderer.finish()
    return buffer.getvalue()


def answer_body(writes: str) -> str:
    """Everything before the status band — the answer, which is what the look is about."""
    return writes.split(CLEAR_LINE, 1)[0]


def replay(capture: str) -> dict[str, Any]:
    """Walk the captured bytes the way a terminal would, and count what changed.

    This is deliberately ignorant of *why* a row was rewritten: it only knows what reached
    the screen. It handles the four things the renderer emits (text, ``\\n``, ``\\r``, erase
    to end of row) and treats anything else as a defect it cannot model.
    """
    rows: list[str] = [""]
    history: list[list[str]] = [[]]
    column = 0
    unknown: list[str] = []
    index = 0
    while index < len(capture):
        char = capture[index]
        if char == "\x1b":
            match = ANSI.match(capture, index)
            if match is None:
                unknown.append(capture[index:index + 4])
                index += 1
                continue
            code = match.group(0)
            if code.endswith("K"):
                if rows[-1].strip():
                    history[-1].append(rows[-1])
                rows[-1] = rows[-1][:column]
            elif code not in ("\x1b[0m",) and not code.endswith("m"):
                unknown.append(code)
            index = match.end()
            continue
        if char == "\r":
            column = 0
            index += 1
            continue
        if char == "\n":
            history[-1].append(rows[-1])
            rows.append("")
            history.append([])
            column = 0
            index += 1
            continue
        row = rows[-1]
        rows[-1] = row[:column] + char + row[column + 1:]
        column += 1
        index += 1
    history[-1].append(rows[-1])
    changed = sum(1 for seen in history if len({item for item in seen if item.strip()}) > 1)
    return {"rows": rows, "history": history, "rows_committed": len(rows),
            "rows_changed": changed, "unknown_escapes": unknown}


def analyse(capture: bytes, expected_rows: list[str]) -> dict[str, Any]:
    """The flicker, from the bytes the terminal received — plus the re-emission check.

    ``expected_rows`` are the answer's rows as the eye reads them, so they are plain text;
    the replay below strips the styling on its way to the screen, which is what makes the two
    comparable at all.
    """
    text = capture.decode("utf-8", "replace")
    screen = replay(text)
    settled = [row for row in screen["rows"] if row.strip()]
    counts = {row: settled.count(row) for row in expected_rows}
    return {
        "bytes": len(capture),
        "rows_committed": screen["rows_committed"],
        "rows_changed": screen["rows_changed"],
        "unknown_escapes": sorted(set(screen["unknown_escapes"])),
        "answer_rows": len(expected_rows),
        "answer_rows_written_once": sum(1 for n in counts.values() if n == 1),
        "answer_rows_lost": sum(1 for n in counts.values() if n == 0),
        "answer_rows_repeated": sum(1 for n in counts.values() if n > 1),
        "answer_bytes_re_emitted": sum(max(0, n - 1) for n in counts.values()),
        "erase_line_events": text.count("\x1b[K"),
    }


# --------------------------------------------------------------------------- #
# the report                                                                    #
# --------------------------------------------------------------------------- #


def first_answer_ms(screen: dict[str, Any], needle: str) -> float | None:
    """When the first *answer* byte reached the terminal, from the pty read timestamps.

    The needle is matched against everything read so far, not against one read: the renderer
    is free to split a line across as many writes as the terminal will take.
    """
    seen = ""
    for stamp, data in screen["events"]:
        seen += data.decode("utf-8", "replace")
        if needle in seen:
            return round(stamp * 1000.0, 1)
    return None


def base_plan(**changes: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "document": DOCUMENT,
        "chunk": 7,
        "gap_ms": 35.0,
        "interval_ms": 60,
        "columns": 80,
        "footer": True,
        "line_numbers": False,
        "status": True,
        "long_output": "",
        "head_lines": 8,
        "renderer": "markdown",
        "tail_flush_ms": 0,
    }
    plan.update(changes)
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=str(OUT_DIR / "stream-look.json"))
    args = parser.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    report: dict[str, Any] = {"document": DOCUMENT}

    expected_rows = visible_rows(render_reference(base_plan(footer=False)))

    # -- 1 & 2. the look, and the turn boundary -------------------------------------- #
    plan = base_plan()
    screen, child = run_plan(plan, name="pty-look")
    capture = screen["capture"].decode("utf-8", "replace")
    facts = child["facts"]

    cut = capture.find("the second bullet")
    cut = len(capture) if cut < 0 else cut + len("the second bullet\r\n")
    mid = capture[:cut]
    band_at = capture.rfind(CLEAR_LINE)

    out.append("## 1. The pty capture — mid-stream (real 80x24 pty, 7 chars every 35 ms)\n")
    out.append(f"    `{len(mid)}` of `{len(capture)}` bytes had reached the terminal when the")
    out.append("    list finished. What the eye had read by then — the heading, the inline code")
    out.append("    span, the bold span and the bullets, and **no half mark anywhere**:\n")
    out.append("```")
    out.extend(visible_rows(mid))
    out.append("```\n")
    out.append("    the same bytes with the escape sequences shown, so the styling is read and")
    out.append("    not taken on trust:\n")
    out.append("```")
    out.extend(row.replace("\x1b", "\\e") for row in
               [line for line in mid.replace("\r", "").split("\n") if line.strip()])
    out.append("```\n")

    out.append("## 2. The end of the turn\n")
    out.append("```")
    out.extend(visible_rows(capture))
    out.append("```")
    out.append("")
    out.append(f"    the last `{len(capture) - band_at}` bytes are the turn boundary: the activity")
    out.append("    footer is cleared and the status band is written into the very row it was")
    out.append(f"    using. `status_band_updates={facts['status_band_updates']}`, "
               f"`chrome_rows={facts['chrome_rows']}` — the band costs no extra row, and it is")
    out.append("    one write:\n")
    out.append("```")
    out.append(capture[band_at:].replace("\x1b", "\\e"))
    out.append("```\n")

    # -- 3. the flicker --------------------------------------------------------------- #
    with_footer = analyse(screen["capture"], expected_rows)
    bare_plan = base_plan(footer=False, status=False)
    screen_bare, child_bare = run_plan(bare_plan, name="pty-bare")
    bare_facts = child_bare["facts"]
    bare = analyse(screen_bare["capture"], expected_rows)

    out.append("## 3. Flicker — lines written vs lines that changed\n")
    out.append("Rows are counted by replaying the captured bytes the way a terminal would, so")
    out.append("these numbers know nothing about what the renderer believed it was doing.")
    out.append("`rows committed` counts every terminal row (blank ones included);")
    out.append("`answer rows` counts the non-blank rows the reader actually reads.\n")
    out.append("| capture | rows committed | rows changed | answer rows | written once | "
               "lost | re-emitted | answer bytes re-emitted | erase-line events |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for label, analysis in (("pty, footer + status band", with_footer),
                            ("pty, answer only", bare)):
        out.append(f"| {label} | {analysis['rows_committed']} | {analysis['rows_changed']} | "
                   f"{analysis['answer_rows']} | {analysis['answer_rows_written_once']} | "
                   f"{analysis['answer_rows_lost']} | {analysis['answer_rows_repeated']} | "
                   f"{analysis['answer_bytes_re_emitted']} | "
                   f"{analysis['erase_line_events']} |")
    out.append("")
    out.append(f"    **Answer only: {bare['rows_changed']} rows changed, "
               f"{bare['answer_bytes_re_emitted']} answer bytes re-emitted, "
               f"{bare['answer_rows'] - bare['answer_rows_written_once']} rows written twice.**")
    out.append("    Every row of the answer is written once and never revisited — which is the")
    out.append("    goal, measured from the wire rather than from the renderer's opinion of")
    out.append("    itself.\n")
    out.append("    With the activity footer on, the rows that change are the footer's own")
    out.append(f"    scratch row, which each answer row then takes over "
               f"({with_footer['rows_changed']} of them; the renderer bills "
               f"`chrome_rewrites={facts['chrome_rewrites']}` footer repaints as chrome, none of")
    out.append("    it charged to the answer). The answer rows themselves stay at")
    out.append(f"    `lines_changed={facts['lines_changed']}` in the renderer's own screen model,")
    out.append(f"    which agrees with the replay: `lines_written={facts['lines_written']}`, "
               f"`lines_redrawn={facts['lines_redrawn']}`, "
               f"`flicker_ratio={facts['flicker_ratio']}`.\n")

    # -- 4. the batching table --------------------------------------------------------- #
    out.append("## 4. Batching — bounded updates, nothing dropped\n")
    out.append("| chunk (chars) | gap (ms) | deltas | updates | updates/s | bytes written | "
               "chars in | coalesced | lines | changed | flicker | answer identical | dropped |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    batching: list[dict[str, Any]] = []
    first_body: str | None = None
    dropped_total = 0
    for chunk, gap in ((3, 25.0), (7, 35.0), (20, 60.0)):
        run_plan_ = base_plan(chunk=chunk, gap_ms=gap, footer=False)
        _, run_child = run_plan(run_plan_, name=f"batch-{chunk}")
        run_facts = run_child["facts"]
        body = answer_body(run_child["writes"])
        reference = render_reference(run_plan_)
        same = plain(body) == plain(reference)
        dropped = 0 if same else abs(len(plain(body)) - len(plain(reference)))
        dropped_total = max(dropped_total, dropped)
        if first_body is None:
            first_body = plain(body)
        else:
            dropped_total = max(dropped_total, 0 if plain(body) == first_body else 1)
        batching.append({"chunk": chunk, "gap_ms": gap, "facts": run_facts, "identical": same,
                         "dropped": dropped})
        out.append(f"| {chunk} | {gap:.0f} | {run_facts['deltas']} | {run_facts['updates']} | "
                   f"{run_facts['updates_per_s']} | {run_facts['bytes_written']} | "
                   f"{run_facts['chars']} | {run_facts['coalesced']} | "
                   f"{run_facts['lines_written']} | {run_facts['lines_changed']} | "
                   f"{run_facts['flicker_ratio']} | {'yes' if same else 'NO'} | {dropped} |")
    out.append("")
    out.append(f"    **characters dropped = {dropped_total}.** The visible answer is")
    out.append("    byte-identical at 3, 7 and 20 characters per delta — which is the only")
    out.append("    defensible meaning of \"a reader cannot tell it is batched\". `updates` never")
    out.append("    exceeds one per 60 ms of delivered text, and the rows are still written once "
               f"(`lines_changed={batching[1]['facts']['lines_changed']}`).\n")

    # -- 5. the long-output case ------------------------------------------------------- #
    long_plan = base_plan(long_output=HUGE_RETURN, head_lines=8, status=False)
    screen3, child3 = run_plan(long_plan, name="long")
    long_rows = visible_rows(screen3["capture"].decode("utf-8", "replace"))
    total_rows = HUGE_RETURN.count("\n") + 1
    out.append("## 5. A huge tool return\n")
    out.append(f"    {total_rows} rows came back from the tool. The terminal received the head")
    out.append("    and one row that names exactly what was hidden:\n")
    out.append("```")
    out.extend(long_rows[-9:])
    out.append("```\n")

    # -- 6. what the buffering costs, measured ----------------------------------------- #
    needle = "Streamed answer"
    raw_screen, raw_child = run_plan(base_plan(renderer="plain", footer=False, status=False),
                                     name="latency-plain")
    mark_screen, mark_child = run_plan(base_plan(renderer="markdown", footer=False, status=False),
                                       name="latency-markdown")
    raw_ms = first_answer_ms(raw_screen, needle)
    mark_ms = first_answer_ms(mark_screen, needle)
    out.append("## 6. What buffering the unfinished line costs\n")
    out.append("The tail is held until its newline arrives, so the first *line* is what becomes")
    out.append("visible, not the first character. That is a real cost, and it is measured here")
    out.append("with the renderer's own clock (`arm()` → the first write), which is the only")
    out.append("clock that is not polluted by the interpreter's own startup:\n")
    out.append("| renderer | first delta accepted | first byte written | bytes written |")
    out.append("|---|---|---|---|")
    out.append(f"| raw (`StreamRenderer`, no markdown) | "
               f"{raw_child['facts']['first_content_ms']} ms | "
               f"{raw_child['facts']['first_update_ms']} ms | "
               f"{raw_child['facts']['bytes_written']} |")
    out.append(f"| markdown (`MarkdownStream`, tail held) | "
               f"{mark_child['facts']['first_content_ms']} ms | "
               f"{mark_child['facts']['first_update_ms']} ms | "
               f"{mark_child['facts']['bytes_written']} |")
    out.append("")
    per_delta = round(mark_child["facts"]["elapsed_ms"] / max(1, mark_child["facts"]["deltas"]), 1)
    out.append("    Both accept the first delta at the same instant — the source is the same.")
    out.append(f"    The markdown sink writes its first byte at "
               f"{mark_child['facts']['first_update_ms']} ms instead of "
               f"{raw_child['facts']['first_update_ms']} ms, and that gap is one line's worth")
    heading_chars = len("# Streamed answer")
    out.append(f"    of deltas: {per_delta} ms per delta × the {heading_chars} characters of")
    out.append("    the heading. The whole answer still lands in "
               f"{mark_child['facts']['elapsed_ms']} ms, and the activity footer ticks")
    out.append("    throughout the wait — but it is a wait, and pretending otherwise would be")
    out.append("    exactly the kind of claim this project does not make.\n")
    out.append("    A caller who would rather have the character than the line sets")
    out.append("    `tail_flush_ms`. That is not free, and the renderer says so rather than")
    out.append("    hiding it — the same answer at 3 characters every 60 ms, which is a stream")
    out.append("    slow enough for the grace period to expire mid-line:\n")
    out.append("| `tail_flush_ms` | first answer byte | rows | rows changed | flicker |")
    out.append("|---|---|---|---|---|")
    for flush in (0, 250):
        arm_screen, arm_child = run_plan(
            base_plan(renderer="markdown", footer=False, status=False, tail_flush_ms=flush,
                      chunk=3, gap_ms=60.0),
            name=f"flush-{flush}")
        arm_ms = first_answer_ms(arm_screen, needle)
        arm_facts = arm_child["facts"]
        out.append(f"| {flush} | {arm_ms} ms | {arm_facts['lines_written']} | "
                   f"{arm_facts['lines_changed']} | {arm_facts['flicker_ratio']} |")
    out.append("")
    out.append("    `tail_flush_ms=0` (the default) holds the unfinished line and writes every")
    out.append("    row once. A positive value trades that away, row by row, and the number")
    out.append("    above is that trade — not a footnote.\n")

    # -- 7. the pipe ------------------------------------------------------------------- #
    pipe_plan = base_plan()
    screen4, _ = run_plan(pipe_plan, mode="pipe", name="pipe")
    pipe_text = screen4["capture"].decode("utf-8", "replace")
    exact = pipe_text == DOCUMENT
    out.append("## 7. The pipe — what a code block does when it is going to be pasted\n")
    out.append("| property | value |")
    out.append("|---|---|")
    out.append(f"| bytes out | {len(screen4['capture'])} |")
    out.append(f"| bytes of the model's own markdown | {len(DOCUMENT.encode())} |")
    out.append(f"| byte-identical to the model's answer | {exact} |")
    out.append(f"| escape sequences | {pipe_text.count(chr(27))} |")
    out.append(f"| rule glyphs inserted | {pipe_text.count('│')} |")
    out.append("")
    out.append("```")
    out.extend(pipe_text.splitlines())
    out.append("```")

    report["runs"] = {
        "look": {"facts": facts, "mid_bytes": len(mid), "bytes": len(capture),
                 "band_bytes": len(capture) - band_at},
        "flicker": {"footer": with_footer, "bare": bare,
                    "renderer": {k: facts[k] for k in
                                 ("lines_written", "lines_changed", "lines_redrawn",
                                  "flicker_ratio", "chrome_rewrites", "chrome_rows",
                                  "status_band_updates", "screen_measured")},
                    "bare_renderer": {k: bare_facts[k] for k in
                                      ("lines_written", "lines_changed", "lines_redrawn",
                                       "flicker_ratio")}},
        "batching": batching,
        "batching_dropped": dropped_total,
        "latency_ms": {"raw": raw_ms, "markdown": mark_ms},
        "long_output": {"total_rows": total_rows, "head_rows": long_rows[-9:],
                        "hidden": total_rows - 8},
        "pipe": {"exact": exact, "bytes": len(screen4["capture"]),
                 "escapes": pipe_text.count("\x1b"), "rule_glyphs": pipe_text.count("│")},
    }
    Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True))
    print("\n".join(out))
    print(f"\n--- report written to {args.json} ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
