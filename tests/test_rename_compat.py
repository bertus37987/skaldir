"""RENAME 1 — the old names still work, the new ones are the product's.

Four promises, each measured rather than asserted by convention:

1. ``import godsv`` **is** ``skaldir`` — the very same module objects, not a re-imported
   copy (module-level state, monkeypatching and ``isinstance`` all stay shared). The
   deprecation notice is a real :class:`DeprecationWarning`, and it appears only on a TTY.
2. ``godsvoice`` is the *same entry point* as ``skaldir``: identical stdout and exit code.
   Its one notice line goes to stderr and only on a TTY, so a pipe, a log file or a
   ``--json`` consumer sees byte-clean streams.
3. ``SKALDIR_*`` is primary and ``GODSV_*`` is still read — for the resolver, the CLI
   envelope's format flag, the LLM layer's ordered env contract, and the ``*_CHECK_*_CMD``
   / vault / theme variables.
4. ``~/.hermes/skaldir`` is the runtime directory this product creates; an installation
   that already has ``~/.hermes/godsv`` keeps using it instead of starting empty.
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
from pathlib import Path

import pytest

from skaldir import cli_contract, env, registry

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
BIN = Path(sys.executable).parent
PYTHON = BIN / "python"

#: The aliases that are allowed to name the two prefixes directly: `env.py` *is* the
#: fallback, `security_audit.py` carries a documented standalone copy of it, and
#: `adapter_http.py`'s literals live inside the *generated* adapter template
#: (``_HTTP_MODULE``) — that module is emitted into the runtime and must stay
#: stdlib-only, so it cannot import the product's env helper at all.
#: ``env.py`` *is* the fallback. ``security_audit.py`` / ``adapter_http.py`` predate the sweep
#: and are pinned here rather than rewritten. ``security_linux.py`` is the sandbox's own child
#: process: it is executed as a **script** (``python <path>/security_linux.py exec …``) precisely
#: so that nothing of the package is imported before the restrictions are applied, which rules
#: ``skaldir.env`` out — it carries its own two-spelling reader (``_prefixed``) instead, and
#: ``test_the_launcher_script_runs_standalone`` proves the no-import property.
PREFIX_LITERAL_EXCEPTIONS = {
    "env.py",
    "security_audit.py",
    "adapter_http.py",
    "security_linux.py",
}


def _clean_env(**extra: str) -> dict:
    """The runner's rule: runtime homes unset, ``PYTHONPATH`` pinned at this checkout."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("HERMES_HOME", "GODSV_HOME", "SKALDIR_HOME")
    }
    environment["PYTHONPATH"] = str(SRC)
    environment.update(extra)
    return environment


def _run(argv: list[str], **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, env=_clean_env(**extra), cwd=REPO, timeout=120
    )


def _run_on_a_pty(argv: list[str]) -> tuple[str, str, int]:
    """Run argv with real ptys as stdout/stderr (``script(1)`` is not installed here)."""
    out_master, out_slave = os.openpty()
    err_master, err_slave = os.openpty()
    proc = subprocess.Popen(
        argv,
        stdout=out_slave,
        stderr=err_slave,
        stdin=subprocess.DEVNULL,
        env=_clean_env(),
        cwd=REPO,
    )
    os.close(out_slave)
    os.close(err_slave)
    chunks: dict[int, list[bytes]] = {out_master: [], err_master: []}
    open_fds = [out_master, err_master]
    while open_fds:
        ready, _, _ = select.select(open_fds, [], [], 10)
        for fd in list(ready):
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if data:
                chunks[fd].append(data)
            else:
                open_fds.remove(fd)
    proc.wait(timeout=30)
    os.close(out_master)
    os.close(err_master)
    return (
        b"".join(chunks[out_master]).decode(),
        b"".join(chunks[err_master]).decode(),
        proc.returncode,
    )


# --------------------------------------------------------------------------- #
# 1. the package alias                                                         #
# --------------------------------------------------------------------------- #


def test_import_godsv_yields_the_very_same_modules():
    import godsv
    import skaldir

    assert godsv is skaldir
    assert godsv.__name__ == "skaldir"
    assert godsv.__version__ == skaldir.__version__

    from godsv import perf_lazy  # noqa: PLC0415 — the import itself is the test

    assert perf_lazy is skaldir.perf_lazy

    import godsv.cli

    assert godsv.cli is skaldir.cli
    assert sys.modules["godsv.cli"] is sys.modules["skaldir.cli"]
    # one module object, one state: a write through the old name is visible in the new one
    skaldir.cli.RENAME_PROBE = 1  # type: ignore[attr-defined]
    assert godsv.cli.RENAME_PROBE == 1  # type: ignore[attr-defined]
    del skaldir.cli.RENAME_PROBE


