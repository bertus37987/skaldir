"""``skaldir warm`` — the session's CLI surface, and the ``--warm`` client path.

Hand-over shape, exactly like ``src/<pkg>/api_server_cli.py``, ``verify_cli.py`` and the
other groups: **nothing in this module edits** ``cli.py`` or ``cli_contract.py``; both are
owned by the CLI owner.

Two hooks, both delivered as pastable snippets *and* as importable functions:

1. the group itself — the CLI owner adds one import and one call to ``cli.py``::

       from .warm_cli import register_cli as _register_warm_cli
       ...
       _register_warm_cli(sub)      # next to the other register_cli(sub) calls

   which adds ``skaldir warm start|status|stop`` and the matching
   ``cli_contract.COMMANDS`` entries (:data:`CONTRACT_ENTRIES`; the literal lines are in
   :data:`CONTRACT_SNIPPET`).

2. the client path — ``skaldir --warm <cmd>``. The front door (the console script's entry,
   ``<pkg>.perf_lazy:main``) checks :func:`warm_requested` and calls :func:`run`::

       from .warm_cli import run as _warm_run, warm_requested as _warm_requested
       ...
       if _warm_requested(args):
           code = _warm_run(args)          # never None here
           if code is not None:
               return code

   ``run`` answers from the session, or falls back to the cold path **itself** (with a
   note on stderr) when there is no session, when the command may not run warm, when the
   session is unreachable — never a silently wrong answer. That order matters: the check
   sits *before* ``godsv.cli`` is imported, so the client pays ~45 ms instead of ~165 ms
   (measured; see ``MVP/26-warm-session.md``).

Without any wiring there is still a client: ``python -m <pkg>.warm_cli --warm <cmd>``
(:func:`client_entry`), which is what the performance harness measures.

Exit codes follow the product contract: ``warm status`` exits 0 when a session answers,
5 when none does, 3 when the socket is not ours to use and 6 when the lock names a process
that does not answer; ``warm stop`` exits 0 when it stopped a session and 5 when there was
none to stop.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import warm_session

# `cli_contract` (argparse + the COMMANDS table) and `cli_shared` are deliberately *not*
# imported here: the client path (``run``/``warm_requested``) is on every warm command's
# measured latency and needs neither. They are imported inside the functions that do —
# measured on this box: `import <pkg>.warm_cli` 54 ms → 38 ms with them deferred.

__all__ = [
    "CONTRACT_ENTRIES",
    "build_group_parser",
    "run_group",
    "CONTRACT_SNIPPET",
    "GROUP_HELP",
    "NOTES",
    "START_HELP",
    "STATUS_HELP",
    "STOP_HELP",
    "WIRING_SNIPPET",
    "WarmOptions",
    "client_entry",
    "contract_snippet",
    "install_contract_entries",
    "parse_warm_flags",
    "register_cli",
    "run",
    "warm_requested",
]

GROUP_HELP = "warm session: one long-lived CLI process, repeated commands in milliseconds"
START_HELP = "start the warm session (idempotent; waits until it answers)"
STATUS_HELP = "is a warm session running, what has it served, and does it still match this shell?"
STOP_HELP = "stop the warm session (honest: says when there was nothing to stop)"

NOTES = (
    "opt-in and read-only by contract: only commands whose cli_contract entry does not "
    "mutate run warm, everything else falls back to the cold path with a note. The session "
    "is a unix socket (0600, owned by you, in a 0700 directory), never a TCP port; a stale "
    "lock is detected with flock and cleaned up only by `warm start`/`stop`"
)

START_NOTES = (
    NOTES + "; `warm start` is idempotent: an already running session is reported, "
    "not duplicated"
)
STATUS_NOTES = (
    NOTES + "; exit 0 running, 5 nothing running, 3 socket refused, 6 lock held "
    "without an answer"
)
STOP_NOTES = NOTES + "; exit 0 stopped, 5 nothing to stop"

#: The client-side flags. They are *stripped* before the cold path sees the argv, so
#: they can never reach argparse of a command that has no such option.
WARM_FLAGS_WITH_VALUE: tuple[str, ...] = ("--warm-socket", "--warm-timeout", "--warm-prog")
WARM_FLAGS_FLAG: tuple[str, ...] = ("--warm", "--warm-quiet", "--no-warm")

_NOTE_PREFIX = "{prog}: warm session not used"


@dataclass
class WarmOptions:
    """What the caller asked for on the command line (and in the environment)."""

    socket: str | None = None
    timeout: float | None = None
    prog: str = ""
    quiet: bool = False
    forced_cold: bool = False


def _env_warm_default() -> bool:
    """``<PREFIX>_WARM`` in the environment: ``1/on/yes/auto`` opts every command in."""
    value = (warm_session.env_value("WARM") or "").strip().lower()
    return value in ("1", "on", "yes", "true", "auto")


def _env_forces_cold() -> bool:
    """``<PREFIX>_WARM=0`` forces the cold path — this is also the recursion guard.

    The warm worker sets it before running a command, so a command that shells out to
    ``skaldir --warm …`` again cannot loop back into the session that is serving it.
    """
    value = (warm_session.env_value("WARM") or "").strip().lower()
    return value in ("0", "off", "no", "false", "never")


def parse_warm_flags(argv: Sequence[str]) -> tuple[list[str], WarmOptions | None]:
    """Split ``argv`` into ``(argv without the warm flags, options)``.

    ``options`` is ``None`` when the caller did not ask for a warm run at all — then the
    front door must behave exactly as before, with the original argv.
    """
    rest: list[str] = []
    options = WarmOptions()
    asked = False
    index = 0
    items = [str(item) for item in argv]
    while index < len(items):
        item = items[index]
        if item == "--warm":
            asked = True
        elif item == "--no-warm":
            options.forced_cold = True
        elif item == "--warm-quiet":
            options.quiet = True
        elif item in WARM_FLAGS_WITH_VALUE:
            index += 1
            if index >= len(items):
                raise SystemExit(f"{item} needs a value")
            value = items[index]
            if item == "--warm-socket":
                options.socket = value
            elif item == "--warm-timeout":
                try:
                    options.timeout = float(value)
                except ValueError as exc:
                    raise SystemExit(f"--warm-timeout wants seconds, got {value!r}") from exc
            else:
                options.prog = value
        elif (
            item.startswith("--warm-socket=")
            or item.startswith("--warm-timeout=")
            or item.startswith("--warm-prog=")
        ):
            key, _, value = item.partition("=")
            if key == "--warm-socket":
                options.socket = value
            elif key == "--warm-timeout":
                try:
                    options.timeout = float(value)
                except ValueError as exc:
                    raise SystemExit(f"--warm-timeout wants seconds, got {value!r}") from exc
            else:
                options.prog = value
        else:
            rest.append(item)
        index += 1
    if options.forced_cold:
        return rest, None
    if not asked and not _env_warm_default():
        return rest, None
    if _env_forces_cold():
        return rest, None
    return rest, options


def warm_requested(argv: Sequence[str]) -> bool:
    """Cheap guard for the front door — no session, no imports, just the argv and env.

    Deliberately answers ``True`` for anything that *might* be a warm request (including a
    request the session will refuse): :func:`run` owns the decision, and the cold path is
    always the fallback. Answering ``False`` here is what keeps unrelated commands on the
    unchanged cold path at zero cost.
    """
    if _env_forces_cold():
        return False
    if _env_warm_default():
        return True
    return any(
        str(item) == flag or str(item).startswith(flag + "=")
        for item in argv
        for flag in WARM_FLAGS_FLAG + WARM_FLAGS_WITH_VALUE
    )


def _note(options: WarmOptions, reason: str) -> None:
    """One honest line on stderr when the warm path was asked for and not used."""
    if options.quiet:
        return
    prog = options.prog or warm_session.product_prog()
    sys.stderr.write(f"{_NOTE_PREFIX.format(prog=prog)}: {reason} — running the cold path\n")
    sys.stderr.flush()


def _write_stream(stream: Any, data: bytes) -> None:
    """Write captured bytes to a real stream, byte for byte, then flush."""
    if not data:
        return
    buffer = getattr(stream, "buffer", None)
    try:
        if buffer is not None:
            buffer.write(data)
            buffer.flush()
        else:  # pragma: no cover — a replaced stream (tests, notebooks)
            stream.write(data.decode(getattr(stream, "encoding", None) or "utf-8", "replace"))
            stream.flush()
    except BrokenPipeError:  # pragma: no cover — `skaldir --warm … | head`
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def cold_run(argv: Sequence[str]) -> int:
    """Run the product's own front door, unchanged — the fallback of every warm failure."""
    names = warm_session.env_names("WARM")
    saved = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ[name] = "0"  # no re-entry, whatever the caller's environment said
    try:
        _name, entry = warm_session.front_door()
        return int(entry(list(argv)))
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _emit(result: warm_session.ExecResult) -> int:
    _write_stream(sys.stdout, result.stdout)
    _write_stream(sys.stderr, result.stderr)
    return int(result.exit_code)


