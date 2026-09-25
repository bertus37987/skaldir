"""The Skaldir API server: one HTTP route per CLI command, OpenAPI from the contract.

This is the transport the GUI (and the Schemathesis tier in ``MVP/08-test-matrix.md`` §0) needs.
It follows ``MVP/02-cli-contract.md`` §5.10: ``daemon serve --http`` / top-level ``skaldir
serve``.

Four design decisions, each with the reason it is the one we need:

1. **The route table is generated, never written.** :func:`routes` walks the *real* parser
   (``cli.build_parser()``) plus ``cli_contract.COMMANDS`` and returns one
   :class:`ApiRoute` per leaf command. A command that lands in the CLI lands in the API in the
   same commit — no second list to keep in sync, no route that outlives its command.

2. **The OpenAPI document comes from the same source.** :func:`openapi_document` derives every
   path, parameter, request body and response schema from those same routes; there is no
   hand-written path anywhere. ``/openapi.json`` serves exactly that document.

3. **A command call is *the command*.** The server runs ``python -m skaldir <command> --json`` and
   returns the CLI's own envelope verbatim (same ``schema_version``, same ``ok``/``exit_code``/
   ``data``/``error``/``evidence``/``warnings``/``next_actions``), and the HTTP status is a pure
   function of the envelope's ``exit_code`` (:data:`HTTP_STATUS_BY_EXIT`). So HTTP cannot drift
   from the CLI: there is only one implementation of each command, and a caller can predict the
   status from the body. Running it out-of-process also buys a real timeout (we can kill a stuck
   command), crash isolation, and freedom from the CLI's module-level emission state.

4. **Loopback by default, token for every mutation.** Binding anywhere else needs the explicit
   ``--allow-remote`` flag. Read-only commands answer without a token so the GUI can draw its
   screens; everything that mutates requires ``Authorization: Bearer <token>`` (or
   ``X-Godsv-Token``), compared in constant time. The token is generated per start, printed
   exactly once, and never written to disk by this module.

Safety rails that are not optional: request body size limit, per-request timeout, no secrets in
logs (a redacting formatter), and a hard refusal to start when the port is taken.

Dependencies: ``fastapi`` and ``uvicorn`` are imported lazily so ``skaldir`` keeps working
without them; a missing one is exit ``3`` (blocked) with the install hint, never a traceback.
They are installed in this repo's venv but are *not* declared in ``pyproject.toml`` yet — the
packaging owner should add an ``api`` extra (see :data:`INSTALL_HINT`).
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import io
import json
import logging
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__, cli_contract
from .env import expand, getenv

# --------------------------------------------------------------------------- #
# constants: the contract's exit codes mapped onto HTTP, once
# --------------------------------------------------------------------------- #

#: The mapping the task fixes: exit code → HTTP status. Total, and a pure function.
HTTP_STATUS_BY_EXIT: dict[int, int] = {
    cli_contract.EXIT_OK: 200,                    # 0
    cli_contract.EXIT_USAGE: 400,                 # 1
    cli_contract.EXIT_VERIFICATION_FAILED: 422,   # 2
    cli_contract.EXIT_BLOCKED: 409,               # 3
    cli_contract.EXIT_NEEDS_CREDENTIALS: 401,     # 4
    cli_contract.EXIT_NOT_FOUND: 404,             # 5
    cli_contract.EXIT_CONFLICT: 409,              # 6
    cli_contract.EXIT_INTERNAL: 500,              # 7
    cli_contract.EXIT_UNKNOWN: 504,               # 8
}

#: One-line reasons for the document/description, measured from the table above.
HTTP_STATUS_NOTE = (
    "HTTP status is a pure function of the envelope's exit_code: "
    "0→200, 1→400, 2→422, 3→409, 4→401, 5→404, 6→409, 7→500, 8→504. "
    "A 401 therefore means either 'token missing/invalid' (server) or "
    "'needs_credentials' (command) — the envelope's error.details.reason tells them apart."
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT_S = 120.0
MAX_TIMEOUT_S = 3600.0
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
#: Seconds an idle HTTP connection is kept open for reuse. The default was 5 s, which is shorter
#: than a client's own idle gap between two calls on the same UI screen — every such call then
#: paid a fresh TCP connect (and, behind a proxy, a fresh TLS handshake). Measured in MVP/56 §5.
DEFAULT_KEEP_ALIVE_S = 30
#: Every route lives under this prefix and its remaining path *is* the command
#: (``tools.show`` → ``/cli/tools/show``). That spelling is deliberate:
#: ``tools/gv_schemathesis_run.py`` already compiles its document with
#: ``command.replace(".", "/")`` under ``/cli``, so the Schemathesis tier can point its
#: existing document at this server with just ``--base-url`` instead of rewriting its scheme.
API_PREFIX = "/cli"
HEALTH_PATH = "/health"
OPENAPI_PATH = "/openapi.json"

#: Flags the CLI contract attaches to *every* leaf (``cli_contract.annotate``) and which the
#: server owns: it always passes ``--json`` (the envelope is the wire format) and
#: ``--no-color``. A caller cannot set them (they are not part of any request schema).
RESERVED_ARGS = frozenset({"json", "format", "quiet", "no_color"})

#: Commands the CLI contract declares as streaming (`MVP/02` §4.4). The transport supports
#: NDJSON for any route; these are the ones whose *own* output is a continuing stream of
#: envelopes, so the document marks them and the CLI should stream them too.
STREAM_COMMANDS = frozenset({"run.watch", "run.start", "daemon.logs", "api.stream"})

#: The wire format for ``?stream=1`` when the caller asks for events (``Accept: text/event-stream``)
#: instead of lines (``application/x-ndjson``). Both are the *same* envelope stream; SSE adds a
#: three-event shape (``begin`` / ``progress`` / ``end``) a browser ``EventSource`` can consume
#: without a JSON parser of its own, plus keep-alive comments for a run that is silent for minutes.
SSE_MEDIA_TYPE = "text/event-stream"
NDJSON_MEDIA_TYPE = "application/x-ndjson"
#: The event names, in order. Pinned here because they are the contract a client codes against.
SSE_EVENTS: tuple[str, str, str] = ("begin", "progress", "end")
#: Version of the frame shapes documented in ``MVP/56-api-latency.md`` §3.
SSE_STREAM_CONTRACT = "sse-1"
#: Seconds of silence after which the server writes one ``: keep-alive <iso>`` comment line. A
#: comment frame is ignored by ``EventSource`` and by every SSE parser, and it is what keeps a
#: long run visible (and a proxy from closing the connection).
DEFAULT_SSE_HEARTBEAT_S = 10.0
#: ``retry:`` hint (ms) written into the ``begin`` frame — what a browser should wait before it
#: reconnects after a dropped stream.
DEFAULT_SSE_RETRY_MS = 3000

#: How a command is actually executed. ``auto`` picks per request (see :func:`transport_for`).
TRANSPORTS: tuple[str, ...] = ("auto", "warm", "inprocess", "spawn")
DEFAULT_TRANSPORT = "auto"
SPAWN_TRANSPORT = "spawn"
WARM_TRANSPORT = "warm"
IN_PROCESS_TRANSPORT = "inprocess"

#: Environment names the in-process path pins. ``build_parser()`` and the commands built from it
#: read a handful of variables (MVP/26 §4 measures the set for the warm session); the server
#: snapshots them when it builds its parser and refuses the in-process path if one of them moved,
#: because a cached parser freezes what the build read.
PARSER_ENV_KEYS: tuple[str, ...] = (
    "HOME", "PWD", "HERMES_HOME", "GODSV_HOME", "SKALDIR_HOME",
    "SKALDIR_FORMAT", "GODSV_FORMAT", "SKALDIR_API_TOKEN", "GODSV_API_TOKEN",
)

#: Commands that cannot be an HTTP request/response route, with the reason. Every entry is
#: reported by :func:`exclusions` (and in the OpenAPI document's ``x-godsv-excluded``) so the
#: omission is visible instead of silent.
NON_HTTP: dict[str, str] = {
    "mcp.serve": "speaks the MCP protocol on stdin/stdout and does not return — an HTTP "
                 "request could never complete",
    "serve": "alias of daemon.serve — this is the server itself",
    "daemon.serve": "this is the server itself",
    "daemon.start": "spawns a server process; not a request/response route",
    "daemon.stop": "manages the server process that would answer the request",
    "daemon.logs": "tailable server log stream, not a per-command payload",
}

INSTALL_HINT = "install the API extra: .venv/bin/pip install 'fastapi>=0.115' 'uvicorn>=0.30'"

TOKEN_ENV = "SKALDIR_API_TOKEN"
DEFAULT_TOKEN_BYTES = 32

#: The two accepted ways to present the token. The ``Authorization`` header is canonical; the
#: second exists for webviews that cannot set it. The token is never accepted in a query string
#: (that would put a secret in every access log).
AUTH_HEADER = "authorization"
TOKEN_HEADER = "x-godsv-token"

REDACTED = "***redacted***"


class ApiServerUnavailable(RuntimeError):
    """The transport cannot be provided here (missing dependency, blocked host, taken port)."""

    def __init__(self, message: str, *, hint: str = "", details: dict | None = None,
                 exit_code: int = cli_contract.EXIT_BLOCKED) -> None:
        super().__init__(message)
        self.message = str(message)
        self.hint = str(hint)
        self.details = dict(details or {})
        self.exit_code = int(exit_code)


class ApiRequestError(Exception):
    """The request itself is wrong (unknown argument, bad value) — exit 1 / HTTP 400."""

    def __init__(self, message: str, *, hint: str = "", details: dict | None = None) -> None:
        super().__init__(message)
        self.message = str(message)
        self.hint = str(hint)
        self.details = dict(details or {})


# --------------------------------------------------------------------------- #
# dependencies, imported lazily
# --------------------------------------------------------------------------- #


def fastapi_modules() -> tuple[Any, Any]:
    """Return ``(fastapi, uvicorn)`` or raise :class:`ApiServerUnavailable`."""
    try:
        import fastapi  # noqa: PLC0415 — lazy on purpose: the CLI must work without it
        import uvicorn  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover — measured in the venv (fastapi 0.141.1)
        raise ApiServerUnavailable(
            f"the HTTP transport needs fastapi and uvicorn ({exc})",
            hint=INSTALL_HINT,
        ) from exc
    return fastapi, uvicorn


# --------------------------------------------------------------------------- #
# the route table (generated from the contract)
# --------------------------------------------------------------------------- #


def _parser() -> argparse.ArgumentParser:
    from . import cli  # noqa: PLC0415 — imported here so this module stays standalone-testable

    return cli.build_parser()


def _leaves(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return {path: sub for path, sub in cli_contract.leaf_commands(parser)}


def _action_index(sub: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {action.dest: action for action in sub._actions}  # noqa: SLF001 — argparse API


def arg_specs(sub: argparse.ArgumentParser, *, exclude_common: bool = True) -> list[dict]:
    """The command's callable arguments, from ``cli_contract.args_of`` plus what it drops.

    ``args_of`` is the contract's own normaliser, so the names/types/choices/help here are the
    same ones ``skaldir contract dump --json`` publishes. On top of it we record what an HTTP
    client needs and the dump does not carry: ``nargs`` (to know a ``nargs="?"`` positional is
    optional and a ``nargs="*"`` one is repeatable), ``multiple`` and ``optional``.
    """
    records = cli_contract.args_of(sub)
    actions = _action_index(sub)
    out: list[dict] = []
    for record in records:
        dest = record["dest"]
        if exclude_common and dest in RESERVED_ARGS:
            continue
        action = actions.get(dest)
        nargs = getattr(action, "nargs", None)
        multiple = bool(nargs in ("*", "+")) or isinstance(action, argparse._AppendAction)  # noqa: SLF001
        optional_positional = bool(record["positional"] and nargs in ("?", "*"))
        item = dict(record)
        item["nargs"] = nargs
        item["multiple"] = multiple
        item["optional"] = optional_positional
        item["required"] = False if optional_positional else bool(record["required"])
        out.append(item)
    return out


def mutually_exclusive_groups(sub: argparse.ArgumentParser) -> list[list[str]]:
    """Argument groups the CLI refuses to see together (e.g. ``registry.migrate``).

    ``argparse``'s group object shares the parser's ``_actions`` list (``_ArgumentGroup
    .__init__`` does ``self._actions = container._actions``), so reading ``group._actions``
    reports *every* option of any parser that owns one exclusive group — measured on
    ``code.run``: 22 "members" instead of ``--file``/``--stdin``, which refused every request
    that needed two arguments. The group's own set is ``_group_actions``.
    """
    groups: list[list[str]] = []
    for group in getattr(sub, "_mutually_exclusive_groups", []) or []:
        members = getattr(group, "_group_actions", None)
        if members is None:  # pragma: no cover — an argparse without _group_actions
            members = getattr(group, "_actions", [])
        names = [action.option_strings[0] for action in members if action.option_strings]
        if names:
            groups.append(sorted(names))
    return sorted(groups)


@dataclass(frozen=True)
class ApiRoute:
    """One CLI leaf command, as an HTTP route."""

    command: str
    group: str
    layer: str
    description: str
    notes: str
    mutates: bool
    requires_credentials: bool
    exit_codes: tuple[int, ...]
    args: tuple[dict, ...]
    data_schema: dict
    streams: bool = False
    mutually_exclusive: tuple[tuple[str, ...], ...] = ()

    @property
    def path(self) -> str:
        return f"{API_PREFIX}/{self.command.replace('.', '/')}"

    @property
    def argv(self) -> list[str]:
        """The CLI words for this command (``tools.show`` → ``["tools", "show"]``)."""
        return self.command.split(".")

    @property
    def operation_id(self) -> str:
        return re.sub(r"[^0-9a-zA-Z_]+", "_", self.command).strip("_")

    def arg(self, dest: str) -> dict | None:
        return next((item for item in self.args if item["dest"] == dest), None)

    def as_dict(self) -> dict:
        return {
            "command": self.command,
            "path": self.path,
            "group": self.group,
            "layer": self.layer,
            "description": self.description,
            "mutates": self.mutates,
            "requires_credentials": self.requires_credentials,
            "exit_codes": list(self.exit_codes),
            "streams": self.streams,
            "args": [dict(item) for item in self.args],
            "mutually_exclusive": [list(item) for item in self.mutually_exclusive],
            "data_schema": self.data_schema,
        }


def routes(parser: argparse.ArgumentParser | None = None) -> list[ApiRoute]:
    """One :class:`ApiRoute` per exposable CLI leaf, sorted by command."""
    parser = parser or _parser()
    out: list[ApiRoute] = []
    for path, sub in _leaves(parser).items():
        if path in NON_HTTP:
            continue
        spec = cli_contract.spec_for(path)
        out.append(
            ApiRoute(
                command=path,
                group=path.rsplit(".", 1)[0] if "." in path else "",
                layer=spec.layer,
                description=spec.description,
                notes=spec.notes,
                mutates=bool(spec.mutates),
                requires_credentials=bool(spec.requires_credentials),
                exit_codes=tuple(spec.exit_codes),
                args=tuple(arg_specs(sub)),
                data_schema=spec.data_schema or cli_contract.NO_SCHEMA,
                streams=path in STREAM_COMMANDS,
                mutually_exclusive=tuple(tuple(item) for item in
                                         mutually_exclusive_groups(sub)),
            )
        )
    out.sort(key=lambda route: route.command)
    return out


def exclusions(parser: argparse.ArgumentParser | None = None) -> list[dict]:
    """Commands the API deliberately does not expose, each with its reason."""
    present = set(_leaves(parser or _parser()))
    return [
        {"command": command, "reason": reason, "present_in_parser": command in present}
        for command, reason in sorted(NON_HTTP.items())
    ]


def route_for(command: str, parser: argparse.ArgumentParser | None = None) -> ApiRoute:
    for route in routes(parser):
        if route.command == command:
            return route
    raise ApiRequestError(f"unknown command: {command}",
                          hint="see GET /openapi.json or `skaldir contract dump --json`")


# --------------------------------------------------------------------------- #
# building the CLI command line from a JSON request
# --------------------------------------------------------------------------- #


def build_argv(route: ApiRoute, values: Mapping[str, Any]) -> list[str]:
    """Turn ``{arg_dest: value}`` into the CLI argv for ``route``.

    Only what the caller sent is passed: no default is ever replayed from the dump, so the CLI
    stays the one place that decides what an omitted argument means.
    """
    by_dest = {item["dest"]: item for item in route.args}
    unknown = sorted(set(values) - set(by_dest))
    if unknown:
        raise ApiRequestError(
            f"unknown argument(s) for {route.command}: {', '.join(unknown)}",
            hint="allowed: " + ", ".join(sorted(by_dest)) or "(none)",
            details={"unknown": unknown, "allowed": sorted(by_dest)},
        )
    if not by_dest and values:
        raise ApiRequestError(f"{route.command} takes no arguments",
                              details={"given": sorted(values)})

    positional: list[str] = []
    options: list[str] = []
    for dest, spec in by_dest.items():
        raw = values.get(dest, None)
        if raw is None or raw == []:
            if spec["required"] and not spec["positional"]:
                raise ApiRequestError(
                    f"{route.command} requires {spec['name']}",
                    hint=f"send {{\"{dest}\": ...}} in the request body",
                    details={"missing": dest},
                )
            if spec["required"] and spec["positional"]:
                raise ApiRequestError(
                    f"{route.command} requires the positional argument {spec['name']}",
                    hint=f"send {{\"{dest}\": ...}} in the request body",
                    details={"missing": dest},
                )
            continue

        if spec["positional"]:
            items = raw if isinstance(raw, (list, tuple)) else [raw]
            if spec["multiple"] and not isinstance(raw, (list, tuple)):
                items = [raw]
            positional.extend(_scalar(route, spec, item) for item in items)
            continue

        if spec["type"] == "flag":
            if not isinstance(raw, bool):
                raise ApiRequestError(
                    f"{route.command} {spec['name']} is a flag: send true or false",
                    details={"argument": spec["name"], "value": raw},
                )
            if raw:
                options.append(spec["name"])
            continue

        items = raw if isinstance(raw, (list, tuple)) else [raw]
        for item in items:
            options.append(spec["name"])
            options.append(_scalar(route, spec, item))

    if route.mutually_exclusive:
        given = {option for option in options if option.startswith("-")}
        for group in route.mutually_exclusive:
            hit = sorted(given.intersection(group))
            if len(hit) > 1:
                raise ApiRequestError(
                    f"{route.command} accepts only one of {', '.join(group)} "
                    f"(got {', '.join(hit)})",
                    details={"mutually_exclusive": list(group), "given": hit},
                )

    # `--json` is the wire format and `--no-color` keeps ANSI out of the payload's company;
    # the server owns both (they are RESERVED_ARGS, so a caller cannot have sent them).
    return [*route.argv, *positional, *options, "--json", "--no-color"]


def _scalar(route: ApiRoute, spec: dict, value: Any) -> str:
    name = spec["name"]
    if isinstance(value, bool):
        raise ApiRequestError(f"{route.command} {name} does not take a boolean",
                             details={"argument": name, "value": value})
    if spec["type"] == "choice":
        text = str(value)
        if spec["choices"] and text not in [str(item) for item in spec["choices"]]:
            raise ApiRequestError(
                f"{route.command} {name}={text!r} is not one of "
                f"{', '.join(str(item) for item in spec['choices'])}",
                details={"argument": name, "choices": list(spec["choices"])},
            )
        return text
    if spec["type"] == "integer":
        try:
            return str(int(value))
        except (TypeError, ValueError) as exc:
            raise ApiRequestError(f"{route.command} {name} must be an integer, got {value!r}",
                                  details={"argument": name, "value": value}) from exc
    if spec["type"] == "number":
        try:
            return str(float(value))
        except (TypeError, ValueError) as exc:
            raise ApiRequestError(f"{route.command} {name} must be a number, got {value!r}",
                                  details={"argument": name, "value": value}) from exc
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def body_arguments(body: Any) -> dict:
    """Read the argument mapping out of a JSON request body.

    Accepted: the argument mapping itself, or ``{"args": {...}}``. ``_``-prefixed keys are
    request metadata (``_stream``), never CLI arguments.
    """
    if body is None or body == "":
        return {}
    if not isinstance(body, dict):
        raise ApiRequestError("the request body must be a JSON object of arguments",
                              details={"type": type(body).__name__})
    for meta in ("args", "arguments"):
        inner = body.get(meta)
        if isinstance(inner, dict):
            return {key: value for key, value in inner.items()}
    return {key: value for key, value in body.items() if not str(key).startswith("_")}


# --------------------------------------------------------------------------- #
# running the CLI (the one implementation of every command)
# --------------------------------------------------------------------------- #


def cli_python() -> str:
    return getenv("API_PYTHON") or sys.executable


def child_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a command runs in.

    ``SKALDIR_FORMAT`` is dropped because the server decides the format (``--json``);
    ``SKALDIR_API_TOKEN`` is dropped because a command has no business seeing the transport's
    credential.
    """
    env = dict(os.environ if base is None else base)
    for name in expand(("SKALDIR_FORMAT", TOKEN_ENV)):
        env.pop(name, None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


@dataclass
class CliResult:
    """One command run, exactly as the CLI reported it."""

    exit_code: int
    envelope: dict
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    argv: tuple[str, ...] = ()

    @property
    def status(self) -> int:
        return status_for_exit(self.exit_code)

    def as_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "http_status": self.status,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "argv": list(self.argv),
        }


