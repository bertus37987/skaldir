"""Cold vs warm equivalence: the same command, the same bytes, the same exit code.

This is the proof that the warm session is a *transport*, not a second implementation:
each command runs twice as a real process — once as ``skaldir <cmd>`` and once through a
live session — and the two answers are compared byte for byte. A third run (cold again)
is the control: whatever the mask below has to erase also differs between two cold runs,
so the mask cannot be hiding a warm-path defect.

The mask is deliberately narrow and named: the envelope carries the command's **own**
duration (``duration_ms``, ``started_at``) and human summaries print their own ``· 28ms``,
both of which differ between any two runs. Nothing else is allowed to differ.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    for candidate in ("skaldir", "godsv"):
        try:
            if importlib.util.find_spec(f"{candidate}.{name}") is not None:
                return importlib.import_module(f"{candidate}.{name}")
        except (ImportError, ValueError):  # pragma: no cover
            continue
    raise ImportError(f"neither skaldir nor godsv ships {name}")


warm_session = _load("warm_session")
PKG = warm_session.APP
CLIENT = [sys.executable, "-m", f"{PKG}.warm_cli"]

#: Read-only commands with different shapes: JSON envelope, human text, a self-timing
#: summary, a usage error (exit 1) and a command that fails on purpose (exit 2/7).
COMMANDS: tuple[tuple[str, ...], ...] = (
    ("tools", "list", "--json"),
    ("contract", "dump", "--json"),
    ("registry", "status", "--json"),
    ("tools", "show", "--nope"),
    ("tools", "list"),
)

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?")
_MS_TEXT = re.compile(r"\b\d+(?:\.\d+)?\s?ms\b")
_SELF_TIMING_KEYS = ("duration_ms", "started_at", "ended_at", "elapsed_ms", "latency_ms")


def _console() -> list[str]:
    bindir = Path(sys.executable).parent
    for name in (PKG, "godsvoice"):
        if (bindir / name).exists():
            return [str(bindir / name)]
    return [sys.executable, "-m", PKG]


def _mask(text: str) -> tuple[str, list[str]]:
    """Erase the command's own timing; report exactly what was erased."""
    erased: list[str] = []
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        if _ISO.search(text):
            text, _ = _ISO.subn("<timing>", text)
            erased.append("iso-timestamp")
        if _MS_TEXT.search(text):
            text, _ = _MS_TEXT.subn("<duration>", text)
            erased.append("self-timing-ms")
        return text, sorted(set(erased))

    def walk(node, key=""):
        if isinstance(node, dict):
            return {name: walk(value, name) for name, value in node.items()}
        if isinstance(node, list):
            return [walk(item, key) for item in node]
        if isinstance(node, str) and _ISO.search(node):
            erased.append(key or "iso")
            return _ISO.sub("<timing>", node)
        if (isinstance(node, (int, float)) and not isinstance(node, bool)
                and key in _SELF_TIMING_KEYS):
            erased.append(key)
            return -1
        return node

    return json.dumps(walk(document), sort_keys=True, ensure_ascii=False), sorted(set(erased))


