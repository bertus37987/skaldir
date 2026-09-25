"""The warm session: policy, security, lifecycle and the fallback rules.

Every test here touches a **throwaway** session directory under ``tmp_path`` (never
``$XDG_RUNTIME_DIR``), and the ones that start a real daemon stop it in teardown.

The package is resolved at runtime because this tree ships it under two names while a
rename wave lands (``skaldir`` is current, ``godsv`` the compatibility shim); the module
itself uses relative imports only, so it does not care which one it is imported as.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import io
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import ``<pkg>.<name>`` from whichever package this tree ships."""
    for candidate in ("skaldir", "godsv"):
        try:
            if importlib.util.find_spec(f"{candidate}.{name}") is not None:
                return importlib.import_module(f"{candidate}.{name}")
        except (ImportError, ValueError):  # pragma: no cover — broken/partial tree
            continue
    raise ImportError(f"neither skaldir nor godsv ships {name}")


warm_session = _load("warm_session")
warm_cli = _load("warm_cli")
cli_contract = _load("cli_contract")

PKG = warm_session.APP
CLIENT = [sys.executable, "-m", f"{PKG}.warm_cli"]


def _console() -> list[str]:
    bindir = Path(sys.executable).parent
    for name in (PKG, "godsvoice"):
        if (bindir / name).exists():
            return [str(bindir / name)]
    return [sys.executable, "-m", PKG]


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A live session in a throwaway directory: ``(module, socket path, options)``."""
    sock = tmp_path / "warm" / "warm.sock"
    for name in warm_session.env_names("WARM_DIR"):
        monkeypatch.setenv(name, str(sock.parent))
    status = warm_session.start_session(sock, idle_timeout=120.0, wait_s=30.0)
    assert status.running, f"session did not start: {status.state}: {status.note}"
    yield warm_session, sock
    warm_session.stop_session(sock, force=True)


# --------------------------------------------------------------------------- #
# policy: what may run warm
# --------------------------------------------------------------------------- #


def test_exit_codes_mirror_the_contract():
    """A wrong exit code in the warm path would be a wrong answer, not a slow one."""
    assert (warm_session.EXIT_OK, warm_session.EXIT_USAGE, warm_session.EXIT_NOT_FOUND,
            warm_session.EXIT_INTERNAL) == (cli_contract.EXIT_OK, cli_contract.EXIT_USAGE,
                                            cli_contract.EXIT_NOT_FOUND,
                                            cli_contract.EXIT_INTERNAL)


@pytest.mark.parametrize("argv", [
    ["tools", "list"],
    ["contract", "dump", "--json"],
    ["selftest", "--json"],
    ["doctor"],
])
def test_read_only_commands_may_run_warm(argv):
    assert warm_session.warm_verdict(argv) == "ok"


@pytest.mark.parametrize("argv,reason_part", [
    (["init"], "mutating"),
    (["tools", "build"], "mutating"),
    (["vault", "add"], "mutating"),
    (["mcp", "serve"], "long-running"),
    (["desktop", "start"], "long-running"),
    (["warm", "start"], "long-running"),
    (["no-such-command"], "not a command"),
    ([], "no command"),
    (["--version"], "front door"),
    (["--help"], "front door"),
])
def test_denied_commands_carry_a_reason(argv, reason_part):
    verdict = warm_session.warm_verdict(argv)
    assert isinstance(verdict, warm_session.WarmDenied), verdict
    assert reason_part in verdict.reason


def test_every_eligible_command_is_read_only_per_the_contract():
    """The allowlist is the contract's, not a second hand-written list."""
    eligible, denied = warm_session.warm_eligible_commands()
    assert eligible, "no command at all would be warm — the policy is broken"
    assert all(not cli_contract.COMMANDS[item].mutates for item in eligible)
    assert all(item not in eligible for item in denied)
    for prefix in warm_session.WARM_DENY_PREFIXES:
        assert not [item for item in eligible if item == prefix or item.startswith(prefix + ".")]


def test_denied_unit_is_not_warm_but_the_verdict_module_is_importable():
    """``warm status`` can hand its policy out without importing the CLI graph."""
    eligible, denied = warm_session.warm_eligible_commands()
    assert len(eligible) + len(denied) == len(cli_contract.COMMANDS)


