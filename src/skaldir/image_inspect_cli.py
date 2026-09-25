"""``skaldir image …`` — inspect a container image WITHOUT a shell.

Two verbs over :mod:`skaldir.image_inspect` (the shell-free half of EXPAND):

======================  ==========================================================
``image inspect``       the whole answer: metadata, the layer/binaries listing, the
                        candidate classification (``inspectable`` / ``needs-shell``
                        / ``impossible``) and — with ``--capability`` — the handoff
                        to the existing adapter generator. ``--no-exec`` keeps the
                        run completely container-free.
``image layers``        metadata + the layer walk only: which layers were read, how
                        many entries each carried, which executables, which shared
                        libraries, which shell. Never starts a container.
======================  ==========================================================

Why a shell is not needed (and why it used to be)
-------------------------------------------------
``toolbuilder.inspect_container_surface`` probes with ``sh -c "cat /etc/os-release"``
and ``sh -c "ls -1 /bin /usr/bin …"``. That is right for a full distribution image
and impossible for ``hello-world`` / a distroless image, where the probe fails with
``executable file not found in $PATH`` and the whole inspection is refused — the
image is rejected for a reason that has nothing to do with the image. This group
reads the same facts from ``docker image inspect`` (config, entrypoint/cmd, env,
labels, ports, volumes, user, architecture) and from the layer tars of
``docker save`` (executables, shared libraries, shells, ``/etc/os-release``), and
probes a binary by *execing it directly* (``docker run --rm <image> <bin> --help``)
instead of through a shell.

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok            a real classification was produced (even when every candidate
                      is ``impossible`` — that IS the answer, not a refusal)
    1   usage         no ``--image`` / argparse refused the call
    3   blocked       docker unreachable, the save stream unreadable, no pin-shaped
                      digest, the image not local
    5   not found     the image is not local (``image inspect`` says so)
    7   internal      an envelope problem

Safety (policy of MVP/05 §3): the only container this group ever starts is a
one-shot, labelled (``godsv.owner=skaldir``), ``--rm``, ``--network none``,
``--read-only``, resource-capped exec probe through
:class:`skaldir.docker_runtime.RunConfig`. Teardown is by explicit id for exactly the
containers that appeared during the run; nothing is pruned and no container that
Skaldir did not create is ever touched (the owner's 7 ``penpot-local`` containers
stay untouched — the label scope simply does not match them).

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``). Insert next to the other top-level group registration
(``build_parser``, right after the ``jev`` block) — the group owns both its verbs and
their ``cli_contract.COMMANDS`` entries::

    # Shell-free image inspection (EXPAND gap C): the group owns its two verbs and the
    # two `cli_contract.COMMANDS` entries for them — see the hand-over note in
    # src/skaldir/image_inspect_cli.py.
    from .image_inspect_cli import register_cli as _register_image_cli

    _register_image_cli(sub)

Two dotted paths must be declared in ``cli_contract.COMMANDS`` or
``tests/test_cli_contract_dump.py`` fails ("a new command cannot ship without a
contract")::

    "image.inspect": _C("EXPAND", (_ev("src/skaldir/image_inspect.py"),),
                        "shell-free image inspection + candidate classification",
                        exit_codes=(0, 1, 3, 5, 7),
                        data_schema={"type": "object", "additionalProperties": True},
                        notes="a produced classification exits 0 even when every "
                              "candidate is 'impossible' — that is the answer"),
    "image.layers": _C("EXPAND", (_ev("src/skaldir/image_inspect.py"),),
                       "layer/executable/library listing from `docker save`",
                       exit_codes=(0, 1, 3, 5, 7),
                       data_schema={"type": "object", "additionalProperties": True},
                       notes="never starts a container (metadata + layers only)"),
"""

from __future__ import annotations

import argparse
from typing import Any

from . import image_inspect as II
from .cli_shared import dump, product_contract, share_common_flags, text_mode

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")


