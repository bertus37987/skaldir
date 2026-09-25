#!/usr/bin/env python3
"""Warm-session performance harness: cold vs warm per command, plus equivalence checks.

Everything in ``MVP/26-warm-session.md`` is reproducible with this one file:

```bash
cd /home/willy/projects/gods-voice
env -u HERMES_HOME -u GODSV_HOME .venv/bin/python tools/gv_warm_perf.py all --runs 10
env -u HERMES_HOME -u GODSV_HOME .venv/bin/python tools/gv_warm_perf.py baseline --runs 10
env -u HERMES_HOME -u GODSV_HOME .venv/bin/python tools/gv_warm_perf.py compare  --runs 10
env -u HERMES_HOME -u GODSV_HOME .venv/bin/python tools/gv_warm_perf.py equivalence
env -u HERMES_HOME -u GODSV_HOME .venv/bin/python tools/gv_warm_perf.py memory --calls 1000
```

Measurement rules this harness keeps (learned the hard way, see ``MVP/19-performance.md``):

* wall time and max RSS come from ``os.wait4`` — the child's own rusage; stdout/stderr go to
  temp files because a ``wait4`` that waits before reading deadlocks on ``contract dump
  --json``'s 155 KB;
* every sample is a **fresh process**, i.e. the same shape a user or an agent gets; the warm
  samples are one client process per command against one live session (never an in-process
  call pretending to be a CLI run);
* the environment is pinned (one scratch ``HERMES_HOME``, no ambient runtime) so cold and
  warm answer the same question;
* p50 **and** p95, plus the interpreter floor and the load average, because a busy box
  distorts absolute times;
* the daemon's own view (``daemon_ms``, parser-cache hit/rebuild) is measured separately, in
  process against the live session socket, so the client's startup can be told apart from
  the command's real cost.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PACKAGE = "skaldir" if (SRC / "skaldir").is_dir() else "godsv"

#: The three commands the performance brief names, plus the ones the equivalence proof
#: needs: a human-output command, a failing command (exit 2) and a usage error (exit 1).
DEFAULT_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("tools", "list", "--json"),
    ("contract", "dump", "--json"),
    ("selftest", "--json"),
    ("registry", "status", "--json"),
    ("completion", "status", "--json"),
    ("doctor",),
    ("tools", "list"),
    ("skills", "list", "--json"),
)

EQUIVALENCE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("tools", "list", "--json"),
    ("contract", "dump", "--json"),
    ("selftest", "--json"),
    ("registry", "status", "--json"),
    ("doctor",),
    ("tools", "list"),
    ("tools", "show", "--nope"),  # argparse usage error → exit 1 + stderr text
    ("skills", "list", "--json"),
)

#: Envelope values that are the command's *own* timing and therefore cannot be equal
#: across two runs (documented in the equivalence section of MVP/26).
TIMING_KEYS = ("duration_ms", "started_at", "ended_at", "elapsed_ms", "latency_ms",
               "generated_at", "checked_at", "timestamp", "scanned_at")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?")
_MS_TEXT = re.compile(r"\b\d+(?:\.\d+)?\s?ms\b")


@dataclass
class Sample:
    wall_ms: float
    rss_mb: float
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass
class Stats:
    label: str
    runs: int
    min_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    rss_mb: float
    exit_codes: list[int] = field(default_factory=list)

    @property
    def exits(self) -> str:
        return ",".join(str(code) for code in self.exit_codes)


# --------------------------------------------------------------------------- #
# process plumbing
# --------------------------------------------------------------------------- #


def _python() -> str:
    """The interpreter that owns the console script (the repo venv's)."""
    for candidate in (REPO / ".venv" / "bin" / "python", REPO / ".venv" / "bin" / "python3"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def resolve_console() -> tuple[list[str], str]:
    """``(argv prefix, prog)`` of the product's cold console script."""
    bindir = Path(_python()).parent
    for candidate in (PACKAGE, "godsvoice", "skaldir"):
        script = bindir / candidate
        if script.exists():
            return [str(script)], candidate
    return [_python(), "-m", PACKAGE], PACKAGE


def warm_client(prog: str) -> list[str]:
    """The warm client's argv prefix (the module entry, before the front-door wiring lands).

    ``--warm-prog`` is passed explicitly so the session's ``argv[0]`` equals the cold
    script's name and both outputs stay comparable byte for byte.
    """
    return [_python(), "-m", f"{PACKAGE}.warm_cli", "--warm-prog", prog]


def measurement_env(scratch: Path) -> dict:
    """A pinned environment: no ambient runtime, one scratch home shared by both sides."""
    env = dict(os.environ)
    for name in ("GODSV_HOME", "SKALDIR_HOME", "GODSV_WARM_DIR", "SKALDIR_WARM_DIR",
                 "GODSV_WARM", "SKALDIR_WARM", "SKALDIR_FORMAT", "GODSV_FORMAT",
                 "NO_COLOR", "COLUMNS", "LINES"):
        env.pop(name, None)
    home = scratch / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["HERMES_HOME"] = str(home)
    env["PYTHONPATH"] = str(SRC)
    return env


def _spawn(argv: Sequence[str], env: dict, cwd: Path) -> Sample:
    """One fresh process: wall ms, the child's max RSS, exit code, captured bytes."""
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        started = time.perf_counter()
        proc = subprocess.Popen(
            list(argv), stdout=out_file, stderr=err_file, stdin=subprocess.DEVNULL,
            env=env, cwd=str(cwd),
        )
        _, status, usage = os.wait4(proc.pid, 0)
        wall_ms = (time.perf_counter() - started) * 1000.0
        out_file.seek(0)
        err_file.seek(0)
        return Sample(wall_ms, usage.ru_maxrss / 1024.0, os.waitstatus_to_exitcode(status),
                      out_file.read(), err_file.read())


def _stats(samples: list[Sample]) -> Stats:
    walls = sorted(sample.wall_ms for sample in samples)
    return Stats(
        label="",
        runs=len(samples),
        min_ms=min(walls),
        p50_ms=statistics.median(walls),
        p95_ms=walls[min(len(walls) - 1, int(round(0.95 * (len(walls) - 1))))],
        max_ms=max(walls),
        rss_mb=statistics.median(sample.rss_mb for sample in samples),
        exit_codes=[sample.exit_code for sample in samples],
    )


def _measure(argv: Sequence[str], runs: int, env: dict, cwd: Path) -> tuple[Stats, list[Sample]]:
    samples = [_spawn(argv, env, cwd) for _ in range(runs)]
    return _stats(samples), samples


def load_average() -> float:
    try:
        return os.getloadavg()[0]
    except OSError:  # pragma: no cover
        return 0.0


def interpreter_floor(env: dict, cwd: Path, runs: int = 3) -> float:
    return _stats([_spawn([_python(), "-c", "pass"], env, cwd) for _ in range(runs)]).p50_ms


def _warm_module() -> Any:
    """The product's warm module, imported for the daemon-side measurements."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return importlib.import_module(f"{PACKAGE}.warm_session")


# --------------------------------------------------------------------------- #
# the session under test
# --------------------------------------------------------------------------- #


class Session:
    """A warm session in a scratch directory, started and stopped by the harness."""

    def __init__(self, scratch: Path, env: dict, parser_cache: bool = True) -> None:
        self.scratch = scratch
        self.cwd = _cwd(scratch)
        self.socket = scratch / "warm" / "warm.sock"
        self.env = dict(env)
        self.env["GODSV_WARM_DIR"] = str(self.socket.parent)
        self.env["SKALDIR_WARM_DIR"] = str(self.socket.parent)
        self.parser_cache = parser_cache
        self.prog = resolve_console()[1]
        self.client = warm_client(self.prog)
        self.module: Any = None

    # -- lifecycle ---------------------------------------------------------- #

    def _session_verb(self, verb: str, *extra: str) -> dict:
        """Run ``warm <verb> --json`` and return the envelope's ``data`` (plus the raw run)."""
        argv = [*self.client[:3], "warm", verb, "--socket", str(self.socket), "--json", *extra]
        sample = _spawn(argv, self.env, self.scratch)
        try:
            document = json.loads(sample.stdout.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            document = {"raw": sample.stdout.decode("utf-8", "replace")[:400]}
        data = document.get("data") if isinstance(document, dict) else None
        payload = dict(data) if isinstance(data, dict) else {"raw": document}
        payload["exit_code"] = sample.exit_code
        payload["envelope_ok"] = document.get("ok") if isinstance(document, dict) else None
        payload["wall_ms"] = round(sample.wall_ms, 1)
        if sample.exit_code != 0:
            payload["stderr"] = sample.stderr.decode("utf-8", "replace")[:600]
        return payload

    def start(self) -> dict:
        extra = [] if self.parser_cache else ["--no-parser-cache"]
        payload = self._session_verb("start", *extra)
        payload["start_ms"] = payload.pop("wall_ms", None)
        return payload

    def stop(self) -> dict:
        return self._session_verb("stop")

    def status(self) -> dict:
        return self._session_verb("status")

    # -- commands ----------------------------------------------------------- #

    def single_command_client(self, command: Sequence[str]) -> list[str]:
        """``skaldir --warm <cmd>`` — what the wired front door will run."""
        return [*self.client[:3], "--warm", "--warm-socket", str(self.socket), *command]

    def daemon_side(self, command: Sequence[str], runs: int = 5) -> dict:
        """The command's cost *inside* the session: no client startup, same socket.

        In-process on purpose: this is the number that says how much of the warm wall time
        is the session's work (the rest is the client interpreter, which a shell always pays).
        """
        if self.module is None:
            self.module = _warm_module()
        times: list[float] = []
        caches: set[str] = set()
        exits: set[int] = set()
        # The in-process call carries *this* process' environment; it must be the same pinned
        # environment the spawned clients used, otherwise the session (rightly) rebuilds the
        # parser and the "in-session" number would include a cost the real client never pays.
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.env)
        try:
            for _ in range(runs):
                result = self.module.run_exec(
                    list(command), socket_path=self.socket, cwd=str(self.cwd),
                    prog=self.prog,
                )
                times.append(result.daemon_ms)
                caches.add(result.cache)
                exits.add(result.exit_code)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return {
            "daemon_p50_ms": round(statistics.median(times), 2),
            "daemon_min_ms": round(min(times), 2),
            "daemon_max_ms": round(max(times), 2),
            "cache": ",".join(sorted(caches)),
            "exit_codes": sorted(exits),
        }


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #


def _speedup(cold: Stats, warm: Stats, key: str) -> float:
    warm_value = getattr(warm, key)
    return getattr(cold, key) / warm_value if warm_value else float("inf")


def _table(rows: list[tuple[str, Stats, Stats]]) -> str:
    lines = ["| command | cold p50 | cold p95 | warm p50 | warm p95 | speedup p50 | speedup p95 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for label, cold, warm in rows:
        lines.append(
            f"| `{label}` | {cold.p50_ms:.0f} ms | {cold.p95_ms:.0f} ms | {warm.p50_ms:.0f} ms | "
            f"{warm.p95_ms:.0f} ms | **{_speedup(cold, warm, 'p50_ms'):.1f}x** | "
            f"{_speedup(cold, warm, 'p95_ms'):.1f}x |"
        )
    return "\n".join(lines)


def _report(payload: dict, args: argparse.Namespace) -> None:
    if getattr(args, "write_json", ""):
        Path(args.write_json).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"json written to {args.write_json}")
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))


