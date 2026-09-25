"""``skaldir image …`` — registry manifest digests: resolve, pin, verify, guard.

Five verbs over :mod:`skaldir.image_pin` (Gap D of the EXPAND layer):

======================  ==========================================================
``image resolve REF``   what the **registry** says the reference resolves to (index
                        digest + the per-platform manifest digest). Read-only, no
                        pull. ``--offline`` reads the digest the daemon recorded for
                        an already-pulled image and says plainly that it is not
                        registry-verified.
``image pin REF``       resolve, then record the digest (atomic store, one file). A
                        different digest for an already-pinned reference needs
                        ``--force``: a re-pin is a decision, never a side effect of a
                        moved tag.
``image verify REF``    re-verify a recorded pin: the local image must still carry the
                        pinned digest (``--remote`` additionally re-checks the tag
                        against the registry). A digest that no longer matches is
                        **refused** — exit 6.
``image guard``         the same check at the three surfaces a *running* capability
                        meets: ``--surface health`` (exit 2 when it fails),
                        ``--surface invoke`` (exit 6), ``--surface rollback`` to a
                        ``--version`` (exit 3). Every guard carries the digest into
                        its evidence, so a health row, an invocation record and a
                        rollback can all name the exact artifact.
``image list``          the recorded pins.
======================  ==========================================================

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok                  resolved / pinned / verified
    1   usage               argparse refused the call, or no reference given
    2   verification failed image guard --surface health: the digest changed
    3   blocked             no network, the Docker daemon is unreachable, or a
                            rollback target cannot be certified
    4   needs credentials   the registry refused the manifest request
    5   not found           no pin recorded, the image was never pulled, or the
                            registry has no such manifest
    6   conflict            a digest mismatch, a moved tag, or an attempted re-pin
    7   internal            an unreadable registry answer or a broken pin store

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``)::

    from .image_pin_cli import register_cli
    register_cli(sub)

The ``image`` group is shared with the shell-free inspector (``image inspect`` /
``image layers``, ``src/skaldir/image_inspect_cli.py``) — two ``add_parser("image", …)``
calls collide. Exactly one module creates the group and the other attaches its verbs::

    from .image_inspect_cli import register_cli as _register_image_cli
    _image_group = _register_image_cli(sub)      # the group is created once
    from .image_pin_cli import attach as _attach_image_pin
    _attach_image_pin(_image_group)              # the digest-pin verbs join it

(or the other way round: :func:`register_cli` returns the group parser, which the other
module can extend).

Nothing here pulls, starts or removes a container: it only asks the daemon and the
registry *what* an artifact is, and refuses to run something that changed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import image_pin as P
from .cli_shared import (
    dump,
    envelope_mode,
    product_contract,
    runtime_dir,
    share_common_flags,
    text_mode,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NEEDS_CREDENTIALS = int(getattr(_CONTRACT, "EXIT_NEEDS_CREDENTIALS", 4))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

#: The evidence kinds the contract accepts (anything else becomes ``code``).
_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")


# --------------------------------------------------------------------------- #
# envelope + shared plumbing                                                    #
# --------------------------------------------------------------------------- #


def _report(
    payload: Any,
    *,
    evidence: Iterable[dict] = (),
    warnings: Iterable[str] = (),
    next_actions: Iterable[tuple[str, str]] = (),
) -> None:
    """Hand the payload and its evidence to the shared envelope (when wired)."""
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


def _fail_with(code: int, message: str, *, hint: str = "", details: dict | None = None,
               next_command: str = "", reason: str = "") -> int:
    """Exit through the shared helper that matches ``code``, else by hand."""
    if _CONTRACT is not None and hasattr(_CONTRACT, "fail"):
        try:
            if code == EXIT_BLOCKED and hasattr(_CONTRACT, "blocked"):
                return int(_CONTRACT.blocked(message, hint=hint, next_command=next_command,
                                             reason=reason or message, details=details))
            if code == EXIT_NEEDS_CREDENTIALS and hasattr(_CONTRACT, "needs_credentials"):
                return int(_CONTRACT.needs_credentials(message, hint=hint, details=details))
            if code == EXIT_NOT_FOUND and hasattr(_CONTRACT, "not_found"):
                return int(_CONTRACT.not_found(message, hint=hint, details=details))
            if code == 2 and hasattr(_CONTRACT, "verification_failed"):
                return int(_CONTRACT.verification_failed(message, hint=hint, details=details))
            return int(_CONTRACT.fail(code, message, hint=hint, details=details))
        except Exception:  # noqa: BLE001 — a contract problem must not hide the verdict
            return int(code)
    return int(code)


def _fail_for_state(state: str, message: str, fix: str = "", details: dict | None = None,
                    next_command: str = "") -> int:
    """Exit with the code the *state* owns (the state table is the only mapping)."""
    code = int(P.exit_code_for(state))
    return _fail_with(code, message, hint=fix, details=details,
                      next_command=next_command, reason=message)


def _emit(args: argparse.Namespace, payload: dict, *, warn: str = "") -> None:
    """Print: human text in ``--text``; the payload only when no envelope is active."""
    if warn:
        print(warn, file=sys.stderr)
    if text_mode(args):
        _print_text(args, payload)
        return
    if not envelope_mode():
        dump(payload)


def _print_text(args: argparse.Namespace, payload: dict) -> None:
    """The human view of each verb (machine-first: text only on explicit request)."""
    command = str(getattr(args, "command_path", "") or "")
    if command == "image.list":
        rows = payload.get("pins") or []
        if not rows:
            print(f"no pins recorded in {payload.get('pin_store')}")
        for row in rows:
            print(f"{row.get('digest')}  {row.get('digest_kind'):<17} "
                  f"{row.get('reference')}  -> {row.get('artifact')}")
        return
    guard = payload.get("guard")
    if guard:
        print(f"{guard.get('state').upper():<14} {guard.get('label')} "
              f"{guard.get('capability') or ''} {guard.get('version') or ''}".rstrip())
        print(guard.get("message", ""))
        if guard.get("fix"):
            print(f"fix: {guard['fix']}")
        return
    resolution = payload.get("resolution") or payload.get("resolution_data")
    if resolution:
        print(f"{resolution.get('state').upper():<14} {resolution.get('reference')}")
        print(resolution.get("message", ""))
        if resolution.get("fix"):
            print(f"fix: {resolution['fix']}")
        print(f"digest: {resolution.get('digest')} "
              f"({resolution.get('digest_kind')}, {resolution.get('platform')})")
        if resolution.get("platform_digest"):
            print(f"platform manifest: {resolution['platform_digest']}")
        return
    verification = payload.get("verification")
    if verification:
        print(f"{verification.get('state').upper():<14} {verification.get('reference')}")
        print(verification.get("message", ""))
        if verification.get("fix"):
            print(f"fix: {verification['fix']}")
        print(f"artifact: {verification.get('artifact') or '(unpinned)'}")
        return
    dump(payload)


# --------------------------------------------------------------------------- #
# arguments → inputs                                                            #
# --------------------------------------------------------------------------- #


def _store(args: argparse.Namespace) -> P.PinStore:
    """The pin store: ``--state`` (a directory or a file) or ``<runtime>/image-pins.json``."""
    explicit = str(getattr(args, "state", "") or "")
    if not explicit:
        return P.PinStore.default(runtime_dir(args))
    path = Path(explicit).expanduser()
    if path.suffix == ".json":
        return P.PinStore(path)
    return P.PinStore(path / P.PIN_FILENAME)


def runner_for(args: argparse.Namespace) -> Any:
    """The docker seam of one call — a test replaces this function, not the module."""
    binary = str(getattr(args, "docker_bin", "") or "").strip()
    return P.DockerCliRunner(binary or None)


def _timeout(args: argparse.Namespace) -> float:
    value = getattr(args, "timeout", None)
    return float(value) if value else float(P.DEFAULT_TIMEOUT)


def _reference(args: argparse.Namespace) -> str:
    return str(getattr(args, "reference", "") or "").strip()


def _registry(args: argparse.Namespace) -> str:
    """``--registry``: ask a pull-through mirror for the same docker.io reference."""
    return str(getattr(args, "registry", "") or "").strip()


def _evidence_for_resolution(resolution: P.DigestResolution) -> list[dict]:
    """One evidence item per resolution — it names the artifact or the refusal."""
    ref = resolution.artifact or resolution.reference
    note = resolution.message or f"state {resolution.state}"
    if resolution.raw:
        note = f"{note} | raw: {resolution.raw.splitlines()[0][:160]}"
    return [{"ref": ref, "kind": "run", "note": note}]


def _resolution_payload(resolution: P.DigestResolution) -> dict:
    return {"resolution": resolution.to_dict(), "state": resolution.state,
            "ok": resolution.ok, "exit_code": resolution.exit_code,
            "artifact": resolution.artifact}


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_resolve(args: argparse.Namespace) -> int:
    """What the registry (or, offline, the daemon's pull record) says a reference is."""
    reference = _reference(args)
    if not reference:
        return _fail_with(EXIT_USAGE, "no image reference given",
                          hint="skaldir image resolve busybox:1.36")
    offline = bool(getattr(args, "offline", False))
    if offline:
        resolution = P.offline_digest(
            reference, platform=str(getattr(args, "platform", "") or ""),
            timeout=_timeout(args), runner=runner_for(args),
        )
    else:
        resolution = P.resolve_registry_digest(
            reference, platform=str(getattr(args, "platform", "") or ""),
            timeout=_timeout(args), runner=runner_for(args), registry=_registry(args),
        )
    payload = _resolution_payload(resolution)
    payload["offline"] = offline
    payload["pin_store"] = str(_store(args).path)
    warnings = [resolution.fix] if resolution.fix and not resolution.ok else []
    if resolution.state == P.STATE_OFFLINE_UNVERIFIED:
        warnings.append(resolution.fix)
    if resolution.details.get("registry_warning"):
        warnings.append(str(resolution.details["registry_warning"]))
    _report(payload, evidence=_evidence_for_resolution(resolution), warnings=warnings)
    _emit(args, payload, warn="" if resolution.ok else resolution.message)
    if resolution.ok:
        return EXIT_OK
    return _fail_for_state(resolution.state, resolution.message, resolution.fix,
                           details=dict(resolution.details))


def cmd_pin(args: argparse.Namespace) -> int:
    """Resolve the registry digest and record it — a re-pin needs ``--force``."""
    reference = _reference(args)
    if not reference:
        return _fail_with(EXIT_USAGE, "no image reference given",
                          hint="skaldir image pin busybox:1.36")
    store = _store(args)
    capability = str(getattr(args, "capability", "") or "")
    version = str(getattr(args, "version", "") or "")
    pin, resolution = P.record_pin(
        store, reference,
        platform=str(getattr(args, "platform", "") or ""),
        capability=capability, version=version,
        replace=bool(getattr(args, "force", False)),
        offline=bool(getattr(args, "offline", False)),
        timeout=_timeout(args), runner=runner_for(args),
        note=str(getattr(args, "note", "") or ""),
        registry=_registry(args),
    )
    payload = _resolution_payload(resolution)
    payload["pin"] = pin.to_dict() if pin is not None else None
    payload["pinned"] = pin is not None
    payload["pin_store"] = str(store.path)
    if pin is not None:
        payload["registration_evidence"] = P.registration_evidence(
            store, reference, capability=capability
        )
    evidence: list[dict] = _evidence_for_resolution(resolution)
    if pin is not None:
        evidence.append({
            "ref": pin.artifact, "kind": "verification",
            "note": f"recorded {pin.digest_kind} pin for {pin.reference} "
                    f"(source {pin.source}, registry_verified={pin.registry_verified}, "
                    f"local_verified={pin.local_verified}) -> {store.path}",
        })
    next_actions: list[tuple[str, str]] = []
    if pin is None:
        next_actions.append(("skaldir image resolve " + reference,
                             "look at what the registry answers before pinning"))
    else:
        next_actions.append((f"skaldir image verify {reference}",
                             "re-verify the recorded pin against the local image"))
        next_actions.append((f"skaldir image guard --surface invoke {reference}",
                             "the check every invocation runs"))
    _report(payload, evidence=evidence, warnings=([resolution.fix] if resolution.fix else []),
            next_actions=next_actions)
    _emit(args, payload, warn="" if pin is not None else resolution.message)
    if pin is None:
        return _fail_for_state(resolution.state, resolution.message, resolution.fix,
                               details=dict(resolution.details))
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-verify a recorded pin: local digest first, the registry on ``--remote``."""
    reference = _reference(args)
    if not reference:
        return _fail_with(EXIT_USAGE, "no image reference given",
                          hint="skaldir image verify busybox:1.36")
    store = _store(args)
    result = P.verify_pin(
        store, reference, remote=bool(getattr(args, "remote", False)),
        timeout=_timeout(args), runner=runner_for(args),
        capability=str(getattr(args, "capability", "") or ""),
        registry=_registry(args),
    )
    payload = {
        "verification": result.to_dict(),
        "state": result.state,
        "ok": result.ok,
        "exit_code": result.exit_code,
        "artifact": result.artifact,
        "pin_store": str(store.path),
    }
    evidence = [{"ref": result.artifact or result.reference, "kind": "verification",
                 "note": f"verify -> {result.state}: {result.message}"}]
    next_actions = list(result.next_actions)
    _report(payload, evidence=evidence, warnings=result.warnings, next_actions=next_actions)
    _emit(args, payload, warn="" if result.ok else result.message)
    if result.ok:
        return EXIT_OK
    return _fail_for_state(result.state, result.message, result.fix,
                           details=dict(result.details))


def cmd_guard(args: argparse.Namespace) -> int:
    """The digest re-verification of one surface: health, invoke or rollback."""
    reference = _reference(args)
    surface = str(getattr(args, "surface", "") or "").strip()
    if not reference:
        return _fail_with(EXIT_USAGE, "no image reference given",
                          hint=f"skaldir image guard --surface {surface or 'invoke'} <ref>")
    store = _store(args)
    guard = P.guard_digest(
        store, reference, surface=surface,
        capability=str(getattr(args, "capability", "") or ""),
        version=str(getattr(args, "version", "") or ""),
        remote=bool(getattr(args, "remote", False)),
        timeout=_timeout(args), runner=runner_for(args),
        registry=_registry(args),
    )
    payload = {
        "guard": guard.to_dict(),
        "state": guard.state,
        "ok": guard.ok,
        "exit_code": guard.exit_code,
        "surface": guard.surface,
        "artifact": guard.artifact,
        "pin_store": str(store.path),
    }
    evidence_item = guard.evidence()
    evidence = [{"ref": guard.artifact or reference, "kind": "verification",
                 "note": f"guard[{guard.surface}] -> {guard.state} "
                         f"{'OK' if guard.ok else 'REFUSED'}: {guard.message}"}]
    next_actions: list[tuple[str, str]] = []
    if not guard.ok:
        next_actions.append((
            f"skaldir image pin {reference} --force",
            "re-pin deliberately once the change is understood",
        ))
    _report(payload, evidence=evidence, warnings=guard.warnings, next_actions=next_actions)
    _emit(args, payload, warn="" if guard.ok else guard.message)
    if guard.ok:
        return EXIT_OK
    return _fail_with(guard.exit_code, guard.message, hint=guard.fix,
                      details={**dict(guard.details), "evidence": evidence_item})


def cmd_list(args: argparse.Namespace) -> int:
    """The recorded pins (the store, or an empty store with its path)."""
    store = _store(args)
    try:
        pins = store.all()
    except P.PinStoreError as exc:
        payload = {"pins": [], "count": 0, "pin_store": str(store.path), "error": str(exc)}
        _report(payload, evidence=[{"ref": str(store.path), "kind": "file",
                                    "note": f"pin store unreadable: {exc}"}],
                warnings=(str(exc),))
        _emit(args, payload, warn=str(exc))
        return _fail_with(EXIT_INTERNAL, str(exc),
                          hint="repair or remove the pin store, then pin again")
    payload = {
        "pins": [pin.to_dict() for pin in pins],
        "count": len(pins),
        "pin_store": str(store.path),
        "exists": store.exists(),
    }
    _report(payload, evidence=[{"ref": str(store.path), "kind": "file",
                                "note": f"{len(pins)} pin(s) recorded"}])
    _emit(args, payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# the parser                                                                    #
# --------------------------------------------------------------------------- #


def add_verbs(sub: Any) -> Any:
    """Add the five digest-pin verbs to an **existing** ``image`` group's subparsers.

    Needed because two modules own parts of the same ``skaldir image`` group: this
    one (the digest pin) and the shell-free inspector (``image inspect`` / ``image
    layers``). Two ``add_parser("image", …)`` calls would collide ("conflicting
    subparser: image"), so exactly one module creates the group and the other attaches
    its verbs here.
    """
    return _add_image_verbs(sub)


def attach(group_parser: Any) -> Any:
    """Attach the digest-pin verbs to an ``image`` group another module created.

    ``group_parser`` is the parser returned by the other module's ``register_cli`` (or
    by :func:`build_image_parser`); the verbs land on its command subparsers, so both
    sets of verbs live in one group with their own dotted ``command_path``.
    """
    for action in group_parser._actions:  # noqa: SLF001 — argparse has no public API
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return _add_image_verbs(action)
    raise ValueError(
        "the image group has no command subparsers to attach to — call register_cli "
        "first, or build the group with build_image_parser()"
    )


def _local_args(parser: Any) -> None:
    """The flags every verb shares: where the pins live and which docker to drive."""
    parser.add_argument("--state", default="",
                        help="pin store: a directory or a .json file "
                             "(default: <runtime>/image-pins.json)")
    parser.add_argument("--runtime", default="", help="runtime dir (default: $SKALDIR_HOME)")
    parser.add_argument("--timeout", type=float, default=0.0,
                        help=f"per-call timeout in seconds (default {P.DEFAULT_TIMEOUT:g})")
    parser.add_argument("--docker-bin", default="",
                        help="the docker binary to drive (default: the one on PATH)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")


def _platform_args(parser: Any) -> None:
    parser.add_argument("--platform", default="",
                        help=f"OCI platform to pin (default: the host, {P.host_platform()})")


def _registry_arg(parser: Any) -> None:
    """``--registry HOST``: ask a pull-through mirror instead of docker.io.

    Measured need: Docker Hub rate-limits anonymous manifest reads (429) on this
    machine; a mirror answers the same reference with the same digest.
    """
    parser.add_argument("--registry", default="",
                        help="resolve a Hub reference via this pull-through mirror "
                             "(e.g. a regional mirror host) instead of the Hub")


def _add_image_verbs(sub: Any) -> Any:
    """Add the five ``image`` verbs to a subparsers handle (one definition)."""
    p_resolve = sub.add_parser(
        "resolve", help="what the registry says a reference resolves to (read-only)")
    p_resolve.add_argument("reference", help="image reference, e.g. busybox:1.36")
    _platform_args(p_resolve)
    _registry_arg(p_resolve)
    p_resolve.add_argument("--offline", action="store_true",
                           help="do not touch the network: use the digest the daemon "
                                "recorded for the pulled image (marked unverified)")
    _local_args(p_resolve)
    p_resolve.set_defaults(func=cmd_resolve, command_path="image.resolve")

    p_pin = sub.add_parser("pin", help="resolve the registry digest and record it")
    p_pin.add_argument("reference", help="image reference, e.g. busybox:1.36")
    _platform_args(p_pin)
    _registry_arg(p_pin)
    p_pin.add_argument("--capability", default="",
                       help="the capability this artifact belongs to")
    p_pin.add_argument("--version", default="",
                       help="the capability version this digest is registered as")
    p_pin.add_argument("--force", action="store_true",
                       help="replace a pin that records a different digest (a decision)")
    p_pin.add_argument("--offline", action="store_true",
                       help="record the digest the daemon recorded for the pulled image "
                            "when the registry cannot be reached (marked unverified)")
    p_pin.add_argument("--note", default="", help="a short human note for the pin")
    _local_args(p_pin)
    p_pin.set_defaults(func=cmd_pin, command_path="image.pin")

    p_verify = sub.add_parser("verify", help="re-verify a recorded pin (local, or --remote)")
    p_verify.add_argument("reference")
    p_verify.add_argument("--capability", default="",
                          help="the capability that must be bound to this pin")
    p_verify.add_argument("--version", default="",
                          help="the capability version the pin must cover")
    p_verify.add_argument("--remote", action="store_true",
                          help="also re-resolve the tag against the registry (needs network)")
    _registry_arg(p_verify)
    _local_args(p_verify)
    p_verify.set_defaults(func=cmd_verify, command_path="image.verify")

    p_guard = sub.add_parser(
        "guard", help="re-verify the digest at health, invocation or rollback")
    p_guard.add_argument("reference")
    p_guard.add_argument("--surface", required=True, choices=list(P.GUARD_SURFACES),
                         help="health (exit 2) | invoke (exit 6) | rollback (exit 3)")
    p_guard.add_argument("--capability", default="", help="the capability being guarded")
    p_guard.add_argument("--version", default="",
                         help="the version being invoked or rolled back to")
    p_guard.add_argument("--remote", action="store_true",
                         help="also re-resolve the tag against the registry")
    _registry_arg(p_guard)
    _local_args(p_guard)
    p_guard.set_defaults(func=cmd_guard, command_path="image.guard")

    p_list = sub.add_parser("list", help="the recorded registry digest pins")
    _local_args(p_list)
    p_list.set_defaults(func=cmd_list, command_path="image.list")

    for parser in (p_resolve, p_pin, p_verify, p_guard, p_list):
        share_common_flags(parser)
    return sub


def build_image_parser(subparsers: Any) -> Any:
    """Add the ``image`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "image",
        help="registry manifest digests (resolve / pin / verify / guard / list)",
        description=(
            "A container tool is only reproducible if it is bound to the digest the "
            "registry serves, not to a local image id. `image` resolves that digest, "
            "records it as a pin, re-verifies it against the local image (and, with "
            "--remote, against the registry) and refuses to run a capability whose "
            "digest changed: resolve and verify are read-only, `pin` writes the pin "
            "store, and `guard` is the check health, every invocation and every "
            "rollback must run."
        ),
    )
    sub = parser.add_subparsers(dest="image_command", required=True)
    _add_image_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    return build_image_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir image",
                                     description="registry manifest digest pinning")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_image_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir image …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "add_verbs",
    "attach",
    "build_image_parser",
    "build_parser",
    "cmd_guard",
    "cmd_list",
    "cmd_pin",
    "cmd_resolve",
    "cmd_verify",
    "main",
    "register_cli",
    "runner_for",
]