def status_for_exit(exit_code: int) -> int:
    """The one mapping (§ HTTP_STATUS_BY_EXIT). An unknown code is an internal error."""
    return HTTP_STATUS_BY_EXIT.get(int(exit_code), HTTP_STATUS_BY_EXIT[cli_contract.EXIT_INTERNAL])


def last_json_document(text: str) -> dict | None:
    """The last complete JSON object in ``text`` (the CLI prints exactly one, last)."""
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        pass
    for line in reversed([item for item in stripped.splitlines() if item.strip()]):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def command_evidence(route: ApiRoute | None, command: str) -> list[dict]:
    """The contract's declared evidence for a command (so a success can never be empty)."""
    spec = cli_contract.spec_for(route.command if route is not None else command)
    evidence = [dict(item) for item in spec.evidence]
    evidence.append({"ref": "src/skaldir/api_server.py", "kind": "code",
                     "note": "HTTP route generated from cli_contract.COMMANDS + build_parser()"})
    return evidence


def server_envelope(
    command: str,
    exit_code: int,
    *,
    data: Any = None,
    message: str = "",
    hint: str = "",
    details: dict | None = None,
    route: ApiRoute | None = None,
    warnings: Sequence[str] = (),
    next_actions: Sequence[dict] = (),
    evidence: Sequence[dict] | None = None,
    error_code: str = "",
) -> dict:
    """A contract-shaped envelope for something the *server* decided (never a fabricated result).

    It goes through ``cli_contract.build_envelope``, so the rules are the CLI's rules: a success
    without evidence is downgraded to exit 7, and ``error`` is set iff ``ok`` is false.
    """
    error: dict | None = None
    if exit_code != cli_contract.EXIT_OK:
        code = error_code or cli_contract.EXIT_ERROR_CODES.get(
            int(exit_code), cli_contract.EXIT_ERROR_CODES[cli_contract.EXIT_INTERNAL]
        )
        error = {
            "code": code,
            "message": message or cli_contract.EXIT_NAMES.get(int(exit_code), "failed"),
            "hint": hint or None,
            "retryable": bool(exit_code in (cli_contract.EXIT_UNKNOWN,)),
            "details": dict(details or {}),
        }
    envelope = cli_contract.build_envelope(
        command=command,
        exit_code=int(exit_code),
        data=data,
        error=error,
        evidence=evidence if evidence is not None else command_evidence(route, command),
        warnings=list(warnings),
        next_actions=list(next_actions),
    )
    return envelope


def run_cli(
    argv: Sequence[str],
    *,
    command: str,
    route: ApiRoute | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    workdir: str | Path | None = None,
    python: str | None = None,
    env: Mapping[str, str] | None = None,
) -> CliResult:
    """Run one ``skaldir`` command out-of-process and return its envelope.

    Out-of-process is deliberate: a real timeout that can actually kill the command, isolation
    from a command that calls ``sys.exit`` or crashes, and no shared emission state between
    concurrent requests. ``stdin`` is ``/dev/null``: a command that wants to prompt on a pipe
    must fail (``MVP/02`` §4.4), not hang the server.
    """
    timeout_s = max(1.0, min(float(timeout_s), MAX_TIMEOUT_S))
    cli_words = [str(item) for item in argv]
    words = [python or cli_python(), "-m", "skaldir", *cli_words]
    started = time.time()
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv is built from the parser, never a shell
            words,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=str(workdir) if workdir else None,
            env=child_env(env),
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        result = CliResult(
            exit_code=cli_contract.EXIT_INTERNAL,
            envelope=server_envelope(
                command, cli_contract.EXIT_INTERNAL,
                message=f"could not start the CLI ({type(exc).__name__}: {exc})",
                hint="check that the interpreter that runs the server can import skaldir",
                details={"argv": cli_words},
                route=route,
            ),
            argv=tuple(cli_words),
        )
        return result

    timed_out = False
    out, err = "", ""
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(proc)
        with contextlib.suppress(Exception):
            out, err = proc.communicate(timeout=5)
        out = out or ""
        err = err or ""

    duration_ms = int((time.time() - started) * 1000)
    code = proc.returncode if proc.returncode is not None else -1

    if timed_out:
        return CliResult(
            exit_code=cli_contract.EXIT_UNKNOWN,
            envelope=server_envelope(
                command, cli_contract.EXIT_UNKNOWN,
                message=f"{command} did not finish within {timeout_s:g}s",
                hint="raise --timeout, or run the command from the CLI directly",
                details={"timeout_s": timeout_s, "argv": cli_words},
                route=route,
                warnings=[f"the command was killed after {timeout_s:g}s"],
            ),
            stdout=out, stderr=err, duration_ms=duration_ms, timed_out=True,
            argv=tuple(cli_words),
        )

    if code < 0:
        return CliResult(
            exit_code=cli_contract.EXIT_UNKNOWN,
            envelope=server_envelope(
                command, cli_contract.EXIT_UNKNOWN,
                message=f"{command} was killed by signal {-code}",
                details={"signal": -code, "argv": cli_words},
                route=route,
                evidence=command_evidence(route, command),
            ),
            stdout=out, stderr=err, duration_ms=duration_ms, argv=tuple(cli_words),
        )

    envelope = last_json_document(out)
    if envelope is None:
        # Never invent a result: report what actually came back.
        tail = "\n".join(str(err or "").strip().splitlines()[-4:])[-600:]
        return CliResult(
            exit_code=cli_contract.EXIT_INTERNAL,
            envelope=server_envelope(
                command, cli_contract.EXIT_INTERNAL,
                message=f"{command} printed no JSON envelope (exit code {code})",
                hint="the command did not honour --json; see stderr_tail",
                details={"argv": cli_words, "exit_code": code, "stderr_tail": tail},
                route=route,
            ),
            stdout=out, stderr=err, duration_ms=duration_ms, argv=tuple(cli_words),
        )

    exit_code = int(envelope.get("exit_code", code))
    if exit_code not in HTTP_STATUS_BY_EXIT:
        exit_code = cli_contract.EXIT_INTERNAL
    # The envelope is the command's own: reused as-is, not rebuilt (§ one envelope).
    if envelope.get("command") in (None, "") and command:
        envelope["command"] = command
    return CliResult(
        exit_code=exit_code, envelope=envelope, stdout=out, stderr=err,
        duration_ms=duration_ms, argv=tuple(cli_words),
    )


