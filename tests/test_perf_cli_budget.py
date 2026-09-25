"""Startup budget for `skaldir` — a regression fails the suite, a busy machine does not.

Every check is judged on the **median** of the samples against a budget, plus a loose
ceiling for a single outlier. Both matter: this block was written during a parallel wave
(five sibling suites, load average 7-25 on 12 cores) in which one RSS sample out of three
was measured at 99 MB next to a 47.5 MB median — a false red is worse than no gate. A real
regression (a new eager import, a new parser-time side effect, a handler moved into the
startup path) moves the median by hundreds of milliseconds.

Measured on this Fedora box (2026-09-20, load < 2): interpreter floor 9-11 ms · `skaldir
--version` 35-38 ms · `skaldir contract dump --json` 583-653 ms · `skaldir selftest --json`
887-945 ms · `skaldir --version` RSS 18.9-20.5 MB. The older figures this file used to carry
(226-500 ms for `--version`, 47.5-48.2 MB RSS) were measured before the console script became
the lazy front door — `skaldir/perf_lazy.py` records the switch as 230 ms / 49 MB → 40 ms /
19 MB, and every budget below names the value it was read against.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
CONSOLE_SCRIPT = Path(sys.executable).parent / "skaldir"
CLI_CODE = "import sys;from godsv.cli import main;sys.exit(main())"
FAST_CODE = "import sys;from godsv.perf_lazy import main;sys.exit(main())"

RUNS = 5

#: (median budget, single-outlier ceiling) — identical to `tools/gv_cli_perf.py: BUDGETS`.
VERSION_MS = (1500.0, 3000.0)  # measured 35-38 ms (lazy front door; was 226-500 pre-`perf_lazy`)
VERSION_FAST_MS = (300.0, 900.0)  # measured 30-74 ms median, 142 ms worst
FAST_IMPORT_MS = (200.0, 500.0)  # in-process import, measured 22-24 ms
CONTRACT_DUMP_MS = (3000.0, 6000.0)  # measured 583-653 ms, 155 KB of JSON
#: `selftest --json` parses the tree once and probes eight layers. Measured 372-540 ms
#: while no provider was configured, **3365-3474 ms** once the runtime's `.env` held a
#: credential — the ROUTE probe's `advise()` was one live LLM call of 2.1-2.3 s (MVP/87) —
#: and 887-945 ms once that probe answered offline (`tests/test_perf_selftest_probes.py`
#: pins the no-provider-request property). The budget itself never moved.
SELFTEST_MS = (3000.0, 6000.0)  # measured 887-945 ms
VERSION_RSS_MB = (96.0, 200.0)  # measured 18.9-20.5 MB


def _env() -> dict:
    env = dict(os.environ)
    for name in ("HERMES_HOME", "GODSV_HOME"):
        env.pop(name, None)
    env["PYTHONPATH"] = str(SRC)
    return env


def _console_argv(*argv: str) -> list[str]:
    if CONSOLE_SCRIPT.exists():
        return [str(CONSOLE_SCRIPT), *argv]
    return [sys.executable, "-c", CLI_CODE, *argv]


def _samples(argv: list[str], runs: int = RUNS) -> list[tuple[float, float]]:
    """(wall ms, max RSS MB) per run — RSS from the child's own ``wait4`` rusage."""

    env = _env()
    out: list[tuple[float, float]] = []
    for _ in range(runs):
        started = time.perf_counter()
        proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        assert proc.returncode is not None
        out.append(((time.perf_counter() - started) * 1000.0, usage.ru_maxrss / 1024.0))
    return out


def _gate(label: str, values: list[float], budget: tuple[float, float]) -> float:
    """Median against the budget, worst against the ceiling. Returns the median."""

    median = statistics.median(values)
    worst = max(values)
    shown = ", ".join(f"{value:.0f}" for value in sorted(values))
    assert median <= budget[0], (
        f"{label}: median {median:.0f} > {budget[0]:.0f} (samples {shown}) — "
        "something new is imported or built before argv is answered"
    )
    assert worst <= budget[1], f"{label}: worst {worst:.0f} > ceiling {budget[1]:.0f} ({shown})"
    return median


def test_interpreter_floor_is_reported_and_cheap():
    """The floor the budgets are read against — a sanity check, not a gate."""

    worst = max(sample[0] for sample in _samples([sys.executable, "-c", "pass"], runs=2))
    assert worst < 500.0, f"python -c pass took {worst:.0f} ms — the box is unusable"


def test_version_within_budget():
    _gate(
        "skaldir --version",
        [sample[0] for sample in _samples(_console_argv("--version"))],
        VERSION_MS,
    )


def test_fast_entry_version_within_budget():
    """The startup budget the goal names: `--version` well under 300 ms."""

    _gate(
        "fast entry --version",
        [sample[0] for sample in _samples([sys.executable, "-c", FAST_CODE, "--version"])],
        VERSION_FAST_MS,
    )


def test_fast_module_import_stays_cheap():
    """In-process import cost of the front door — the tightest stable gate.

    Measured 12-15 ms (with the `godsv/__init__.py` snippet applied) and 38-61 ms
    without it, against an interpreter floor of 16-20 ms. If someone re-adds a
    module-level `from .cli import main` to `perf_lazy`, this jumps past 200 ms.
    """

    probe = (
        "import time;t=time.perf_counter();import godsv.perf_lazy;"
        "print((time.perf_counter()-t)*1000)"
    )
    values = []
    for _ in range(3):
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, env=_env(), timeout=60, text=True
        )
        assert result.returncode == 0, result.stderr
        values.append(float(result.stdout.strip()))
    _gate("import godsv.perf_lazy", values, FAST_IMPORT_MS)


