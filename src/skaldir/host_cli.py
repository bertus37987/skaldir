"""``skaldir host …`` — the host bridge's CLI group: the HOST's native features.

The verbs (all read-only, all ``--json`` through the product envelope):

    host status              is a host installed, is one running, which version, which home
    host sessions            the host's session store (``GET /api/sessions``, profile named)
    host memory search TEXT  the host's own search index (FTS5 over its message store)
    host skills              the host's installed skills
    host cron                the host's scheduled jobs
    host tools               the host's toolsets and their tools
    host doctor              what of the host is reachable from here, route by route

Exit codes — the product CLI's ``cli_contract`` vocabulary is adopted when that module
exists, otherwise the same numbers are restated (see ``host_bridge``):

    0 [design 0]       ok — the host answered
    1 [design 1]       usage: the caller asked for something impossible
    3 [design 3]       blocked — the host is installed but the feature cannot be read
                       (no backend answering, a route refused, …); the reason is the payload
    4 [design 4]       needs setup — no host installed on this machine at all
    7 [design 7]       internal — our bug

**No write verb is exposed.** Sending a message, running a job, installing a skill or
touching the host's config is out of scope for this block by decision: the group drives
the host only through reads. ``host sessions --via cli`` is the one non-JSON transport
(it parses the host CLI's human table) and it says so in its own payload
(``"fragile": true``); the default is always HTTP.

WIRING — ``register_cli`` is the whole contract (the group owns its verbs *and* their
``cli_contract.COMMANDS`` entries, like ``act_cli``):

    from .host_cli import register_cli as _register_host_bridge

    _register_host_bridge(sub)
"""

from __future__ import annotations

import argparse
from typing import Any

from .cli_shared import install_contract_entries, product_contract

__all__ = [
    "COMMAND_EVIDENCE",
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_NEEDS_SETUP",
    "EXIT_OK",
    "EXIT_USAGE",
    "build_host_parser",
    "build_parser",
    "contract_entries",
    "main",
    "register_cli",
    "register_contract_entries",
]

#: The design's exit codes restated, so the group also runs without the contract module.
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_NEEDS_SETUP = 4
EXIT_INTERNAL = 7


_CONTRACT = product_contract()


def _bridge():
    """``skaldir.host_bridge`` — imported lazily so ``--help`` never pays for httpx."""
    from . import host_bridge

    return host_bridge


# --------------------------------------------------------------------------- #
# the envelope                                                                  #
# --------------------------------------------------------------------------- #
def _report(
    payload: Any, *, evidence: tuple[dict, ...] = (), warnings: tuple[str, ...] = ()
) -> None:
    """Fill the shared envelope (data + evidence); harmless when running standalone."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")), str(item.get("note", ""))
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _runtime_evidence(payload: dict, path: str) -> tuple[dict, ...]:
    """The evidence that is *this run's*: which backend answered which request.

    The contract entry already carries the code reference as default evidence, so a
    verb adds only what a default cannot know — the live backend and the request.
    """
    backend = payload.get("backend") or {}
    url = str(backend.get("url") or "")
    request = str(payload.get("request") or f"GET {path}")
    if not url:
        return ({"ref": request, "kind": "run", "note": "the host CLI's own command"},) \
            if request else ()
    return ({"ref": f"{url}{path}", "kind": "run", "note": request},)


def _verdict(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    """One error record + its exit code, through the contract when it is there."""
    if _CONTRACT is None:
        print(message)
        return int(code)
    body = dict(details or {})
    if code == EXIT_NEEDS_SETUP:
        # The contract's code 4 is named NEEDS_CREDENTIALS; for this group it means
        # "the host itself is missing". Say so in the record instead of pretending.
        body.setdefault("needs", "a host installation (`hermes`) on this machine")
        body.setdefault(
            "contract_code_4",
            "the contract spells 4 needs_credentials; this group reads it as "
            "needs-setup (the host is absent)")
        hint = hint or "install the host (the `hermes` CLI), then re-run `skaldir host doctor`"
    return _CONTRACT.fail(code, message, hint=hint, details=body)


def _host_failure(exc: Exception) -> int:
    """Map a bridge verdict onto the product CLI's codes (3 blocked / 4 needs setup)."""
    bridge = _bridge()
    code = int(getattr(exc, "exit_code", getattr(bridge, "EXIT_INTERNAL", EXIT_INTERNAL)))
    message = str(getattr(exc, "reason", "") or exc)
    hint = str(getattr(exc, "hint", "") or "")
    details = dict(getattr(exc, "details", {}) or {})
    return _verdict(code, message, hint=hint, details=details)