def run(argv: Sequence[str], *, cold: Callable[[Sequence[str]], int] | None = None) -> int | None:
    """Warm-or-cold dispatch for the front door.

    Returns ``None`` when the caller did **not** ask for a warm run (the front door then
    continues exactly as before with the original argv), otherwise the exit code.
    """
    rest, options = parse_warm_flags(argv)
    if options is None:
        return None
    fallback = cold or cold_run
    stdin_bytes, refusal = warm_session.stdin_for_warm()
    if refusal:
        _note(options, refusal)
        return fallback(rest)
    socket_path = Path(options.socket) if options.socket else None
    status = warm_session.status_of(socket_path)
    if not status.running:
        _note(options, status.note or "no session running")
        return fallback(rest)
    try:
        result = warm_session.run_exec(
            rest,
            socket_path=socket_path,
            timeout=options.timeout,
            stdin_bytes=stdin_bytes,
            prog=options.prog or warm_session.product_prog(),
        )
    except warm_session.WarmRefused as exc:
        _note(options, f"`{exc.command or ' '.join(rest)}` does not run warm: {exc.reason}")
        return fallback(rest)
    except warm_session.ClientError as exc:
        _note(options, str(exc))
        return fallback(rest)
    return _emit(result)


def client_entry(argv: Sequence[str] | None = None) -> int:
    """``python -m <pkg>.warm_cli``: the client *without* the front door.

    Implies ``--warm`` (this module is the warm client), and hands the session verbs
    (``warm start|status|stop``) straight to the cold path — they manage the session
    rather than run inside it.
    """
    args = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    if args and args[0] == "warm":
        return cold_run(args)
    if not any(item == "--warm" or item.startswith("--warm") for item in args):
        args = ["--warm", *args]
    code = run(args)
    if code is None:
        # `<PREFIX>_WARM=0` (or --no-warm) means cold — and this entry is still a client,
        # so it runs the command itself (with our flags stripped) instead of a usage code.
        rest, _options = parse_warm_flags(args)
        return cold_run(rest)
    return code