def _terminate(proc: subprocess.Popen) -> None:
    import signal  # noqa: PLC0415

    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), sig)
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def iter_envelopes(
    argv: Sequence[str],
    *,
    command: str,
    route: ApiRoute | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    workdir: str | Path | None = None,
    python: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Iterator[dict]:
    """Yield the command's envelopes as they are printed (NDJSON streaming).

    Each *line* of the CLI's stdout is one envelope (``cli_contract.emit_json_line`` writes one
    document per line). A line that is not complete JSON is buffered until it parses, so a
    pretty-printed document still streams as one envelope instead of fragments. A command that
    prints exactly one envelope yields exactly one line and then stops — that is the correct
    behaviour for ``?stream=1`` on a normal command, and the real reason to stream is a command
    whose own output continues (``run.watch``).
    """
    timeout_s = max(1.0, min(float(timeout_s), MAX_TIMEOUT_S))
    cli_words = [str(item) for item in argv]
    words = [python or cli_python(), "-m", "skaldir", *cli_words]
    started = time.time()
    try:
        proc = subprocess.Popen(  # noqa: S603
            words, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            cwd=str(workdir) if workdir else None, env=child_env(env),
            text=True, encoding="utf-8", errors="replace", start_new_session=True,
        )
    except OSError as exc:
        yield server_envelope(
            command, cli_contract.EXIT_INTERNAL,
            message=f"could not start the CLI ({type(exc).__name__}: {exc})",
            hint="check that the interpreter that runs the server can import skaldir",
            route=route,
        )
        return

    buffer = ""
    noise: list[str] = []
    emitted = 0
    deadline = started + timeout_s
    try:
        assert proc.stdout is not None
        while True:
            if time.time() > deadline:
                _terminate(proc)
                yield server_envelope(
                    command, cli_contract.EXIT_UNKNOWN,
                    message=f"{command} did not finish within {timeout_s:g}s",
                    details={"timeout_s": timeout_s, "streamed": emitted},
                    route=route,
                    warnings=["the stream was stopped by the request timeout"],
                )
                return
            line = proc.stdout.readline()
            if line == "":
                break
            buffer += line
            stripped = buffer.strip()
            if not stripped:
                buffer = ""
                continue
            try:
                parsed = json.loads(stripped)
            except (ValueError, TypeError):
                # Either an unfinished document (keep reading) or a line that is not JSON at
                # all — the CLI contract says stdout carries envelopes only, so the latter is
                # remembered and reported instead of being silently swallowed.
                if stripped.startswith(("{", "[")):
                    buffer = stripped
                else:
                    noise.append(stripped)
                    buffer = ""
                continue
            buffer = ""
            if isinstance(parsed, dict):
                emitted += 1
                yield parsed
        rest = buffer.strip()
        if rest:
            try:
                parsed = json.loads(rest)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                emitted += 1
                yield parsed
            else:
                yield server_envelope(
                    command, cli_contract.EXIT_INTERNAL,
                    message=f"{command} streamed output that is not a JSON envelope",
                    details={"tail": rest[-400:]},
                    route=route,
                )
                return
        proc.wait(timeout=5)
        code = proc.returncode or 0
        if emitted == 0:
            yield server_envelope(
                command, cli_contract.EXIT_INTERNAL if code == 0 else int(code),
                message=f"{command} streamed nothing (exit code {code})",
                details={"exit_code": code, "argv": cli_words,
                         "stdout_lines": noise[-3:]},
                route=route,
            )
    finally:
        if proc.poll() is None:
            _terminate(proc)
        for stream in (proc.stdout, proc.stderr):
            with contextlib.suppress(Exception):
                if stream is not None:
                    stream.close()


# --------------------------------------------------------------------------- #
# transports: spawn (always) / warm session (keep-alive) / in-process (safe subset)
# --------------------------------------------------------------------------- #


class TransportUnavailable(RuntimeError):
    """A faster transport answered "not here" — the caller falls back to the next one."""

    def __init__(self, transport: str, reason: str) -> None:
        super().__init__(f"{transport}: {reason}")
        self.transport = transport
        self.reason = reason


def parser_env_fingerprint(env: Mapping[str, str] | None = None) -> tuple[tuple[str, str], ...]:
    """The pinned values of :data:`PARSER_ENV_KEYS` — the parser cache's key."""
    source = os.environ if env is None else env
    return tuple((name, str(source.get(name, ""))) for name in PARSER_ENV_KEYS)


#: Commands the in-process path is *measured* to serve (``tools/gv_api_perf.py audit``
#: regenerates this list, and ``tests/test_api_latency_stream.py`` re-proves it).
#:
#: A member has to clear four bars, and the last one is measurement, not opinion:
#:
#: 1. ``mutates is False`` and ``requires_credentials is False`` in ``cli_contract.COMMANDS``;
#: 2. no display, no socket, no network, no long run — i.e. no :data:`IN_PROCESS_REFUSED` reason;
#: 3. its cost is *structurally bounded* (a fixed-size table, a registry row, a small file), not
#:    "grows with the user's data" — an in-process call cannot be killed, so a promise about its
#:    duration is the only safety rail left;
#: 4. the audit proved the envelope it returns is byte-identical to the child process' envelope
#:    (timing fields excluded) and its p50 is under :data:`IN_PROCESS_BUDGET_MS`.
IN_PROCESS_ALLOWLIST: frozenset[str] = frozenset({
    "act.subtool.list",
    "completion.history",
    "completion.status",
    "contract.dump",
    "contract.list",
    "image.list",
    "jev.kinds",
    "jev.packs",
    "ledger-show",
    "memory.scope",
    "pact-show",
    "permission.show",
    "prompts.list",
    "provider.list",
    "registry.status",
    "session.policy",
    "session.status",
    "skills.list",
    "style.palette",
    "tools-core.list",
    "tools-core.policy",
    "tools.builds",
    "tools.candidates",
    "tools.list",
    "voice.status",
    "workflows.list",
})

#: The measured bound every allowlist member clears (p50 over the audit's runs). It is a bound on
#: *duration*, because an in-process command has no process to kill.
IN_PROCESS_BUDGET_MS = 250

#: Read-only commands that are *not* in the allowlist, with the reason. Every entry is served by
#: the warm session or the child process; :func:`in_process_verdict` reports the reason, so the
#: endpoint's own answer says why the fast path was not taken. The measured numbers behind each
#: group are in ``MVP/56-api-latency.md`` §4.
IN_PROCESS_REFUSED: dict[str, str] = {
    # -- an audit round measured the two paths DISAGREEING ---------------------------------------
    # A round that agrees is not proof (three members flipped between two rounds of the same
    # audit), so these stay out on the evidence of the round that saw the difference.
    "goal.show": "measured: the two paths disagreed — the server process' goal state is not the "
                 "child process' goal state",
    "permission.explain": "measured: the two paths disagreed — it resolves the live process "
                          "context",
    "code.explain": "measured: the two paths disagreed in an audit round",
    "eval": "measured: the two paths disagreed; it also runs a sweep",
    "selftest": "measured: the two paths disagreed and the call takes ~0.3 s — a probe suite "
                "belongs in its own process",
    "verify.plan": "measured: the two paths disagreed in an audit round",
    "voice.explain": "measured: the two paths disagreed; it also calls the voice stack",
    "voice.triage": "measured: the two paths disagreed; it also calls the voice stack",
    "daemon.status": "measured: the two paths disagreed — and it opens a TCP connection, which "
                     "must not sit in the server process",
    # -- measured to differ *between runs*: live store state, not a table -------------------------
    "runs.status": "its payload carries live counters, a checked_at timestamp and a writer "
                   "heartbeat (measured: differs between the two paths, and between two runs)",
    "spend.list": "the spend ledger is live state (measured: the two paths disagreed)",
    "spend.allowance": "derived from the live spend ledger (measured: the two paths disagreed)",
    # -- measured in-process p50 above the budget ------------------------------------------------
    "adversarial.selftest": "measured 10.6 s in-process — an un-killable call that long is not "
                            "allowed",
    "security.audit": "measured 6.2 s in-process — an un-killable call that long is not allowed",
    "code.diagnostics": "measured 0.5 s in-process — over the budget",
    "doctor": "probes deployment, network and credentials; measured 0.3 s in-process",
    "host.doctor": "probes the host bridge; measured 0.5 s in-process",
    # -- a real resource the server process must not hold ------------------------------------------
    "warm.status": "opens the session's unix socket",
    "host.status": "queries the host agent over a socket",
    "host.tools": "queries the host agent over a socket",
    "host.skills": "queries the host agent over a socket",
    "host.sessions": "queries the host agent over a socket",
    "host.memory.search": "queries the host agent over a socket",
    "host.cron": "reads and reconciles the host's cron surface",
    "mcp.probe": "speaks the MCP protocol to an external server",
    "mcp.call": "speaks the MCP protocol to an external server",
    "provider.doctor": "calls the configured provider over the network",
    "provider.test": "calls the configured provider over the network",
    "provider.catalog": "reads the provider's live catalogue",
    "cu.check": "needs a real display",
    "cu.demo": "needs a real display",
    "desktop.start": "starts a GUI process with its own lifetime",
    "desktop.status": "reads another process' state while it changes",
    "desktop.build-app": "builds and installs the Electron app",
    "image.inspect": "pulls an image from the registry",
    "image.layers": "reads an image from the local store",
    "image.resolve": "resolves a digest against the registry",
    "image.verify": "resolves and verifies against the registry",
    "image.guard": "pulls and verifies an image layer by layer",
    "tools.health": "probes a tool over its own transport",
    "tools-core.health": "probes the core toolset",
    "tools-hub": "walks the hub manifest and its sources",
    "tools.api.call": "calls an external HTTP capability",
    "tools.api.health": "probes an external HTTP capability",
    "tools.api.inspect": "discovers an external HTTP service",
    "tools.api.register": "registers an adapter into the live registry",
    "tools.api.rollback": "retires a registered capability",
    "tools.compose": "mines candidates and can call out",
    "vision.capabilities": "probes the local vision stack",
    "vision.describe": "calls a local model",
    "vision.ocr": "calls a local model",
    "vision.screenshot": "captures the display",
    "voice.check": "probes the voice stack",
    # -- not measured by the audit (it needs an argument the harness cannot invent) ---------------
    "contract.show": "not audited: it needs a command name the audit cannot invent",
    "workflows.show": "not audited: it needs a workflow id the audit cannot invent",
    "vault.list": "not audited: the route was not in the audited set",
    "code.tools": "walks the code toolchain",
    "code.changed": "reads git history",
    "code.search": "walks the workspace index",
    "code.symbols": "walks the workspace index",
    "scan": "walks a directory tree of unknown size",
    "security.scan": "walks the workspace",
    "tools.scan": "walks a directory tree",
    "memory-audit": "walks the whole memory store; the cost grows with the store",
    "memory-search": "queries the whole memory store",
    "memory-retrieval-check": "runs a retrieval sweep over the store",
    "memory-stats": "aggregates the whole store",
    "memory.recall": "ranks over the whole store",
    "memory.explain": "reads and renders a whole memory record",
    "session.list": "the cost grows with the session store",
    "session.show": "renders a whole session",
    "session.search": "queries the whole session store",
    "session.verify": "verifies a session end to end",
    "session.resume": "resumes a session and can block on it",
    "agent.list": "reads the run store, which grows with every run",
    "agent.show": "reads a stored run",
    "agent.transcript": "reads a stored transcript",
    "agent.collect": "collects a child run's output",
    "approval.list": "reads the approval ledger, which grows",
    "approval.check": "checks live approval state",
    "compose.candidates": "mines the registry for candidates",
    "compose.show": "reads a composed tool",
    "edit.dry-run": "applies an edit transaction in dry-run mode",
    "edit.show": "renders an edit transaction",
    "context.compact": "runs a compaction pass",
    "context.explain": "assembles the live context",
    "context.show": "assembles the live context",
    "context.usage": "reads live usage counters",
    "context.fingerprint": "builds the full prompt fingerprint",
    "prompt.build": "assembles a prompt from the live context",
    "prompt.explain": "explains a prompt against the live context",
    "prompt.fingerprint": "builds a prompt fingerprint",
    "verify.status": "reads live verification state",
    "verify.evidence": "reads the evidence store",
    "verify.run": "executes the verification plan",
    "adversarial.plan": "builds an adversarial plan over the workspace",
    "skills.show": "reads a skill artifact from the workspace",
    "skills.validate": "runs the skill validator",
    "tools.show": "reads a tool spec and its manifest",
    "tools.adapter.show": "reads a staged adapter",
    "tools.api.show": "reads a staged adapter",
    "tools-core.show": "reads a core tool",
    "act.check": "probes the display and the accessibility stack",
    "act.subtool.show": "reads a sub-tool definition",
    "image.pin": "writes a digest pin",
    "jev.history": "reads the decision history, which grows",
    "jev.selftest": "runs the Jev probes",
    "style.logo": "renders the logo",
    "style.preview": "renders a preview",
    "registry.migrate": "migrates the registry database",
    "spend.request": "writes a spend request",
    "vault.describe": "resolves a credential handle",
    "metrics": "aggregates every counter; measured ~1.4 s cold",
    "agent.run": "long-running: it needs its own process, its own deadline and a kill switch",
    "agent.spawn": "long-running: spawns and waits for a child run",
    "agent.retry": "long-running: retries a child run",
    "code.run": "runs code in a sandbox and can outlive the request",
    "compose.run": "runs a composed tool chain",
    "workflows.run": "runs a workflow",
    "nightshift": "long-running loop",
}


def in_process_verdict(command: str, route: ApiRoute | None = None) -> tuple[bool, str]:
    """``(allowed, reason)`` for the in-process path. Never a guess: the reason is always named."""
    if route is not None and route.mutates:
        return False, "mutating: it needs the child process' isolation and signals"
    if route is not None and route.requires_credentials:
        return False, "needs credentials: it must not read them from the server's environment"
    if command not in IN_PROCESS_ALLOWLIST:
        return False, IN_PROCESS_REFUSED.get(
            command,
            "read-only, but not on the measured allowlist: its cost is not structurally bounded "
            "(see MVP/56-api-latency.md §4) — it keeps its own process so the request deadline "
            "can still kill it",
        )
    return True, "measured allowlist: read-only, envelope-identical to the child process"


def _parser_cache() -> tuple[Any, tuple[tuple[str, str], ...]]:
    """The parser this server built once, plus the environment it was built from."""
    global _PARSER_CACHE  # noqa: PLW0603 — one process-wide parser, deliberately
    with _PARSER_LOCK:
        if _PARSER_CACHE is None:
            _PARSER_CACHE = (_parser(), parser_env_fingerprint())
        return _PARSER_CACHE


_PARSER_CACHE: tuple[Any, tuple[tuple[str, str], ...]] | None = None
_PARSER_LOCK = threading.Lock()

#: The CLI's own emission context is module-global (``cli_contract._CURRENT``), so two in-process
#: commands may never overlap. One lock, held for the whole call.
_IN_PROCESS_LOCK = threading.Lock()


def run_cli_inprocess(
    argv: Sequence[str],
    *,
    command: str,
    route: ApiRoute | None = None,
    parser: Any = None,
    parser_env: tuple[tuple[str, str], ...] | None = None,
) -> CliResult:
    """Run one command **in this process**, through the CLI's own parser and handler.

    This is not a second implementation: it calls ``parser.parse_args`` + ``cli_contract
    .run_command`` — the two lines ``cli.main`` is made of — with stdout/stderr captured, so the
    bytes it returns are the same bytes the child process would have written. What it does *not*
    have is the child process' freedom:

    * no hard deadline (nothing can kill it), so only the measured allowlist may come here;
    * no isolation from the CLI's module-level state, so exactly one in-process command runs at a
      time (``_IN_PROCESS_LOCK``), and a mutating command never comes here at all;
    * a parser the server built once — its environment is compared against the fingerprint taken
      at build time, and a difference refuses the path instead of answering from a stale tree.
    """
    words = [str(item) for item in argv]
    started = time.time()
    if parser is None:
        parser, built_env = _parser_cache()
        parser_env = built_env if parser_env is None else parser_env
    if parser_env is not None and parser_env != parser_env_fingerprint():
        raise TransportUnavailable(
            IN_PROCESS_TRANSPORT,
            "the parser cache was built with a different environment",
        )
    out_buffer = io.StringIO()
    err_buffer = io.StringIO()
    code = cli_contract.EXIT_INTERNAL
    with _IN_PROCESS_LOCK:
        with contextlib.redirect_stdout(out_buffer), contextlib.redirect_stderr(err_buffer):
            try:
                try:
                    args = parser.parse_args(words)
                except SystemExit as exc:  # argparse refused the command line
                    code = int(exc.code) if isinstance(exc.code, int) else cli_contract.EXIT_USAGE
                    cli_contract.emit_json_line(cli_contract.usage_envelope(words, code))
                else:
                    func = getattr(args, "func", None)
                    if func is None:
                        raise ApiRequestError(  # noqa: TRY301 — one error, one envelope
                            f"{command or ' '.join(words)} has no handler in this build",
                            hint="the route table and the parser disagree; this is a bug",
                        )
                    code = int(cli_contract.run_command(args, func))
            except ApiRequestError as exc:
                code = cli_contract.EXIT_USAGE
                cli_contract.emit_json_line(
                    server_envelope(command, code, message=exc.message, hint=exc.hint,
                                    details=exc.details, route=route)
                )
            except BaseException as exc:  # noqa: BLE001 — never let one command kill the server
                code = cli_contract.EXIT_INTERNAL
                cli_contract.emit_json_line(
                    server_envelope(
                        command, code,
                        message=f"the in-process call failed ({type(exc).__name__}: {exc})",
                        hint="re-run with the spawn path (--transport spawn) to isolate it",
                        route=route,
                    )
                )
    duration_ms = int((time.time() - started) * 1000)
    return _result_from_text(command, route, code, out_buffer.getvalue(), err_buffer.getvalue(),
                             duration_ms, words)


def _warm_status(socket_path: str, *, ttl_s: float = 2.0) -> bool:
    """Is a warm session answering? Cached for ``ttl_s`` so a dead socket costs ~one probe."""
    global _WARM_CACHE  # noqa: PLW0603
    now = time.monotonic()
    with _WARM_LOCK:
        if _WARM_CACHE and (_WARM_CACHE["path"] == socket_path
                            and now - _WARM_CACHE["at"] < ttl_s):
            return bool(_WARM_CACHE["ok"])
    ok = False
    try:
        from . import warm_session  # noqa: PLC0415 — only when the warm path is asked for

        target = Path(socket_path) if socket_path else warm_session.default_socket_path()
        socket_path = str(target)
        ok = bool(warm_session.status_of(target, timeout=0.5).running)
    except Exception:  # noqa: BLE001 — a missing/broken session is "not available", never an error
        ok = False
    with _WARM_LOCK:
        _WARM_CACHE = {"at": now, "path": socket_path, "ok": ok}
    return ok


_WARM_CACHE: dict[str, Any] = {}
_WARM_LOCK = threading.Lock()


def run_cli_warm(
    argv: Sequence[str],
    *,
    command: str,
    route: ApiRoute | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    workdir: str | Path | None = None,
    socket_path: str = "",
    env: Mapping[str, str] | None = None,
) -> CliResult:
    """Run one command in the already-running warm session (the keep-alive path).

    The session is one long-lived process (``MVP/26-warm-session.md``) that forks a worker per
    command: the command still runs *out of process* — same isolation, same bytes — but the
    interpreter start and the package import are paid once per session instead of once per
    request. The command's own environment is built by :func:`child_env`, so the transport's
    token never reaches it.
    """
    from . import warm_session  # noqa: PLC0415

    limit = max(1.0, min(float(timeout_s), MAX_TIMEOUT_S))
    target = Path(socket_path) if socket_path else warm_session.default_socket_path()
    words = [str(item) for item in argv]
    request_env = child_env(env)
    started = time.time()
    try:
        answer = warm_session.run_exec(
            words, socket_path=target, timeout=limit,
            cwd=str(workdir) if workdir else None, env=request_env,
            tty_out=False, tty_err=False, prog=warm_session.product_prog(),
        )
    except (warm_session.ClientError, warm_session.WarmRefused) as exc:
        raise TransportUnavailable(WARM_TRANSPORT, str(exc)) from exc
    duration_ms = int((time.time() - started) * 1000)
    out = answer.stdout.decode("utf-8", "replace")
    err = answer.stderr.decode("utf-8", "replace")
    if answer.exit_code < 0:
        return CliResult(
            exit_code=cli_contract.EXIT_UNKNOWN,
            envelope=server_envelope(
                command, cli_contract.EXIT_UNKNOWN,
                message=f"{command} was killed by signal {-answer.exit_code} in the warm session",
                details={"signal": -answer.exit_code, "argv": words},
                route=route,
            ),
            stdout=out, stderr=err, duration_ms=duration_ms, argv=tuple(words),
        )
    return _result_from_text(command, route, answer.exit_code, out, err, duration_ms, words)


def _result_from_text(
    command: str,
    route: ApiRoute | None,
    code: int,
    out: str,
    err: str,
    duration_ms: int,
    argv: Sequence[str],
) -> CliResult:
    """The CLI's own envelope out of the text it printed — the rule ``run_cli`` already applies."""
    envelope = last_json_document(out)
    if envelope is None:
        tail = "\n".join(str(err or "").strip().splitlines()[-4:])[-600:]
        return CliResult(
            exit_code=cli_contract.EXIT_INTERNAL,
            envelope=server_envelope(
                command, cli_contract.EXIT_INTERNAL,
                message=f"{command} printed no JSON envelope (exit code {code})",
                hint="the command did not honour --json; see stderr_tail",
                details={"argv": list(argv), "exit_code": code, "stderr_tail": tail},
                route=route,
            ),
            stdout=out, stderr=err, duration_ms=duration_ms, argv=tuple(argv),
        )
    exit_code = int(envelope.get("exit_code", code))
    if exit_code not in HTTP_STATUS_BY_EXIT:
        exit_code = cli_contract.EXIT_INTERNAL
    if envelope.get("command") in (None, "") and command:
        envelope["command"] = command
    return CliResult(exit_code=exit_code, envelope=envelope, stdout=out, stderr=err,
                     duration_ms=duration_ms, argv=tuple(argv))


@dataclass
class Routed:
    """One answer plus the transport that produced it — the path is evidence, so it travels."""

    result: CliResult
    transport: str
    note: str = ""

    def as_dict(self) -> dict:
        return {"transport": self.transport, "note": self.note, **self.result.as_dict()}


def transport_for(
    command: str,
    route: ApiRoute | None = None,
    *,
    transport: str = DEFAULT_TRANSPORT,
    workdir: str | Path | None = None,
    cwd: str | None = None,
    warm_socket: str = "",
) -> tuple[str, str]:
    """Which transport this request should use, and why. ``auto`` decides per command and state."""
    forced = str(transport or DEFAULT_TRANSPORT).lower()
    if forced not in TRANSPORTS:
        forced = DEFAULT_TRANSPORT
    if forced != DEFAULT_TRANSPORT:
        return forced, f"forced by --transport {forced}"
    if workdir is not None and str(workdir) != str(cwd or os.getcwd()):
        return SPAWN_TRANSPORT, "the server runs commands in --workdir, which in-process cannot set"
    allowed, reason = in_process_verdict(command, route)
    if allowed:
        return IN_PROCESS_TRANSPORT, reason
    if _warm_status(warm_socket):
        return WARM_TRANSPORT, "a warm session is running (MVP/26) — one process, forked per call"
    return SPAWN_TRANSPORT, reason


def run_command_routed(
    argv: Sequence[str],
    *,
    command: str,
    route: ApiRoute | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    workdir: str | Path | None = None,
    python: str | None = None,
    transport: str = DEFAULT_TRANSPORT,
    warm_socket: str = "",
    parser: Any = None,
    parser_env: tuple[tuple[str, str], ...] | None = None,
    env: Mapping[str, str] | None = None,
) -> Routed:
    """Run a command on the fastest transport that is allowed, falling back *honestly*.

    Every fallback is reported in ``Routed.note`` (and travels to the client as
    ``X-Godsv-Transport-Note``): a request never silently loses the fast path, and never silently
    keeps a slow one either.
    """
    chosen, note = transport_for(command, route, transport=transport, workdir=workdir,
                                 warm_socket=warm_socket)
    if chosen == IN_PROCESS_TRANSPORT:
        try:
            result = run_cli_inprocess(argv, command=command, route=route, parser=parser,
                                       parser_env=parser_env)
            return Routed(result, IN_PROCESS_TRANSPORT, note)
        except TransportUnavailable as exc:
            note = f"in-process refused: {exc.reason}"
            chosen = SPAWN_TRANSPORT
    if chosen == WARM_TRANSPORT:
        try:
            result = run_cli_warm(argv, command=command, route=route, timeout_s=timeout_s,
                                  workdir=workdir, socket_path=warm_socket, env=env)
            return Routed(result, WARM_TRANSPORT, note)
        except TransportUnavailable as exc:
            with _WARM_LOCK:
                _WARM_CACHE.clear()
            note = f"warm session unavailable: {exc.reason}"
    result = run_cli(argv, command=command, route=route, timeout_s=timeout_s, workdir=workdir,
                     python=python, env=env)
    return Routed(result, SPAWN_TRANSPORT, note)


# --------------------------------------------------------------------------- #
# server-sent events (the long-run transport, MVP/56 §3)
# --------------------------------------------------------------------------- #


def sse_frame(event: str, data: Any, *, event_id: Any = None, retry_ms: int | None = None) -> str:
    """One SSE frame: optional ``id:``/``retry:``, the event name, then one ``data:`` per line."""
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False, default=str)
    for line in payload.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def sse_comment(text: str) -> str:
    """A comment frame — ignored by every SSE parser, which is exactly why it is the keep-alive."""
    return ": " + str(text).replace("\n", " ") + "\n\n"


