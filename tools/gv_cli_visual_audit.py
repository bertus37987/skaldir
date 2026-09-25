#!/usr/bin/env python3
"""Visual audit harness for the Skaldir CLI (`godsv`/`godsvoice`).

Why this exists
---------------
`script` (util-linux) is not installed on this machine, and a screenshot alone is not
evidence that can be diffed. This tool drives the real CLI inside a **real pty** of an
**exact size**, so a TTY-only code path (colour, logo banner, tables) can be captured
byte-for-byte and re-inspected later.

What it emits, per scenario
---------------------------
* ``<name>.ansi``  — the raw byte stream the terminal received (escape codes intact)
* ``<name>.txt``   — the same stream with ANSI stripped (what a human actually reads)
* ``<name>.meta.json`` — exit code, terminal size, SGR run count, overflow diagnostics
* ``<name>.png``   — a faithful *text rendering* of ``.txt`` (ImageMagick), when available

It is honest about what it is: the PNGs are rendered text, not photographs of a screen.
Real desktop screenshots are taken separately (computer-use) and named ``desktop-*``.

Usage
-----
    python3 tools/gv_cli_visual_audit.py --out outputs/cli-visual
    python3 tools/gv_cli_visual_audit.py --only banner,contract-list --cols 80 --rows 24
    python3 tools/gv_cli_visual_audit.py --list

No sudo, no network, read-only against the runtime (except the one deliberate error case,
which is expected to fail).
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VENV_PY = REPO / ".venv" / "bin" / "python"
REAL_RUNTIME = Path("/home/willy/.hermes/godsv")

#: Set by --src / --python. A *pinned snapshot* of the tree is the only way to get a
#: reproducible visual audit while sibling waves edit `src/` underneath us (measured
#: 2026-09-18: a `src/godsv` → `src/skaldir` rename landed mid-audit and the module
#: entry point stopped importing).
SRC_DIR: Path = REPO / "src"
PYTHON_BIN: Path = VENV_PY
MODULE_ROOT_BASE = REPO
#: The package directory name inside SRC_DIR (`godsv` before the rename, `skaldir` after).
PACKAGE = "godsv"

#: ANSI/CSI + OSC removal. Deliberately conservative: only real escape sequences go.
_ANSI = re.compile(
    r"""
    \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)   # OSC ... BEL/ST
  | \x1b[@-Z\\-_]                       # Fe escape (incl. ESC c, ESC M)
  | \x1b\[[0-?]*[ -/]*[@-~]             # CSI
  | \x1b[()][A-Za-z0-9]                 # charset select
  | \x1b[=>]                            # keypad modes
    """,
    re.VERBOSE,
)
_SGR = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def sgr_runs(text: str) -> list[str]:
    return _SGR.findall(text)


def visual_len(line: str) -> int:
    """Display width of a stripped line, counting East-Asian wide chars as 2."""
    import unicodedata

    width = 0
    for ch in line:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


@dataclass
class Scenario:
    name: str
    argv: list[str]
    description: str
    cols: int = 100
    rows: int = 30
    #: bytes written to the pty after start (an interactive session needs input)
    feed: list[tuple[float, bytes]] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: `python -m godsv.repl` style: run the module, not the console script
    module: str | None = None
    #: run this snippet with `python -c` instead of a module
    script: str | None = None
    expect_exit: int | None = None


def scenarios() -> list[Scenario]:
    return [
        Scenario(
            name="banner-100x30",
            argv=[],
            description="bare call on a TTY — logo art + welcome + hint sections",
        ),
        Scenario(
            name="banner-80x24",
            argv=[],
            description="bare call at the classic 80x24 — does the art still read?",
            cols=80,
            rows=24,
        ),
        Scenario(
            name="help-80x24",
            argv=["--help"],
            description="the root command list at 80x24 — wrapping behaviour",
            cols=80,
            rows=24,
        ),
        Scenario(
            name="contract-list-100x30",
            argv=["contract", "list"],
            description="table-heavy command: every leaf command with layer/json/rw",
        ),
        Scenario(
            name="contract-list-80x24",
            argv=["contract", "list"],
            description="the same table narrowed — alignment under pressure",
            cols=80,
            rows=24,
        ),
        Scenario(
            name="error-unknown-command",
            argv=["frobnicate"],
            description="unknown command — usage error path, exit 1",
            cols=80,
            rows=24,
            expect_exit=1,
        ),
        Scenario(
            name="error-not-found-json",
            argv=["tools", "show", "definitely-not-a-tool", "--json"],
            description="machine path: one envelope on stdout, exit 5 (not found)",
            expect_exit=5,
        ),
        Scenario(
            name="selftest-100x30",
            argv=["selftest"],
            description="the honest layer report as a human table",
        ),
        Scenario(
            name="plain-pipe",
            argv=["contract", "list"],
            description="stdout is a pipe, not a TTY — must be pure ASCII, zero escapes",
            cols=100,
            rows=30,
            env={"GODSV_FORCE_PIPE": "1"},
        ),
        Scenario(
            name="shell-palette",
            argv=[],
            module="godsv.repl",
            description="interactive shell: prompt, then `/` palette (fed keystrokes)",
            feed=[(1.6, b"/"), (1.2, b"\r"), (1.2, b"/quit\r")],
        ),
        Scenario(
            name="shell-overlay",
            argv=[],
            module="godsv.repl",
            description="interactive shell: `?` help overlay (fed keystrokes)",
            feed=[(1.8, b"?"), (1.6, b"\x1b"), (1.4, b"/quit\r")],
        ),
        Scenario(
            name="palette-100x40",
            argv=[],
            description="the `/` palette rendered by slash.render() over the REAL parser "
                        "tree and the REAL selftest reachability (outside the TUI, so it is "
                        "deterministic; the TUI draws the same string)",
            script=(
                "from godsv import cli_contract\n"
                "from godsv.cli import build_parser\n"
                "from godsv.complete import build_tree\n"
                "from godsv.slash import entries, render\n"
                "parser = build_parser()\n"
                "tree = build_tree(parser)\n"
                "report = cli_contract.run_selftest(None)\n"
                "reach = {r['layer']: bool(r.get('ok')) for r in report.get('layers', [])}\n"
                "print(render(entries(tree, reachable=reach, report=report), width=96))\n"
            ),
        ),
    ]


def clean_env(sc: Scenario) -> dict[str, str]:
    env = dict(os.environ)
    # The measured traps: a leaked PYTHONPATH runs a *foreign* checkout, and a leaked
    # GODSV_HOME/HERMES_HOME measures a sandbox nobody ships.
    for var in ("PYTHONPATH", "GODSV_HOME", "GODSV_FORMAT", "NO_COLOR", "GODSV_COLOR",
                "GODSV_MODE", "GODSV_LLM"):
        env.pop(var, None)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["GODSV_HOME"] = str(REAL_RUNTIME)
    env["HERMES_HOME"] = "/home/willy/.hermes"
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["LANG"] = "en_US.UTF-8"
    env.update(sc.env)
    return env


def run_in_pty(sc: Scenario, timeout: float = 25.0) -> tuple[bytes, int]:
    """Run the scenario inside a real pty of exactly sc.cols x sc.rows."""
    module = sc.module.replace("godsv.", f"{PACKAGE}.") if sc.module else PACKAGE
    if sc.script:
        argv = [str(PYTHON_BIN), "-c", sc.script]
    else:
        argv = [str(PYTHON_BIN), "-m", module, *sc.argv]
    env = clean_env(sc)

    if sc.env.get("GODSV_FORCE_PIPE"):
        # a pipe is not a TTY: run it with a pipe and hand the bytes back
        proc = subprocess.run(argv, cwd=str(MODULE_ROOT_BASE), env=env, capture_output=True)
        blob = proc.stdout + proc.stderr
        return blob, proc.returncode

    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ,
                struct.pack("HHHH", sc.rows, sc.cols, 0, 0))

    proc = subprocess.Popen(
        argv, cwd=str(MODULE_ROOT_BASE), env=env,
        stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True, close_fds=True,
    )
    os.close(slave)

    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    pending = list(sc.feed)
    started = time.monotonic()

    while time.monotonic() < deadline:
        now = time.monotonic()
        while pending and now - started >= pending[0][0]:
            _, payload = pending.pop(0)
            try:
                os.write(master, payload)
            except OSError:
                pending = []
        if not pending and proc.poll() is not None:
            # drain what is left
            while True:
                try:
                    ready, _, _ = select.select([master], [], [], 0.05)
                except OSError:
                    ready = []
                if not ready:
                    break
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            break
        try:
            ready, _, _ = select.select([master], [], [], 0.15)
        except OSError:
            break
        if ready:
            try:
                chunks.append(os.read(master, 65536))
            except OSError as exc:
                if exc.errno == errno.EIO:  # pty closed
                    break
                raise
        if pending and time.monotonic() > started + 12.0:
            break

    if proc.poll() is None:
        with __import__("contextlib").suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with __import__("contextlib").suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=3)

    with __import__("contextlib").suppress(OSError):
        os.close(master)
    return b"".join(chunks), (proc.returncode if proc.returncode is not None else -1)


def render_png(text: str, dest: Path, cols: int, rows: int, pointsize: int = 15) -> str | None:
    """Render captured text to a PNG with ImageMagick. Returns the font used, or None."""
    if not shutil.which("convert"):
        return None
    font = None
    for candidate in ("DejaVu-Sans-Mono", "Source-Code-Pro", "Liberation-Mono",
                      "Noto-Sans-Mono"):
        probe = subprocess.run(["convert", "-list", "font"],
                               capture_output=True, text=True)
        if candidate.lower() in probe.stdout.lower():
            font = candidate
            break
    tmp = dest.with_suffix(".render.txt")
    tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    width = max(40, cols) * int(pointsize * 0.62)
    height = max(8, rows) * int(pointsize * 1.32)
    cmd = [
        "convert", "-background", "#0c0c0e", "-fill", "#d8d8d8",
        "-pointsize", str(pointsize), "-interline-spacing", "2",
        "-size", f"{width}x{height}", f"label:@{tmp}",
        "-bordercolor", "#0c0c0e", "-border", "12", str(dest),
    ]
    if font:
        cmd[1:1] = ["-font", font]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        return None
    return font or "default"


MARKERS = ("ready", "not wired here", "blocked", "no probe", "selftest:")


def marker_column(line: str) -> int | None:
    """1-based column where a right-edge state marker starts, or None.

    A marker only counts when it is the *last* thing on the line (nothing but the marker
    text follows), so prose that merely mentions "ready" is not mistaken for a column.
    """
    stripped = line.rstrip()
    hits = []
    for marker in MARKERS:
        idx = stripped.rfind(marker)
        if idx == -1:
            continue
        tail = stripped[idx + len(marker):]
        if marker == "selftest:" or tail.strip() == "":
            hits.append(idx + 1)
    return max(hits) if hits else None


def alignment_report(paths: list[Path]) -> list[dict]:
    """Per-file column check: a "table" is only aligned if the columns agree."""
    out: list[dict] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        cols = [c for c in (marker_column(ln) for ln in lines) if c is not None]
        rows = [ln for ln in lines if ln.strip()]
        indents = sorted({len(ln) - len(ln.lstrip()) for ln in rows})
        out.append({
            "file": str(path),
            "lines": len(lines),
            "marker_columns": cols,
            "marker_spread": (max(cols) - min(cols)) if cols else 0,
            "marker_aligned": len(set(cols)) <= 1,
            "indent_levels": indents,
        })
    return out


def diagnostics(raw: str, stripped: str, cols: int) -> dict:
    lines = stripped.split("\n")
    widths = [visual_len(line) for line in lines]
    over = [{"line": i + 1, "width": w, "text": lines[i][:120]}
            for i, w in enumerate(widths) if w > cols]
    trailing = sum(1 for line in lines if line != line.rstrip())
    non_ascii = sorted({ch for ch in stripped if ord(ch) > 126})
    return {
        "lines": len(lines),
        "max_line_width": max(widths) if widths else 0,
        "overflow_lines": over[:12],
        "overflow_count": len(over),
        "lines_with_trailing_space": trailing,
        "sgr_runs": len(sgr_runs(raw)),
        "distinct_non_ascii": non_ascii[:24],
        "pure_ascii": not non_ascii,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "outputs" / "cli-visual"))
    ap.add_argument("--only", default="", help="comma-separated scenario names")
    ap.add_argument("--cols", type=int, default=0, help="override every scenario's columns")
    ap.add_argument("--rows", type=int, default=0, help="override every scenario's rows")
    ap.add_argument("--src", default="", help="pinned <dir>/src to measure instead of the repo's")
    ap.add_argument("--python", default="", help="interpreter to run the CLI with")
    ap.add_argument("--package", default="", help="package name inside --src (godsv|skaldir)")
    ap.add_argument("--cwd", default="", help="working directory the CLI starts in")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--align", default="", metavar="FILES",
                    help="comma-separated captured .txt files to check for column alignment")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    if args.align:
        paths = [Path(p.strip()) for p in args.align.split(",") if p.strip()]
        for record in alignment_report(paths):
            verdict = "aligned" if record["marker_aligned"] else "RAGGED"
            print(f"{Path(record['file']).name:32} lines={record['lines']:<4} "
                  f"state-markers={len(record['marker_columns']):<3} "
                  f"spread={record['marker_spread']:<3} {verdict} "
                  f"indent={record['indent_levels']}")
        return 0

    global SRC_DIR, PYTHON_BIN, MODULE_ROOT_BASE, PACKAGE
    if args.src:
        SRC_DIR = Path(args.src).resolve()
    if args.python:
        # NOT `.resolve()`: `.venv/bin/python` is a symlink to the uv-managed CPython, and
        # resolving it makes the child run as *bare* CPython — the venv (and every installed
        # dependency) silently disappears. Measured 2026-09-18: `ModuleNotFoundError: yaml`.
        PYTHON_BIN = Path(args.python).absolute()
    if args.cwd:
        MODULE_ROOT_BASE = Path(args.cwd).resolve()
    if args.package:
        PACKAGE = str(args.package)

    if not (SRC_DIR / PACKAGE).is_dir():
        print(f"error: no package {PACKAGE!r} under {SRC_DIR}", file=sys.stderr)
        return 2

    all_scenarios = scenarios()
    if args.list:
        for sc in all_scenarios:
            print(f"{sc.name:26} {sc.cols}x{sc.rows:<4} {sc.description}")
        return 0

    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    for sc in all_scenarios:
        if wanted and sc.name not in wanted:
            continue
        cols = args.cols or sc.cols
        rows = args.rows or sc.rows
        scenario = Scenario(**{**sc.__dict__, "cols": cols, "rows": rows})
        started = time.monotonic()
        blob, code = run_in_pty(scenario)
        elapsed = round((time.monotonic() - started) * 1000)
        raw = blob.decode("utf-8", "replace")
        text = strip_ansi(raw)
        (out_dir / f"{sc.name}.ansi").write_text(raw, encoding="utf-8")
        (out_dir / f"{sc.name}.txt").write_text(text, encoding="utf-8")
        diag = diagnostics(raw, text, cols)
        png = None
        if not args.no_png:
            png = render_png(text, out_dir / f"{sc.name}.png", cols, min(rows, 60))
        meta = {
            "scenario": sc.name,
            "description": sc.description,
            "argv": ["python", "-m",
                     (sc.module.replace("godsv.", f"{PACKAGE}.") if sc.module else PACKAGE),
                     *sc.argv],
            "source_dir": str(SRC_DIR),
            "cwd": str(MODULE_ROOT_BASE),
            "cols": cols,
            "rows": rows,
            "exit_code": code,
            "expected_exit": sc.expect_exit,
            "exit_matches": (sc.expect_exit is None or sc.expect_exit == code),
            "duration_ms": elapsed,
            "bytes": len(blob),
            "png": str(out_dir / f"{sc.name}.png") if png else None,
            "png_note": "rendered text, not a screen photograph" if png else "no png",
            **diag,
        }
        (out_dir / f"{sc.name}.meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        summary.append(meta)
        flag = "ok " if meta["exit_matches"] else "EXIT!"
        print(f"{flag} {sc.name:26} exit={code:<3} {meta['bytes']:>7}B "
              f"lines={diag['lines']:<4} maxw={diag['max_line_width']:<4} "
              f"overflow={diag['overflow_count']:<3} sgr={diag['sgr_runs']:<5} "
              f"ascii={diag['pure_ascii']}")

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {len(summary)} scenario(s) to {out_dir}")
    return 0 if all(item["exit_matches"] for item in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