def _run(argv: list[str], env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, env=env,
                          cwd=str(cwd), stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def live_session(tmp_path_factory):
    """One session for the whole module, in a throwaway directory."""
    scratch = tmp_path_factory.mktemp("warm-equiv")
    socket_path = scratch / "warm" / "warm.sock"
    saved = {name: os.environ.get(name) for name in warm_session.env_names("WARM_DIR")}
    for name in warm_session.env_names("WARM_DIR"):
        os.environ[name] = str(socket_path.parent)
    status = warm_session.start_session(socket_path, idle_timeout=600.0, wait_s=30.0)
    assert status.running, f"session did not start: {status.state}: {status.note}"
    env = dict(os.environ)
    for name in ("GODSV_HOME", "SKALDIR_HOME", "GODSV_WARM", "SKALDIR_WARM", "COLUMNS", "LINES"):
        env.pop(name, None)
    env["HERMES_HOME"] = str(scratch / "home")
    env["PYTHONPATH"] = str(REPO / "src")
    Path(env["HERMES_HOME"]).mkdir(parents=True, exist_ok=True)
    cwd = scratch / "cwd"
    cwd.mkdir()
    try:
        yield {"socket": socket_path, "env": env, "cwd": cwd, "session": status}
    finally:
        warm_session.stop_session(socket_path, force=True)
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _warm_argv(live, command) -> list[str]:
    return [*CLIENT, "--warm", "--warm-socket", str(live["socket"]),
            "--warm-prog", _console()[0].rsplit("/", 1)[-1], *command]


@pytest.mark.parametrize("command", COMMANDS, ids=[" ".join(item) for item in COMMANDS])
def test_exit_codes_match_the_cold_path(live_session, command):
    cold = _run([*_console(), *command], live_session["env"], live_session["cwd"])
    warm = _run(_warm_argv(live_session, command), live_session["env"], live_session["cwd"])
    assert cold.returncode == warm.returncode, (cold.stderr[-400:], warm.stderr[-400:])


@pytest.mark.parametrize("command", COMMANDS, ids=[" ".join(item) for item in COMMANDS])
def test_stdout_is_identical_modulo_the_commands_own_timing(live_session, command):
    control = _run([*_console(), *command], live_session["env"], live_session["cwd"])
    cold = _run([*_console(), *command], live_session["env"], live_session["cwd"])
    warm = _run(_warm_argv(live_session, command), live_session["env"], live_session["cwd"])
    cold_masked, cold_keys = _mask(cold.stdout)
    warm_masked, warm_keys = _mask(warm.stdout)
    control_masked, control_keys = _mask(control.stdout)
    assert cold_masked == warm_masked, (
        f"warm stdout differs from cold beyond {warm_keys}:\n"
        f"cold={cold.stdout[:400]!r}\nwarm={warm.stdout[:400]!r}"
    )
    # the control: the erasure is not hiding a warm-only difference
    assert control_masked == cold_masked
    assert set(warm_keys) <= set(cold_keys) | set(control_keys) | {"duration_ms", "started_at"}
    if not cold.stdout:
        assert not warm.stdout


@pytest.mark.parametrize("command", COMMANDS, ids=[" ".join(item) for item in COMMANDS])
def test_stderr_is_identical_modulo_self_timing(live_session, command):
    cold = _run([*_console(), *command], live_session["env"], live_session["cwd"])
    warm = _run(_warm_argv(live_session, command), live_session["env"], live_session["cwd"])
    cold_masked, _ = _mask(cold.stderr)
    warm_masked, _ = _mask(warm.stderr)
    assert cold_masked == warm_masked, (
        f"warm stderr differs from cold:\ncold={cold.stderr[:400]!r}\nwarm={warm.stderr[:400]!r}"
    )


def test_the_warm_path_adds_no_note_when_it_is_used(live_session):
    """A successful warm run is silent about being warm — the bytes are the command's."""
    warm = _run(_warm_argv(live_session, ("tools", "list", "--json")), live_session["env"],
                live_session["cwd"])
    assert "warm session not used" not in warm.stderr
    assert "--warm" not in warm.stderr


def test_json_envelope_is_the_same_document(live_session):
    command = ("tools", "list", "--json")
    cold = json.loads(_run([*_console(), *command], live_session["env"],
                           live_session["cwd"]).stdout)
    warm = json.loads(_run(_warm_argv(live_session, command), live_session["env"],
                           live_session["cwd"]).stdout)
    for key in ("command", "schema_version", "exit_code", "ok", "godsvoice_version"):
        assert cold[key] == warm[key], key
    assert cold["data"] == warm["data"]


def test_the_fast_path_version_is_not_run_warm_but_is_identical(live_session):
    """`--version` is answered by the front door; the client says so and stays cold."""
    cold = _run([*_console(), "--version"], live_session["env"], live_session["cwd"])
    warm = _run(_warm_argv(live_session, ("--version",)), live_session["env"], live_session["cwd"])
    assert cold.returncode == warm.returncode == 0
    assert cold.stdout == warm.stdout
    assert "does not run warm" in warm.stderr


def test_equivalence_survives_a_second_client_from_another_directory(live_session, tmp_path):
    """cwd is the client's, not the session's: a command from elsewhere answers the same."""
    command = ("tools", "list", "--json")
    elsewhere = tmp_path / "other-cwd"
    elsewhere.mkdir()
    cold = _run([*_console(), *command], live_session["env"], elsewhere)
    warm = _run(_warm_argv(live_session, command), live_session["env"], elsewhere)
    assert cold.returncode == warm.returncode == 0
    assert _mask(cold.stdout)[0] == _mask(warm.stdout)[0]


def test_warm_is_faster_than_cold_on_a_read_only_command(live_session):
    """A loose gate (the harness in tools/gv_warm_perf.py owns the real numbers).

    Measured on this box: 6-13 ms in-session for these commands against 210-310 ms cold.
    The bound is deliberately soft so a loaded machine cannot flake it into a false red.
    """
    command = ("tools", "list", "--json")
    cold_times: list[float] = []
    warm_times: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        cold = _run([*_console(), *command], live_session["env"], live_session["cwd"])
        cold_times.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        warm = _run(_warm_argv(live_session, command), live_session["env"], live_session["cwd"])
        warm_times.append((time.perf_counter() - started) * 1000)
        assert cold.returncode == warm.returncode == 0
    cold_p50 = statistics.median(cold_times)
    warm_p50 = statistics.median(warm_times)
    assert warm_p50 <= cold_p50 * 0.85, (
        f"warm p50 {warm_p50:.0f} ms is not meaningfully below cold {cold_p50:.0f} ms"
    )


def test_the_session_served_the_commands_it_reported(live_session):
    status = warm_session.status_of(live_session["socket"])
    assert status.running
    assert status.info["requests"] >= len(COMMANDS)
    assert status.info["last_exit_code"] in (0, 1, 2, 5, 7)
    assert status.info["rss_mb"] > 0
    assert status.info["parser_cache"] is True