def _report(payload: Any, *, evidence: tuple[dict, ...] = (),
            warnings: tuple[str, ...] = (), next_actions: tuple[tuple[str, str], ...] = ()) -> None:
    """Hand the payload and its evidence to the shared envelope (when wired)."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            kind = str(item.get("kind", "code"))
            _CONTRACT.add_evidence(str(item.get("ref", "")),
                                   kind if kind in _EVIDENCE_KINDS else "code",
                                   str(item.get("note", "")))
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _blocked(message: str, *, hint: str = "", next_command: str = "", reason: str = "",
             details: dict | None = None) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "blocked"):
        return int(_CONTRACT.blocked(message, hint=hint, next_command=next_command,
                                     reason=reason, details=details))
    print(f"blocked: {message}", flush=True)
    return EXIT_BLOCKED


def _not_found(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "not_found"):
        return int(_CONTRACT.not_found(message, hint=hint, details=details))
    print(f"not found: {message}", flush=True)
    return EXIT_NOT_FOUND


def _usage(message: str, *, hint: str = "") -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "usage_error"):
        return int(_CONTRACT.usage_error(message, hint=hint))
    print(f"usage: {message}", flush=True)
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# arguments → inputs                                                            #
# --------------------------------------------------------------------------- #


def _inspect_args(parser: Any) -> None:
    parser.add_argument("--image", required=True,
                        help="image reference or digest to inspect (e.g. hello-world)")
    parser.add_argument("--digest", default="",
                        help="the pinned sha256:<64 hex> digest (default: the local image id)")
    parser.add_argument("--capability", default="",
                        help="hand the inspected surface to the adapter generator for "
                             "this capability (e.g. 'hash a string with sha256')")
    parser.add_argument("--name", default="", help="tool name for the generated adapter")
    parser.add_argument("--no-exec", action="store_true",
                        help="container-free: metadata + layers only, no exec probe")
    parser.add_argument("--exec-probes", type=int, default=II.DEFAULT_EXEC_PROBES,
                        help="how many candidates get a direct `docker run` exec probe")
    parser.add_argument("--exec-timeout", type=float, default=II.DEFAULT_EXEC_TIMEOUT,
                        help="seconds per exec probe")
    parser.add_argument("--timeout", type=float, default=II.DEFAULT_TIMEOUT,
                        help="seconds for one docker call (inspect / save stream)")
    parser.add_argument("--max-save-mb", type=int,
                        default=int(II.DEFAULT_MAX_SAVE_BYTES // (1024 * 1024)),
                        help="cap for the spooled `docker save` stream in MiB")
    parser.add_argument("--spool-dir", default="",
                        help="where the temporary image tar is written (default: system temp)")
    parser.add_argument("--candidates", type=int, default=II.DEFAULT_CANDIDATE_LIMIT,
                        help="how many filesystem executables are sampled as candidates")
    parser.add_argument("--compare-shell", action="store_true",
                        help="also run the existing sh -c inspector and report what it says")


def _layers_args(parser: Any) -> None:
    parser.add_argument("--image", required=True, help="image reference or digest")
    parser.add_argument("--digest", default="", help="the pinned sha256:<64 hex> digest")
    parser.add_argument("--limit", type=int, default=12,
                        help="how many executables/libraries/directories to list")
    parser.add_argument("--timeout", type=float, default=II.DEFAULT_TIMEOUT)
    parser.add_argument("--max-save-mb", type=int,
                        default=int(II.DEFAULT_MAX_SAVE_BYTES // (1024 * 1024)))
    parser.add_argument("--spool-dir", default="")


# --------------------------------------------------------------------------- #
# verbs                                                                         #
# --------------------------------------------------------------------------- #


def _inspect_evidence(report: dict) -> tuple[dict, ...]:
    """The evidence a produced classification stands on (never empty on success)."""
    metadata = report.get("metadata") or {}
    filesystem = report.get("filesystem") or {}
    evidence = [
        {"ref": f"docker image inspect --format {{{{json .}}}} {report.get('image')}",
         "kind": "run",
         "note": f"architecture {metadata.get('architecture')}/{metadata.get('os')}, "
                 f"entrypoint {metadata.get('entrypoint') or '(none)'}, "
                 f"cmd {metadata.get('cmd') or '(none)'}"},
        {"ref": f"docker save {report.get('image_digest')} | tar (layers scanned in-process)",
         "kind": "file",
         "note": f"{filesystem.get('layer_count')} layer(s) via "
                 f"{filesystem.get('layer_source')}, "
                 f"{filesystem.get('entries_scanned')} entries, "
                 f"{len(filesystem.get('executables') or [])} executable(s), "
                 f"{len(filesystem.get('libraries') or [])} shared library(ies)"},
    ]
    for probe in report.get("probes") or []:
        if not probe.get("attempted"):
            continue
        evidence.append({
            "ref": "docker run --rm " + " ".join(probe.get("argv") or []),
            "kind": "run",
            "note": f"exit {probe.get('exit_code')} — a direct exec, no shell",
        })
    return tuple(evidence)


def cmd_inspect(args: argparse.Namespace) -> int:
    """``skaldir image inspect`` — the shell-free report + the classification."""
    image = str(getattr(args, "image", "") or "").strip()
    if not image:
        return _usage("image inspect needs --image <reference>",
                      hint="e.g. skaldir image inspect --image hello-world --json")
    report = II.inspect_image(
        image=image,
        image_digest=str(getattr(args, "digest", "") or ""),
        capability=str(getattr(args, "capability", "") or ""),
        name=str(getattr(args, "name", "") or ""),
        probe_exec=not bool(getattr(args, "no_exec", False)),
        exec_probe_limit=int(getattr(args, "exec_probes", II.DEFAULT_EXEC_PROBES)),
        exec_timeout=float(getattr(args, "exec_timeout", II.DEFAULT_EXEC_TIMEOUT)),
        timeout=float(getattr(args, "timeout", II.DEFAULT_TIMEOUT)),
        max_save_bytes=int(getattr(args, "max_save_mb", 256)) * 1024 * 1024,
        spool_dir=str(getattr(args, "spool_dir", "") or "") or None,
        candidate_limit=int(getattr(args, "candidates", II.DEFAULT_CANDIDATE_LIMIT)),
        compare_shell=bool(getattr(args, "compare_shell", False)),
    )
    if not report.get("ok"):
        if report.get("blocked") == "image_not_found":
            return _not_found(str(report.get("reason") or "the image is not local"),
                              hint="pull it first or name a local image, then retry")
        return _blocked(str(report.get("reason") or "the image could not be inspected"),
                        hint="check the docker daemon and the image reference",
                        next_command=f"skaldir image layers --image {image} --json",
                        reason="the container-free layer listing may still answer",
                        details={"blocked": report.get("blocked", ""),
                                 "image": image})
    if text_mode(args):
        print(II.render_text(report))
    else:
        dump(report)
    warnings = list(filesystem_warnings(report))
    sweep = (report.get("containers") or {}).get("sweep") or {}
    if sweep.get("remaining"):
        warnings.append(
            "cleanup_partial: container(s) started by this run are still listed ("
            + ", ".join(str(item) for item in sweep["remaining"])
            + ") — the failed teardown is reported, never hidden"
        )
    adapter = report.get("adapter") or {}
    if adapter.get("handoff") == "generated-with-warning":
        warnings.append(str(adapter.get("warning") or ""))
    _report(report, evidence=_inspect_evidence(report), warnings=tuple(w for w in warnings if w),
            next_actions=(("skaldir image layers --image " + image + " --json",
                           "the raw layer/executable listing behind this classification"),))
    return EXIT_OK


def filesystem_warnings(report: dict) -> tuple[str, ...]:
    """The layer walk's own honest caveats (a truncated or unreadable layer)."""
    filesystem = report.get("filesystem") or {}
    warnings = [str(item) for item in (filesystem.get("warnings") or []) if str(item)]
    if filesystem.get("truncated"):
        warnings.append("the layer walk hit its entry/stream cap — the listing is partial")
    if (report.get("classification") or {}).get("verdict") == "impossible":
        warnings.append("no candidate could be described: the image has no shell and no "
                        "executable ran for the requested surface")
    return tuple(warnings)


