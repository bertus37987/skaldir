"""The CLI surface for the API server: ``skaldir serve --http`` / ``skaldir daemon serve``.

Hand-over shape, exactly like ``src/skaldir/jev/cli.py`` and ``src/skaldir/verify_cli.py``:

1. the CLI owner adds one import and one call to ``src/skaldir/cli.py``::

       from .api_server_cli import register_cli as _register_api_server_cli
       ...
       _register_api_server_cli(sub)   # right after the other register_cli(sub) calls

2. three entries go into ``src/skaldir/cli_contract.COMMANDS`` — the literal lines are in
   :data:`CONTRACT_SNIPPET`, and :func:`install_contract_entries` installs the identical records
   at runtime when they are missing (so ``tests/test_cli_contract_dump.py`` — "a new command
   cannot ship without a contract" — stays green even before the paste lands).

Nothing in this module edits ``cli.py`` or ``cli_contract.py``; both are owned by the CLI owner.

The commands:

* ``skaldir serve --http [--port P] [--host H] [--token-file F]`` — the HTTP API from
  ``src/skaldir/api_server.py``. ``--json-http`` is accepted as the synonym its name in
  ``MVP/08-test-matrix.md`` §0 uses (``skaldir serve --json-http --port <p>``), so the
  Schemathesis tier can call either spelling.
* ``skaldir daemon serve …`` — the same handler, matching ``MVP/02`` §5.10.
* ``skaldir daemon status [--port P]`` — is a Skaldir API answering there, and what does it
  expose? A real TCP connect plus ``GET /health``; there is no daemon manager behind it, and it
  does not pretend to be one.

Two naming notes for the CLI owner:

* The per-request command deadline is ``--request-timeout``, deliberately **not** ``--timeout``:
  ``--timeout`` is a §4.1 *common* flag a later wave may attach to every leaf, and a duplicate
  option string makes ``argparse`` fail at ``build_parser()`` time — one shared server command
  would then take the whole CLI down.
* No credential is ever mirrored into a ``data`` payload: the token goes to stderr once
  (``sys.stderr``, at start) and never to stdout or a file.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from collections.abc import Sequence
from typing import Any

from . import api_server, cli_contract, cli_shared

GROUP_HELP = "HTTP transport for the GUI and for API testing (one route per CLI command)"

SERVE_HELP = (
    "serve the Skaldir HTTP API: one route per CLI command + an OpenAPI 3.1 document at "
    "/openapi.json (loopback by default, token required for mutating routes)"
)

STATUS_HELP = "report whether a Skaldir API server answers on a host:port (read-only probe)"

SERVE_NOTES = (
    "long-running command: the startup facts — including the API token, which is printed "
    "exactly once — go to stderr so stdout stays reserved for the single envelope; the "
    "envelope is emitted when the server stops. A missing fastapi/uvicorn, a non-loopback host "
    "without --allow-remote, and a taken port all exit 3 (blocked) naming the exact reason"
)

STATUS_NOTES = (
    "read-only TCP connect + GET /health (0.6 s timeouts); exit 5 when nothing answers, so a "
    "GUI can tell 'no server' apart from 'server refused my call'. Reporting here is honest: "
    "there is no daemon manager behind this command"
)

#: ``daemon.status``'s payload schema, used by both the contract entry and the handler.
STATUS_DATA_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "running": {"type": "boolean"},
        "endpoint": {"type": "string"},
        "host": {"type": "string"},
        "port": {"type": "integer"},
        "port_open": {"type": "boolean",
                      "description": "something is listening on the port"},
        "skaldir": {"type": "boolean", "description": "true when GET /health answered"},
        "godsvoice_version": {"type": ["string", "null"]},
        "schema_version": {"type": ["string", "null"]},
        "routes": {"type": ["integer", "null"]},
        "mutating_routes": {"type": ["integer", "null"]},
        "token_required_for_mutations": {"type": ["boolean", "null"]},
        "openapi_path": {"type": ["string", "null"]},
        "excluded": {"type": "array", "items": {"type": "string"}},
        "latency_ms": {"type": "integer"},
    },
    "required": ["running", "endpoint", "host", "port", "port_open", "skaldir", "latency_ms"],
}


def _ev(ref: str, kind: str = "code", note: str = "") -> dict:
    return {"ref": ref, "kind": kind, "note": note}


def _spec(**kwargs: Any) -> cli_contract.CommandSpec:
    return cli_contract.CommandSpec(**kwargs)


#: The three ``COMMANDS`` entries, ready to paste into ``src/skaldir/cli_contract.py``.
#: ``install_contract_entries`` installs exactly these objects when they are missing.
CONTRACT_ENTRIES: dict[str, cli_contract.CommandSpec] = {
    "serve": _spec(
        layer="kernel",
        evidence=(
            _ev("src/skaldir/api_server.py", "code",
                "one route per CLI command, generated from cli_contract.COMMANDS + "
                "build_parser()"),
            _ev("GET /openapi.json", "file", "the OpenAPI 3.1 document served at runtime"),
        ),
        description="serve the HTTP API (one route per CLI command) with an OpenAPI document",
        exit_codes=(0, 1, 3, 7),
        mutates=False,
        notes=SERVE_NOTES,
    ),
    "daemon.serve": _spec(
        layer="kernel",
        evidence=(
            _ev("src/skaldir/api_server.py", "code",
                "one route per CLI command, generated from cli_contract.COMMANDS + "
                "build_parser()"),
            _ev("GET /openapi.json", "file", "the OpenAPI 3.1 document served at runtime"),
        ),
        description="the `daemon` spelling of `skaldir serve` (MVP/02 §5.10)",
        exit_codes=(0, 1, 3, 7),
        mutates=False,
        notes=SERVE_NOTES,
    ),
    "daemon.status": _spec(
        layer="kernel",
        evidence=(
            _ev("src/skaldir/api_server.py", "code",
                "probe_existing_server + status_for_exit; the handler adds the live endpoint"),
        ),
        description="report whether a Skaldir API server answers on a host:port",
        exit_codes=(0, 5, 7),
        mutates=False,
        data_schema=STATUS_DATA_SCHEMA,
        notes=STATUS_NOTES,
    ),
}


def _text_chunks(text: str, limit: int) -> list[str]:
    """Split ``text`` into pieces of at most ``limit`` characters, breaking at spaces."""
    chunks: list[str] = []
    current = ""
    for word in str(text).split(" "):
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


def _paste_literal(text: str, *, room: int) -> list[str]:
    """A string as source lines: ``"one piece"`` or adjacent literals that fit ``room`` columns."""
    chunks = _text_chunks(text, max(24, room - 3))
    if len(chunks) == 1:
        return [json.dumps(chunks[0], ensure_ascii=False)]
    return [json.dumps(chunk + " ", ensure_ascii=False) for chunk in chunks[:-1]] + [
        json.dumps(chunks[-1], ensure_ascii=False)
    ]


def _paste_assignment(key: str, value: str, *, indent: str, room: int = 98) -> list[str]:
    """``key="…"`` with the value wrapped into adjacent literals that stay inside ``room``."""
    prefix = f"{indent}{key}="
    pieces = _paste_literal(value, room=room - len(prefix) - 4)
    lines = [f"{prefix}{pieces[0]}"]
    lines.extend(f"{indent}    {piece}" for piece in pieces[1:])
    lines[-1] += ","
    return lines


def _paste_evidence(item: dict, *, indent: str, room: int = 98) -> list[str]:
    """One ``_ev(…)`` call, wrapped when its note would push the line past ``room``."""
    head = (f"{indent}_ev({json.dumps(item['ref'], ensure_ascii=False)}, "
            f"{json.dumps(item['kind'], ensure_ascii=False)}")
    note = str(item.get("note") or "")
    if not note:
        return [f"{head}),"]
    one_line = f'{head}, {json.dumps(note, ensure_ascii=False)}),'
    if len(one_line) <= room:
        return [one_line]
    pieces = _paste_literal(note, room=room - len(indent) - 4)
    lines = [f"{head},"]
    lines.extend(f"{indent}    {piece}" for piece in pieces[:-1])
    lines.append(f"{indent}    {pieces[-1]}),")
    return lines


def contract_snippet() -> str:
    """The literal block to paste into ``cli_contract.COMMANDS``, generated *from* the entries.

    Generated rather than hand-written so the paste and what :func:`install_contract_entries`
    installs cannot drift apart, and wrapped to stay inside the repo's 100-column lint rule.
    ``tests/test_api_server.py`` executes this text and compares the objects it builds against
    :data:`CONTRACT_ENTRIES`, field by field.
    """
    out: list[str] = [
        "    # -- the API server (kernel): the GUI + Schemathesis transport ------- #"
    ]
    for command, spec in CONTRACT_ENTRIES.items():
        out.append(f'    "{command}": _C(')
        out.append('        layer="kernel",')
        out.append("        evidence=(")
        for item in spec.evidence:
            out.extend(_paste_evidence(item, indent="            "))
        out.append("        ),")
        out.extend(_paste_assignment("description", spec.description, indent="        "))
        out.append(f"        exit_codes={tuple(spec.exit_codes)!r},")
        if spec.mutates:
            out.append("        mutates=True,")
        if spec.requires_credentials:
            out.append("        requires_credentials=True,")
        if spec.data_schema is not None:
            schema_lines = json.dumps(spec.data_schema, indent=4, ensure_ascii=False).splitlines()
            out.append(f"        data_schema={schema_lines[0]}")
            out.extend(f"        {line}" for line in schema_lines[1:])
            out[-1] += ","
        out.extend(_paste_assignment("notes", spec.notes, indent="        "))
        out.append("    ),")
    return "\n".join(out) + "\n"


#: The snippet text, quoted verbatim in the hand-over report.
CONTRACT_SNIPPET = contract_snippet()


def install_contract_entries() -> list[str]:
    """Add the missing contract entries to ``cli_contract.COMMANDS`` (additive, idempotent).

    The CLI owner still has to paste :data:`CONTRACT_SNIPPET` into ``cli_contract.py`` — this
    only keeps the dump invariant (``test_cli_contract_dump.py``) green in the window between
    wiring ``register_cli`` and landing the paste. Existing entries are never overwritten.
    """
    return cli_shared.install_contract_entries(CONTRACT_ENTRIES, cli_contract)


# --------------------------------------------------------------------------- #
# argument registration
# --------------------------------------------------------------------------- #


def _add_serve_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--http", action="store_true",
        help="use the HTTP transport (required; it is the only transport implemented today)",
    )
    parser.add_argument(
        "--json-http", dest="json_http", action="store_true",
        help="synonym of --http (the spelling MVP/08 §0 uses for the Schemathesis tier)",
    )
    parser.add_argument("--port", type=int, default=api_server.DEFAULT_PORT,
                        help=f"TCP port (default: {api_server.DEFAULT_PORT})")
    parser.add_argument("--host", default=api_server.DEFAULT_HOST,
                        help=f"bind address (default: {api_server.DEFAULT_HOST}; loopback "
                             f"unless --allow-remote)")
    parser.add_argument(
        "--allow-remote", dest="allow_remote", action="store_true",
        help="allow a non-loopback bind (explicit opt-in; mutating routes still need a token)",
    )
    parser.add_argument(
        "--token-file", dest="token_file", default="",
        help="read the API token from FILE instead of generating one (0600 recommended)",
    )
    parser.add_argument(
        "--request-timeout", dest="request_timeout", type=float,
        default=api_server.DEFAULT_TIMEOUT_S,
        help=f"deadline for one command in seconds (default: "
             f"{api_server.DEFAULT_TIMEOUT_S:g}; expiry is exit 8 / HTTP 504)",
    )
    parser.add_argument(
        "--max-body-bytes", dest="max_body_bytes", type=int,
        default=api_server.DEFAULT_MAX_BODY_BYTES,
        help=f"maximum request body in bytes (default: "
             f"{api_server.DEFAULT_MAX_BODY_BYTES})",
    )
    parser.add_argument(
        "--workdir", default="",
        help="working directory the served commands run in (default: the server's own cwd)",
    )
    parser.add_argument(
        "--cors-origin", dest="cors_origins", action="append", default=[],
        metavar="ORIGIN", help="allow this browser origin (repeatable; off by default, since "
                               "token-free read-only routes must not be reachable from a page)",
    )
    parser.add_argument(
        "--openapi", default="",
        help="also write the OpenAPI document to FILE at start (the Schemathesis input)",
    )
    parser.add_argument(
        "--transport", default=api_server.DEFAULT_TRANSPORT,
        choices=list(api_server.TRANSPORTS),
        help="how a command is executed: auto (per-request: in-process for the measured "
             "allowlist, else the warm session, else a child process; default), warm (the "
             "running warm session only), inprocess (this process only), spawn (a child "
             "process per request — the pre-streaming behaviour)",
    )
    parser.add_argument(
        "--warm-socket", dest="warm_socket", default="",
        help="unix socket of the warm session to reuse (default: the session's own path, "
             "i.e. $SKALDIR_WARM_SOCKET or $XDG_RUNTIME_DIR/<pkg>/warm.sock)",
    )
    parser.add_argument(
        "--sse-heartbeat", dest="sse_heartbeat", type=float,
        default=api_server.DEFAULT_SSE_HEARTBEAT_S,
        help="seconds of silence before an SSE stream writes a keep-alive comment "
             f"(default: {api_server.DEFAULT_SSE_HEARTBEAT_S:g})",
    )
    parser.add_argument(
        "--keep-alive", dest="keep_alive", type=float,
        default=float(api_server.DEFAULT_KEEP_ALIVE_S),
        help="seconds an idle HTTP connection is kept open for reuse "
             f"(default: {api_server.DEFAULT_KEEP_ALIVE_S:g})",
    )
    parser.add_argument(
        "--log-level", dest="log_level", default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn/log level (default: info)")


def _add_status_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=api_server.DEFAULT_HOST, help="host to probe")
    parser.add_argument("--port", type=int, default=api_server.DEFAULT_PORT, help="port to probe")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output: one JSON envelope on stdout")


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #


def _report_blocked(exc: api_server.ApiServerUnavailable) -> int:
    """Turn a transport refusal into the contract's error record (never a traceback)."""
    # In text mode the CLI prints no envelope, so the reason has to reach stderr or the user
    # sees a bare exit code. In --json mode run_command moves this to the diagnostics stream.
    sys.stderr.write(f"skaldir: {exc.message}\n")
    if exc.hint:
        sys.stderr.write(f"skaldir: hint: {exc.hint}\n")
    return cli_contract.fail(exc.exit_code, exc.message, hint=exc.hint, details=exc.details)