def cmd_baseline(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="gv-warm-baseline-"))
    env = measurement_env(scratch)
    cwd = _cwd(scratch)
    console, prog = resolve_console()
    commands = args.commands or DEFAULT_COMMANDS
    floor = interpreter_floor(env, cwd)
    payload: dict[str, Any] = {"mode": "baseline", "console": console, "prog": prog,
                               "runs": args.runs, "floor_ms": round(floor, 1),
                               "load1": round(load_average(), 2), "commands": {}}
    for command in commands:
        stats, _samples = _measure([*console, *command], args.runs, env, cwd)
        payload["commands"][" ".join(command)] = asdict(stats)
        print(f"{' '.join(command):30s} p50 {stats.p50_ms:7.1f} ms  p95 {stats.p95_ms:7.1f} ms  "
              f"min {stats.min_ms:7.1f}  max {stats.max_ms:7.1f}  rss {stats.rss_mb:5.1f} MB  "
              f"exit {stats.exits}")
    print(f"\ninterpreter floor (python -c pass) p50 {floor:.1f} ms · load1 {payload['load1']} · "
          f"{args.runs} runs per command")
    _report(payload, args)
    return 0


def cmd_warm(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="gv-warm-run-"))
    env = measurement_env(scratch)
    cwd = _cwd(scratch)
    session = Session(scratch, env, parser_cache=not args.no_parser_cache)
    start = session.start()
    if start.get("exit_code") != 0:
        print(f"session did not start: {json.dumps(start, indent=1)}")
        return 1
    print(f"session ready in {start.get('start_ms')} ms · pid {start.get('pid')} · "
          f"entry {start.get('entry')} · eligible {start.get('eligible_commands')} commands")
    commands = args.commands or DEFAULT_COMMANDS
    payload: dict[str, Any] = {"mode": "warm", "start": start, "runs": args.runs,
                               "load1": round(load_average(), 2), "commands": {}}
    try:
        for command in commands:
            stats, samples = _measure(session.single_command_client(command), args.runs, env, cwd)
            daemon = session.daemon_side(command)
            payload["commands"][" ".join(command)] = {**asdict(stats), **daemon}
            print(f"{' '.join(command):30s} p50 {stats.p50_ms:7.1f} ms  p95 {stats.p95_ms:7.1f} ms  "
                  f"min {stats.min_ms:7.1f}  max {stats.max_ms:7.1f}  rss {stats.rss_mb:5.1f} MB  "
                  f"exit {stats.exits}  | in-session p50 {daemon['daemon_p50_ms']:7.2f} ms "
                  f"({daemon['cache']})")
        payload["session_after"] = session.status()
    finally:
        payload["stop"] = session.stop()
    _report(payload, args)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="gv-warm-compare-"))
    env = measurement_env(scratch)
    cwd = _cwd(scratch)
    console, prog = resolve_console()
    session = Session(scratch, env, parser_cache=not args.no_parser_cache)
    start = session.start()
    if start.get("exit_code") != 0:
        print(f"session did not start: {json.dumps(start, indent=1)}")
        return 1
    commands = args.commands or DEFAULT_COMMANDS
    rows: list[tuple[str, Stats, Stats]] = []
    details: list[dict] = []
    try:
        for command in commands:  # interleaved: a drifting box moves both sides together
            cold_samples: list[Sample] = []
            warm_samples: list[Sample] = []
            for _ in range(args.runs):
                cold_samples.append(_spawn([*console, *command], env, cwd))
                warm_samples.append(_spawn(session.single_command_client(command), env, cwd))
            cold, warm = _stats(cold_samples), _stats(warm_samples)
            daemon = session.daemon_side(command)
            rows.append((f"{prog} {' '.join(command)}", cold, warm))
            details.append({"command": " ".join(command), "cold": asdict(cold),
                            "warm": asdict(warm), **daemon})
    finally:
        session.stop()
    print(f"cold = `{console[0]} <cmd>` · warm = the same command through the session · "
          f"{args.runs} interleaved rounds each")
    print(f"load1 {load_average():.2f} · interpreter floor {interpreter_floor(env, cwd):.1f} ms · "
          f"session start {start.get('start_ms')} ms\n")
    print(_table(rows))
    print("\nin-session (client startup excluded, median of 5 in-process calls):")
    for item in details:
        print(f"  {' '.join([item['command']]):30s} {item['daemon_p50_ms']:7.2f} ms  "
              f"min {item['daemon_min_ms']:.2f}  max {item['daemon_max_ms']:.2f}  cache {item['cache']}")
    _report({"mode": "compare", "console": console, "runs": args.runs,
             "load1": round(load_average(), 2), "start": start, "commands": details}, args)
    return 0