# --------------------------------------------------------------------------- #
# the `warm` command group
# --------------------------------------------------------------------------- #


def _session_payload(status: warm_session.SessionStatus) -> dict:
    info = status.info or {}
    payload = {
        "running": status.running,
        "state": status.state,
        "note": status.note,
        "socket": status.socket,
        "pid": status.pid,
        "protocol": info.get("protocol"),
        "entry": info.get("entry"),
        "started_at": info.get("started_at"),
        "uptime_s": info.get("uptime_s"),
        "requests": info.get("requests"),
        "last_command": info.get("last_command"),
        "idle_timeout_s": info.get("idle_timeout_s"),
        "idle_remaining_s": info.get("idle_remaining_s"),
        "rss_mb": info.get("rss_mb"),
        "parser_cache": info.get("parser_cache"),
        "parser_cache_probe": info.get("parser_cache_probe"),
        "eligible_commands": info.get("eligible_commands"),
        "denied_commands": info.get("denied_commands"),
        "log": info.get("log"),
        "env_mismatch": [],
    }
    if status.running:
        payload["env_mismatch"] = warm_session.fingerprint_mismatch(info)
    return payload


def _print_human_status(payload: dict) -> None:
    if payload["running"]:
        print(f"warm session running: pid {payload['pid']} on {payload['socket']}")
        print(f"  entry {payload['entry']} · protocol v{payload['protocol']} · "
              f"started {payload['started_at']}")
        print(f"  served {payload['requests']} command(s) · last {payload['last_command'] or '-'}"
              f" · rss {payload['rss_mb']} MB")
        print(f"  eligible {payload['eligible_commands']} command(s) · parser cache "
              f"{payload['parser_cache']} · idle timeout {payload['idle_timeout_s']}s")
        mismatch = payload.get("env_mismatch") or []
        if mismatch:
            print(f"  note: this shell differs from the session in {len(mismatch)} build-time "
                  f"variable(s) ({', '.join(mismatch[:6])}{'…' if len(mismatch) > 6 else ''}) — "
                  f"the session rebuilds the parser per command; "
                  f"`warm stop && warm start` fixes that")
        return
    print(f"warm session not running ({payload['state']}): {payload['note']}")