def _cmd_serve(args: argparse.Namespace) -> int:
    if not (getattr(args, "http", False) or getattr(args, "json_http", False)):
        cli_contract.add_evidence("src/skaldir/api_server_cli.py", "code", "flag check")
        return cli_contract.usage_error(
            "the API server needs the HTTP transport, which --http selects",
            hint="skaldir serve --http --port 8765",
        )
    # Validate the port before serve() touches a socket: the OS's bind() raises
    # OverflowError past 65535, which used to surface as a raw traceback (R-1).
    port = int(getattr(args, "port", api_server.DEFAULT_PORT))
    if not 0 <= port <= 65535:
        cli_contract.add_evidence("src/skaldir/api_server_cli.py", "code", "port range")
        return cli_contract.usage_error(
            f"port must be 0-65535, not {port}",
            hint="skaldir serve --http --port 8765 (0-65535)",
        )
    try:
        code = api_server.serve(
            host=str(getattr(args, "host", api_server.DEFAULT_HOST)),
            port=int(getattr(args, "port", api_server.DEFAULT_PORT)),
            allow_remote=bool(getattr(args, "allow_remote", False)),
            token_file=str(getattr(args, "token_file", "") or "") or None,
            timeout_s=float(getattr(args, "request_timeout", api_server.DEFAULT_TIMEOUT_S)),
            max_body_bytes=int(getattr(args, "max_body_bytes",
                                       api_server.DEFAULT_MAX_BODY_BYTES)),
            workdir=str(getattr(args, "workdir", "") or "") or None,
            log_level=str(getattr(args, "log_level", "info")),
            cors_origins=tuple(getattr(args, "cors_origins", []) or ()),
            openapi_out=str(getattr(args, "openapi", "") or "") or None,
            transport=str(getattr(args, "transport", api_server.DEFAULT_TRANSPORT)),
            warm_socket=str(getattr(args, "warm_socket", "") or "") or None,
            sse_heartbeat_s=float(getattr(args, "sse_heartbeat",
                                          api_server.DEFAULT_SSE_HEARTBEAT_S)),
            keep_alive_s=float(getattr(args, "keep_alive",
                                       api_server.DEFAULT_KEEP_ALIVE_S)),
        )
    except api_server.ApiServerUnavailable as exc:
        return _report_blocked(exc)
    cli_contract.set_data(None)
    return code