def cmd_equivalence(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="gv-warm-equiv-"))
    env = measurement_env(scratch)
    cwd = _cwd(scratch)
    console, prog = resolve_console()
    session = Session(scratch, env, parser_cache=not args.no_parser_cache)
    start = session.start()
    if start.get("exit_code") != 0:
        print(f"session did not start: {json.dumps(start, indent=1)}")
        return 1
    commands = args.commands or EQUIVALENCE_COMMANDS
    report: list[dict] = []
    failures = 0
    try:
        for command in commands:
            control = _spawn([*console, *command], env, cwd)   # cold vs cold: the noise floor
            cold = _spawn([*console, *command], env, cwd)
            warm = _spawn(session.single_command_client(command), env, cwd)
            masked = _masked_diff(cold.stdout, warm.stdout)
            control_diff = _masked_diff(control.stdout, cold.stdout)
            err_masked = _masked_diff(cold.stderr, warm.stderr)
            row = {
                "command": " ".join(command),
                "exit_control": control.exit_code,
                "exit_cold": cold.exit_code,
                "exit_warm": warm.exit_code,
                "stdout_bytes_cold": len(cold.stdout),
                "stdout_bytes_warm": len(warm.stdout),
                "stdout_identical": cold.stdout == warm.stdout,
                "stdout_identical_modulo_timing": masked["identical"],
                "masked_keys": masked["keys"],
                "stderr_identical": cold.stderr == warm.stderr,
                "stderr_identical_modulo_timing": err_masked["identical"],
                "stderr_bytes_cold": len(cold.stderr),
                "stderr_bytes_warm": len(warm.stderr),
                "control_cold_vs_cold_identical": control_diff["identical"],
                "control_cold_vs_cold_exact": control.stdout == cold.stdout,
            }
            ok = row["exit_cold"] == row["exit_warm"] and masked["identical"] and err_masked["identical"]
            if not ok:
                failures += 1
            mark = "OK " if row["stdout_identical"] else ("~  " if masked["identical"] else "BAD")
            extra = "" if row["stdout_identical"] else f"  [timing keys: {', '.join(masked['keys']) or 'none'}]"
            if row["stderr_identical"]:
                stderr_label = "identical"
            elif err_masked["identical"]:
                stderr_label = f"timing-only ({', '.join(err_masked['keys'])})"
            else:
                stderr_label = f"DIFFERENT ({len(cold.stderr)}/{len(warm.stderr)}B)"
            print(f"{mark} {' '.join(command):30s} exit {cold.exit_code}=={warm.exit_code}  "
                  f"stdout {len(cold.stdout)}B/{len(warm.stdout)}B  stderr {stderr_label}{extra}")
            report.append(row)
    finally:
        session.stop()
    verdict = "PASS" if failures == 0 else f"FAIL ({failures})"
    print(f"\nequivalence: {verdict} — same exit codes, and stdout byte-identical (or identical "
          f"after masking the envelope's own timing values)")
    _report({"mode": "equivalence", "verdict": verdict, "failures": failures,
             "commands": report}, args)
    return 0 if failures == 0 else 1


