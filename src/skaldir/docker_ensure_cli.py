"""``skaldir docker ensure`` — the CLI verb of the DOCKER-ENSURE capability.

One verb, one job: bring the container runtime up through password-free paths
(systemd socket activation, then rootless podman) and report the truth. It exists
because a *shell user* wants the same thing the agent tool gives the model::

    skaldir docker ensure --json          # 0 running · 3 blocked, with the exact command
    skaldir docker ensure                 # human line, black/white
    skaldir docker ensure --no-start      # read-only status: probe, attempt nothing
    skaldir docker ensure --no-fallback   # docker only (the "is docker up?" question)

Exit codes are the product CLI's vocabulary (``cli_contract``): ``0`` a runtime
answered · ``1`` usage · ``3`` blocked (nothing answered; the payload carries
``reason`` and ``exact_command``) · ``7`` internal.

**Integration — the only change ``cli.py`` needs (2 lines).** In
``cli.build_parser()``, next to the other group hooks::

    from .docker_ensure_cli import register_cli as _register_docker_ensure  # +
    _register_docker_ensure(sub)                                            # +

``register_cli`` installs its own ``cli_contract.COMMANDS`` entry for
``docker.ensure`` through ``cli_shared.install_contract_entries`` — the same
mechanism ``tools_core``/``act_cli`` use — so nothing has to be pasted into
``cli_contract.py`` and ``contract dump --json`` keeps ``unregistered: []``. The
same call also installs the capability's ``skaldir selftest`` probe (see
:func:`skaldir.docker_ensure.install_selftest_probe`), so one wiring line covers
both surfaces. ``proposed_contract_entries()`` returns the identical
``COMMANDS`` entry for a reviewer who prefers the literal table.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import docker_ensure as DE

try:  # the shared group plumbing (single implementation, never copied)
    from .cli_shared import (
        dump,
        install_contract_entries,
        product_contract,
        share_common_flags,
        text_mode,
    )
except Exception:  # pragma: no cover — a build without the product CLI helpers
    install_contract_entries = None  # type: ignore[assignment]

    def product_contract() -> Any:  # type: ignore[misc]
        return None

    def share_common_flags(parser: Any) -> None:  # type: ignore[misc]
        return None

    def text_mode(args: argparse.Namespace) -> bool:  # type: ignore[misc]
        return False

    def dump(payload: Any) -> None:  # type: ignore[misc]
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


_CONTRACT = product_contract()

_COMMAND = "docker.ensure"
_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")

#: True while ``python -m skaldir.docker_ensure_cli`` drives the verb itself (no
#: product CLI envelope around it): then *this* module prints, human or ``--json``.
_STANDALONE = False
_JSON_REQUESTED = False


def _standalone() -> bool:
    return _STANDALONE


def _human(args: argparse.Namespace) -> bool:
    """Print human text? Standalone does unless ``--json``; otherwise ``--text``."""
    if _standalone():
        return not _JSON_REQUESTED
    if text_mode(args) or _CONTRACT is None:
        return True
    return False


# --------------------------------------------------------------- envelope glue


def _report(
    payload: Any,
    *,
    evidence: tuple[dict, ...] = (),
    warnings: tuple[str, ...] = (),
    next_actions: tuple[tuple[str, str], ...] = (),
) -> None:
    """Hand the payload and its evidence to the shared envelope (when wired).

    Standalone (``python -m skaldir.docker_ensure_cli``) there is no envelope:
    with ``--json`` this prints the payload as one JSON document instead.
    """
    if _standalone():
        if _JSON_REQUESTED:
            dump(payload)
        return
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            kind = str(item.get("kind", "code"))
            _CONTRACT.add_evidence(
                str(item.get("ref", "")),
                kind if kind in _EVIDENCE_KINDS else "code",
                str(item.get("note", "")),
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _blocked(
    message: str, *, hint: str = "", next_command: str = "", reason: str = "",
    details: dict | None = None,
) -> int:
    """Exit 3 through the product's helper when wired, else by hand."""
    if _CONTRACT is not None and not _standalone() and hasattr(_CONTRACT, "blocked"):
        return int(
            _CONTRACT.blocked(
                message, hint=hint, next_command=next_command, reason=reason, details=details
            )
        )
    print(f"blocked: {message}", file=sys.stderr, flush=True)
    return DE.EXIT_BLOCKED


def _usage(message: str, *, hint: str = "") -> int:
    if _CONTRACT is not None and not _standalone() and hasattr(_CONTRACT, "usage_error"):
        return int(_CONTRACT.usage_error(message, hint=hint))
    print(f"usage: {message}", file=sys.stderr, flush=True)
    return 1


# --------------------------------------------------------------------- the verb