def _port_open(host: str, port: int, *, timeout_s: float = 0.4) -> bool:
    family = socket.AF_INET6 if ":" in str(host) else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout_s)
        try:
            return probe.connect_ex((str(host), int(port))) == 0
        except OSError:
            return False


def _cmd_daemon_status(args: argparse.Namespace) -> int:
    import time

    host = str(getattr(args, "host", api_server.DEFAULT_HOST))
    port = int(getattr(args, "port", api_server.DEFAULT_PORT))
    endpoint = f"http://{host}:{port}"
    started = time.time()
    health = api_server.probe_existing_server(host, port)
    open_port = health is None and _port_open(host, port)
    duration_ms = int((time.time() - started) * 1000)

    data: dict = {
        "running": health is not None,
        "endpoint": endpoint,
        "host": host,
        "port": port,
        "port_open": bool(health is not None or open_port),
        "skaldir": health is not None,
        "godsvoice_version": None,
        "schema_version": None,
        "routes": None,
        "mutating_routes": None,
        "token_required_for_mutations": None,
        "openapi_path": None,
        "excluded": [],
        "latency_ms": duration_ms,
    }
    if health is not None:
        payload = health.get("data") or {}
        data.update(
            {
                "godsvoice_version": health.get("godsvoice_version"),
                "schema_version": health.get("schema_version"),
                "routes": payload.get("routes"),
                "mutating_routes": payload.get("mutating_routes"),
                "token_required_for_mutations": payload.get("token_required_for_mutations"),
                "openapi_path": f"{endpoint}{api_server.OPENAPI_PATH}",
                "excluded": list(payload.get("excluded") or []),
            }
        )
        cli_contract.set_data(data)
        cli_contract.add_evidence(f"{endpoint}{api_server.HEALTH_PATH}", "run",
                                  "the probed server's own health envelope")
        cli_contract.add_evidence("src/skaldir/api_server.py", "code",
                                  "probe_existing_server + status_for_exit")
        return cli_contract.EXIT_OK

    cli_contract.set_data(data)
    cli_contract.add_evidence(f"tcp://{host}:{port}", "file",
                              "connect_ex probe for a listener")
    if open_port:
        return cli_contract.fail(
            cli_contract.EXIT_CONFLICT,
            f"something is listening on {endpoint} but it is not a Skaldir API server "
            f"(GET {api_server.HEALTH_PATH} did not answer with our envelope)",
            hint="check what holds the port: ss -ltnp | grep :" + str(port),
            details={"endpoint": endpoint, "port_open": True, "skaldir": False},
        )
    return cli_contract.not_found(
        f"no Skaldir API server answers on {endpoint}",
        hint=f"start one with: skaldir serve --http --port {port}",
        details={"endpoint": endpoint},
    )


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #


