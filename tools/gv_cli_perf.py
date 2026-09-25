#!/usr/bin/env python3
"""CLI performance harness for the ``godsvoice`` cold start and registry scale.

Four measurements, all of them raw numbers instead of adjectives:

``measure``  cold start of one or more commands: wall time, per-child max RSS,
             exit code, over N runs — reported as min / median / max.
``imports``  where the cold start goes: per-module import cost of a module
             graph, parsed from ``python -X importtime``.
``scale``    the capability-catalogue path against a synthesised runtime
             (``generated_tools/*/manifest.yaml`` + ``tools.db`` rows) at
             several catalogue sizes — prints the curve, flags superlinear growth.
``budget``   checks the startup budgets and exits non-zero when one is broken
             (the pytest startup-budget test shells out to this).

Everything defaults to the repository's own ``.venv`` and unsets
``HERMES_HOME``/``GODSV_HOME`` so a measurement is never taken against a
sandboxed runtime by accident (``scale`` sets ``GODSV_HOME`` on purpose, to the
synthesised tree, and says so).

Examples::

    .venv/bin/python tools/gv_cli_perf.py measure --runs 5
    .venv/bin/python tools/gv_cli_perf.py imports --top 20
    .venv/bin/python tools/gv_cli_perf.py scale --sizes 0,100,1000,5000,10000
    .venv/bin/python tools/gv_cli_perf.py budget
    .venv/bin/python tools/gv_cli_perf.py report --out MVP-table.md
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = REPO / ".venv" / "bin" / "python"
DEFAULT_CLI = REPO / ".venv" / "bin" / "skaldir"

#: The commands the budget and the report are about; a "trivial" command with a
#: real handler is included so the table shows the floor, not only the ceiling.
DEFAULT_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("--version", ("--version",)),
    ("contract dump --json", ("contract", "dump", "--json")),
    ("selftest --json", ("selftest", "--json")),
    ("doctor", ("doctor",)),
    ("tools list --json", ("tools", "list", "--json")),
)

_IMPORTTIME = re.compile(r"^import time:\s+(\d+)\s*\|\s*(\d+)\s*\|\s*(.*)$")


# --------------------------------------------------------------------------- env


def clean_env(extra: dict[str, str] | None = None, keep: dict[str, str] | None = None) -> dict:
    """The measurement environment: the runner's rule (both runtime vars unset)."""

    env = dict(os.environ)
    for name in ("HERMES_HOME", "GODSV_HOME"):
        env.pop(name, None)
    if keep:
        env.update(keep)
    if extra:
        env.update(extra)
    env.pop("PYTHONPATH", None)
    return env


def run_once(argv: list[str], env: dict) -> dict:
    """One subprocess run: wall time + the child's own max RSS (via ``wait4``).

    stdout/stderr go to temporary FILES, never to pipes: ``wait4`` waits before
    anything is read, so a command that writes more than the 64 KB pipe buffer
    (`contract dump --json` emits 155 KB of JSON) would deadlock against its own
    parent.
    """

    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        started = time.perf_counter()
        proc = subprocess.Popen(argv, stdout=out_file, stderr=err_file, env=env)
        _, status, usage = os.wait4(proc.pid, 0)
        wall_ms = (time.perf_counter() - started) * 1000.0
        proc.returncode = os.waitstatus_to_exitcode(status)
        stdout_bytes = out_file.tell()
        err_file.seek(0, os.SEEK_END)
        stderr_bytes = err_file.tell()
    return {
        "wall_ms": wall_ms,
        "rss_kb": int(usage.ru_maxrss),
        "user_ms": usage.ru_utime * 1000.0,
        "sys_ms": usage.ru_stime * 1000.0,
        "exit_code": proc.returncode,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
    }