def cmd_layers(args: argparse.Namespace) -> int:
    """``skaldir image layers`` — metadata + the layer walk, never a container."""
    image = str(getattr(args, "image", "") or "").strip()
    if not image:
        return _usage("image layers needs --image <reference>",
                      hint="e.g. skaldir image layers --image alpine:latest --json")
    payload = II.layers_only(
        image=image,
        image_digest=str(getattr(args, "digest", "") or ""),
        timeout=float(getattr(args, "timeout", II.DEFAULT_TIMEOUT)),
        max_save_bytes=int(getattr(args, "max_save_mb", 256)) * 1024 * 1024,
        spool_dir=str(getattr(args, "spool_dir", "") or "") or None,
        excerpt_limit=int(getattr(args, "limit", 12)),
    )
    if not payload.get("ok"):
        if payload.get("blocked") == "image_not_found":
            return _not_found(str(payload.get("reason") or "the image is not local"))
        return _blocked(str(payload.get("reason") or "the layer listing could not be read"),
                        hint="check the docker daemon and the image reference",
                        details={"blocked": payload.get("blocked", ""), "image": image})
    filesystem = payload.get("filesystem") or {}
    if text_mode(args):
        print(f"image {payload.get('image')}  digest {payload.get('image_digest')}")
        print(f"  layers: {filesystem.get('layer_count')} via "
              f"{filesystem.get('layer_source')} · entries "
              f"{filesystem.get('entries_scanned')} · container started: "
              f"{payload.get('container_started')}")
        for item in filesystem.get("layers") or []:
            print(f"    {item.get('status'):>10} {item.get('entries')} entries · "
                  f"{item.get('executables')} executables · {item.get('libraries')} libraries")
        print(f"  executables ({filesystem.get('executables_total')}): "
              f"{', '.join(filesystem.get('executables') or [])}")
        print(f"  libraries ({filesystem.get('libraries_total')}): "
              f"{', '.join(filesystem.get('libraries') or [])}")
        print(f"  shells: {', '.join((payload.get('shell') or {}).get('paths') or []) or '(none)'}")
        print(f"  classification: {(payload.get('classification') or {}).get('verdict')} "
              f"{(payload.get('classification') or {}).get('counts')}")
    else:
        dump(payload)
    _report(payload, evidence=(
        {"ref": f"docker save {payload.get('image_digest')} | tar (layers read in-process)",
         "kind": "file",
         "note": f"{filesystem.get('layer_count')} layer(s) via "
                 f"{filesystem.get('layer_source')}, "
                 f"{filesystem.get('entries_scanned')} entries scanned, "
                 f"{filesystem.get('executables_total')} executable(s)"},
        {"ref": f"docker image inspect --format {{{{json .}}}} {payload.get('image')}",
         "kind": "run",
         "note": "metadata read from the daemon, no container and no shell"},
    ), warnings=tuple(filesystem.get("warnings") or ()))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser + entry points                                                         #