def sse_frames(
    ticks: Iterator[dict | None],
    *,
    command: str,
    route_path: str = "",
    transport: str = SPAWN_TRANSPORT,
    transport_note: str = "",
    heartbeat_s: float = DEFAULT_SSE_HEARTBEAT_S,
    retry_ms: int = DEFAULT_SSE_RETRY_MS,
    stream_id: str = "",
    started_at: str = "",
    api_version: str = "",
    schema_version: str = "",
) -> Iterator[str]:
    """The ``begin`` / ``progress`` / ``end`` frame stream.

    ``ticks`` yields one envelope per event and ``None`` for "nothing yet, send a keep-alive".
    The shapes are fixed (``MVP/56-api-latency.md`` §3): ``begin`` announces the stream, every
    ``progress`` carries one of the command's own envelope documents verbatim, and ``end``
    repeats the **terminal** envelope — so the last event is always the answer, whatever happened
    on the way (a timeout ends with the exit-8 envelope, not with a truncated stream).
    """
    started_at = started_at or cli_contract.now_iso()
    stream_id = stream_id or f"{command}-{int(time.time() * 1000)}"
    yield sse_frame(
        SSE_EVENTS[0],
        {
            "stream_contract": SSE_STREAM_CONTRACT,
            "stream_id": stream_id,
            "command": command,
            "route": route_path,
            "transport": transport,
            "transport_note": transport_note,
            "heartbeat_s": float(heartbeat_s),
            "started_at": started_at,
            "api_version": api_version,
            "schema_version": schema_version,
            "events": list(SSE_EVENTS),
        },
        event_id=0,
        retry_ms=retry_ms,
    )
    sequence = 0
    terminal: dict | None = None
    for tick in ticks:
        if tick is None:
            yield sse_comment(f"keep-alive {cli_contract.now_iso()}")
            continue
        terminal = tick
        sequence += 1
        yield sse_frame(SSE_EVENTS[1], tick, event_id=sequence)
    if terminal is None:
        terminal = server_envelope(
            command, cli_contract.EXIT_INTERNAL,
            message=f"{command} streamed nothing at all",
            hint="the stream closed before the command said anything",
        )
    yield sse_frame(SSE_EVENTS[2], terminal, event_id=sequence + 1)