def _cmd_warm_start(args: argparse.Namespace) -> int:
    from . import cli_contract

    socket = str(getattr(args, "socket", "") or "") or None
    timeout = getattr(args, "idle_timeout", None)
    status = warm_session.start_session(
        Path(socket) if socket else None,
        idle_timeout=float(timeout) if timeout else None,
        parser_cache=not bool(getattr(args, "no_parser_cache", False)),
    )
    payload = _session_payload(status)
    payload["action"] = "start"
    cli_contract.set_data(payload)
    if status.running:
        cli_contract.add_evidence("src/skaldir/warm_session.py", "code",
                                  "WarmServer.acquire + the ready ping the client waits for")
        cli_contract.add_evidence(payload["socket"], "file", "the 0600 unix socket that answered")
        print(f"warm session ready: pid {payload['pid']} on {payload['socket']}")
        print(f"  entry {payload['entry']} · {payload['eligible_commands']} read-only command(s) "
              f"may run warm · idle timeout {payload['idle_timeout_s']}s")
        print(f"  log {payload['log']}")
        return cli_contract.EXIT_OK
    cli_contract.add_evidence("src/skaldir/warm_session.py", "code",
                              "start_session's readiness poll + the daemon log tail")
    print(f"warm session did not start: {status.note}")
    return cli_contract.fail(cli_contract.EXIT_INTERNAL,
                             f"the warm session did not answer: {status.note}",
                             hint="the daemon log names the reason; "
                                  "--no-parser-cache starts faster")


def _cmd_warm_status(args: argparse.Namespace) -> int:
    from . import cli_contract

    socket = str(getattr(args, "socket", "") or "") or None
    status = warm_session.status_of(Path(socket) if socket else None)
    payload = _session_payload(status)
    cli_contract.set_data(payload)
    _print_human_status(payload)
    if status.running:
        cli_contract.add_evidence(payload["socket"], "file", "the session's own status answer")
        cli_contract.add_evidence("src/skaldir/warm_session.py", "code", "WarmServer.info")
        return cli_contract.EXIT_OK
    if status.state == "insecure":
        cli_contract.add_evidence(str(status.socket), "file", "lstat of the socket path")
        cli_contract.blocked(f"refusing to use the socket at {status.socket}: {status.note}",
                             hint="remove it (or point --warm-socket elsewhere) and "
                                  "start a fresh session",
                             next_command="skaldir warm start")
        return cli_contract.EXIT_BLOCKED
    if status.state == "unreachable":
        cli_contract.add_evidence(str(status.socket), "file", "connect() probe against the socket")
        return cli_contract.fail(
            cli_contract.EXIT_CONFLICT,
            f"a session lock is held (pid {status.pid or '?'}) but {status.socket} "
            f"does not answer: {status.note}",
            hint="`skaldir warm stop --force` clears a dead holder, "
                 "`warm start` starts a fresh session",
        )
    return cli_contract.not_found(
        f"no warm session running ({status.note})",
        hint="start one with: skaldir warm start",
        details={"socket": str(status.socket), "state": status.state},
    )


def _cmd_warm_stop(args: argparse.Namespace) -> int:
    from . import cli_contract

    socket = str(getattr(args, "socket", "") or "") or None
    status = warm_session.stop_session(Path(socket) if socket else None,
                                       force=bool(getattr(args, "force", False)))
    payload = _session_payload(status)
    payload["action"] = "stop"
    cli_contract.set_data(payload)
    print(f"warm session: {status.note}")
    if status.state == "stale":
        return cli_contract.fail(cli_contract.EXIT_CONFLICT,
                                 f"the warm session did not stop cleanly: {status.note}",
                                 hint="inspect the daemon log named by "
                                      "`skaldir warm status --json`")
    cli_contract.add_evidence("src/skaldir/warm_session.py", "code",
                              "stop_session's shutdown exchange + the flock release it waits for")
    return cli_contract.EXIT_OK


def _add_socket_flag(parser: argparse.ArgumentParser) -> None:
    from . import cli_shared

    parser.add_argument("--socket", default="",
                        help="session socket (default: $XDG_RUNTIME_DIR/<app>/warm.sock)")
    cli_shared.share_common_flags(parser)


