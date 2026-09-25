"""Wave 9 — runtime files are never resolved relative to the caller's working directory.

Measured (17.09.): every `--db` / `--events` / `--memory` / `--vault` default was
``.godsv/<file>``, i.e. relative to the cwd. Run from anywhere but the project root,
``skaldir metrics`` answered ``no events db at .godsv/events.db`` while the runtime actually
lives in ``$GODSV_HOME`` / ``$HERMES_HOME/godsv`` (the ONE default,
``registry.default_runtime_dir``). `.godsv` stays the default for the project-scaffolding
commands (``init`` / ``pact-show`` / ``ledger-show``), which are about the cwd on purpose.
"""

from __future__ import annotations

import pytest

from skaldir import cli
from skaldir.incidents import Incident, IncidentStore

DEFAULTS = [
    (["memory-stats"], "db", "memory.db"),
    (["memory-search", "--signature", "fp"], "db", "memory.db"),
    (["metrics"], "events", "events.db"),
    (["metrics"], "memory", "memory.db"),
    (["skills", "list"], "db", "memory.db"),
    (["spend", "list"], "db", "treasury.db"),
    (["vault", "list"], "vault", "vault.json"),
]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A throwaway runtime directory, addressed through the established override."""
    rt = tmp_path / "runtime"
    rt.mkdir()
    monkeypatch.setenv("GODSV_HOME", str(rt))
    return rt


@pytest.mark.parametrize("argv,attr,name", DEFAULTS, ids=[str(item[0]) for item in DEFAULTS])
def test_defaults_resolve_into_the_runtime(runtime, argv, attr, name):
    args = cli.build_parser().parse_args(argv)
    assert getattr(args, attr) == str(runtime / name), getattr(args, attr)


def test_no_default_is_left_cwd_relative():
    """A `.godsv/...` default silently depends on where the user stands."""
    parser = cli.build_parser()
    for argv, attr, _name in DEFAULTS:
        value = str(getattr(parser.parse_args(argv), attr))
        assert not value.startswith(".godsv"), (argv, value)


def test_memory_stats_reads_the_runtime_db_from_a_foreign_cwd(
    runtime, tmp_path, monkeypatch, capsys
):
    """The production path: a real DB in the runtime, the user standing somewhere else."""
    incidents = IncidentStore(runtime / "memory.db")
    incidents.upsert(Incident(signature="fpX", title="Import-Fix", status="active"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.main(["memory-stats"]) == 0
    assert "incidents: 1" in capsys.readouterr().out


def test_a_missing_runtime_db_is_reported_with_the_real_path(
    runtime, tmp_path, monkeypatch, capsys
):
    """Counter-case: nothing in the runtime — say so with the path that was looked at."""
    elsewhere = tmp_path / "elsewhere2"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    code = cli.main(["memory-stats"])
    out = capsys.readouterr().out
    assert code == 1
    assert str(runtime / "memory.db") in out
    assert ".godsv" not in out


def test_an_explicit_db_still_wins(runtime, tmp_path, capsys):
    explicit = tmp_path / "explicit.db"
    code = cli.main(["memory-stats", "--db", str(explicit)])
    out = capsys.readouterr().out
    assert code == 1
    assert str(explicit) in out
    assert str(runtime / "memory.db") not in out