def measure(argv: list[str], runs: int, env: dict, label: str | None = None) -> dict:
    samples = [run_once(argv, env) for _ in range(runs)]
    walls = sorted(s["wall_ms"] for s in samples)
    rss = sorted(s["rss_kb"] for s in samples)
    return {
        "argv": argv,
        "label": label or " ".join(argv[1:]),
        "runs": runs,
        "wall_ms": {
            "min": round(walls[0], 1),
            "median": round(statistics.median(walls), 1),
            "max": round(walls[-1], 1),
            "mean": round(statistics.fmean(walls), 1),
        },
        # NB: valid only while the MEASURING process is smaller than the child:
        # `ru_maxrss` after a fork includes the parent's shared pages (measured:
        # a 313 MB measuring process makes `python -c pass` look like 313 MB).
        # The pytest budget test therefore shells out to this harness, whose own
        # RSS is ~15 MB, instead of measuring inside a large pytest process.
        "rss_kb": {"min": rss[0], "median": int(statistics.median(rss)), "max": rss[-1]},
        "exit_codes": sorted({s["exit_code"] for s in samples}),
        "stdout_bytes": sorted({s["stdout_bytes"] for s in samples}),
        "stderr_bytes": sorted({s["stderr_bytes"] for s in samples}),
        "user_ms_median": round(statistics.median([s["user_ms"] for s in samples]), 1),
    }


# ----------------------------------------------------------------------- imports


def import_costs(
    python: Path, module: str = "godsv.cli", env: dict | None = None, top: int = 20
) -> dict:
    """Per-module import cost of ``module`` from ``-X importtime``."""

    proc = subprocess.run(
        [str(python), "-X", "importtime", "-c", f"import {module}"],
        capture_output=True,
        text=True,
        env=env if env is not None else clean_env(),
    )
    rows: dict[str, dict] = {}
    for line in proc.stderr.splitlines():
        match = _IMPORTTIME.match(line)
        if not match:
            continue
        self_us, cumulative_us, raw = match.groups()
        name = raw.strip()
        entry = rows.setdefault(name, {"self_us": 0, "cumulative_us": 0})
        entry["self_us"] += int(self_us)
        entry["cumulative_us"] = max(entry["cumulative_us"], int(cumulative_us))
    ranked = sorted(rows.items(), key=lambda item: -item[1]["self_us"])
    return {
        "module": module,
        "modules_imported": len(rows),
        "total_ms": round(rows.get(module, {}).get("cumulative_us", 0) / 1000.0, 1),
        "by_cumulative_ms": [
            {
                "module": name,
                "self_ms": round(v["self_us"] / 1000.0, 2),
                "cumulative_ms": round(v["cumulative_us"] / 1000.0, 2),
            }
            for name, v in sorted(rows.items(), key=lambda i: -i[1]["cumulative_us"])[:top]
        ],
        "by_self_ms": [
            {
                "module": name,
                "self_ms": round(v["self_us"] / 1000.0, 2),
                "cumulative_ms": round(v["cumulative_us"] / 1000.0, 2),
            }
            for name, v in ranked[:top]
        ],
    }


# ------------------------------------------------------------------------- scale


def _manifest(index: int) -> str:
    return (
        "name: synth-tool-{i}\n"
        "type: cli\n"
        "version: 1.0.{i}\n"
        "status: ready\n"
        "scope: global\n"
        "project_id: proj-synth\n"
        "purpose: synthetic capability number {i} for the scaling curve\n"
        "runtime: rtx-{i}\n"
        "image: synth/image:{i}\n"
        "image_digest: sha256:{digest}\n"
        "created_by: tools/gv_cli_perf.py\n"
        "validation:\n"
        "  status: validated\n"
        "  checks:\n"
        "    - synth\n"
        "network:\n"
        "  allowed_domains:\n"
        "    - example.com\n"
        "filesystem:\n"
        "  access: read\n"
        "required_secrets: []\n"
    ).format(i=index, digest=format(index, "064x"))


def build_catalogue(root: Path, size: int, *, db_rows: bool = True) -> dict:
    """A synthetic runtime: ``size`` generated tools + matching ``tools.db`` rows."""

    runtime = Path(root)
    tools_root = runtime / "generated_tools"
    if runtime.exists():
        shutil.rmtree(runtime)
    tools_root.mkdir(parents=True, exist_ok=True)
    for index in range(size):
        tool_dir = tools_root / f"synth-tool-{index:05d}"
        tool_dir.mkdir()
        (tool_dir / "manifest.yaml").write_text(_manifest(index), encoding="utf-8")
    if db_rows:
        sys.path.insert(0, str(REPO / "src"))
        from godsv.tools import ToolSpec, ToolStore  # noqa: PLC0415 — deliberate

        store = ToolStore(runtime / "tools.db")
        try:
            for index in range(size):
                store.upsert(
                    ToolSpec(
                        name=f"synth-tool-{index:05d}",
                        title=f"Synthetic tool {index}",
                        description="scaling probe",
                        tags=["synth"],
                        kind="cli",
                        module="synth",
                        entrypoint="run",
                        status="approved",
                    )
                )
        finally:
            store.close()
        sys.path.pop(0)
    return {
        "runtime": str(runtime),
        "catalogue_size": size,
        "manifests": size,
        "db_rows": size if db_rows else 0,
    }