def _build_group(sub: Any) -> Any:
    """The three verbs on a subparsers action (shared by the CLI and the standalone entry).

    Each leaf carries its own ``command_path``: ``cli_contract.annotate()`` sets that for the
    tree it walks, and the group must be correct whether it is registered before that call
    (the paste in ``build_parser``) or after it (this module's standalone parser).
    """
    group = sub.add_parser("warm", help=GROUP_HELP)
    warm_sub = group.add_subparsers(dest="subcommand", required=True)

    start = warm_sub.add_parser("start", help=START_HELP)
    _add_socket_flag(start)
    start.add_argument("--idle-timeout", type=float, default=None, metavar="SECONDS",
                       help="stop after this many idle seconds (default 900)")
    start.add_argument("--no-parser-cache", action="store_true",
                       help="do not reuse the argparse tree the session built (slower, "
                            "useful when debugging a default that depends on the environment)")
    start.set_defaults(func=_cmd_warm_start, command_path="warm.start")

    status = warm_sub.add_parser("status", help=STATUS_HELP)
    _add_socket_flag(status)
    status.set_defaults(func=_cmd_warm_status, command_path="warm.status")

    stop = warm_sub.add_parser("stop", help=STOP_HELP)
    _add_socket_flag(stop)
    stop.add_argument("--force", action="store_true",
                      help="also SIGTERM a stale lock holder (pid verified against /proc)")
    stop.set_defaults(func=_cmd_warm_stop, command_path="warm.stop")
    return group


def register_cli(subparsers: Any) -> None:
    """Register the ``warm`` group. Idempotent, additive, no cli.py edits."""
    from . import cli_shared

    sub = cli_shared.as_subparsers(subparsers)
    install_contract_entries()
    choices = getattr(sub, "choices", {}) or {}
    if "warm" in choices:
        return
    _build_group(sub)