#: Marks the end of the reader thread's queue.
_TICKS_DONE = object()


def envelope_ticks(
    route: ApiRoute,
    argv: Sequence[str],
    cfg: ServerConfig,
) -> Iterator[dict | None]:
    """``iter_envelopes`` with keep-alives: a dict per envelope, ``None`` per silent heartbeat.

    The reader runs in a thread because ``iter_envelopes`` blocks on the child's stdout; the
    generator polls its queue instead, so a stream that says nothing for minutes still writes a
    frame every :data:`DEFAULT_SSE_HEARTBEAT_S` seconds (and a client can tell "still working"
    from "gone").
    """
    boxes: queue.Queue = queue.Queue()
    command = route.command
    timeout_s = float(cfg.timeout_s)
    workdir = cfg.workdir
    python = cfg.python

    def reader() -> None:
        try:
            for envelope in iter_envelopes(argv, command=command, route=route, timeout_s=timeout_s,
                                           workdir=workdir, python=python):
                boxes.put(envelope)
        except BaseException:  # noqa: BLE001 — the stream must always end with an envelope
            pass
        finally:
            boxes.put(_TICKS_DONE)

    thread = threading.Thread(target=reader, name=f"stream-{route.operation_id}", daemon=True)
    thread.start()
    while True:
        try:
            item = boxes.get(timeout=max(0.5, float(cfg.sse_heartbeat_s)))
        except queue.Empty:
            yield None
            continue
        if item is _TICKS_DONE:
            return
        yield item


def _header_text(text: str) -> str:
    """HTTP header values are latin-1; a reason with a typographic dash must not break the reply."""
    return str(text).encode("latin-1", "replace").decode("latin-1")


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


def resolve_token(
    token_file: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    generate: bool = True,
) -> tuple[str, str]:
    """Return ``(token, source)`` with source in ``file``/``env``/``generated``.

    Precedence: ``--token-file`` wins over ``SKALDIR_API_TOKEN`` wins over a fresh token. The
    generated token exists only in this process's memory; this module never writes one to disk.
    """
    environment = os.environ if env is None else env
    if token_file:
        path = Path(str(token_file)).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ApiServerUnavailable(
                f"cannot read the token file {path} ({exc.strerror or exc})",
                hint="create it with: printf '%s' \"$(python -c "
                     "'import secrets;print(secrets.token_urlsafe(32))')\" > tokenfile && "
                     "chmod 600 tokenfile",
            ) from exc
        token = text.strip().splitlines()[0].strip() if text.strip() else ""
        if not token:
            raise ApiServerUnavailable(f"the token file {path} is empty")
        if len(token) < 16:
            raise ApiServerUnavailable(
                f"the token in {path} is too short ({len(token)} chars)",
                hint="use at least 16 characters; 32 random bytes are recommended",
            )
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            logging.getLogger(__name__).warning(
                "token file %s is readable by others (mode %o) — chmod 600 it", path, mode
            )
        return token, "file"
    from_env = str(getenv(TOKEN_ENV, "", environ=environment) or "").strip()
    if from_env:
        return from_env, "env"
    if not generate:
        return "", "missing"
    import secrets  # noqa: PLC0415

    return secrets.token_urlsafe(DEFAULT_TOKEN_BYTES), "generated"


def token_matches(provided: str | None, expected: str) -> bool:
    """Constant-time comparison; an empty expectation never matches (fail closed)."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(str(provided).encode("utf-8"), str(expected).encode("utf-8"))


def presented_token(headers: Mapping[str, str]) -> str | None:
    """Read the token from the request headers. A query string is never accepted."""
    lowered = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    raw = lowered.get(AUTH_HEADER, "")
    if raw:
        parts = raw.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return ""  # a malformed Authorization header is a rejection, not a fallback
    token = lowered.get(TOKEN_HEADER, "")
    return token.strip() or None


def authorization_failure(
    route: ApiRoute, headers: Mapping[str, str], expected: str
) -> dict | None:
    """``None`` when the call may proceed; otherwise the refusal envelope (exit 4 / HTTP 401)."""
    if not route.mutates:
        return None
    provided = presented_token(headers)
    if not expected:
        # Fail closed: a server without a token authorizes nothing that changes state.
        return server_envelope(
            route.command, cli_contract.EXIT_NEEDS_CREDENTIALS,
            message=f"{route.command} changes state and this server has no token configured",
            hint="restart with --token-file FILE (or let it generate one and print it once)",
            details={"reason": "no_token_configured", "mutates": True, "auth": "bearer"},
            route=route,
            error_code="needs_credentials",
        )
    if provided is None:
        return server_envelope(
            route.command, cli_contract.EXIT_NEEDS_CREDENTIALS,
            message=f"{route.command} changes state and needs the API token",
            hint="send Authorization: Bearer <token> (the token the server printed once on start)",
            details={"reason": "missing_token", "mutates": True, "auth": "bearer"},
            route=route,
            error_code="needs_credentials",
        )
    if not token_matches(provided, expected):
        return server_envelope(
            route.command, cli_contract.EXIT_NEEDS_CREDENTIALS,
            message=f"the API token presented for {route.command} is not valid",
            hint="use the token this server printed on start, or restart with --token-file",
            details={"reason": "invalid_token", "mutates": True, "auth": "bearer"},
            route=route,
            error_code="needs_credentials",
        )
    return None


# --------------------------------------------------------------------------- #
# logging (no secrets in logs)
# --------------------------------------------------------------------------- #


class RedactingFormatter(logging.Formatter):
    """A formatter that replaces the API token (and any bearer header) with a marker."""

    def __init__(self, fmt: str | None = None, datefmt: str | None = None,
                 secrets_to_hide: Iterable[str] = (), **kwargs: Any) -> None:
        # dictConfig passes ``format=`` when a formatter is given as a factory.
        super().__init__(fmt or kwargs.pop("format", None), datefmt)
        self._secrets = [item for item in secrets_to_hide if item and len(str(item)) >= 6]

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 — logging's API
        text = super().format(record)
        return redact(text, self._secrets)


_BEARER_RE = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._~+/=-]{6,}")


def redact(text: str, secrets_to_hide: Iterable[str] = ()) -> str:
    """Remove anything that looks like a credential from a log line."""
    out = _BEARER_RE.sub(lambda match: f"{match.group(1)} {REDACTED}", str(text))
    for secret in secrets_to_hide:
        out = out.replace(str(secret), REDACTED)
    return out


def configure_logging(level: str = "info", token: str = "") -> logging.Logger:
    """Point uvicorn's loggers at stderr with a redacting formatter.

    ``uvicorn`` is started with ``log_config=None`` so this is the only configuration in play:
    one handler, one formatter, and both the error and the access logger go through the
    redactor.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter("%(levelname)s %(name)s: %(message)s", token or ""))
    numeric = getattr(logging, str(level or "info").upper(), logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers[:] = [handler]
        logger.propagate = False
        logger.setLevel(numeric)
    logging.getLogger(__name__).handlers[:] = [handler]
    logging.getLogger(__name__).setLevel(numeric)
    return logging.getLogger("uvicorn")


# --------------------------------------------------------------------------- #
# the listening socket: refuse to start when the port is taken
# --------------------------------------------------------------------------- #


def _is_loopback(host: str) -> bool:
    import ipaddress  # noqa: PLC0415

    text = str(host or "").strip()
    if text.lower() == "localhost":
        return True
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(text).is_loopback
    return False


def check_bind_allowed(host: str, *, allow_remote: bool) -> None:
    """Loopback unless ``--allow-remote`` was given explicitly."""
    if _is_loopback(host) or allow_remote:
        return
    raise ApiServerUnavailable(
        f"refusing to bind {host!r}: only loopback is allowed by default",
        hint="pass --allow-remote to bind a non-loopback address (every mutating call still "
             "needs the token)",
        details={"host": host, "allow_remote": False},
        exit_code=cli_contract.EXIT_USAGE,
    )


def probe_existing_server(host: str, port: int, *, timeout_s: float = 0.6) -> dict | None:
    """Is a Skaldir API already answering on this port? (short GET /health, best effort)"""
    import http.client  # noqa: PLC0415

    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
    except OSError:
        return None
    try:
        conn.request("GET", HEALTH_PATH)
        response = conn.getresponse()
        payload = response.read(65536).decode("utf-8", "replace")
        if response.status != 200:
            return None
        document = json.loads(payload)
        if isinstance(document, dict) and document.get("command") == "api.health":
            return document
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        with contextlib.suppress(Exception):
            conn.close()
    return None


def bind_listener(host: str, port: int, *, backlog: int = 128) -> socket.socket:
    """Bind and listen, or raise :class:`ApiServerUnavailable` naming who holds the port.

    ``SO_REUSEADDR`` (never ``SO_REUSEPORT``) so a restart after a crash works while a second
    live listener still fails loudly — that failure is the feature.
    """
    family = socket.AF_INET6 if ":" in str(host) else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    try:
        sock.bind((str(host), int(port)))
    except OSError as exc:
        sock.close()
        other = probe_existing_server(host, port)
        detail = (f"a Skaldir API server (v{other.get('godsvoice_version')}) is already "
                  f"answering on http://{host}:{port}"
                  if other else "another process holds the address")
        raise ApiServerUnavailable(
            f"cannot start: port {port} on {host} is already in use — {detail} "
            f"[{exc.strerror or exc}]",
            hint=f"pick another port (--port {int(port) + 1}) or stop the process that holds "
                 f"{port}",
            details={"host": host, "port": int(port), "errno": exc.errno,
                     "existing_server": bool(other), "error": exc.strerror or str(exc)},
        ) from exc
    sock.listen(backlog)
    sock.set_inheritable(True)
    return sock


# --------------------------------------------------------------------------- #
# the OpenAPI document (same source of truth)
# --------------------------------------------------------------------------- #


def _json_schema_for_args(route: ApiRoute) -> dict:
    properties: dict[str, dict] = {}
    required: list[str] = []
    for spec in route.args:
        if spec["type"] == "flag":
            schema: dict = {"type": "boolean",
                            "description": spec["help"] or f"pass {spec['name']}"}
        else:
            schema = {"type": {"integer": "integer", "number": "number"}.get(
                spec["type"], "string")}
            if spec["choices"]:
                schema["enum"] = [str(item) for item in spec["choices"]]
        if spec["multiple"]:
            schema = {"type": "array", "items": schema,
                      "description": spec["help"] or f"repeatable {spec['name']}"}
        elif spec["help"]:
            schema["description"] = spec["help"]
        if spec["default"] not in (None, "", False, []):
            schema["x-godsv-cli-default"] = spec["default"]
        if spec["positional"]:
            schema["x-godsv-positional"] = True
        properties[spec["dest"]] = schema
        if spec["required"]:
            required.append(spec["dest"])
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = sorted(required)
    if route.mutually_exclusive:
        schema["x-godsv-mutually-exclusive"] = [list(item) for item in route.mutually_exclusive]
    return schema


def _envelope_schema() -> dict:
    """The §4.2 envelope, as a JSON Schema, with ``data`` left open.

    The per-command payload schema is inlined per operation
    (``x-godsv-data-schema`` and the ``200`` response), so one generic envelope is enough here.
    """
    error_schema = {
        "type": ["object", "null"],
        "properties": {
            "code": {"type": "string",
                     "enum": list(cli_contract.ERROR_CODES)},
            "message": {"type": "string"},
            "hint": {"type": ["string", "null"]},
            "retryable": {"type": "boolean"},
            "details": {"type": "object"},
        },
        "required": ["code", "message", "retryable", "details"],
        "additionalProperties": True,
    }
    return {
        "type": "object",
        "description": "The CLI envelope (MVP/02-cli-contract.md §4.2). HTTP status is "
                       "status_for_exit(exit_code).",
        "properties": {
            "schema_version": {"type": "string", "enum": [cli_contract.SCHEMA_VERSION]},
            "ok": {"type": "boolean"},
            "command": {"type": "string"},
            "godsvoice_version": {"type": "string"},
            "started_at": {"type": "string"},
            "duration_ms": {"type": "integer", "minimum": 0},
            "exit_code": {"type": "integer", "minimum": 0, "maximum": 8},
            "data": True,
            "error": error_schema,
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "kind": {"type": "string",
                                 "enum": list(cli_contract.EVIDENCE_KINDS)},
                        "note": {"type": "string"},
                    },
                    "required": ["ref", "kind"],
                    "additionalProperties": True,
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
            "next_actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["command", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["schema_version", "ok", "command", "godsvoice_version", "started_at",
                     "duration_ms", "exit_code", "data", "error", "evidence", "warnings",
                     "next_actions"],
        "additionalProperties": True,
    }