def register_cli(subparsers: Any) -> None:
    """Register ``serve`` and the ``daemon`` group. Idempotent, additive, no cli.py edits."""
    sub = cli_shared.as_subparsers(subparsers)
    install_contract_entries()
    choices = getattr(sub, "choices", {}) or {}

    if "daemon" not in choices:
        p_daemon = sub.add_parser("daemon", help=GROUP_HELP)
        daemon_sub = p_daemon.add_subparsers(dest="subcommand", required=True)
        p_serve = daemon_sub.add_parser("serve", help=SERVE_HELP)
        _add_serve_flags(p_serve)
        p_serve.set_defaults(func=_cmd_serve)
        p_status = daemon_sub.add_parser("status", help=STATUS_HELP)
        _add_status_flags(p_status)
        p_status.set_defaults(func=_cmd_daemon_status)

    if "serve" not in choices:
        p_top = sub.add_parser("serve", help=SERVE_HELP)
        _add_serve_flags(p_top)
        p_top.set_defaults(func=_cmd_serve)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover — convenience only
    """Standalone entry point (``python -m skaldir.api_server_cli …``) — same parser, same codes."""
    from . import cli

    return cli.main(list(argv) if argv is not None else None)


__all__ = [
    "CONTRACT_ENTRIES",
    "CONTRACT_SNIPPET",
    "GROUP_HELP",
    "SERVE_HELP",
    "SERVE_NOTES",
    "STATUS_DATA_SCHEMA",
    "STATUS_HELP",
    "STATUS_NOTES",
    "contract_snippet",
    "install_contract_entries",
    "register_cli",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