def _declared_paths(args: argparse.Namespace) -> tuple[tuple[str, ...], str]:
    """The declared path order from the flags — ``--no-fallback`` and ``--path``.

    Returns ``(paths, problem)``. ``--no-fallback`` is the docker-only question:
    it declares *no* start path at all, which is a legitimate request and is
    reported as such (``paths: []``) rather than silently skipped.
    """
    if bool(getattr(args, "no_fallback", False)):
        return (), ""
    given = [str(item) for item in (getattr(args, "path", None) or [])]
    if not given:
        return DE.DEFAULT_START_PATHS, ""
    unknown = [item for item in given if item not in DE.KNOWN_PATHS]
    if unknown:
        return (), (
            f"unknown start path(s): {', '.join(unknown)} "
            f"(known: {', '.join(DE.KNOWN_PATHS)})"
        )
    return tuple(given), ""


def _evidence(report: DE.EnsureReport) -> tuple[dict, ...]:
    """Code evidence for the state machine, run evidence for every command it ran."""
    items: list[dict] = [
        {
            "ref": DE.EVIDENCE_REF,
            "kind": "code",
            "note": "the state machine that produced this report",
        }
    ]
    items.extend(
        {"ref": command, "kind": "run", "note": "executed by docker ensure"}
        for command in report.commands
    )
    return tuple(items)


def _print_human(report: DE.EnsureReport) -> None:
    """One honest line per outcome; the blocked case names the exact command."""
    mark = "running" if report.ok else "unavailable"
    if report.ok:
        print(
            f"[{mark:11s}] {report.provider} {report.server_version or '?'} via "
            f"{report.answered_by} — storage driver {report.storage_driver or 'unknown'} "
            f"({report.duration_ms} ms)"
        )
        return
    print(f"[{mark:11s}] no container runtime answered ({report.duration_ms} ms)")
    print(f"             reason: {report.reason}")
    for attempt in report.attempts:
        state = "ok" if attempt.ok else "no"
        print(f"             tried {attempt.path}: {state} — {attempt.detail}")
    print(f"             next:   {report.exact_command}")


def _cmd_ensure(args: argparse.Namespace) -> int:
    paths, problem = _declared_paths(args)
    if problem:
        return _usage(
            problem, hint="pass --path socket-activation,rootless-podman or --no-fallback"
        )
    report = DE.ensure(
        timeout_s=DE.clamp_timeout(getattr(args, "timeout_s", DE.DEFAULT_TIMEOUT_S)),
        ready_s=DE.clamp_timeout(getattr(args, "ready_s", DE.ACTIVATION_READY_S)),
        start=not bool(getattr(args, "no_start", False)),
        paths=paths,
        docker_bin=str(getattr(args, "docker_bin", "") or "") or None,
        podman_bin=str(getattr(args, "podman_bin", "") or "") or None,
    )
    payload = report.to_dict()
    _report(payload, evidence=_evidence(report))
    if _human(args):
        _print_human(report)
    if report.ok:
        return DE.EXIT_OK
    return _blocked(
        f"no container runtime answered: {report.reason}",
        hint=f"run `{report.exact_command}` (this capability never does it for you)",
        next_command=report.exact_command,
        reason=report.reason,
        details={"state": report.state, "attempts": [a.to_dict() for a in report.attempts]},
    )


# ------------------------------------------------------------ parser + contract


def _add_verbs(verbs: Any) -> None:
    parser = verbs.add_parser(
        "ensure",
        help="bring the container runtime up password-free, or report why it is not",
        description=(
            "Probe the docker daemon (bounded), then try the declared password-free "
            "start paths in order — systemd socket activation, then rootless podman — "
            "and report which one answered. When none does: exit 3, the measured "
            "reason and the exact command for the owner. Never a privileged start, "
            "never a container operation."
        ),
    )
    share_common_flags(parser)
    parser.add_argument(
        "--timeout-s", type=float, default=DE.DEFAULT_TIMEOUT_S,
        help=f"wall-clock bound per probe (default {DE.DEFAULT_TIMEOUT_S}, "
        f"clamped to {DE.MIN_TIMEOUT_S}…{DE.MAX_TIMEOUT_S})",
    )
    parser.add_argument(
        "--ready-s", type=float, default=DE.ACTIVATION_READY_S,
        help="how long a just-activated daemon may take to answer (default 5.0)",
    )
    parser.add_argument(
        "--no-start", action="store_true",
        help="read-only: probe the runtimes, attempt no start path",
    )
    parser.add_argument(
        "--no-fallback", action="store_true",
        help="docker only: declare no start path at all (the 'is docker up?' question)",
    )
    parser.add_argument(
        "--path", action="append", choices=list(DE.KNOWN_PATHS), default=None,
        help="a start path to try, in the order given (repeatable; default: both)",
    )
    parser.add_argument("--docker-bin", default="", help="the docker binary to probe")
    parser.add_argument("--podman-bin", default="", help="the podman binary to probe")
    parser.set_defaults(func=_cmd_ensure)