SCALE_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tools list --json", ("tools", "list", "--json")),
    ("tools-hub --json", ("tools-hub", "--json")),
    ("registry status --json", ("registry", "status", "--json")),
)


#: The three entry-point shapes of the SAME source tree. ``console-script`` is
#: today's wiring (``godsvoice = "godsv.cli:main"``); ``cli-module`` reproduces
#: it without the generated wrapper (the control); ``fast-entry`` is the proposed
#: wiring (``godsvoice = "godsv.perf_lazy:main"``).
ENTRYPOINTS: tuple[tuple[str, str], ...] = (
    ("console-script  python .venv/bin/godsvoice", "script"),
    ("cli-module      godsv.cli:main", "cli"),
    ("fast-entry      godsv.perf_lazy:main", "fast"),
)

_ENTRY_CODE = {
    "cli": "import sys;from godsv.cli import main;sys.exit(main())",
    "fast": "import sys;from godsv.perf_lazy import main;sys.exit(main())",
}


def entrypoint_compare(
    cli: Path,
    python: Path,
    runs: int,
    argv: list[str] | None = None,
    *,
    pythonpath: str | None = None,
) -> list[dict]:
    """Cold start of ``--version`` through all three entry-point shapes."""

    argv = list(argv or ["--version"])
    env = clean_env()
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    rows = [
        measure(
            [str(cli), *argv],
            runs,
            env,
            label=ENTRYPOINTS[0][0],
        )
    ]
    for label, key in ENTRYPOINTS[1:]:
        row = measure([str(python), "-c", _ENTRY_CODE[key], *argv], runs, env, label=label)
        row["entrypoint"] = key
        rows.append(row)
    rows[0]["entrypoint"] = "script"
    return rows