def test_import_godsv_emits_a_deprecation_warning_on_a_tty_only():
    code = "import godsv, skaldir; assert godsv is skaldir; print('same')"

    piped = _run([str(PYTHON), "-c", code])
    assert piped.returncode == 0, piped.stderr
    assert piped.stdout.strip() == "same"
    assert piped.stderr == "", "a piped stderr must stay byte-clean"

    out, err, code_on_tty = _run_on_a_pty([str(PYTHON), "-c", code])
    assert code_on_tty == 0
    assert out.startswith("same")
    assert "DeprecationWarning" in err
    assert "'godsv' is deprecated" in err


def test_python_m_godsv_is_the_same_entry_point_as_python_m_skaldir():
    alias = _run([str(PYTHON), "-m", "godsv", "--version"])
    primary = _run([str(PYTHON), "-m", "skaldir", "--version"])
    assert alias.returncode == primary.returncode == 0
    assert alias.stdout == primary.stdout
    assert alias.stdout.startswith("skaldir ")
    assert "Traceback" not in alias.stderr
    # `-m godsv.cli` is not an entry point in either spelling, but it must not traceback
    # (it did: runpy asked the alias loader for code it did not have).
    direct = _run([str(PYTHON), "-m", "godsv.cli", "--version"])
    assert "AttributeError" not in direct.stderr
    assert "Traceback" not in direct.stderr


# --------------------------------------------------------------------------- #
# 2. the console script alias                                                  #
# --------------------------------------------------------------------------- #


def test_godsvoice_is_byte_identical_to_skaldir_and_silent_on_a_pipe():
    primary = _run([str(BIN / "skaldir"), "--version"])
    alias = _run([str(BIN / "godsvoice"), "--version"])
    assert primary.returncode == alias.returncode == 0
    assert alias.stdout == primary.stdout
    assert alias.stdout.strip() == f"skaldir {_pyproject_version()}"
    assert alias.stderr == "", "the notice must not reach a pipe or a log"


def test_godsvoice_announces_itself_on_a_tty_only():
    out, err, code = _run_on_a_pty([str(BIN / "godsvoice"), "--version"])
    assert code == 0
    assert out.startswith("skaldir ")
    assert "`godsvoice` is deprecated" in err
    assert err.count("deprecated") == 1, "exactly one notice line"

    out, err, code = _run_on_a_pty([str(BIN / "skaldir"), "--version"])
    assert code == 0
    assert err == "", "the primary script never announces a deprecation"


def _pyproject_version() -> str:
    import tomllib

    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["project"]["version"]


def test_both_scripts_are_declared_for_the_one_entry_point():
    import tomllib

    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    assert scripts["skaldir"] == "skaldir.perf_lazy:main"
    assert scripts["godsvoice"] == scripts["skaldir"], "the alias is the same entry point"

    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "skaldir-agent"


# --------------------------------------------------------------------------- #
# 3. environment variables: SKALDIR_* primary, GODSV_* accepted                #
# --------------------------------------------------------------------------- #


def test_the_env_helper_prefers_the_primary_name_and_still_reads_the_legacy_one(monkeypatch):
    monkeypatch.delenv("SKALDIR_PROBE_X", raising=False)
    monkeypatch.delenv("GODSV_PROBE_X", raising=False)
    assert env.getenv("PROBE_X") is None
    assert env.isset("PROBE_X") is False

    monkeypatch.setenv("GODSV_PROBE_X", "legacy")
    assert env.getenv("PROBE_X") == "legacy"
    assert env.isset("PROBE_X") is True

    monkeypatch.setenv("SKALDIR_PROBE_X", "primary")
    assert env.getenv("PROBE_X") == "primary", "the primary name wins when both are set"

    monkeypatch.setenv("SKALDIR_PROBE_X", "")
    assert env.getenv("PROBE_X") == "", "an explicitly empty primary value is a value"

    monkeypatch.setenv("SKALDIR_PROBE_FLAG", "1")
    assert env.flag("PROBE_FLAG") is True
    monkeypatch.delenv("SKALDIR_PROBE_FLAG")
    monkeypatch.setenv("GODSV_PROBE_FLAG", "yes")
    assert env.flag("PROBE_FLAG") is True


def test_expand_and_child_env_cover_both_spellings():
    assert env.expand(("SKALDIR_A", "OPENAI_K")) == ("SKALDIR_A", "GODSV_A", "OPENAI_K")
    assert env.child_env({"CHECK_ID": "pytest"}) == {
        "SKALDIR_CHECK_ID": "pytest",
        "GODSV_CHECK_ID": "pytest",
    }