# --------------------------------------------------------------------------- #
# paths and security
# --------------------------------------------------------------------------- #


def test_socket_path_lives_in_the_session_dir_and_honours_both_env_spellings(tmp_path, monkeypatch):
    for name in ("SKALDIR_WARM_DIR", "GODSV_WARM_DIR", "SKALDIR_WARM_SOCKET", "GODSV_WARM_SOCKET",
                 "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert warm_session.session_dir() == tmp_path / PKG
    assert warm_session.default_socket_path() == tmp_path / PKG / "warm.sock"
    for name in warm_session.env_names("WARM_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "elsewhere"))
    assert warm_session.session_dir() == tmp_path / "elsewhere", "an explicit dir wins over XDG"


def test_a_private_runtime_dir_wins_over_the_state_dir(tmp_path, monkeypatch):
    """The socket belongs in $XDG_RUNTIME_DIR when it is ours and not group/other."""
    runtime = tmp_path / "run"
    runtime.mkdir()
    os.chmod(runtime, 0o700)
    monkeypatch.delenv(warm_session.env_names("WARM_DIR")[0], raising=False)
    monkeypatch.delenv(warm_session.env_names("WARM_DIR")[1], raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert warm_session.session_dir() == runtime / PKG
    os.chmod(runtime, 0o777)  # a world-writable runtime dir must not host the socket
    assert warm_session.session_dir() == tmp_path / "state" / PKG


def test_check_socket_security_refuses_a_world_writable_socket(tmp_path):
    path = tmp_path / "wide.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    try:
        os.chmod(path, 0o666)
        ok, reason = warm_session.check_socket_security(path)
        assert not ok and "group/other" in reason
        os.chmod(path, 0o600)
        ok, reason = warm_session.check_socket_security(path)
        assert ok and "0600" in reason
    finally:
        server.close()


def test_check_socket_security_refuses_non_sockets_and_symlinks(tmp_path):
    regular = tmp_path / "not-a-socket"
    regular.write_text("hello")
    ok, reason = warm_session.check_socket_security(regular)
    assert not ok and "not a socket" in reason
    link = tmp_path / "link.sock"
    link.symlink_to(regular)
    ok, reason = warm_session.check_socket_security(link)
    assert not ok and "symlink" in reason


def test_ensure_private_dir_tightens_permissions(tmp_path):
    target = tmp_path / "private" / "nested"
    target.mkdir(parents=True)
    os.chmod(target, 0o755)
    warm_session.ensure_private_dir(target)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o700


def test_status_reports_insecure_for_a_foreign_socket(tmp_path):
    path = tmp_path / "wide.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    try:
        os.chmod(path, 0o666)
        status = warm_session.status_of(path, timeout=0.5)
        assert not status.running and status.state == "insecure"
        assert "group/other" in status.note
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# lifecycle: start, status, stop, stale locks, idle timeout
# --------------------------------------------------------------------------- #


def test_started_session_reports_a_0600_socket_owned_by_us(session):
    ws, sock = session
    mode = stat.S_IMODE(os.stat(sock).st_mode)
    assert mode == 0o600, oct(mode)
    assert os.stat(sock).st_uid == os.getuid()
    assert stat.S_IMODE(os.stat(sock.parent).st_mode) == 0o700
    status = ws.status_of(sock)
    assert status.running and status.pid
    assert status.info["entry"].endswith(":main")
    assert status.info["eligible_commands"] > 0


def test_the_lock_names_the_owner_and_is_released_on_stop(tmp_path, monkeypatch):
    sock = tmp_path / "warm.warm.sock"
    for name in warm_session.env_names("WARM_DIR"):
        monkeypatch.setenv(name, str(sock.parent))
    status = warm_session.start_session(sock, idle_timeout=60.0, wait_s=30.0)
    assert status.running
    lock = warm_session.lock_path_for(sock)
    meta = warm_session.read_lock(lock)
    assert meta["pid"] == status.pid
    assert warm_session.lock_is_held(lock)
    stopped = warm_session.stop_session(sock)
    assert not stopped.running
    assert not warm_session.lock_is_held(lock)
    assert not sock.exists()


def test_a_stale_lock_is_reported_and_taken_over(tmp_path, monkeypatch):
    """A dead daemon's lock file must be named as stale, never trusted as a session."""
    sock = tmp_path / "warm" / "warm.sock"
    for name in warm_session.env_names("WARM_DIR"):
        monkeypatch.setenv(name, str(sock.parent))
    warm_session.ensure_private_dir(sock.parent)
    lock = warm_session.lock_path_for(sock)
    lock.write_text(json.dumps({"pid": 999_999, "socket": str(sock), "protocol": 1}))
    os.chmod(lock, 0o600)
    status = warm_session.status_of(sock)
    assert not status.running
    assert status.state == "stale" and status.pid == 999_999
    started = warm_session.start_session(sock, idle_timeout=60.0, wait_s=30.0)
    try:
        assert started.running, started.note
        assert warm_session.read_lock(lock)["pid"] == started.pid != 999_999
        assert started.pid != 999_999
    finally:
        warm_session.stop_session(sock, force=True)


def test_stop_without_a_session_is_honest(tmp_path):
    sock = tmp_path / "warm" / "warm.sock"
    stopped = warm_session.stop_session(sock)
    assert not stopped.running
    assert stopped.state in ("not_running", "stale")
    assert "no session" in stopped.note or "no socket" in stopped.note


def test_stop_force_refuses_a_pid_that_is_not_this_daemon(tmp_path, monkeypatch):
    """``--force`` must never signal an arbitrary pid out of a hand-written lock file."""
    sock = tmp_path / "warm" / "warm.sock"
    for name in warm_session.env_names("WARM_DIR"):
        monkeypatch.setenv(name, str(sock.parent))
    warm_session.ensure_private_dir(sock.parent)
    lock = warm_session.lock_path_for(sock)
    lock.write_text(json.dumps({"pid": os.getpid(), "socket": str(sock)}))
    status = warm_session.stop_session(sock, force=True)
    assert not status.running
    assert "nothing to force" in status.note or "refusing" in status.note


def test_idle_timeout_stops_the_daemon(tmp_path, monkeypatch):
    sock = tmp_path / "warm" / "warm.sock"
    for name in warm_session.env_names("WARM_DIR"):
        monkeypatch.setenv(name, str(sock.parent))
    status = warm_session.start_session(sock, idle_timeout=1.0, wait_s=30.0)
    assert status.running
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and warm_session.status_of(sock, timeout=0.5).running:
        time.sleep(0.2)
    assert not warm_session.status_of(sock, timeout=0.5).running
    assert not warm_session.lock_is_held(warm_session.lock_path_for(sock))
    assert not sock.exists()


# --------------------------------------------------------------------------- #
# the protocol
# --------------------------------------------------------------------------- #


def test_request_over_the_socket_is_refused_for_a_wrong_protocol_version(session):
    ws, sock = session
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.settimeout(5.0)
        raw.connect(str(sock))
        raw.sendall(json.dumps({"v": 99, "op": "ping"}).encode() + b"\n")
        line = b""
        while not line.endswith(b"\n"):
            line += raw.recv(4096)
    header = json.loads(line)
    assert header["status"] == "error" and "protocol mismatch" in header["reason"]


def test_request_over_the_socket_is_refused_for_an_unknown_op(session):
    ws, sock = session
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.settimeout(5.0)
        raw.connect(str(sock))
        raw.sendall(json.dumps({"v": ws.PROTOCOL_VERSION, "op": "rm-rf"}).encode() + b"\n")
        line = b""
        while not line.endswith(b"\n"):
            line += raw.recv(4096)
    header = json.loads(line)
    assert header["status"] == "error" and "unknown op" in header["reason"]


def test_a_refused_command_is_reported_as_a_refusal_not_as_a_failure(session):
    """The client has to tell "do not run this warm" apart from "the session is broken"."""
    ws, sock = session
    with pytest.raises(ws.WarmRefused) as excinfo:
        ws.run_exec(["desktop", "start"], socket_path=sock)
    assert excinfo.value.command == "desktop.start"
    assert "long-running" in excinfo.value.reason


def test_run_exec_returns_the_bytes_and_the_exit_code(session):
    ws, sock = session
    result = ws.run_exec(["tools", "list", "--json"], socket_path=sock)
    assert result.exit_code == 0
    assert json.loads(result.stdout.decode())["command"] == "tools.list"
    assert result.daemon_ms >= 0.0 and result.daemon_pid > 0
    assert result.cache in ("hit", "rebuild", "off")


def test_the_protocol_survives_a_client_that_hangs_up(session):
    """A half-sent request must not take the daemon down."""
    ws, sock = session
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.connect(str(sock))
        raw.sendall(b'{"v": 1, "op": "exec", "argv": [')
    assert ws.status_of(sock, timeout=2.0).running
    assert ws.run_exec(["tools", "list", "--json"], socket_path=sock).exit_code == 0


def test_client_env_outside_the_watchlist_does_not_force_a_parser_rebuild(session):
    """Measured, not assumed: only the variables the build read may pin the cache."""
    ws, sock = session
    status = ws.status_of(sock)
    keys = status.info.get("env_keys") or []
    assert keys, "the session measured no environment keys — the recorder failed"
    for name in ("HERMES_HOME", "HOME"):
        assert name in keys, keys
    assert ws.fingerprint_mismatch(status.info, os.environ) == []


def test_fingerprint_mismatch_names_the_differing_keys(session):
    ws, sock = session
    info = ws.status_of(sock).info
    env = dict(os.environ)
    env["HERMES_HOME"] = "/somewhere/else"
    assert "HERMES_HOME" in ws.fingerprint_mismatch(info, env)


# --------------------------------------------------------------------------- #
# the client path (`--warm`) and the session verbs
# --------------------------------------------------------------------------- #


def _run(argv, env=None, cwd=None, stdin="", timeout=60):
    """Run a client process; ``stdin`` is text to pipe, or ``None`` for /dev/null."""
    kwargs: dict = {"capture_output": True, "text": True, "timeout": timeout,
                    "env": env or _env(), "cwd": str(cwd) if cwd else None}
    if stdin is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin
    return subprocess.run(argv, **kwargs)


def _env(**extra) -> dict:
    env = dict(os.environ)
    for name in ("GODSV_HOME", "SKALDIR_HOME", "GODSV_WARM", "SKALDIR_WARM"):
        env.pop(name, None)
    env["PYTHONPATH"] = str(REPO / "src")
    env.update({key: str(value) for key, value in extra.items()})
    return env


def test_warm_requested_is_cheap_and_exact(monkeypatch):
    for name in warm_session.env_names("WARM"):
        monkeypatch.delenv(name, raising=False)
    assert not warm_cli.warm_requested(["tools", "list"])
    assert warm_cli.warm_requested(["--warm", "tools", "list"])
    assert warm_cli.warm_requested(["--warm-socket", "/tmp/x", "tools", "list"])
    assert warm_cli.warm_requested(["tools", "list", "--warm-timeout=3"])
    monkeypatch.setenv(warm_session.env_names("WARM")[0], "1")
    assert warm_cli.warm_requested(["tools", "list"])
    monkeypatch.setenv(warm_session.env_names("WARM")[0], "0")
    assert not warm_cli.warm_requested(["--warm", "tools", "list"]), "an explicit 0 must win"


def test_parse_warm_flags_strips_only_our_own_flags():
    rest, options = warm_cli.parse_warm_flags(
        ["tools", "list", "--warm", "--warm-quiet", "--warm-socket", "/tmp/s", "--json"]
    )
    assert rest == ["tools", "list", "--json"]
    assert options is not None and options.socket == "/tmp/s" and options.quiet
    assert warm_cli.parse_warm_flags(["tools", "list"]) == (["tools", "list"], None)


def test_client_falls_back_cold_with_a_note_when_no_session_runs(tmp_path):
    sock = tmp_path / "nothing" / "warm.sock"
    proc = _run([*CLIENT, "--warm", "--warm-socket", str(sock), "tools", "list", "--json"])
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["command"] == "tools.list"
    assert "warm session not used" in proc.stderr and "no socket" in proc.stderr


def test_client_never_silently_ignores_warm_for_a_mutating_command(session):
    ws, sock = session
    proc = _run([*CLIENT, "--warm", "--warm-socket", str(sock), "tools", "show", "nope",
                 "--json"], cwd=REPO)
    assert proc.returncode == 5, proc.stderr[-400:]
    assert "does not run warm" in proc.stderr


def test_client_runs_a_read_only_command_through_the_session(session):
    ws, sock = session
    proc = _run([*CLIENT, "--warm", "--warm-socket", str(sock), "tools", "list", "--json"])
    assert proc.returncode == 0
    document = json.loads(proc.stdout)
    assert document["command"] == "tools.list"
    assert "warm session not used" not in proc.stderr, "the warm path added its own noise"


def test_client_forwards_a_pipe_it_can_already_read(session):
    """Stdin that is already there is forwarded; the note is only for the unknown case."""
    ws, sock = session
    proc = _run([*CLIENT, "--warm", "--warm-socket", str(sock), "tools", "list", "--json"],
                stdin="irrelevant bytes\n")
    assert proc.returncode == 0
    assert "warm session not used" not in proc.stderr


def test_client_stays_cold_when_stdin_may_still_arrive(session):
    """`sleep 1; echo x | skaldir --warm …` cannot be run warm without changing stdin."""
    ws, sock = session
    script = (f"(sleep 1; echo late) | {sys.executable} -m {PKG}.warm_cli --warm "
              f"--warm-socket {sock} tools list --json")
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env=_env(), timeout=60)
    assert proc.returncode == 0
    assert "stdin is a pipe with nothing to read yet" in proc.stderr
    assert json.loads(proc.stdout)["command"] == "tools.list"


def test_client_stays_cold_with_an_explicit_opt_out(session):
    ws, sock = session
    env = _env(**{warm_session.env_names("WARM")[0]: "0"})
    proc = _run([*CLIENT, "--warm", "--warm-socket", str(sock), "tools", "list", "--json"],
                env=env)
    # The switch off wins over --warm (that is also the recursion guard), so the module
    # entry runs the command itself — cold, with the same bytes and exit code.
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["command"] == "tools.list"


def test_warm_start_is_idempotent_and_status_reports_the_session(tmp_path):
    sock = tmp_path / "warm" / "warm.sock"
    env = _env(**{warm_session.env_names("WARM_DIR")[0]: str(sock.parent)})
    first = _run([*CLIENT, "warm", "start", "--socket", str(sock), "--json"], env=env)
    try:
        assert first.returncode == 0, first.stderr
        started = json.loads(first.stdout)["data"]
        assert started["running"] and started["pid"]
        again = _run([*CLIENT, "warm", "start", "--socket", str(sock), "--json"], env=env)
        assert again.returncode == 0
        assert json.loads(again.stdout)["data"]["pid"] == started["pid"], "start must not duplicate"
        status = _run([*CLIENT, "warm", "status", "--socket", str(sock), "--json"], env=env)
        data = json.loads(status.stdout)["data"]
        assert data["running"] and data["requests"] >= 0
        assert data["env_mismatch"] == [], "the CLI client must match the session's build env"
    finally:
        _run([*CLIENT, "warm", "stop", "--socket", str(sock), "--json"], env=env)


def test_warm_status_exit_codes_are_honest(tmp_path):
    sock = tmp_path / "warm" / "warm.sock"
    env = _env(**{warm_session.env_names("WARM_DIR")[0]: str(sock.parent)})
    missing = _run([*CLIENT, "warm", "status", "--socket", str(sock), "--json"], env=env)
    assert missing.returncode == 5, missing.stdout
    assert json.loads(missing.stdout)["data"]["running"] is False
    stopped = _run([*CLIENT, "warm", "stop", "--socket", str(sock), "--json"], env=env)
    assert stopped.returncode == 0
    assert "no session" in json.loads(stopped.stdout)["data"]["note"]


@pytest.fixture
def contract_restored():
    """``install_contract_entries`` mutates the global table — put it back afterwards."""
    before = dict(cli_contract.COMMANDS)
    yield
    cli_contract.COMMANDS.clear()
    cli_contract.COMMANDS.update(before)


def test_the_group_parser_is_usable_without_the_cli_wiring(contract_restored):
    """`python -m <pkg>.warm_cli warm …` works before cli.py is wired at all."""
    parser = warm_cli.build_group_parser()
    args = parser.parse_args(["warm", "status", "--json"])
    assert args.command_path == "warm.status"
    assert callable(args.func)
    assert set(warm_cli.CONTRACT_ENTRIES) == {"warm.start", "warm.status", "warm.stop"}
    installed = warm_cli.install_contract_entries()
    assert isinstance(installed, list)
    assert "warm.status" in cli_contract.COMMANDS
    assert cli_contract.spec_for("warm.status").layer == "kernel"


def _subparsers_of(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    action = next((item for item in parser._actions
                   if isinstance(item, argparse._SubParsersAction)), None)
    assert action is not None
    return action


def test_the_paste_produces_a_real_cli_command(tmp_path, monkeypatch, capsys, contract_restored):
    """The one-line wiring, proven in process: `skaldir warm status` through the envelope.

    ``register_cli(sub)`` is exactly what the CLI owner pastes into ``build_parser()``; this
    test performs that paste on a *copy* of the real parser, so the group's envelope, exit
    codes and ``--json`` behaviour are the product's, not a private parser's.
    """
    cli = _load("cli")
    for name in warm_session.env_names("WARM_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "warm"))
    original = cli.build_parser

    def patched():
        parser = original()
        warm_cli.register_cli(_subparsers_of(parser))
        return parser

    monkeypatch.setattr(cli, "build_parser", patched)
    code = cli.main(["warm", "status", "--json"])
    document = json.loads(capsys.readouterr().out)
    assert document["command"] == "warm.status"
    assert document["exit_code"] == code == cli_contract.EXIT_NOT_FOUND
    assert document["data"]["running"] is False
    assert document["ok"] is False

    status = warm_session.start_session(tmp_path / "warm" / "warm.sock", idle_timeout=60.0,
                                        wait_s=30.0)
    assert status.running, status.note
    try:
        code = cli.main(["warm", "status", "--json"])
        document = json.loads(capsys.readouterr().out)
        assert code == cli_contract.EXIT_OK
        assert document["data"]["running"] is True
        assert document["data"]["pid"] == status.pid
        code = cli.main(["warm", "stop", "--json"])
        document = json.loads(capsys.readouterr().out)
        assert code == cli_contract.EXIT_OK and document["data"]["action"] == "stop"
    finally:
        warm_session.stop_session(tmp_path / "warm" / "warm.sock", force=True)


def test_stdin_for_warm_reads_a_regular_file(tmp_path):
    """`skaldir --warm … < file` may run warm: the bytes are knowable."""
    payload = tmp_path / "input.txt"
    payload.write_text("hello stdin")
    handle = os.open(payload, os.O_RDONLY)
    saved = os.dup(0)
    original = sys.stdin
    try:
        os.dup2(handle, 0)
        sys.stdin = io.TextIOWrapper(io.FileIO(0, "r", closefd=False), encoding="utf-8")
        data, reason = warm_session.stdin_for_warm()
        assert reason == "" and data == b"hello stdin"
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(handle)
        sys.stdin = original


def test_stdin_for_warm_says_nothing_for_a_terminal(session):
    class _Tty:
        def isatty(self) -> bool:
            return True

        buffer = None  # pragma: no cover — a tty never reaches the buffer path

    original = sys.stdin
    try:
        sys.stdin = _Tty()  # type: ignore[assignment]
        assert warm_session.stdin_for_warm() == (b"", "")
    finally:
        sys.stdin = original


def test_the_snippet_and_the_notes_are_delivered_for_the_cli_owner():
    """The hand-over has to be real: the hook, the group snippet and the security notes."""
    assert "warm_requested" in warm_cli.WIRING_SNIPPET
    assert "run as _warm_run" in warm_cli.WIRING_SNIPPET
    assert "perf_lazy.py" in warm_cli.WIRING_SNIPPET
    assert "warm.start" in warm_cli.CONTRACT_SNIPPET
    assert "0600" in warm_cli.NOTES and "unix socket" in warm_cli.NOTES
    assert "no cli.py edits" in warm_cli.register_cli.__doc__