def cmd_memory(args: argparse.Namespace) -> int:
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="gv-warm-mem-"))
    env = measurement_env(scratch)
    cwd = _cwd(scratch)
    session = Session(scratch, env, parser_cache=not args.no_parser_cache)
    start = session.start()
    if start.get("exit_code") != 0:
        print(f"session did not start: {json.dumps(start, indent=1)}")
        return 1
    command = args.commands[0] if args.commands else ("tools", "list", "--json")
    checkpoints = [1, 10, 100, 500, args.calls]
    samples: list[dict] = []
    failures = 0
    try:
        for call in range(1, args.calls + 1):
            if _spawn(session.single_command_client(command), env, cwd).exit_code != 0:
                failures += 1
            if call in checkpoints:
                info = session.status()
                samples.append({"calls": call, "rss_mb": info.get("rss_mb"),
                                "requests": info.get("requests")})
        info = session.status()
        log = Path(str(info.get("log") or ""))
        log_bytes = log.stat().st_size if log.exists() else 0
        first = next((item["rss_mb"] for item in samples if item["rss_mb"]), None)
        last = samples[-1]["rss_mb"]
        growth = None if first is None or last is None else round(last - first, 1)
        print(f"{args.calls} warm calls of `{' '.join(command)}` · failed calls {failures}")
        for item in samples:
            print(f"  after {item['calls']:5d} calls: daemon RSS {item['rss_mb']} MB "
                  f"(served {item['requests']})")
        print(f"  daemon pid {info.get('pid')} · parser cache {info.get('parser_cache')} · "
              f"log {log_bytes} B")
        bound = float(args.max_growth_mb)
        verdict = "PASS" if failures == 0 and (growth is None or growth <= bound) else "FAIL"
        print(f"memory verdict: {verdict} (bound: <= {bound} MB growth from the first to the "
              f"{args.calls}th call; measured {growth} MB)")
        _report({"mode": "memory", "calls": args.calls, "failures": failures, "samples": samples,
                 "growth_mb": growth, "bound_mb": bound, "log_bytes": log_bytes,
                 "verdict": verdict}, args)
        return 0 if verdict == "PASS" else 1
    finally:
        session.stop()