def _response_sets(route: ApiRoute) -> dict:
    """One response per exit code the command declares, plus the transports' own failures."""
    responses: dict[str, dict] = {}
    codes = sorted(set(route.exit_codes) | {cli_contract.EXIT_USAGE})
    if route.mutates:
        codes.append(cli_contract.EXIT_NEEDS_CREDENTIALS)
    for code in sorted(set(codes)):
        status = str(status_for_exit(code))
        payload_schema: dict = {"$ref": "#/components/schemas/Envelope"}
        if code == cli_contract.EXIT_OK:
            bearer = {"allOf": [{"$ref": "#/components/schemas/Envelope"},
                               {"type": "object",
                                "properties": {"data": route.data_schema,
                                               "exit_code": {"const": 0},
                                               "ok": {"const": True}}}]}
            if bearer["allOf"][1]["properties"]["data"] is None:
                bearer["allOf"][1]["properties"].pop("data")
            payload_schema = bearer
        responses[status] = {
            "description": (f"exit {code} {cli_contract.EXIT_NAMES.get(code, '')} — "
                            f"error.code={cli_contract.EXIT_ERROR_CODES.get(code)}"
                            + (" (also the server's token refusal)" if route.mutates
                               and code == cli_contract.EXIT_NEEDS_CREDENTIALS else "")),
            "content": {"application/json": {"schema": payload_schema}},
        }
        if code == cli_contract.EXIT_OK:
            # The same envelope, streamed: one line per envelope (NDJSON) or the
            # begin/progress/end event shape (SSE), both documented in MVP/56-api-latency.md.
            responses[status]["content"][NDJSON_MEDIA_TYPE] = {
                "schema": {"type": "string",
                           "description": "One JSON envelope per line as the command produces "
                                          "them; the last line is the terminal envelope."},
            }
            responses[status]["content"][SSE_MEDIA_TYPE] = {
                "schema": {"type": "string",
                           "description": f"Server-sent events, contract {SSE_STREAM_CONTRACT}: "
                                          f"{', '.join(SSE_EVENTS)}; every data: line is a JSON "
                                          f"document and the end event repeats the terminal "
                                          f"envelope. See the document's x-godsv-sse."},
            }
    # The transport's own 401 (the bearer token every *mutating* route needs) is only
    # documented when it can really happen — but a read-only route that declares exit 4
    # itself (a provider key, the host bridge credentials) keeps its 401: dropping it
    # would contradict that route's own ``x-godsv-exit-codes``.
    if not route.mutates and cli_contract.EXIT_NEEDS_CREDENTIALS not in route.exit_codes:
        responses.pop(str(status_for_exit(cli_contract.EXIT_NEEDS_CREDENTIALS)), None)
    responses.setdefault(str(status_for_exit(cli_contract.EXIT_USAGE)), {
        "description": "exit 1 USAGE — a bad request (unknown argument, bad value, oversized "
                       "body, wrong content type, unknown route)",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}},
    })
    # A catch-all, and not an escape hatch: the transport maps whatever exit code the command
    # really returns, and a command's declared set can be narrower than its behaviour (measured:
    # `tools.show` declares (0,1,7) and answers 5 for an unknown name). Consumers must accept the
    # full mapping for every route.
    responses["default"] = {
        "description": "any other exit code this command really returns — the status is always "
                       "status_for_exit(envelope.exit_code), see x-godsv-http-status-by-exit "
                       "(0→200, 1→400, 2→422, 3→409, 4→401, 5→404, 6→409, 7→500, 8→504)",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}},
    }
    return responses


def openapi_document(
    route_list: Sequence[ApiRoute] | None = None,
    *,
    base_url: str = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
    title: str = "Skaldir API (skaldir)",
    token_required: bool = True,
) -> dict:
    """Build the OpenAPI 3.1 document from the route table — no hand-written path anywhere."""
    route_list = list(route_list if route_list is not None else routes())
    paths: dict[str, dict] = {}
    for route in route_list:
        body_schema = _json_schema_for_args(route)
        responses = _response_sets(route)
        post: dict = {
            "operationId": f"{route.operation_id}_post",
            "tags": [route.layer],
            "summary": route.description or route.command,
            "description": _operation_description(route, method="post"),
            "responses": responses,
            "x-godsv-command": route.command,
            "x-godsv-layer": route.layer,
            "x-godsv-mutates": route.mutates,
            "x-godsv-exit-codes": list(route.exit_codes),
            "x-godsv-data-schema": route.data_schema,
            "x-godsv-streams": route.streams,
            "x-godsv-http-status-by-exit": {str(key): value
                                            for key, value in sorted(HTTP_STATUS_BY_EXIT.items())},
        }
        if route.mutates:
            post["security"] = [{"bearerAuth": []}]
        else:
            post["security"] = []
        if body_schema["properties"] or body_schema.get("required"):
            post["requestBody"] = {
                "required": bool(body_schema.get("required")),
                "content": {"application/json": {"schema": body_schema}},
            }
        operations: dict[str, dict] = {"post": post}

        # A read-only command is also callable with GET + query parameters: that is what a GUI
        # screen (and a crawler) reaches for first. Mutating commands are POST-only.
        if not route.mutates:
            get_op = dict(post)
            get_op.pop("requestBody", None)
            get_op["operationId"] = f"{route.operation_id}_get"
            get_op["description"] = _operation_description(route, method="get")
            get_op["parameters"] = _query_parameters(route)
            operations["get"] = get_op

        paths[route.path] = operations

    document: dict = {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": __version__,
            "description": _api_description(route_list, token_required=token_required),
            "x-godsv-schema-version": cli_contract.SCHEMA_VERSION,
        },
        "servers": [{"url": base_url, "description": "loopback only unless --allow-remote"}],
        "tags": [{"name": layer, "description": f"{layer} layer commands"}
                 for layer in sorted({route.layer for route in route_list})],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Required for every route whose x-godsv-mutates is true. The "
                                   "server prints the token once at start, or reads it from "
                                   "--token-file / SKALDIR_API_TOKEN. Read-only commands need no "
                                   "token.",
                }
            },
            "schemas": {"Envelope": _envelope_schema()},
        },
        "paths": paths,
        "x-godsv-schema-version": cli_contract.SCHEMA_VERSION,
        "x-godsv-token-required": bool(token_required),
        "x-godsv-command-count": len(route_list),
        "x-godsv-http-status-by-exit": {str(key): value
                                        for key, value in sorted(HTTP_STATUS_BY_EXIT.items())},
        "x-godsv-excluded": exclusions(),
        "x-godsv-transports": list(TRANSPORTS),
        "x-godsv-transport-default": DEFAULT_TRANSPORT,
        "x-godsv-in-process-allowlist": sorted(IN_PROCESS_ALLOWLIST),
        "x-godsv-sse": {
            "contract": SSE_STREAM_CONTRACT,
            "media_type": SSE_MEDIA_TYPE,
            "events": list(SSE_EVENTS),
            "keep_alive_comment": ": keep-alive <iso>",
            "default_heartbeat_s": DEFAULT_SSE_HEARTBEAT_S,
            "requested_by": ["Accept: text/event-stream", "?sse=1", '{"_sse": true}'],
            "doc": "MVP/56-api-latency.md",
        },
        "x-godsv-http-keep-alive-s": DEFAULT_KEEP_ALIVE_S,
    }
    return document


def _query_parameters(route: ApiRoute) -> list[dict]:
    parameters: list[dict] = []
    for spec in route.args:
        if spec["type"] == "flag":
            item: dict = {"type": "boolean"}
        else:
            item = {"type": {"integer": "integer", "number": "number"}.get(
                spec["type"], "string")}
            if spec["choices"]:
                item["enum"] = [str(choice) for choice in spec["choices"]]
        parameter: dict = {
            "name": spec["dest"],
            "in": "query",
            "required": bool(spec["required"]),
            "schema": item if not spec["multiple"] else {"type": "array", "items": item},
            "description": spec["help"] or spec["name"],
        }
        if spec["multiple"]:
            parameter["style"] = "form"
            parameter["explode"] = True
        parameters.append(parameter)
    return parameters


def _operation_description(route: ApiRoute, *, method: str) -> str:
    parts = [
        f"Runs `skaldir {' '.join(route.argv)}` in a child process and returns its envelope.",
        HTTP_STATUS_NOTE,
    ]
    if route.mutates:
        parts.append("Mutating: requires Authorization: Bearer <token> (401 without it).")
    else:
        parts.append("Read-only: no token required (the server is loopback-only).")
    if method == "get":
        parts.append("GET takes the arguments as query parameters; the values are the CLI "
                     "arguments' own names.")
    else:
        parts.append("POST body: the argument mapping, e.g. {\"name\": \"...\"}; "
                     "{\"args\": {...}} is accepted too.")
    if route.streams:
        parts.append("Streaming command: add ?stream=1 (or Accept: application/x-ndjson) for "
                     "one envelope per line as they are produced.")
    if route.notes:
        parts.append(f"Contract note: {route.notes}")
    return " ".join(parts)


def _api_description(route_list: Sequence[ApiRoute], *, token_required: bool) -> str:
    mutating = sum(1 for route in route_list if route.mutates)
    auth = ("a bearer token is required for every mutating route" if token_required
            else "no token configured — mutating routes will refuse (401)")
    return (
        "Skaldir (skaldir) HTTP API — one route per CLI command, generated from "
        f"`cli_contract.COMMANDS` and the real `build_parser()` ({len(route_list)} routes, "
        f"{mutating} of them mutating). Every response body is the CLI's own envelope; the "
        f"status is status_for_exit(exit_code). {HTTP_STATUS_NOTE} "
        f"Auth: {auth}. Commands that cannot be an HTTP route are listed in "
        "x-godsv-excluded with their reason."
    )