def test_the_console_script_is_the_fast_entry_point():
    """The console script MUST be the lazy front door (was: assert it was the slow path).

    History: this test used to assert ``fast * 2 <= slow`` — i.e. that the console script was at
    least twice as slow as the in-process fast code. That premise died the moment the entry point
    was switched to ``godsv.perf_lazy:main`` (measured 0.04 s / 19 MB instead of 0.24 s / 49 MB),
    so the assertion now verifies the FIX instead of the gap: the installed script must cost about
    the same as the fast entry. If someone reverts ``pyproject.toml`` to ``godsv.cli:main``, this
    fails again — which is exactly what we want it to do.
    """

    console = statistics.median(sample[0] for sample in _samples(_console_argv("--version")))
    fast = statistics.median(
        sample[0] for sample in _samples([sys.executable, "-c", FAST_CODE, "--version"])
    )
    assert console <= fast * 2, (
        f"console {console:.0f} ms vs fast entry {fast:.0f} ms — the console script is no longer "
        "the lazy front door (check pyproject: skaldir = \"godsv.perf_lazy:main\")"
    )


def test_memory_budget_measured_from_a_clean_process():
    """Peak RSS is measured where the instrument is valid.

    ``ru_maxrss`` from ``os.wait4`` includes the parent's shared pages after a fork
    (measured: a 313 MB measuring process makes ``python -c pass`` look like 313 MB),
    and this file runs inside a pytest process that has already imported the package.
    So the memory gate shells out to ``tools/gv_cli_perf.py budget``, whose own RSS is
    ~15 MB, and reads the ``version_rss_mb`` check out of it — the same numbers
    `MVP/19-performance.md` quotes.

    Scope (the harness gates two different things): this test owns the **memory**
    metric, and reads it out of the document instead of standing in for the whole
    command. Measured: while the ROUTE probe was spending a 2.1-2.3 s provider request,
    the harness's ``selftest_ms`` check read 3364.8 ms and returned exit 1 next to a
    healthy 18.9 MB RSS — so asserting the exit code here made a *time* defect look like
    a memory result. The wall-clock gates stay mandatory in
    ``test_json_endpoints_within_budget``; the harness's own exit-code contract is
    checked below for agreement, not used as a proxy for RSS.
    """

    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "gv_cli_perf.py"), "budget", "--json", "--runs", "3"],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=600,
    )
    assert result.stdout.strip(), result.stderr[-2000:]
    document = json.loads(result.stdout)
    checks = {item["check"]: item for item in document["budget"]["checks"]}
    assert "version_rss_mb" in checks, sorted(checks)
    # `ok` is exactly "every check within budget" — the harness contract, kept honest
    # here without letting a time overrun masquerade as a memory measurement.
    assert document["budget"]["ok"] is all(item["ok"] for item in checks.values())
    assert result.returncode == (0 if document["budget"]["ok"] else 1), (
        "the harness must exit 0 exactly when every check is within budget"
    )
    rss = checks["version_rss_mb"]
    assert rss["ok"], (
        f"--version RSS median {rss['measured_median']} MB / max {rss['measured_max']} MB "
        f"over budget {VERSION_RSS_MB[0]:.0f} MB (ceiling {VERSION_RSS_MB[1]:.0f} MB)"
    )
    assert rss["measured_median"] <= VERSION_RSS_MB[0]


@pytest.mark.parametrize(
    "argv, budget",
    [
        (("contract", "dump", "--json"), CONTRACT_DUMP_MS),
        (("selftest", "--json"), SELFTEST_MS),
    ],
)
def test_json_endpoints_within_budget(argv, budget):
    _gate(
        f"skaldir {' '.join(argv)}",
        [sample[0] for sample in _samples(_console_argv(*argv))],
        budget,
    )


def test_harness_budget_check_agrees_with_the_test():
    """`tools/gv_cli_perf.py budget` is the same contract the suite enforces."""

    import importlib.util

    spec = importlib.util.spec_from_file_location("gv_cli_perf", REPO / "tools" / "gv_cli_perf.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    budgets = module.BUDGETS
    assert (budgets["version_ms"]["median"], budgets["version_ms"]["max"]) == VERSION_MS
    assert (budgets["version_fast_ms"]["median"], budgets["version_fast_ms"]["max"]) == (
        VERSION_FAST_MS
    )
    assert (budgets["fast_import_ms"]["median"], budgets["fast_import_ms"]["max"]) == FAST_IMPORT_MS
    assert (budgets["version_rss_mb"]["median"], budgets["version_rss_mb"]["max"]) == VERSION_RSS_MB
    assert (budgets["contract_dump_ms"]["median"], budgets["contract_dump_ms"]["max"]) == (
        CONTRACT_DUMP_MS
    )
    assert (budgets["selftest_ms"]["median"], budgets["selftest_ms"]["max"]) == SELFTEST_MS
    assert budgets["version_fast_ms"]["median"] < budgets["version_ms"]["median"]