def scale_curve(
    cli: Path,
    sizes: list[int],
    runs: int,
    scratch: Path,
    env: dict | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for size in sizes:
        facts = build_catalogue(scratch / f"rt-{size}", size)
        run_env = dict(env if env is not None else clean_env())
        run_env["GODSV_HOME"] = facts["runtime"]
        for label, argv in SCALE_COMMANDS:
            result = measure([str(cli), *argv], runs, run_env)
            result.update({"label": label, "catalogue_size": size, **facts})
            rows.append(result)
    return rows


def growth_ratio(rows: list[dict], label: str, sizes: list[int]) -> dict:
    """Doubling ratio of the median wall time, per size step — the scaling exponent."""

    by_size = {row["catalogue_size"]: row for row in rows if row["label"] == label}
    out: list[dict] = []
    for small, large in zip(sizes, sizes[1:], strict=False):
        if small <= 0 or small not in by_size or large not in by_size:
            continue
        a = by_size[small]["wall_ms"]["median"]
        b = by_size[large]["wall_ms"]["median"]
        factor = large / small
        ratio = b / a if a > 0 else 0.0
        out.append(
            {
                "from": small,
                "to": large,
                "size_factor": factor,
                "time_factor": round(ratio, 2),
                "exponent": round((math.log(ratio) / math.log(factor)) if ratio > 0 else 0.0, 2),
                "superlinear": ratio > factor * 1.25,
            }
        )
    return {"command": label, "steps": out, "superlinear": any(s["superlinear"] for s in out)}


# ------------------------------------------------------------------------ budget

#: Startup budgets. Two numbers per check: the bound the **median** must meet, and
#: a loose ceiling for a single outlier. Deliberately generous: the point is to catch a
#: *regression* (a new eager import, a new parser-time side effect), not to fail on a
#: busy machine. Measured values sit next to each bound; see MVP/19-performance.md.
BUDGETS: dict[str, dict[str, float]] = {
    # measured 35-38 ms with the lazy front door (`skaldir.perf_lazy:main`); the
    # 226-500 ms behind this bound belong to the pre-`perf_lazy` console script
    "version_ms": {"median": 1500.0, "max": 3000.0},
    # fast path: measured 30-74 ms median, 142 ms worst at load 25
    "version_fast_ms": {"median": 300.0, "max": 900.0},
    # in-process `import skaldir.perf_lazy`: measured 22-24 ms
    "fast_import_ms": {"median": 200.0, "max": 500.0},
    # contract dump --json: measured 583-653 ms (155 KB of JSON)
    "contract_dump_ms": {"median": 3000.0, "max": 6000.0},
    # selftest --json: measured 887-945 ms (probes all eight layers). The bound was
    # written when it measured 372-540 ms because no provider was configured; with a
    # credential in the runtime's `.env` the ROUTE probe's one live `advise()` call
    # (2.1-2.3 s, MVP/87) put the median at 3365-3474 ms. The probe answers offline now
    # (it reports the mode the settings resolve to), so this measures the tree again —
    # `tests/test_perf_selftest_probes.py` pins that no provider request is spent.
    "selftest_ms": {"median": 3000.0, "max": 6000.0},
    # max RSS of `--version`: measured 18.9-20.5 MB with the lazy front door (47.5-48.2 MB
    # behind the pre-`perf_lazy` one), one 99 MB outlier under memory pressure in a
    # parallel wave (five sibling suites) — hence median + ceiling
    "version_rss_mb": {"median": 96.0, "max": 200.0},
}


def budget_check(cli: Path, python: Path, runs: int = 5) -> tuple[list[dict], bool]:
    """Evaluate every budget; the second return value is "all within budget".

    Every check is judged as ``median <= budget["median"] and max <= budget["max"]``:
    under a parallel wave a single sample can double (a 99 MB RSS outlier was
    measured next to a 47.5 MB median), while a real regression moves the median.
    """

    checks: list[dict] = []
    env = clean_env()

    def record(
        name: str,
        unit: str,
        command: str,
        median: float,
        worst: float,
        budget: dict[str, float],
    ) -> None:
        checks.append(
            {
                "check": name,
                "unit": unit,
                "command": command,
                "measured_median": median,
                "measured_max": worst,
                "budget": budget["median"],
                "ceiling": budget["max"],
                "ok": median <= budget["median"] and worst <= budget["max"],
            }
        )

    for name, argv in (
        ("version_ms", ["--version"]),
        ("contract_dump_ms", ["contract", "dump", "--json"]),
        ("selftest_ms", ["selftest", "--json"]),
    ):
        measured = measure([str(cli), *argv], max(runs, 5), env)
        record(
            name,
            "ms",
            "godsvoice " + " ".join(argv),
            measured["wall_ms"]["median"],
            measured["wall_ms"]["max"],
            BUDGETS[name],
        )

    fast = measure(
        [
            str(python),
            "-c",
            "import sys;from godsv.perf_lazy import main;sys.exit(main())",
            "--version",
        ],
        max(runs, 5),
        env,
    )
    record(
        "version_fast_ms",
        "ms",
        "godsvoice --version  (via godsv.perf_lazy:main)",
        fast["wall_ms"]["median"],
        fast["wall_ms"]["max"],
        BUDGETS["version_fast_ms"],
    )

    probe = (
        "import time;t=time.perf_counter();import godsv.perf_lazy;"
        "print((time.perf_counter()-t)*1000)"
    )
    import_samples: list[float] = []
    for _ in range(5):
        proc = subprocess.run([str(python), "-c", probe], capture_output=True, text=True, env=env)
        if proc.returncode == 0:
            try:
                import_samples.append(float(proc.stdout.strip()))
            except ValueError:  # pragma: no cover
                continue
    if import_samples:
        record(
            "fast_import_ms",
            "ms",
            "python -c 'import godsv.perf_lazy' (in-process import cost)",
            round(statistics.median(import_samples), 1),
            round(max(import_samples), 1),
            BUDGETS["fast_import_ms"],
        )

    rss = measure([str(cli), "--version"], max(runs, 5), env)
    record(
        "version_rss_mb",
        "MB",
        "godsvoice --version",
        round(rss["rss_kb"]["median"] / 1024.0, 1),
        round(rss["rss_kb"]["max"] / 1024.0, 1),
        BUDGETS["version_rss_mb"],
    )
    return checks, all(check["ok"] for check in checks)


# ----------------------------------------------------------------------- report


def render_measure_table(rows: list[dict], title: str) -> str:
    lines = [
        f"### {title}",
        "",
        "| command | min ms | median ms | max ms | max RSS MB | exit |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        shown = row["label"]
        lines.append(
            "| `{}` | {:.0f} | {:.0f} | {:.0f} | {:.1f} | {} |".format(
                shown,
                row["wall_ms"]["min"],
                row["wall_ms"]["median"],
                row["wall_ms"]["max"],
                row["rss_kb"]["max"] / 1024.0,
                ",".join(str(code) for code in row["exit_codes"]),
            )
        )
    return "\n".join(lines) + "\n"


def measure_all(cli: Path, python: Path, runs: int, env: dict, *, floor: bool = True) -> list[dict]:
    """Every default command, plus the interpreter floor as the baseline row."""

    rows: list[dict] = []
    if floor:
        rows.append(
            measure(
                [str(python), "-c", "pass"],
                runs,
                env,
                label="python -c pass (interpreter floor)",
            )
        )
    rows.extend(measure([str(cli), *argv], runs, env) for _, argv in DEFAULT_COMMANDS)
    return rows


def render_scale_table(rows: list[dict]) -> str:
    labels = sorted({row["label"] for row in rows})
    sizes = sorted({row["catalogue_size"] for row in rows})
    lines = [
        "| catalogue | " + " | ".join(f"`{label}` median ms" for label in labels) + " |",
        "|---|" + "---:|" * len(labels),
    ]
    for size in sizes:
        cells = []
        for label in labels:
            hit = [r for r in rows if r["label"] == label and r["catalogue_size"] == size]
            cells.append(f"{hit[0]['wall_ms']['median']:.0f}" if hit else "-")
        lines.append(f"| {size} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------- main


def _print_measure(rows: list[dict]) -> None:
    for row in rows:
        print(
            "{:40s} min {:7.1f}  median {:7.1f}  max {:7.1f} ms"
            "   RSS max {:6.1f} MB   exit {}".format(
                row["label"],
                row["wall_ms"]["min"],
                row["wall_ms"]["median"],
                row["wall_ms"]["max"],
                row["rss_kb"]["max"] / 1024.0,
                ",".join(str(c) for c in row["exit_codes"]),
            )
        )


DESCRIPTION = (
    "godsvoice CLI performance harness: cold start, import cost, registry scale, startup budget."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=DESCRIPTION,
        epilog="every sub-command accepts --python/--cli/--runs/--json/--out; "
        "options come after the sub-command (e.g. `gv_cli_perf.py measure --runs 5`)",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--python", default=str(DEFAULT_PYTHON))
    common.add_argument("--cli", default=str(DEFAULT_CLI))
    common.add_argument("--runs", type=int, default=5)
    common.add_argument("--json", action="store_true")
    common.add_argument("--out", default=None, help="markdown output path")

    imports_opts = argparse.ArgumentParser(add_help=False)
    imports_opts.add_argument("--module", default="godsv.cli")
    imports_opts.add_argument("--top", type=int, default=20)

    scale_opts = argparse.ArgumentParser(add_help=False)
    scale_opts.add_argument("--sizes", default="0,100,1000,5000,10000")
    scale_opts.add_argument("--scratch", default=str(Path.home() / "gv-perf-scale"))

    entry_opts = argparse.ArgumentParser(add_help=False)
    entry_opts.add_argument("--argv", default="--version", help="space-separated argv to compare")
    entry_opts.add_argument("--pythonpath", default=None)

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("measure", parents=[common])
    sub.add_parser("imports", parents=[common, imports_opts])
    sub.add_parser("scale", parents=[common, scale_opts])
    sub.add_parser("entrypoint", parents=[common, entry_opts])
    sub.add_parser("budget", parents=[common])
    sub.add_parser("all", parents=[common, imports_opts, scale_opts, entry_opts])

    args = parser.parse_args(argv)
    python = Path(args.python)
    cli = Path(args.cli)
    markdown: list[str] = []
    payload: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": args.runs}

    if args.cmd in ("measure", "all"):
        rows = measure_all(cli, python, args.runs, clean_env())
        payload["measure"] = rows
        markdown.append(render_measure_table(rows, f"Cold start ({args.runs} runs each)"))

    if args.cmd in ("imports", "all"):
        costs = import_costs(python, args.module, clean_env(), args.top)
        payload["imports"] = costs
        markdown.append(
            "### Import cost of `{}` ({} modules, {:.1f} ms cumulative)\n\n"
            "| module | self ms | cumulative ms |\n|---|---:|---:|\n".format(
                costs["module"], costs["modules_imported"], costs["total_ms"]
            )
            + "\n".join(
                f"| `{row['module']}` | {row['self_ms']} | {row['cumulative_ms']} |"
                for row in costs["by_cumulative_ms"]
            )
            + "\n"
        )

    if args.cmd in ("scale", "all"):
        sizes = [int(part) for part in args.sizes.split(",") if part.strip()]
        rows = scale_curve(cli, sizes, max(1, args.runs), Path(args.scratch))
        payload["scale"] = rows
        markdown.append(render_scale_table(rows))
        markdown.append("`GODSV_HOME` pointed at a synthesised runtime for every scale run.")
        payload["scale_growth"] = [growth_ratio(rows, label, sizes) for label, _ in SCALE_COMMANDS]

    if args.cmd in ("entrypoint", "all"):
        rows = entrypoint_compare(
            cli,
            python,
            args.runs,
            getattr(args, "argv", "--version").split(),
            pythonpath=getattr(args, "pythonpath", None),
        )
        payload["entrypoints"] = rows
        markdown.append(render_measure_table(rows, "Entry-point shapes for `--version`"))

    if args.cmd in ("budget", "all"):
        checks, ok = budget_check(cli, python, args.runs)
        payload["budget"] = {"checks": checks, "ok": ok}
        lines = [
            "### Startup budget",
            "",
            "| check | command | measured median / max | budget | ok |",
            "|---|---|---:|---:|---|",
        ]
        for check in checks:
            ceiling = (
                f" (ceiling {check['ceiling']} {check['unit']})" if check.get("ceiling") else ""
            )
            lines.append(
                f"| {check['check']} | `{check['command']}` | "
                f"{check['measured_median']} / {check['measured_max']} {check['unit']} | "
                f"{check['budget']} {check['unit']}{ceiling} | "
                f"{'yes' if check['ok'] else '**NO**'} |"
            )
        markdown.append("\n".join(lines) + "\n")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.cmd == "measure":
        _print_measure(payload["measure"])
    elif args.cmd == "imports":
        costs = payload["imports"]
        print(
            f"{costs['module']}: {costs['modules_imported']} modules, "
            f"{costs['total_ms']} ms cumulative\n"
        )
        print("by cumulative cost (= what deferring the subtree saves):")
        for row in costs["by_cumulative_ms"]:
            print(
                f"  {row['cumulative_ms']:8.2f} ms  cum  "
                f"{row['self_ms']:7.2f} self  {row['module']}"
            )
        print("\nby own (self) cost:")
        for row in costs["by_self_ms"]:
            print(f"  {row['self_ms']:8.2f} ms  self  {row['module']}")
    elif args.cmd == "scale":
        print(render_scale_table(payload["scale"]))
        for growth in payload["scale_growth"]:
            print(
                f"{growth['command']}: "
                + ", ".join(
                    f"{s['from']}→{s['to']} x{s['time_factor']} (exp {s['exponent']})"
                    for s in growth["steps"]
                )
                + ("  ← SUPERLINEAR" if growth["superlinear"] else "")
            )
    elif args.cmd == "budget":
        for check in payload["budget"]["checks"]:
            print(
                "{:22s} median {:>8} {}   worst {:>8} {}   (budget {} / ceiling {})   {}".format(
                    check["check"],
                    check["measured_median"],
                    check["unit"],
                    check["measured_max"],
                    check["unit"],
                    check["budget"],
                    check["ceiling"],
                    "ok" if check["ok"] else "OVER BUDGET",
                )
            )
    elif args.cmd == "entrypoint":
        _print_measure(payload["entrypoints"])
    else:
        print("\n".join(markdown))

    if args.out:
        Path(args.out).write_text("\n".join(markdown), encoding="utf-8")
        print(f"wrote {args.out}")

    if args.cmd == "budget" and not payload["budget"]["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