def validate_document(document: dict, route_list: Sequence[ApiRoute] | None = None) -> list[str]:
    """Structural self-check of a document we generated (used by the tests and by --openapi)."""
    if route_list is None:
        route_list = routes()
    problems: list[str] = []
    if document.get("openapi") != "3.1.0":
        problems.append("openapi version is not 3.1.0")
    for key in ("info", "paths", "components"):
        if not isinstance(document.get(key), dict) or not document[key]:
            problems.append(f"missing or empty {key}")
    commands = set()
    for path, operations in (document.get("paths") or {}).items():
        if not str(path).startswith(f"{API_PREFIX}/"):
            problems.append(f"{path} is outside {API_PREFIX}")
        if not operations:
            problems.append(f"{path} has no operations")
        for method, operation in operations.items():
            if method not in ("get", "post"):
                problems.append(f"{path} uses unsupported method {method}")
            if not operation.get("operationId"):
                problems.append(f"{path}.{method} has no operationId")
            if not operation.get("responses"):
                problems.append(f"{path}.{method} has no responses")
            command = operation.get("x-godsv-command")
            if not command:
                problems.append(f"{path}.{method} does not name its command")
            else:
                commands.add(command)
    declared = {route.command for route in route_list}
    if commands and not commands.issubset(declared):
        problems.append(f"document names unknown commands: {sorted(commands - declared)}")
    return problems


# --------------------------------------------------------------------------- #
# the ASGI app
# --------------------------------------------------------------------------- #


class BodyLimitMiddleware:
    """Reject an oversized request body before a command is ever started."""

    def __init__(self, app: Any, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1")
                   for key, value in scope.get("headers") or []}
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send, int(declared))
            return
        seen = 0

        async def counting_receive() -> dict:
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body") or b"")
                if seen > self.max_bytes:
                    raise _BodyTooLarge(seen)
            return message

        try:
            await self.app(scope, counting_receive, send)
        except _BodyTooLarge as exc:
            await self._reject(scope, send, exc.seen)

    async def _reject(self, scope: dict, send: Any, seen: int) -> None:
        from starlette.responses import JSONResponse  # noqa: PLC0415

        command = _command_from_path(scope.get("path", ""))
        envelope = server_envelope(
            command, cli_contract.EXIT_USAGE,
            message=f"request body too large ({seen} bytes, limit {self.max_bytes})",
            hint="send fewer/smaller arguments, or raise the limit with --max-body-bytes",
            details={"reason": "payload_too_large", "limit_bytes": self.max_bytes,
                     "received_bytes": seen},
        )
        response = JSONResponse(envelope, status_code=status_for_exit(envelope["exit_code"]))
        await response(scope, _discarding_receive, send)


class _BodyTooLarge(Exception):
    def __init__(self, seen: int) -> None:
        super().__init__(f"body too large: {seen}")
        self.seen = seen


async def _discarding_receive() -> dict:  # pragma: no cover — never awaited
    return {"type": "http.disconnect"}


def _command_from_path(path: str) -> str:
    """The inverse of :attr:`ApiRoute.path` (best effort, for error envelopes)."""
    text = str(path or "").strip("/")
    prefix = API_PREFIX.strip("/")
    if text == prefix:
        text = ""
    elif text.startswith(f"{prefix}/"):
        text = text[len(prefix) + 1:]
    return text.rstrip("/").replace("/", ".") or "api"


@dataclass
class ServerConfig:
    """Everything the handlers need, in one place."""

    token: str = ""
    token_source: str = "generated"
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    workdir: str | None = None
    python: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    route_map: dict[str, ApiRoute] = field(default_factory=dict)
    #: ``auto`` | ``spawn`` | ``warm`` | ``inprocess`` — see :func:`transport_for`.
    transport: str = DEFAULT_TRANSPORT
    #: Session socket for the warm keep-alive path (``""`` = the session's own default).
    warm_socket: str = ""
    #: Seconds of silence before an SSE stream writes a keep-alive comment.
    sse_heartbeat_s: float = DEFAULT_SSE_HEARTBEAT_S
    #: Idle seconds a client connection may be reused for (HTTP keep-alive).
    keep_alive_s: float = float(DEFAULT_KEEP_ALIVE_S)
    #: A parser built once, its environment fingerprint, and the transport counters.
    parser: Any = None
    parser_env: tuple[tuple[str, str], ...] | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def as_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "endpoint": self.base_url,
            "token_source": self.token_source,
            "token_required_for_mutations": True,
            "timeout_s": self.timeout_s,
            "max_body_bytes": self.max_body_bytes,
            "workdir": self.workdir or os.getcwd(),
            "routes": len(self.route_map),
            "cli_python": self.python or cli_python(),
            "transport": self.transport,
            "transports_available": list(TRANSPORTS),
            "warm_socket": self.warm_socket or None,
            "sse_heartbeat_s": self.sse_heartbeat_s,
            "keep_alive_s": self.keep_alive_s,
        }


def _wants_sse(request: Any, values: Mapping[str, Any] | None = None) -> bool:
    """``Accept: text/event-stream``, ``?sse=1``, or ``{"_sse": true}`` in the body."""
    accept = str(request.headers.get("accept", "") or "")
    if SSE_MEDIA_TYPE in accept:
        return True
    query = getattr(request, "query_params", None)
    if query is not None and str(query.get("sse", "")).lower() in ("1", "true", "yes"):
        return True
    if values:
        return str(values.get("_sse")).lower() in ("1", "true", "yes")
    return False


def _wants_stream(request: Any, values: Mapping[str, Any] | None = None) -> bool:
    """``Accept: application/x-ndjson``, ``?stream=1``, or ``{"_stream": true}`` in the body."""
    accept = str(request.headers.get("accept", "") or "")
    if "application/x-ndjson" in accept:
        return True
    query = getattr(request, "query_params", None)
    if query is not None and str(query.get("stream", "")).lower() in ("1", "true", "yes"):
        return True
    if values:
        flag = values.get("_stream")
        return str(flag).lower() in ("1", "true", "yes")
    return False


#: Request-body keys that describe the *request* rather than the command.
STREAM_KEYS = ("_stream", "stream", "_sse", "sse")


def _consume_stream_flag(body: Any) -> tuple[Any, bool]:
    """Pull the streaming switch out of a JSON body — it is request metadata, not an argument."""
    if not isinstance(body, dict):
        return body, False
    present = [key for key in STREAM_KEYS if key in body]
    if not present:
        return body, False
    requested = any(str(body[key]).lower() in ("1", "true", "yes") for key in present)
    remaining = {key: value for key, value in body.items() if key not in STREAM_KEYS}
    return remaining, requested


def _headers_for(envelope: dict, result: CliResult | None = None) -> dict:
    headers = {
        "X-Godsv-Command": str(envelope.get("command", "")),
        "X-Godsv-Exit-Code": str(envelope.get("exit_code", "")),
        "X-Godsv-Schema-Version": str(envelope.get("schema_version", "")),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if result is not None:
        headers["X-Godsv-Duration-Ms"] = str(result.duration_ms)
    return headers


def build_app(config: ServerConfig) -> Any:
    """The FastAPI app: a route per CLI command, an OpenAPI document, and the guards."""
    fastapi, _uvicorn = fastapi_modules()
    from fastapi import Request  # noqa: PLC0415
    from fastapi.responses import (  # noqa: PLC0415
        JSONResponse,
        PlainTextResponse,
        StreamingResponse,
    )
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415

    # With PEP 563 (``from __future__ import annotations``) the handler annotation is the string
    # "Request" and FastAPI resolves it against the *module* globals — a function-local import is
    # not found there, and the parameter is then taken for a query field (measured: HTTP 422
    # "missing query request" on every route). Publishing the name keeps the import itself lazy.
    globals().setdefault("Request", Request)

    route_list = list(config.route_map.values())
    document = openapi_document(route_list, base_url=config.base_url)
    app = fastapi.FastAPI(
        title="Skaldir API (skaldir)",
        version=__version__,
        description=document["info"]["description"],
        docs_url="/docs",
        redoc_url=None,
        openapi_url=None,  # we serve our own, generated from the contract
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_body_bytes)
    app.state.config = config
    app.state.openapi_document = document
    log = logging.getLogger(__name__)
    refusal_log: list[dict] = []
    app.state.auth_refusals = refusal_log
    #: Per-transport request counters, reported by /health so the chosen path is observable.
    transports_used: dict[str, int] = {}
    app.state.transports_used = transports_used

    def api_envelope(command: str, exit_code: int, **kwargs: Any) -> JSONResponse:
        envelope = server_envelope(command, exit_code, **kwargs)
        return JSONResponse(envelope, status_code=status_for_exit(envelope["exit_code"]),
                            headers=_headers_for(envelope))

    @app.get(HEALTH_PATH)
    async def health() -> Any:  # noqa: ANN202 — FastAPI endpoint
        envelope = server_envelope(
            "api.health", cli_contract.EXIT_OK,
            data={
                "status": "ok",
                "endpoint": config.base_url,
                "godsvoice_version": __version__,
                "schema_version": cli_contract.SCHEMA_VERSION,
                "routes": len(route_list),
                "mutating_routes": sum(1 for route in route_list if route.mutates),
                "token_required_for_mutations": True,
                "token_source": config.token_source,
                "transport": config.transport,
                "transports_available": list(TRANSPORTS),
                "transports_used": dict(sorted(transports_used.items())),
                "in_process_allowlist": sorted(IN_PROCESS_ALLOWLIST),
                "warm_socket": config.warm_socket or None,
                "warm_session_running": bool(_warm_status(config.warm_socket)),
                "sse_stream_contract": SSE_STREAM_CONTRACT,
                "sse_events": list(SSE_EVENTS),
                "sse_heartbeat_s": config.sse_heartbeat_s,
                "keep_alive_s": config.keep_alive_s,
                "excluded": [item["command"] for item in exclusions()],
                "http_status_by_exit": {str(key): value
                                        for key, value in sorted(HTTP_STATUS_BY_EXIT.items())},
            },
            evidence=[{"ref": "src/skaldir/api_server.py", "kind": "code",
                       "note": "route table generated from cli_contract.COMMANDS"},
                      {"ref": "GET /openapi.json", "kind": "file",
                       "note": f"{len(route_list)} path(s) published"},
                      {"ref": "MVP/56-api-latency.md", "kind": "file",
                       "note": "the SSE frame shapes (begin/progress/end) and the transport rules"},
                      ],
        )
        return JSONResponse(envelope, headers=_headers_for(envelope))

    @app.get(OPENAPI_PATH)
    async def openapi() -> Any:  # noqa: ANN202
        return JSONResponse(document, headers={"Cache-Control": "no-store"})

    @app.get("/")
    async def index() -> Any:  # noqa: ANN202
        lines = [
            f"Skaldir API {__version__} — {len(route_list)} command routes",
            f"OpenAPI document: {OPENAPI_PATH}   docs: /docs   health: {HEALTH_PATH}",
            "Read-only routes answer without a token; mutating routes need Authorization: "
            "Bearer <token>.",
            *[f"  {route.path}  ({'mutating' if route.mutates else 'read-only'})"
              for route in route_list],
        ]
        return PlainTextResponse("\n".join(lines) + "\n",
                                 headers={"Cache-Control": "no-store"})

    def values_from_request(request: Any, route: ApiRoute, body: Any) -> dict:
        values: dict[str, Any] = {}
        for spec in route.args:
            if request.query_params is not None and spec["dest"] in request.query_params:
                raw = request.query_params.getlist(spec["dest"])
                if spec["type"] == "flag":
                    values[spec["dest"]] = str(raw[-1]).lower() in ("1", "true", "yes", "on")
                elif spec["multiple"]:
                    values[spec["dest"]] = list(raw)
                else:
                    values[spec["dest"]] = _coerce(spec, raw[-1])
        if body is not None:
            incoming = body_arguments(body)
            overlap = sorted(set(incoming).intersection(values))
            if overlap:
                raise ApiRequestError(
                    f"{route.command}: {', '.join(overlap)} given both as query parameter and "
                    "in the body",
                    details={"duplicated": overlap},
                )
            values.update(incoming)
        return values

    def _coerce(spec: dict, value: Any) -> Any:
        if spec["type"] == "integer":
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ApiRequestError(f"{spec['name']} must be an integer") from None
        if spec["type"] == "number":
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ApiRequestError(f"{spec['name']} must be a number") from None
        return value

    def make_handler(route: ApiRoute, method: str) -> Any:
        async def handler(request: Request) -> Any:  # noqa: ANN202
            log.debug("%s %s", method.upper(), route.path)
            refusal = authorization_failure(route, request.headers, config.token)
            if refusal is not None:
                refusal_log.append({"command": route.command, "at": cli_contract.now_iso(),
                                    "reason": refusal["error"]["details"].get("reason"),
                                    "client": getattr(request.client, "host", None)})
                return JSONResponse(
                    refusal, status_code=status_for_exit(refusal["exit_code"]),
                    headers={**_headers_for(refusal), "WWW-Authenticate": "Bearer"},
                )

            body: Any = None
            stream_requested = _wants_stream(request, None)
            sse_requested = _wants_sse(request, None)
            if method == "post":
                raw = await request.body()
                if raw:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError) as exc:
                        return api_envelope(
                            route.command, cli_contract.EXIT_USAGE,
                            message=f"the request body is not valid JSON ({exc})",
                            hint='send {"<argument>": <value>} or {"args": {...}}',
                            route=route,
                            details={"bytes": len(raw)},
                        )
                    sse_requested = sse_requested or _wants_sse(request, body)
                    body, body_stream = _consume_stream_flag(body)
                    stream_requested = stream_requested or body_stream
            try:
                values = values_from_request(request, route, body)
                argv = build_argv(route, values)
            except ApiRequestError as exc:
                return api_envelope(
                    route.command, cli_contract.EXIT_USAGE,
                    message=exc.message, hint=exc.hint, route=route, details=exc.details,
                )

            if sse_requested:
                return StreamingResponse(
                    _sse_generator(route, argv, config, transport=SPAWN_TRANSPORT,
                                   note="streaming runs in a child process: it needs bytes "
                                        "as they are produced"),
                    media_type=SSE_MEDIA_TYPE,
                    headers={
                        "X-Godsv-Command": route.command,
                        "X-Godsv-Exit-Code": "stream",
                        "X-Godsv-Stream-Contract": SSE_STREAM_CONTRACT,
                        "X-Godsv-Sse-Events": ",".join(SSE_EVENTS),
                        "Cache-Control": "no-store",
                        "X-Accel-Buffering": "no",
                    },
                )
            if stream_requested:
                generator = _stream_generator(route, argv, config)
                return StreamingResponse(
                    generator, media_type=NDJSON_MEDIA_TYPE,
                    headers={"X-Godsv-Command": route.command,
                             "X-Godsv-Exit-Code": "stream",
                             "Cache-Control": "no-store",
                             "X-Accel-Buffering": "no"},
                )

            # The command is blocking code (a child process' stdout, or the CLI's own handler);
            # running it on the event loop would serialize the whole server behind one long
            # command. A worker thread keeps /health and every other route answering (MVP/56 §5).
            routed = await run_in_threadpool(
                run_command_routed, argv, command=route.command, route=route,
                timeout_s=config.timeout_s, workdir=config.workdir, python=config.python,
                transport=config.transport, warm_socket=config.warm_socket,
                parser=config.parser, parser_env=config.parser_env,
            )
            result = routed.result
            transports_used[routed.transport] = transports_used.get(routed.transport, 0) + 1
            if log.isEnabledFor(logging.DEBUG):
                log.debug("%s -> exit %s in %sms on %s", route.command, result.exit_code,
                          result.duration_ms, routed.transport)
            headers = _headers_for(result.envelope, result)
            headers["X-Godsv-Transport"] = routed.transport
            if routed.note:
                headers["X-Godsv-Transport-Note"] = _header_text(routed.note)
            return JSONResponse(result.envelope, status_code=result.status, headers=headers)

        handler.__name__ = f"{route.operation_id}_{method}"
        return handler

    def _stream_generator(route: ApiRoute, argv: list[str], cfg: ServerConfig) -> Iterator[str]:
        for envelope in iter_envelopes(
            argv, command=route.command, route=route, timeout_s=cfg.timeout_s,
            workdir=cfg.workdir, python=cfg.python,
        ):
            yield json.dumps(envelope, ensure_ascii=False, default=str) + "\n"

    def _sse_generator(route: ApiRoute, argv: list[str], cfg: ServerConfig, *,
                       transport: str, note: str) -> Iterator[str]:
        """``begin`` … ``progress``* … ``end`` over ``text/event-stream`` (MVP/56 §3)."""
        return sse_frames(
            envelope_ticks(route, argv, cfg),
            command=route.command,
            route_path=route.path,
            transport=transport,
            transport_note=note,
            heartbeat_s=cfg.sse_heartbeat_s,
            stream_id=f"{route.command}-{int(time.time() * 1000)}",
            api_version=__version__,
            schema_version=cli_contract.SCHEMA_VERSION,
        )

    for route in route_list:
        app.post(route.path, name=f"{route.operation_id}_post")(make_handler(route, "post"))
        if not route.mutates:
            app.get(route.path, name=f"{route.operation_id}_get")(make_handler(route, "get"))

    @app.exception_handler(404)
    async def _not_found(request: Request, exc: Any) -> Any:  # noqa: ANN202, ARG001
        return api_envelope(
            _command_from_path(request.url.path), cli_contract.EXIT_NOT_FOUND,
            message=f"no route for {request.url.path}",
            hint=f"GET {OPENAPI_PATH} for the route list",
            details={"path": request.url.path,
                     "routes": [route.path for route in route_list]},
        )

    @app.exception_handler(405)
    async def _method_not_allowed(request: Request, exc: Any) -> Any:  # noqa: ANN202, ARG001
        return api_envelope(
            _command_from_path(request.url.path), cli_contract.EXIT_USAGE,
            message=f"{request.method} is not allowed on {request.url.path}",
            hint="mutating commands are POST-only; read-only commands accept GET and POST",
        )

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception) -> Any:  # noqa: ANN202
        log.error("unhandled error on %s: %s", request.url.path, type(exc).__name__)
        return api_envelope(
            _command_from_path(request.url.path), cli_contract.EXIT_INTERNAL,
            message=f"the API server failed internally ({type(exc).__name__})",
            hint="see the server log; this is a bug in the transport, not in your request",
            details={"path": request.url.path},
        )

    app.openapi = lambda: document  # type: ignore[method-assign]
    return app