def test_a_real_command_reads_the_legacy_runtime_variable():
    """End to end, through the installed console script: `GODSV_HOME` still selects the runtime."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="skaldir-rename-") as tmp:
        dump = _run([str(BIN / "skaldir"), "contract", "dump", "--json"], GODSV_HOME=tmp)
        assert dump.returncode == 0, dump.stderr
        vault = _default_of(json.loads(dump.stdout), "vault.list", "--vault")
        assert vault == str(Path(tmp) / "vault.json")

        primary = _run([str(BIN / "skaldir"), "contract", "dump", "--json"], SKALDIR_HOME=tmp)
        assert primary.returncode == 0, primary.stderr
        assert _default_of(json.loads(primary.stdout), "vault.list", "--vault") == vault


def _default_of(document: dict, command: str, flag: str) -> str:
    for record in document["data"]["commands"]:
        if record["command"] == command:
            for arg in record["args"]:
                if arg["name"] == flag:
                    return arg["default"]
    raise AssertionError(f"{command} {flag} not found in the dump")


def test_the_legacy_format_variable_still_switches_the_envelope_to_json(monkeypatch):
    class _Args:
        json = False
        format = ""

    monkeypatch.delenv("SKALDIR_FORMAT", raising=False)
    monkeypatch.setenv("GODSV_FORMAT", "json")
    assert cli_contract.json_requested(_Args(), dict(os.environ)) is True
    assert cli_contract.json_requested(_Args(), {"SKALDIR_FORMAT": "json"}) is True
    assert cli_contract.json_requested(_Args(), {"GODSV_FORMAT": "text"}) is False


def test_the_llm_layer_accepts_the_legacy_llm_variables(monkeypatch):
    from skaldir import llm_provider

    monkeypatch.delenv("SKALDIR_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SKALDIR_LLM_API_BASE", raising=False)
    monkeypatch.setenv("GODSV_LLM_BASE_URL", "http://legacy.example/v1")
    config = llm_provider.resolve_provider_config(env={"GODSV_LLM_BASE_URL": "http://legacy.example/v1"})
    assert config.base_url == "http://legacy.example/v1"

    config = llm_provider.resolve_provider_config(env={"SKALDIR_LLM_BASE_URL": "http://new.example/v1"})
    assert config.base_url == "http://new.example/v1"


def test_no_module_reads_the_two_prefixes_directly_anymore():
    """The fallback lives in one place — a direct literal read is how it gets forgotten."""
    offenders: list[str] = []
    pattern = re.compile(
        r"""os\.environ(?:\.get\(|\s*\[)["'](SKALDIR_|GODSV_)|["'](SKALDIR_|GODSV_)[A-Z0-9_]*["']\s+in\s+os\.environ"""
    )
    for path in sorted((SRC / "skaldir").rglob("*.py")):
        if path.name in PREFIX_LITERAL_EXCEPTIONS:
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO)}:{line}")
    assert offenders == [], (
        "these read a prefixed variable without the skaldir.env fallback: " + ", ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 4. the runtime directory                                                     #
# --------------------------------------------------------------------------- #


def test_runtime_dir_is_skaldir_and_an_existing_godsv_directory_still_wins(tmp_path):
    fresh = tmp_path / "fresh-hermes-home"
    fresh.mkdir()
    assert registry.default_runtime_dir.__module__ == "skaldir.registry"
    assert registry.runtime_dir_for(fresh) == fresh / "skaldir"
    assert registry.RUNTIME_DIRNAME == "skaldir"
    assert registry.LEGACY_RUNTIME_DIRNAME == "godsv"

    migrating = tmp_path / "existing-hermes-home"
    (migrating / "godsv").mkdir(parents=True)
    assert registry.runtime_dir_for(migrating) == migrating / "godsv", (
        "an installation that already has state keeps it instead of starting empty"
    )

    (migrating / "skaldir").mkdir()
    assert registry.runtime_dir_for(migrating) == migrating / "skaldir", (
        "the primary directory wins as soon as it exists"
    )


def test_the_legacy_runtime_directories_reason_names_the_variable_it_read(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "runtime"))
    context = registry.resolve_registry()
    assert context.runtime_dir == tmp_path / "runtime"
    assert "GODSV_HOME" in context.selection_reason

    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path / "runtime-new"))
    assert "SKALDIR_HOME" in registry.resolve_registry().selection_reason


def test_the_default_runtime_directory_is_reported_as_the_primary_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    monkeypatch.delenv("GODSV_HOME", raising=False)
    assert registry.default_runtime_dir() == tmp_path / "skaldir"
    assert "$HERMES_HOME/skaldir" in registry.resolve_registry().selection_reason


@pytest.mark.parametrize("check", ["SKALDIR_CHECK_RUN_CMD", "GODSV_CHECK_RUN_CMD"])
def test_declared_checks_read_either_prefix(check, monkeypatch, tmp_path):
    """`<workspace>/.godsv/checks.json` and the env variable behind a canonical check id."""
    from skaldir import verify_service

    (tmp_path / ".godsv").mkdir()
    monkeypatch.setenv("GODSV_CHECK_RUN_CMD", "")
    monkeypatch.delenv("SKALDIR_CHECK_RUN_CMD", raising=False)
    monkeypatch.setenv(check, "echo declared")
    command, source = verify_service.declared_command(tmp_path, "run")
    assert command == "echo declared"
    assert "CHECK_RUN_CMD" in source