def cmd_all(args: argparse.Namespace) -> int:
    for mode in (cmd_baseline, cmd_warm, cmd_compare, cmd_equivalence, cmd_memory):
        print(f"\n===== {mode.__name__[4:]} =====")
        code = mode(args)
        if code != 0 and mode is not cmd_memory:
            return code
    return 0


# --------------------------------------------------------------------------- #
# equivalence helpers
# --------------------------------------------------------------------------- #


def _mask_timing(text: str) -> tuple[str, list[str]]:
    """Replace the command's *own* timing values with a placeholder, count what was replaced.

    Two shapes exist: the ``--json`` envelope (``duration_ms``/``started_at``) and the human
    summaries, which print their own measured cost (``· 28ms``). Both differ between two runs
    of the same command — the equivalence report also runs a cold/cold control to show that.
    """
    masked: list[str] = []
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        found = []
        if _ISO.search(text):
            text, found = _ISO.sub("<timing>", text), [*found, "iso-timestamp"]
        if _MS_TEXT.search(text):
            text, found = _MS_TEXT.sub(" <duration>", text), [*found, "self-timing-ms"]
        return text, sorted(set(found))

    def walk(node: Any, key: str = "") -> Any:
        if isinstance(node, dict):
            return {name: walk(value, name) for name, value in node.items()}
        if isinstance(node, list):
            return [walk(item, key) for item in node]
        if isinstance(node, str) and _ISO.search(node):
            masked.append(key or "iso")
            return _ISO.sub("<timing>", node)
        if isinstance(node, (int, float)) and not isinstance(node, bool) and key in TIMING_KEYS:
            masked.append(key)
            return -1
        return node

    return json.dumps(walk(document), sort_keys=True, ensure_ascii=False), sorted(set(masked))