# --------------------------------------------------------------------------- #
# the commands                                                                  #
# --------------------------------------------------------------------------- #
def cmd_status(args: argparse.Namespace) -> int:
    """``host status`` — installed / running / version / backend, honestly."""
    bridge = _bridge()
    payload = bridge.status(
        url=str(getattr(args, "url", "") or ""),
        token=str(getattr(args, "token", "") or ""),
        timeout=_timeout(args),
    )
    _report(
        payload,
        evidence=_runtime_evidence(payload, "/api/status"),
    )
    state = "installed" if payload.get("installed") else "not installed"
    running = "running" if payload.get("running") else "not running"
    where = str((payload.get("backend") or {}).get("url") or "") if payload.get("running") else ""
    print(f"host {payload.get('version') or '?'}: {state}, {running}"
          + (f" at {where}" if where else ""))
    if not payload.get("installed"):
        return _verdict(
            EXIT_NEEDS_SETUP,
            str(payload.get("reason") or "no host installed on this machine"),
            hint="install the host (the `hermes` CLI) — then `skaldir host doctor`",
        )
    if not payload.get("running"):
        return _verdict(
            EXIT_BLOCKED,
            str(payload.get("reason") or "the host is installed but no backend is answering"),
            hint=str(payload.get("hint") or "start the desktop app or `hermes serve`"),
            details=payload.get("discovery") or {},
        )
    return EXIT_OK


def cmd_sessions(args: argparse.Namespace) -> int:
    """``host sessions`` — the host's session store, read-only."""
    bridge = _bridge()
    limit = _positive_int(getattr(args, "limit", None), 20)
    if limit < 1:
        return _verdict(EXIT_USAGE, "--limit must be >= 1", hint="host sessions --limit 20")
    via = str(getattr(args, "via", "http") or "http")
    try:
        if via == "cli":
            payload = bridge.sessions_via_cli(limit)
        else:
            payload = bridge.sessions(
                url=str(getattr(args, "url", "") or ""),
                token=str(getattr(args, "token", "") or ""),
                profile=str(getattr(args, "profile", "default") or "default"),
                limit=limit,
                offset=int(getattr(args, "offset", 0) or 0),
                order=str(getattr(args, "order", "recent") or "recent"),
                timeout=_timeout(args),
            )
    except Exception as exc:  # noqa: BLE001 — a bridge verdict, mapped
        return _host_failure(exc)
    warnings = ()
    if payload.get("fragile"):
        warnings = (
            "FRAGILE transport: this payload was parsed from the host CLI's human table "
            "(`hermes sessions list`), not from a JSON surface — use --via http when a "
            "backend runs",
        )
    _report(
        payload,
        evidence=_runtime_evidence(payload, "/api/sessions"),
        warnings=warnings,
    )
    for row in payload.get("sessions", [])[:limit]:
        print(f"  {row.get('id')}  {(row.get('title') or '')[:48]}  "
              f"{(row.get('last_active') or row.get('last_active_at') or '')}")
    print(
        f"{payload.get('count')} session(s) from {payload.get('store')} "
        f"(profile {payload.get('profile')}"
        + (f", total {payload.get('total')}" if payload.get("total") is not None else "")
        + (f", {payload.get('pinned')} pinned past the page" if payload.get("pinned") else "")
        + ")"
    )
    return EXIT_OK