def proposed_contract_entries() -> dict[str, Any]:
    """The ``COMMANDS`` entry this group installs (identical object, for review)."""
    if _CONTRACT is None:
        return {}
    spec = _CONTRACT.CommandSpec
    return {
        _COMMAND: spec(
            "EXPAND",
            (
                {"ref": DE.EVIDENCE_REF, "kind": "code",
                 "note": "probe → socket activation → rootless podman → blocked"},
                {"ref": "src/skaldir/tools_core/docker.py", "kind": "code",
                 "note": "the same capability as the built-in agent tool docker.ensure"},
            ),
            "ensure a container runtime is up (password-free) or report why it is not",
            exit_codes=(DE.EXIT_OK, 1, DE.EXIT_BLOCKED, 7),
            mutates=False,
            data_schema={
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "exit_code": {"type": "integer"},
                    "provider": {"type": "string"},
                    "answered_by": {"type": "string"},
                    "server_version": {"type": "string"},
                    "storage_driver": {"type": "string"},
                    "reason": {"type": "string"},
                    "exact_command": {"type": "string"},
                    "attempts": {"type": "array"},
                    "commands": {"type": "array"},
                    "container_ops_attempted": {"type": "integer"},
                    "socket": {"type": "string"},
                    "probed_at": {"type": "string"},
                },
                "required": ["state", "ok", "exit_code", "provider", "server_version",
                             "storage_driver", "reason", "exact_command"],
            },
            notes=(
                "exit 3 (blocked) is the honest failure: nothing answered and every "
                "password-free path was tried; the payload's `exact_command` is the literal "
                "one-liner for the owner (this command never runs a privileged start). "
                "`mutates` is false on purpose: no file, no container and no registry is "
                "written — when the socket-activation path wakes docker.service, the "
                "privileged start is systemd's action in response to one connect, and this "
                "capability never takes it itself"
            ),
        )
    }


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Adds the ``docker`` group with its ``ensure`` verb, installs the group's
    ``cli_contract.COMMANDS`` entry (so ``unregistered`` stays empty) and installs
    this capability's ``skaldir selftest`` probe. Idempotent: a second call returns
    the existing group instead of raising an argparse conflict.
    """
    from .cli_shared import as_subparsers

    sub = as_subparsers(subparsers)
    if "docker" in getattr(sub, "choices", {}):
        return sub.choices["docker"]
    parent = sub.add_parser(
        "docker", help="the container runtime (docker/podman) the agent may start password-free"
    )
    verbs = parent.add_subparsers(dest="docker_command", required=True)
    _add_verbs(verbs)
    for parser in (parent, verbs):
        try:
            share_common_flags(parser)
        except Exception:  # noqa: BLE001 — the flags are a convenience, not the contract
            continue
    if install_contract_entries is not None and _CONTRACT is not None:
        install_contract_entries(proposed_contract_entries(), _CONTRACT)
    # One wiring line covers both surfaces: the verb above and the selftest probe.
    DE.install_selftest_probe(_CONTRACT)
    return parent


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(
        prog="skaldir docker", description="the container runtime, password-free"
    )
    verbs = parser.add_subparsers(dest="docker_command", required=True)
    _add_verbs(verbs)
    ensure = verbs.choices["ensure"]
    if _CONTRACT is not None and hasattr(_CONTRACT, "add_common_flags"):
        _CONTRACT.add_common_flags(ensure)  # idempotent: it checks existing option strings
    else:  # pragma: no cover — a build without the product CLI
        ensure.add_argument("--json", action="store_true", help="one JSON document")
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir docker ensure …`` — returns the exit code, never raises for a request.

    The standalone flags are *scoped to this call*: they are restored in ``finally``,
    so a process that ran the standalone verb can still drive the wired group (the
    product CLI's envelope path) without the two modes bleeding into each other.
    """
    global _STANDALONE, _JSON_REQUESTED
    invoked = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    previous = (_STANDALONE, _JSON_REQUESTED)
    _STANDALONE, _JSON_REQUESTED = True, "--json" in invoked
    try:
        try:
            args = parser.parse_args(invoked)
        except SystemExit as exc:
            return DE.EXIT_OK if not exc.code else 1
        func = getattr(args, "func", None)
        if func is None:  # pragma: no cover — required subparser, so argparse exits first
            parser.print_help()
            return 1
        return int(func(args))
    except DE.PrivilegedStartRefused as exc:  # pragma: no cover — the guard is upstream
        print(f"blocked: {exc}", file=sys.stderr, flush=True)
        return DE.EXIT_BLOCKED
    finally:
        _STANDALONE, _JSON_REQUESTED = previous


if __name__ == "__main__":  # `python -m skaldir.docker_ensure_cli ensure --json`
    raise SystemExit(main())


__all__ = [
    "build_parser",
    "main",
    "proposed_contract_entries",
    "register_cli",
]