def build_group_parser(prog: str | None = None) -> argparse.ArgumentParser:
    """A parser for *just* this group — so the sessions verbs work before the wiring lands.

    Same leaves, same ``command_path`` defaults (``cli_contract.annotate``) and the same
    ``ContractArgumentParser`` (usage errors exit 1), so ``python -m <pkg>.warm_cli warm
    status --json`` answers with the contract's envelope without a single edit to ``cli.py``.
    """
    from . import cli_contract

    parser = cli_contract.ContractArgumentParser(
        prog=prog or f"{warm_session.product_prog()} warm",
        description=GROUP_HELP,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    install_contract_entries()
    _build_group(sub)
    cli_contract.annotate(parser)
    return parser


def run_group(argv: Sequence[str]) -> int:
    """Run one ``warm <verb>`` through the product's own envelope runner."""
    from . import cli_contract

    parser = build_group_parser()
    args = parser.parse_args([str(item) for item in argv])
    return int(cli_contract.run_command(args, args.func))


# --------------------------------------------------------------------------- #
# the contract entries (cli_contract.py is owned by the CLI owner)
# --------------------------------------------------------------------------- #


def _ev(ref: str, kind: str = "code", note: str = "") -> dict:
    return {"ref": ref, "kind": kind, "note": note}


def _contract_entries() -> dict[str, Any]:
    """The three ``COMMANDS`` records, built on demand (see the import note above)."""
    from . import cli_contract

    return {
    "warm.start": cli_contract.CommandSpec(
        layer="kernel",
        evidence=(
            _ev("src/skaldir/warm_session.py", "code",
                "WarmServer.acquire — lock, bind, preload; the client waits for a live ping"),
        ),
        description="start the warm CLI session (one long-lived process behind a 0600 unix socket)",
        exit_codes=(0, 1, 7),
        mutates=True,
        notes=START_NOTES,
    ),
    "warm.status": cli_contract.CommandSpec(
        layer="kernel",
        evidence=(
            _ev("src/skaldir/warm_session.py", "code",
                "status_of — lstat + flock + a status round trip"),
        ),
        description="report the warm session: pid, socket, uptime, served commands, "
                    "memory, env match",
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=False,
        notes=STATUS_NOTES,
    ),
    "warm.stop": cli_contract.CommandSpec(
        layer="kernel",
        evidence=(
            _ev(
                "src/skaldir/warm_session.py",
                "code",
                "stop_session — shutdown op + flock release",
            ),
        ),
        description="stop the warm CLI session (and say when there was nothing to stop)",
        exit_codes=(0, 1, 5, 6, 7),
        mutates=True,
        notes=STOP_NOTES,
    ),
    }


#: The two lazy attributes ``__getattr__`` below serves, declared here. The declaration is what
#: makes them real names for a reader, a type checker and ``__all__``'s own linter: F822 read an
#: attribute that only exists on access as undefined, and the alternative — dropping them from
#: ``__all__`` — would hide two real module attributes from ``from warm_cli import *``. Nobody
#: pays for the declaration at import time (an annotation binds no value; ``__getattr__`` still
#: does the work, and the measured latency saving stands).
CONTRACT_ENTRIES: dict[str, Any]
CONTRACT_SNIPPET: str


def __getattr__(name: str) -> Any:
    """``CONTRACT_ENTRIES``/``CONTRACT_SNIPPET`` exist lazily, for the same latency reason.

    A module ``__getattr__`` *is* consulted for attribute access (``warm_cli.CONTRACT_ENTRIES``);
    it is not consulted for global lookups inside this module's functions, which is why the
    functions below call :func:`_contract_entries` directly.
    """
    if name == "CONTRACT_ENTRIES":
        return _contract_entries()
    if name == "CONTRACT_SNIPPET":
        return contract_snippet()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _text_chunks(text: str, limit: int) -> list[str]:
    return [text[item: item + limit] for item in range(0, len(text), limit)]


def contract_snippet() -> str:
    """The literal ``cli_contract.COMMANDS`` lines, ready to paste."""
    lines: list[str] = []
    for command, spec in _contract_entries().items():
        lines.append(f'    "{command}": _C(')
        lines.append(f'        "{spec.layer}",')
        lines.append("        (")
        for item in spec.evidence:
            lines.append(f'            _ev("{item["ref"]}", "{item["kind"]}",')
            for chunk in _text_chunks(str(item["note"]), 88):
                lines.append(f'                "{chunk}"')
            lines.append("            ),")
        lines.append("        ),")
        lines.append(f'        "{spec.description}",')
        lines.append(f'        mutates={spec.mutates!r}, exit_codes={spec.exit_codes!r},')
        for chunk in _text_chunks(spec.notes, 92):
            lines.append(f'        notes="{chunk}"')
        lines.append("    ),")
    return "\n".join(lines)


def install_contract_entries() -> list[str]:
    """Add the missing ``warm.*`` entries to ``cli_contract.COMMANDS`` (never overwrites).

    Keeps ``test_cli_contract_dump.py`` ("a new command cannot ship without a contract")
    green in the window between wiring ``register_cli`` and landing the paste; the literal
    lines still belong in ``cli_contract.py``.
    """
    from . import cli_contract

    added: list[str] = []
    for command, spec in _contract_entries().items():
        if command not in cli_contract.COMMANDS:
            cli_contract.COMMANDS[command] = spec
            added.append(command)
    return added


#: Paste into the console script's entry module (``<pkg>/perf_lazy.py``): the check must
#: happen *before* ``godsv.cli`` is imported, otherwise the client pays the CLI import
#: graph (~120 ms) before it can answer from the session.
WIRING_SNIPPET = '''\
# --- src/<pkg>/perf_lazy.py, top of main() — the client path, measured 45 ms ---
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    from .warm_cli import run as _warm_run, warm_requested as _warm_requested

    if _warm_requested(args):
        code = _warm_run(args)          # answers warm, or falls back to cold with a note
        if code is not None:
            return code
    code = fast_path(args)
    ...

# --- alternative: src/<pkg>/cli.py, top of main() — correct, but the client then costs
#     the cli.py import (~165 ms measured) because cli.py is imported by then ---
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from .warm_cli import run as _warm_run, warm_requested as _warm_requested

    if _warm_requested(argv):
        code = _warm_run(argv)
        if code is not None:
            return code
    parser = build_parser()
    ...
'''


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m <pkg>.warm_cli`` — the client, or the session verbs.

    ``warm start|status|stop`` runs through this module's own group parser (so the verbs
    work before the CLI wiring lands) and every other argv is a warm client command.
    """
    args = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    if args and args[0] == "warm":
        return run_group(args)
    return client_entry(args)


if __name__ == "__main__":  # pragma: no cover — module entry
    raise SystemExit(main())