def cmd_memory_search(args: argparse.Namespace) -> int:
    """``host memory search`` — the host's own search, read-only."""
    bridge = _bridge()
    query = str(getattr(args, "query", "") or "").strip()
    if not query:
        return _verdict(
            EXIT_USAGE,
            "a query is required",
            hint='skaldir host memory search "penpot" --limit 10 --json',
        )
    try:
        payload = bridge.memory_search(
            query,
            url=str(getattr(args, "url", "") or ""),
            token=str(getattr(args, "token", "") or ""),
            profile=str(getattr(args, "profile", "default") or "default"),
            limit=max(1, _positive_int(getattr(args, "limit", None), 20)),
            timeout=_timeout(args),
        )
    except Exception as exc:  # noqa: BLE001
        return _host_failure(exc)
    _report(payload, evidence=_runtime_evidence(payload, "/api/sessions/search"))
    wanted = max(1, _positive_int(getattr(args, "limit", None), 20))
    for row in payload.get("results", [])[:wanted]:
        print(f"  {row.get('session_id')}  {row.get('role')}  {(row.get('snippet') or '')[:80]}")
    print(f"{payload.get('count')} result(s) from {payload.get('source')}")
    return EXIT_OK


def cmd_skills(args: argparse.Namespace) -> int:
    """``host skills`` — the host's installed skills."""
    return _simple_list(args, "skills", "skills", "/api/skills", "skill(s)")


def cmd_cron(args: argparse.Namespace) -> int:
    """``host cron`` — the host's scheduled jobs."""
    return _simple_list(args, "cron_jobs", "jobs", "/api/cron/jobs", "job(s)")


def cmd_tools(args: argparse.Namespace) -> int:
    """``host tools`` — the host's toolsets and their tools."""
    return _simple_list(args, "toolsets", "toolsets", "/api/tools/toolsets", "toolset(s)")