def _masked_diff(cold: bytes, warm: bytes) -> dict:
    cold_text = cold.decode("utf-8", "replace")
    warm_text = warm.decode("utf-8", "replace")
    masked_cold, keys_cold = _mask_timing(cold_text)
    masked_warm, keys_warm = _mask_timing(warm_text)
    return {"identical": masked_cold == masked_warm,
            "keys": sorted(set(keys_cold) | set(keys_warm))}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _cwd(scratch: Path) -> Path:
    path = scratch / "cwd"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runs", type=int, default=10, help="samples per command (default 10)")
    common.add_argument("--scratch", default="", help="scratch directory (default: a fresh tmpdir)")
    common.add_argument("--command", action="append", default=None, metavar="CMD",
                        help="a command to measure, quoted (repeatable)")
    common.add_argument("--json", action="store_true", help="also print the payload as JSON")
    common.add_argument("--write-json", dest="write_json", default="", help="write the payload here")
    common.add_argument("--no-parser-cache", action="store_true",
                        help="start the session without the cached argparse tree")
    common.add_argument("--calls", type=int, default=1000, help="warm calls for `memory`")
    common.add_argument("--max-growth-mb", dest="max_growth_mb", type=float, default=8.0,
                        help="allowed daemon RSS growth between the first and last call")
    parser = argparse.ArgumentParser(prog="gv_warm_perf", parents=[common],
                                     description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="mode")
    for name, help_text in (("baseline", "cold cost per command (median/p95 of N)"),
                            ("warm", "the same commands through a live session"),
                            ("compare", "the before/after table, interleaved cold/warm"),
                            ("equivalence", "exit codes + stdout bytes, cold vs warm"),
                            ("memory", "daemon RSS across N warm calls"),
                            ("all", "baseline + warm + compare + equivalence + memory")):
        sub.add_parser(name, parents=[common], help=help_text)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args([str(item) for item in (argv if argv is not None else sys.argv[1:])])
    args.commands = [tuple(shlex.split(item)) for item in (args.command or [])] or None
    modes = {"baseline": cmd_baseline, "warm": cmd_warm, "compare": cmd_compare,
             "equivalence": cmd_equivalence, "memory": cmd_memory, "all": cmd_all}
    if not getattr(args, "mode", None):
        parser.print_help()
        return 1
    return int(modes[args.mode](args))


if __name__ == "__main__":
    raise SystemExit(main())