# --------------------------------------------------------------------------- #
# starting the server
# --------------------------------------------------------------------------- #


def startup_lines(config: ServerConfig, token: str, source: str, *, port: int) -> list[str]:
    """The lines printed once at start. The token appears here and nowhere else."""
    mutating = sum(1 for route in config.route_map.values() if route.mutates)
    lines = [
        f"skaldir: Skaldir API listening on {config.base_url} "
        f"(v{__version__}, {len(config.route_map)} command routes, {mutating} mutating)",
        f"skaldir: OpenAPI document {config.base_url}{OPENAPI_PATH} "
        f"(browser UI: {config.base_url}/docs)",
        "skaldir: read-only routes need no token; mutating routes need "
        "Authorization: Bearer <token>",
    ]
    if source == "generated":
        lines.append(f"skaldir: API token (printed once — store it in a secret, not in the "
                     f"repo): {token}")
    elif source == "file":
        lines.append("skaldir: API token loaded from --token-file (not printed)")
    else:
        lines.append(f"skaldir: API token loaded from {TOKEN_ENV} (not printed)")
    lines.append("skaldir: loopback-only unless --allow-remote; Ctrl-C stops the server")
    lines.append(f"skaldir: transport={config.transport} "
                 f"(in-process allowlist {len(IN_PROCESS_ALLOWLIST)} commands, warm session "
                 f"{'available' if _warm_status(config.warm_socket) else 'not running'}), "
                 f"SSE {SSE_STREAM_CONTRACT} ({'/'.join(SSE_EVENTS)})")
    return lines


def emit_startup(lines: Sequence[str]) -> None:
    """Startup facts go to stderr: stdout stays reserved for the one envelope (§4.4)."""
    for line in lines:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    allow_remote: bool = False,
    token_file: str | Path | None = None,
    token: str = "",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    workdir: str | Path | None = None,
    python: str | None = None,
    log_level: str = "info",
    cors_origins: Sequence[str] = (),
    openapi_out: str | Path | None = None,
    parser: argparse.ArgumentParser | None = None,
    transport: str = DEFAULT_TRANSPORT,
    warm_socket: str | Path | None = None,
    sse_heartbeat_s: float = DEFAULT_SSE_HEARTBEAT_S,
    keep_alive_s: float = float(DEFAULT_KEEP_ALIVE_S),
) -> int:
    """Bind, print the token once, serve. Returns the CLI exit code."""
    _fastapi, uvicorn = fastapi_modules()  # raises ApiServerUnavailable when the extra is missing
    check_bind_allowed(host, allow_remote=allow_remote)
    # logging goes up first: resolving the token can warn (a world-readable --token-file) and
    # that warning should be formatted like every other line, not through logging's last resort.
    configure_logging(log_level)
    resolved, source = (str(token), "env") if token else resolve_token(token_file)

    configure_logging(log_level, resolved)
    log = logging.getLogger(__name__)

    if cors_origins:
        log.warning("CORS enabled for %s — read-only routes are token-free on loopback",
                    ", ".join(cors_origins))

    try:
        listener = bind_listener(host, int(port))
    except ApiServerUnavailable:
        raise

    route_list = routes(parser)
    # The parser is built once for the in-process transport; the fingerprint of the environment
    # it was built from travels with it, so a changed environment refuses that path instead of
    # answering from a stale tree (MVP/56 §4).
    built_parser = parser if parser is not None else _parser()
    config = ServerConfig(
        token=resolved, token_source=source, timeout_s=float(timeout_s),
        max_body_bytes=int(max_body_bytes), workdir=str(workdir) if workdir else None,
        python=python or cli_python(), host=host, port=int(port),
        route_map={route.command: route for route in route_list},
        transport=str(transport or DEFAULT_TRANSPORT).lower(),
        warm_socket=str(warm_socket) if warm_socket else "",
        sse_heartbeat_s=max(0.5, float(sse_heartbeat_s)),
        keep_alive_s=max(1.0, float(keep_alive_s)),
        parser=built_parser, parser_env=parser_env_fingerprint(),
    )
    app = build_app(config)
    if cors_origins:
        from fastapi.middleware.cors import CORSMiddleware  # noqa: PLC0415

        app.add_middleware(
            CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type", "X-Godsv-Token"],
            allow_credentials=False,
        )

    if openapi_out:
        target = Path(str(openapi_out)).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(app.state.openapi_document, indent=2) + "\n",
                          encoding="utf-8")
        emit_startup([f"skaldir: OpenAPI document written to {target}"])

    problems = validate_document(app.state.openapi_document, route_list)
    if problems:
        # The document is generated, so a structural problem is a bug in the generator — say so
        # loudly instead of serving a document a consumer cannot use.
        log.warning("the generated OpenAPI document has %d problem(s): %s",
                    len(problems), "; ".join(problems))

    emit_startup(startup_lines(config, resolved, source, port=int(port)))
    log.info("uvicorn %s serving fastapi on %s (transport=%s, keep-alive %gs)",
             getattr(uvicorn, "__version__", "?"), config.base_url, config.transport,
             config.keep_alive_s)

    server = uvicorn.Server(
        uvicorn.Config(
            app, log_config=None, log_level=log_level, access_log=True, server_header=False,
            date_header=False, timeout_keep_alive=config.keep_alive_s,
        )
    )
    try:
        server.run(sockets=[listener])
    except KeyboardInterrupt:  # pragma: no cover — Ctrl-C in a terminal
        emit_startup(["skaldir: stopped"])
    finally:
        with contextlib.suppress(Exception):
            listener.close()
    refusals = getattr(app.state, "auth_refusals", None) or []
    if refusals:
        log.info("%d auth refusal(s) during this run", len(refusals))
    return cli_contract.EXIT_OK


__all__ = [
    "API_PREFIX",
    "DEFAULT_HOST",
    "DEFAULT_KEEP_ALIVE_S",
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_PORT",
    "DEFAULT_SSE_HEARTBEAT_S",
    "DEFAULT_SSE_RETRY_MS",
    "DEFAULT_TIMEOUT_S",
    "HEALTH_PATH",
    "HTTP_STATUS_BY_EXIT",
    "HTTP_STATUS_NOTE",
    "IN_PROCESS_ALLOWLIST",
    "IN_PROCESS_BUDGET_MS",
    "IN_PROCESS_REFUSED",
    "INSTALL_HINT",
    "NDJSON_MEDIA_TYPE",
    "NON_HTTP",
    "OPENAPI_PATH",
    "PARSER_ENV_KEYS",
    "RESERVED_ARGS",
    "SPAWN_TRANSPORT",
    "SSE_EVENTS",
    "SSE_MEDIA_TYPE",
    "SSE_STREAM_CONTRACT",
    "STREAM_COMMANDS",
    "TOKEN_ENV",
    "TRANSPORTS",
    "WARM_TRANSPORT",
    "ApiRequestError",
    "ApiRoute",
    "ApiServerUnavailable",
    "BodyLimitMiddleware",
    "CliResult",
    "RedactingFormatter",
    "Routed",
    "ServerConfig",
    "TransportUnavailable",
    "arg_specs",
    "authorization_failure",
    "bind_listener",
    "body_arguments",
    "build_app",
    "build_argv",
    "check_bind_allowed",
    "child_env",
    "cli_python",
    "configure_logging",
    "emit_startup",
    "envelope_ticks",
    "exclusions",
    "fastapi_modules",
    "in_process_verdict",
    "iter_envelopes",
    "openapi_document",
    "parser_env_fingerprint",
    "presented_token",
    "probe_existing_server",
    "redact",
    "resolve_token",
    "route_for",
    "routes",
    "run_cli",
    "run_cli_inprocess",
    "run_cli_warm",
    "run_command_routed",
    "server_envelope",
    "serve",
    "sse_comment",
    "sse_frame",
    "sse_frames",
    "startup_lines",
    "status_for_exit",
    "token_matches",
    "transport_for",
    "validate_document",
]