# --------------------------------------------------------------------------- #


def _add_text_flag(parser: Any) -> None:
    """``--text`` asks for the human rendering (the product adds ``--format`` itself)."""
    parser.add_argument("--text", action="store_true",
                        help="human-readable output instead of the JSON report")


def _add_image_verbs(sub: Any) -> Any:
    """Add the two ``image`` verbs to a subparsers handle (one definition)."""
    p_inspect = sub.add_parser(
        "inspect",
        help="shell-free inspection + candidate classification (inspectable/needs-shell/"
             "impossible)",
    )
    _inspect_args(p_inspect)
    _add_text_flag(p_inspect)
    p_inspect.set_defaults(func=cmd_inspect, command_path="image.inspect")

    p_layers = sub.add_parser("layers", help="layer/executable/library listing (no container)")
    _layers_args(p_layers)
    _add_text_flag(p_layers)
    p_layers.set_defaults(func=cmd_layers, command_path="image.layers")

    for parser in (p_inspect, p_layers):
        share_common_flags(parser)
    return sub


def build_image_parser(subparsers: Any) -> Any:
    """Add the ``image`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "image",
        help="inspect container images without a shell (works for distroless images)",
        description=(
            "Read what an image is — config, entrypoint/cmd, env, labels, ports, user, "
            "architecture — from `docker image inspect`, enumerate its filesystem "
            "(executables, shared libraries, shells, /etc/os-release) from the layer tars "
            "of `docker save`, probe a binary by execing it directly, and classify every "
            "candidate as inspectable / needs-shell / impossible. No `sh -c` is required "
            "anywhere, which is why an image without a shell (hello-world, distroless) is "
            "described instead of refused."
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
                                     description="shell-free image inspection")
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
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "build_image_parser",
    "build_parser",
    "cmd_inspect",
    "cmd_layers",
    "filesystem_warnings",
    "main",
    "register_cli",
]


if __name__ == "__main__":  # pragma: no cover — a standalone run, not a product path
    raise SystemExit(main())