def _simple_list(args: argparse.Namespace, func_name: str, key: str, path: str, noun: str) -> int:
    """The three flat lists share one body: call, report, print the human line."""
    bridge = _bridge()
    profile = str(getattr(args, "profile", "") or "")
    kwargs: dict[str, Any] = {
        "url": str(getattr(args, "url", "") or ""),
        "token": str(getattr(args, "token", "") or ""),
        "timeout": _timeout(args),
    }
    if profile:
        kwargs["profile"] = profile
    try:
        payload = getattr(bridge, func_name)(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return _host_failure(exc)
    _report(payload, evidence=_runtime_evidence(payload, path))
    rows = payload.get(key) or []
    for row in rows[:20]:
        label = row.get("name") or row.get("id") or ""
        if key == "toolsets":
            print(f"  {label}  enabled={row.get('enabled')}  tools={len(row.get('tools') or [])}")
        elif key == "jobs":
            print(f"  {label}  {row.get('schedule') or ''}  enabled={row.get('enabled')}")
        else:
            print(f"  {label}  [{row.get('category') or '-'}]  enabled={row.get('enabled')}")
    extra = (
        f", {payload.get('tool_count')} tool(s)" if payload.get("tool_count") is not None else ""
    )
    print(f"{payload.get('count')} {noun}{extra} from {payload.get('source')}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """``host doctor`` — one probe per route this bridge actually uses."""
    bridge = _bridge()
    payload = bridge.doctor(
        url=str(getattr(args, "url", "") or ""),
        token=str(getattr(args, "token", "") or ""),
        probe_cli=bool(getattr(args, "probe_cli", False)),
        timeout=_timeout(args),
    )
    probes = ", ".join(str(entry.get("path")) for entry in payload.get("routes", []))
    _report(
        payload,
        evidence=(
            {"ref": "/api/health … /api/logs", "kind": "run",
             "note": f"the routes probed, in order: {probes}" if probes else "no route answered"},
            {"ref": "src/skaldir/host_bridge.py:106", "kind": "code",
             "note": "READ_ONLY_ROUTES — the whitelist that keeps this group read-only"},
        ),
    )
    for entry in payload.get("routes", []):
        print(f"  {'ok  ' if entry.get('ok') else 'FAIL'} {entry.get('name'):<14} "
              f"{entry.get('path')}  HTTP {entry.get('status')}  {entry.get('detail')}")
    if payload.get("blocked"):
        print("blocked: " + ", ".join(sorted(payload["blocked"])))
    if not payload.get("installed"):
        return _verdict(
            EXIT_NEEDS_SETUP,
            str(payload.get("reason") or "no host installed on this machine"),
            hint="install the host (the `hermes` CLI), then re-run `skaldir host doctor`",
        )
    if not payload.get("running"):
        return _verdict(
            EXIT_BLOCKED,
            str(payload.get("reason") or "the host is installed but no backend is answering"),
            hint=str(payload.get("hint") or "start the desktop app or `hermes serve`"),
            details=payload.get("discovery") or {},
        )
    if payload.get("blocked"):
        return _verdict(
            EXIT_BLOCKED,
            "some host routes are blocked: " + ", ".join(sorted(payload["blocked"])),
            hint="the per-route status and detail are in data.routes",
            details={"blocked": payload["blocked"]},
        )
    print(f"host {payload.get('version')} at {(payload.get('backend') or {}).get('url')}: "
          f"{len(payload.get('reachable') or {})} route(s) reachable, none blocked")
    return EXIT_OK


def _positive_int(value: Any, default: int) -> int:
    """A count the user typed: ``None`` means "not given", anything else is parsed.

    Written out because ``int(value or default)`` quietly turns an explicit ``0``
    (a caller's real mistake, which must be a usage error) into the default.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timeout(args: argparse.Namespace) -> float:
    try:
        return float(getattr(args, "timeout", 8.0) or 8.0)
    except (TypeError, ValueError):
        return 8.0


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #
def _add_host_flags(parser: Any) -> None:
    """The flags every verb takes: which backend, which token, how long to wait."""
    parser.add_argument(
        "--url", default="", metavar="URL",
        help="a host backend, e.g. http://127.0.0.1:42703 (default: scan /proc, "
             "else $SKALDIR_HOST_URL)",
    )
    parser.add_argument(
        "--token", default="", metavar="TOKEN",
        help="the backend's loopback session token (default: the backend process's own "
             "environment, else its SPA document; $SKALDIR_HOST_TOKEN is the env fallback)",
    )
    parser.add_argument(
        "--timeout", type=float, default=8.0, help="per-request seconds (default 8)"
    )
    _share(parser)


def _share(parser: Any) -> None:
    """``--json/--format/--quiet/--no-color`` — the product CLI's shared flags."""
    try:
        from .cli_shared import share_common_flags

        share_common_flags(parser)
    except Exception:  # noqa: BLE001 — a flag helper must never break the group
        parser.add_argument("--json", action="store_true")


def _add_host_verbs(sub: Any) -> Any:
    """Add the ``host`` verbs to a subparsers handle (one definition)."""
    p_status = sub.add_parser(
        "status",
        help="is a host installed, is one running, which version, which home (read-only)",
        description=(
            "Installation facts come from the host's own CLI (`hermes --version`); live "
            "facts come from the host backend's public /api/health and /api/status. Exit 4 "
            "when no host is installed, exit 3 when one is installed but no backend answers "
            "— never a fabricated 'fine'."
        ),
    )
    _add_host_flags(p_status)
    p_status.set_defaults(func=cmd_status)

    p_sessions = sub.add_parser(
        "sessions",
        help="the host's session store, read-only (GET /api/sessions)",
        description=(
            "The machine-level profile is the default (`--profile default`): the bare "
            "route without a profile reads the *backend process's own* store, which for "
            "the desktop-spawned backend is the app's sandbox and is usually empty. "
            "`--profile -` sends no profile parameter at all."
        ),
    )
    p_sessions.add_argument("--limit", type=int, default=20, help="rows to ask for (default 20)")
    p_sessions.add_argument("--offset", type=int, default=0, help="page offset (default 0)")
    p_sessions.add_argument(
        "--order", choices=("recent", "created"), default="recent",
        help="sort order (default recent)",
    )
    p_sessions.add_argument(
        "--profile", default="default",
        help="which profile's store to read; `-` omits the parameter (default: default)",
    )
    p_sessions.add_argument(
        "--via", choices=("http", "cli"), default="http",
        help="http (the machine-readable API, default) or cli (parses the host CLI's "
             "human table — FRAGILE, marked in the payload)",
    )
    _add_host_flags(p_sessions)
    p_sessions.set_defaults(func=cmd_sessions)

    p_memory = sub.add_parser("memory", help="the host's memory/session search (read-only)")
    memory_sub = p_memory.add_subparsers(dest="memory_command", required=True)
    p_search = memory_sub.add_parser(
        "search",
        help="search the host's own index (FTS5 over its message store), read-only",
        description=(
            "GET /api/sessions/search on the host backend: FTS5 over the host's message "
            "index with compression-lineage dedup. It searches the host's *session store*, "
            "not MEMORY.md — the built-in memory files are reported by `host doctor`."
        ),
    )
    p_search.add_argument("query", help="the text to search for")
    p_search.add_argument("--limit", type=int, default=20, help="results to ask for (default 20)")
    p_search.add_argument(
        "--profile", default="default", help="which profile's store to search (default: default)"
    )
    _add_host_flags(p_search)
    p_search.set_defaults(func=cmd_memory_search)

    p_skills = sub.add_parser("skills", help="the host's installed skills (read-only)")
    p_skills.add_argument("--profile", default="", help="a profile's skills (default: host's own)")
    _add_host_flags(p_skills)
    p_skills.set_defaults(func=cmd_skills)

    p_cron = sub.add_parser("cron", help="the host's scheduled jobs (read-only)")
    p_cron.add_argument("--profile", default="all", help="which profile's jobs (default: all)")
    _add_host_flags(p_cron)
    p_cron.set_defaults(func=cmd_cron)

    p_tools = sub.add_parser("tools", help="the host's toolsets and their tools (read-only)")
    p_tools.add_argument("--profile", default="", help="a profile's toolsets (default: host's own)")
    _add_host_flags(p_tools)
    p_tools.set_defaults(func=cmd_tools)

    p_doctor = sub.add_parser(
        "doctor",
        help="what of the host is reachable from here, route by route (read-only)",
        description=(
            "Probes exactly the routes the verbs use, with the parameters those verbs send, "
            "and reports each HTTP status. `--probe-cli` additionally runs "
            "`hermes serve --status` as evidence — that command cannot see a `--port 0` "
            "serve, which is why discovery reads /proc instead."
        ),
    )
    p_doctor.add_argument(
        "--probe-cli", dest="probe_cli", action="store_true",
        help="also run `hermes serve --status` and report it as (blind) evidence",
    )
    _add_host_flags(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)
    return sub


def build_host_parser(subparsers: Any) -> Any:
    """Add the ``host`` command group to a CLI's ``subparsers`` handle and return it."""
    parser = subparsers.add_parser(
        "host",
        help="the installed host's native features: sessions, memory search, skills, cron, tools",
        description=(
            "Reads the HOST's own native features through the host's own sanctioned "
            "surfaces — its loopback backend's documented JSON routes (machine-readable, "
            "preferred) and its CLI for installation facts. The Skaldir CLI keeps its own "
            "core; this group is the bridge, and it is read-only: no write verb is exposed."
        ),
    )
    sub = parser.add_subparsers(dest="host_command", required=True)
    _add_host_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent — called twice on the same handle it returns the existing ``host``
    group instead of letting argparse raise ``conflicting subparser``. It also adds
    this group's ``cli_contract.COMMANDS`` entries, so a wired-in group never ships
    without a contract (``tests/test_cli_contract_dump.py``).
    """
    existing = getattr(subparsers, "choices", {}).get("host")
    if existing is not None:
        register_contract_entries()
        return existing
    parser = build_host_parser(subparsers)
    register_contract_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (``python -m skaldir.host_cli``).

    Same verbs as ``build_host_parser`` minus the wrapping ``host`` word, so
    ``main(["status", "--json"])`` and ``skaldir host status --json`` run the same
    handler.
    """
    parser = argparse.ArgumentParser(prog="skaldir host", description="host bridge (read-only)")
    sub = parser.add_subparsers(dest="command")
    _add_host_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir host …`` — returns the exit code, never raises for a bad request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already chose a code
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


# --------------------------------------------------------------------------- #
# the contract entries (registered at CLI build time)                           #
# --------------------------------------------------------------------------- #
#: The code each verb's answer is read from — the evidence a reader can check.
COMMAND_EVIDENCE: dict[str, tuple[dict, ...]] = {
    "host.status": (
        {"ref": "src/skaldir/host_bridge.py:629", "kind": "code",
         "note": "status() — install facts + the live backend's own /api/health and /api/status"},
    ),
    "host.sessions": (
        {"ref": "src/skaldir/host_bridge.py:690", "kind": "code",
         "note": "sessions() — GET /api/sessions with an explicit profile"},
    ),
    "host.memory.search": (
        {"ref": "src/skaldir/host_bridge.py:820", "kind": "code",
         "note": "memory_search() — GET /api/sessions/search (FTS5)"},
    ),
    "host.skills": (
        {"ref": "src/skaldir/host_bridge.py:873", "kind": "code",
         "note": "skills() — GET /api/skills"},
    ),
    "host.cron": (
        {"ref": "src/skaldir/host_bridge.py:910", "kind": "code",
         "note": "cron_jobs() — GET /api/cron/jobs"},
    ),
    "host.tools": (
        {"ref": "src/skaldir/host_bridge.py:943", "kind": "code",
         "note": "toolsets() — GET /api/tools/toolsets"},
    ),
    "host.doctor": (
        {"ref": "src/skaldir/host_bridge.py:991", "kind": "code",
         "note": "doctor() — one GET per route, with each verb's own params"},
    ),
}


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entries for this group (registered at build time).

    A new ``skaldir`` command cannot ship without a contract entry; the group adds
    its own here so the CLI's module keeps its single ownership and nothing has to be
    pasted by hand.
    """
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    reads = (EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NEEDS_SETUP, EXIT_INTERNAL)
    entries = {
        "host.status": spec(
            "kernel",
            COMMAND_EVIDENCE["host.status"],
            "is a host installed, is one running, which version, which home",
            exit_codes=reads,
            data_schema={
                "type": "object",
                "properties": {
                    "installed": {"type": "boolean"},
                    "binary": {"type": ["string", "null"]},
                    "version": {"type": ["string", "null"]},
                    "version_source": {"type": ["string", "null"]},
                    "hermes_home": {"type": "string"},
                    "running": {"type": "boolean"},
                    "backend": {"type": ["object", "null"]},
                    "auth_required": {"type": ["boolean", "null"]},
                    "gateway": {"type": ["object", "null"]},
                    "components": {"type": ["object", "null"]},
                    "discovery": {"type": ["object", "null"]},
                    "reason": {"type": "string"},
                },
                "required": ["installed", "running", "hermes_home"],
            },
            notes="reads the host's own surfaces only; no token is needed for its two "
            "public routes. exit 4 = no host installed, exit 3 = installed but no "
            "backend answering",
        ),
        "host.sessions": spec(
            "kernel",
            COMMAND_EVIDENCE["host.sessions"],
            "the host's session store, read-only",
            exit_codes=reads,
            data_schema={
                "type": "object",
                "properties": {
                    "backend": {"type": ["object", "null"]},
                    "request": {"type": "string"},
                    "profile": {"type": "string"},
                    "store": {"type": "string"},
                    "total": {"type": ["integer", "null"]},
                    "count": {"type": "integer"},
                    "pinned": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "order": {"type": "string"},
                    "note": {"type": "string"},
                    "sessions": {"type": "array"},
                    "transport": {"type": "string"},
                    "fragile": {"type": "boolean"},
                },
                "required": ["profile", "count", "sessions", "transport", "fragile"],
            },
            notes="--via http (default) is the machine-readable route; --via cli parses the "
            "host CLI's human table and marks the payload fragile:true. The default profile is "
            "the machine-level store: the bare route reads the backend process's own "
            "(usually empty)",
        ),
        "host.memory.search": spec(
            "kernel",
            COMMAND_EVIDENCE["host.memory.search"],
            "search the host's own index (FTS5 over its message store)",
            exit_codes=reads,
            data_schema={
                "type": "object",
                "properties": {
                    "backend": {"type": ["object", "null"]},
                    "request": {"type": "string"},
                    "query": {"type": "string"},
                    "profile": {"type": "string"},
                    "source": {"type": "string"},
                    "searches": {"type": "string"},
                    "count": {"type": "integer"},
                    "results": {"type": "array"},
                    "transport": {"type": "string"},
                    "fragile": {"type": "boolean"},
                },
                "required": ["query", "count", "results", "fragile"],
            },
            notes="read-only search over the host's session/message index — not MEMORY.md",
        ),
        "host.skills": spec(
            "kernel",
            COMMAND_EVIDENCE["host.skills"],
            "the host's installed skills",
            exit_codes=reads,
            data_schema={
                "type": "object",
                "properties": {
                    "backend": {"type": ["object", "null"]},
                    "request": {"type": "string"},
                    "profile": {"type": "string"},
                    "source": {"type": "string"},
                    "count": {"type": "integer"},
                    "enabled": {"type": "integer"},
                    "by_provenance": {"type": "object"},
                    "skills": {"type": "array"},
                    "transport": {"type": "string"},
                    "fragile": {"type": "boolean"},
                },
                "required": ["count", "skills", "source"],
            },
            notes="the host's own /api/skills catalogue (bundled/hub/agent provenance, "
            "enabled flags)",
        ),
        "host.cron": spec(
            "kernel",
            COMMAND_EVIDENCE["host.cron"],
            "the host's scheduled jobs",
            exit_codes=reads,
            data_schema={
                "type": "object",
                "properties": {
                    "backend": {"type": ["object", "null"]},
                    "request": {"type": "string"},
                    "profile": {"type": "string"},
                    "source": {"type": "string"},
                    "count": {"type": "integer"},
                    "jobs": {"type": "array"},
                    "transport": {"type": "string"},
                    "fragile": {"type": "boolean"},
                },
                "required": ["count", "jobs", "source"],
            },
            notes="listing only — creating, running or pausing a job is out of scope for "
            "this block",
        ),
        "host.tools": spec(
            "kernel",
            COMMAND_EVIDENCE["host.tools"],
            "the host's toolsets and their tools",
            exit_codes=reads,
            data_schema={
                "type": "object",
                "properties": {
                    "backend": {"type": ["object", "null"]},
                    "request": {"type": "string"},
                    "profile": {"type": "string"},
                    "source": {"type": "string"},
                    "count": {"type": "integer"},
                    "tool_count": {"type": "integer"},
                    "toolsets": {"type": "array"},
                    "transport": {"type": "string"},
                    "fragile": {"type": "boolean"},
                },
                "required": ["count", "toolsets", "source"],
            },
            notes="what the host has and what is enabled/configured — enabling or disabling "
            "is a write",
        ),
        "host.doctor": spec(
            "kernel",
            COMMAND_EVIDENCE["host.doctor"],
            "what of the host is reachable from here, route by route",
            exit_codes=reads,
            data_schema={
                "type": "object",
                "properties": {
                    "installed": {"type": "boolean"},
                    "running": {"type": "boolean"},
                    "backend": {"type": ["object", "null"]},
                    "discovery": {"type": ["object", "null"]},
                    "routes": {"type": "array"},
                    "reachable": {"type": "object"},
                    "blocked": {"type": "object"},
                    "cli_serve_status": {"type": "object"},
                    "notes": {"type": "array"},
                },
                "required": ["installed", "running", "routes", "reachable", "blocked"],
            },
            notes="exit 0 only when every probed route answered; --probe-cli adds "
            "`hermes serve --status` as evidence (it cannot see a --port 0 serve)",
        ),
    }
    return entries


def register_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added."""
    global _CONTRACT
    _CONTRACT = product_contract()
    return install_contract_entries(contract_entries(), _CONTRACT)


if __name__ == "__main__":  # pragma: no cover — the module runner
    raise SystemExit(main())
