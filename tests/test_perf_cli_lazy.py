"""`skaldir` cold-start fast path (`godsv.perf_lazy`) — behaviour, not vibes.

Three things are pinned here:

1. the *reason* the fast path exists — PEP 562 module ``__getattr__`` is not
   consulted for global-name lookups inside the module's own functions, so the
   eager ``from .treasury import SpendLedger`` bindings in ``cli.py`` cannot be
   deferred behind a module hook (measured on this interpreter, below);
2. the fast path answers exactly the argv shapes it claims and nothing else;
3. it is byte-identical to the real CLI for those shapes and for a delegated
   command, and it does not drag in the heavy graph.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
CONSOLE_SCRIPT = Path(sys.executable).parent / "skaldir"
CLI_CODE = "import sys;from godsv.cli import main;sys.exit(main())"
FAST_CODE = "import sys;from godsv.perf_lazy import main;sys.exit(main())"

sys.path.insert(0, str(SRC))

from skaldir import perf_lazy  # noqa: E402


def _env() -> dict:
    """The runner's rule: both runtime homes unset, PYTHONPATH pointed at src/."""

    env = dict(os.environ)
    for name in ("HERMES_HOME", "GODSV_HOME"):
        env.pop(name, None)
    env["PYTHONPATH"] = str(SRC)
    return env


def _run(argv: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, env=_env(), timeout=timeout, text=True)


def _console_argv(*argv: str) -> list[str]:
    """The real console script when it exists, else the exact call it makes."""

    if CONSOLE_SCRIPT.exists():
        return [str(CONSOLE_SCRIPT), *argv]
    return [sys.executable, "-c", CLI_CODE, *argv]


# --------------------------------------------------------------- the measured design fact


def test_module_getattr_does_not_serve_global_lookups(tmp_path):
    """The measured reason for a front door instead of a module-level lazy hook.

    If this ever starts failing (a future interpreter serving LOAD_GLOBAL from
    ``__getattr__``), a *simpler* fix becomes possible and this design note —
    and `perf_lazy`'s docstring — must be revisited. Until then, binding
    ``SpendLedger`` lazily is not an option: the name resolves eagerly.
    """

    module = tmp_path / "lazy_probe.py"
    module.write_text(
        textwrap.dedent(
            """
            import sys

            def __getattr__(name):
                value = 42
                setattr(sys.modules[__name__], name, value)
                return value

            def use():
                return VALUE + 1
            """
        ),
        encoding="utf-8",
    )
    probe = _run(
        [
            sys.executable,
            "-c",
            f"import sys;sys.path.insert(0,{str(tmp_path)!r});import lazy_probe;"
            "print(lazy_probe.use())",
        ]
    )
    assert probe.returncode != 0, "module __getattr__ served a LOAD_GLOBAL — design note stale"
    assert "NameError" in probe.stderr
    # ...while plain attribute access does go through __getattr__:
    attr = _run(
        [
            sys.executable,
            "-c",
            f"import sys;sys.path.insert(0,{str(tmp_path)!r});import lazy_probe;"
            "print(lazy_probe.VALUE)",
        ]
    )
    assert attr.returncode == 0 and attr.stdout.strip() == "42"


# ---------------------------------------------------------------------------- the fast path


@pytest.mark.parametrize("argv", [["--version"], ["-V"]])
def test_fast_path_answers_the_version_shapes(argv, capsys):
    assert perf_lazy.fast_path(argv) == 0
    assert capsys.readouterr().out.startswith("skaldir ")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--help"],
        ["-h"],
        ["--json"],
        ["doctor"],
        ["contract", "dump", "--json"],
        ["--version", "--json"],
        ["-V", "extra"],
        ["selftest", "--json"],
    ],
)
def test_fast_path_declines_everything_else(argv):
    assert perf_lazy.fast_path(argv) is None


def test_cached_version_matches_godsv_version():
    """`--version` must not drift from the package's own single source of truth."""

    import skaldir

    assert perf_lazy.cached_version() == skaldir.__version__


def test_cached_version_falls_back_when_no_dist_info(tmp_path):
    assert perf_lazy._version_from_dist_info("definitely-not-installed", [str(tmp_path)]) is None
    assert perf_lazy.cached_version("definitely-not-installed") == "0.0.0+source"


def test_fast_entry_defers_the_heavy_graph(tmp_path):
    """`import godsv.perf_lazy` must stay cheap: no CLI graph, no parser, no yaml."""

    probe = _run(
        [
            sys.executable,
            "-c",
            "import sys;from godsv.perf_lazy import main;"
            "heavy=[m for m in sys.modules if m.split('.')[0] in "
            "('yaml','cryptography','argparse')];"
            "print('HEAVY', heavy);print('CLI', 'godsv.cli' in sys.modules)",
        ]
    )
    assert probe.returncode == 0, probe.stderr
    assert "HEAVY []" in probe.stdout, probe.stdout
    assert "CLI False" in probe.stdout, probe.stdout


# ------------------------------------------------------------------ equivalence with the CLI


def test_fast_entry_version_is_byte_identical_to_the_console_script():
    expected = _run(_console_argv("--version"))
    actual = _run([sys.executable, "-c", FAST_CODE, "--version"])
    assert actual.returncode == expected.returncode == 0
    assert actual.stdout == expected.stdout
    assert actual.stdout.startswith("skaldir ")


def test_fast_entry_delegates_a_real_command_identically():
    """`doctor` (trivial, no db, exit 0) must behave the same through both entries."""

    expected = _run(_console_argv("doctor"))
    actual = _run([sys.executable, "-c", FAST_CODE, "doctor"])
    assert actual.returncode == expected.returncode == 0
    assert actual.stdout == expected.stdout


@pytest.mark.parametrize("argv", [["nope"], ["--not-a-flag"]])
def test_fast_entry_keeps_the_usage_exit_code(argv):
    """argparse usage errors exit 1 through both entries (never silently 0)."""

    expected = _run(_console_argv(*argv))
    actual = _run([sys.executable, "-c", FAST_CODE, *argv])
    assert expected.returncode == actual.returncode == 1


def test_fast_entry_json_command_is_identical():
    """A `--json` envelope survives the wrapper unchanged (modulo timing fields)."""

    expected = _run(_console_argv("contract", "dump", "--json"))
    actual = _run([sys.executable, "-c", FAST_CODE, "contract", "dump", "--json"])
    assert expected.returncode == actual.returncode == 0

    def payload(text: str) -> dict:
        data = json.loads(text)
        for key in ("started_at", "duration_ms"):
            data.pop(key, None)
        return data

    assert payload(actual.stdout) == payload(expected.stdout)


def _load_harness():
    """Import `tools/gv_cli_perf.py` by path (it is a tool, not a package module)."""

    import importlib.util

    spec = importlib.util.spec_from_file_location("gv_cli_perf", REPO / "tools" / "gv_cli_perf.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_perf_harness_is_importable_and_reports_its_budgets():
    """`tools/gv_cli_perf.py` stays a usable tool, and its budgets stay documented."""

    module = _load_harness()
    budgets = module.BUDGETS
    assert budgets["version_fast_ms"]["median"] < budgets["version_ms"]["median"]
    assert budgets["version_fast_ms"]["max"] <= budgets["version_ms"]["max"]
    assert budgets["fast_import_ms"]["median"] > 0
    assert module.DEFAULT_CLI.name == "skaldir"
