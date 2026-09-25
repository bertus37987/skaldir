"""``skaldir context …`` — what project context would be injected, and why.

Three verbs over :mod:`skaldir.project_context`:

============================  ==============================================================
``context show``              the block **verbatim** — the exact text a run would inject,
                              including the provenance header with its fingerprint. The block
                              goes to stdout alone, so ``skaldir context show > context.md``
                              is a clean paste; the provenance footer goes to stderr.
``context explain``           the whole resolution trail: the ladder that was applied, every
                              visited level, which file won which entry (and what the loser
                              said), the budget plan, and every refusal with its reason.
``context fingerprint``       just the digests — the block fingerprint, the per-file sha256 and
                              the source list — for a run record or an evidence compare.
============================  ==============================================================

``--json`` (or ``--format json``) prints one JSON document on every verb. Without it the human
view is printed; nothing is JSON-only.

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok                 the block was built (possibly empty, when a file was found)
    1   usage              argparse refused the call, or ``--max-chars`` was not a number
    3   blocked            the start directory cannot be walked (missing, or not a directory)
    5   not found          no instruction file was found — and, unless ``--allow-empty`` was
                           given, that is a finding: an empty context must not look like a
                           success (``--allow-empty`` turns it into exit 0 with an empty block)
    7   internal           the context could not be built (the reason is printed)

The group is added by the product CLI through one call (the CLI owner wires it; this module
never edits ``cli.py``)::

    from .project_context_cli import register_cli
    register_cli(sub)

Nothing here writes: every file is opened read-only, symlinks that leave the workspace are
refused instead of followed, and the fingerprint carried in the output is the evidence that a
run can prove *which* context it used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import project_context as P
from .cli_shared import (
    blocked,
    dump,
    envelope_mode,
    install_contract_entries,
    not_found,
    product_contract,
    share_common_flags,
    text_mode,
)

GROUP_HELP = "project context files (AGENTS.md / SOUL.md / SKALDIR.md / CLAUDE.md)"

SHOW_HELP = "print the exact block that would be injected (stdout), provenance to stderr"
EXPLAIN_HELP = "show what would be injected and why: precedence, budget, refusals, overrides"
FINGERPRINT_HELP = "print the block fingerprint and the per-file digests (no block)"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_NOT_FOUND = 5
EXIT_INTERNAL = 7

_CONTRACT = product_contract()

#: The three ``COMMANDS`` entries, installed when the group is registered (idempotent). The
#: CLI's ``cli_contract`` keeps its single ownership; nothing is pasted by hand.
CONTRACT_ENTRIES: dict[str, Any] = {}
CONTRACT_EVIDENCE: dict[str, tuple[dict, ...]] = {
    "context.show": (
        {
            "ref": "src/skaldir/project_context.py",
            "kind": "code",
            "note": "discovery ladder + precedence ladder + the typed block",
        },
        {
            "ref": "AGENTS.md / SOUL.md / SKALDIR.md / CLAUDE.md",
            "kind": "file",
            "note": "the files this command reads (read-only) — .skaldir/context.md too",
        },
    ),
    "context.explain": (
        {
            "ref": "src/skaldir/project_context.py:explain",
            "kind": "code",
            "note": "the ladder, the budget plan, the per-entry win/lose trail",
        },
    ),
    "context.fingerprint": (
        {
            "ref": "sha256",
            "kind": "file",
            "note": "block fingerprint + per-file digests, recordable as run evidence",
        },
    ),
}


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entries for this group (built once, installed on wired-in).

    A new ``skaldir`` command cannot ship without a contract entry
    (``tests/test_cli_contract_dump.py``), so the group carries its own — the CLI's module keeps
    its single ownership and nothing has to be pasted by hand.
    """
    global CONTRACT_ENTRIES
    if CONTRACT_ENTRIES:
        return CONTRACT_ENTRIES
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    spec = _CONTRACT.CommandSpec
    CONTRACT_ENTRIES = {
        "context.show": spec(
            "ENFORCE",
            CONTRACT_EVIDENCE["context.show"],
            SHOW_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "block": {"type": "string"},
                    "fingerprint": {"type": "string"},
                    "fingerprint_short": {"type": "string"},
                    "chars": {"type": "integer"},
                    "max_chars": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                    "empty": {"type": "boolean"},
                    "sources": {"type": "array"},
                    "refused": {"type": "array"},
                    "warnings": {"type": "array"},
                },
                "required": ["block", "fingerprint", "chars", "sources"],
            },
            notes=(
                "read-only: the files are inputs, nothing is written; exit 5 when no "
                "instruction file was found and --allow-empty was not given, because an empty "
                "context is a finding — the block always carries its fingerprint"
            ),
        ),
        "context.explain": spec(
            "ENFORCE",
            CONTRACT_EVIDENCE["context.explain"],
            EXPLAIN_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "ladder": {"type": "object"},
                    "budget": {"type": "object"},
                    "levels": {"type": "array"},
                    "sources": {"type": "array"},
                    "refused": {"type": "array"},
                    "overrides": {"type": "array"},
                    "resolutions": {"type": "array"},
                    "warnings": {"type": "array"},
                },
                "required": ["ladder", "budget", "sources", "warnings"],
            },
            notes="the same build as context.show plus the why: refusals, overrides, truncation",
        ),
        "context.fingerprint": spec(
            "ENFORCE",
            CONTRACT_EVIDENCE["context.fingerprint"],
            FINGERPRINT_HELP,
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_NOT_FOUND, EXIT_INTERNAL),
            data_schema={
                "type": "object",
                "properties": {
                    "fingerprint": {"type": "string"},
                    "fingerprint_short": {"type": "string"},
                    "scheme": {"type": "string"},
                    "chars": {"type": "integer"},
                    "sources": {"type": "array"},
                },
                "required": ["fingerprint", "fingerprint_short", "sources"],
            },
            notes="stable for the same workspace content — record it in a run's evidence",
        ),
    }
    return CONTRACT_ENTRIES


def register_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added.

    The contract is re-resolved here (the group may be registered after the CLI module was
    imported) and, when it is there, it *replaces* the handle this module holds — a resolved
    contract is never downgraded back to ``None``.
    """
    global _CONTRACT
    resolved = product_contract()
    if resolved is not None:
        _CONTRACT = resolved
    return install_contract_entries(contract_entries(), _CONTRACT)


# --------------------------------------------------------------------------- #
# the verbs
# --------------------------------------------------------------------------- #


def _common_args(parser: argparse.ArgumentParser) -> None:
    """The three arguments every verb takes: where to look, what boundary, how much."""
    parser.add_argument(
        "--dir",
        dest="start",
        default="",
        help="start directory of the walk up (default: the current working directory)",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="boundary the walk may not leave (default: the git root, else the filesystem root)",
    )
    parser.add_argument(
        "--max-chars",
        dest="max_chars",
        type=int,
        default=P.DEFAULT_MAX_CHARS,
        help=f"total character budget of the injected block (default {P.DEFAULT_MAX_CHARS})",
    )


def _share_flags(parser: argparse.ArgumentParser) -> None:
    """The product CLI's shared flags plus ``--json`` for the standalone parser."""
    share_common_flags(parser)
    if not any("--json" in action.option_strings for action in parser._actions):
        parser.add_argument("--json", action="store_true", help="print one JSON document")


def _build_for(args: argparse.Namespace) -> tuple[P.ProjectContext | None, int]:
    """Build the context for this call, or return the exit code that refusal deserves."""
    start = str(getattr(args, "start", "") or "") or None
    if start:
        candidate = Path(start).expanduser()
        if not candidate.is_dir():
            return None, not_found(
                f"no directory at {candidate}",
                hint="context show --dir <a directory>",
                details={"path": str(candidate)},
            )
    max_chars = int(getattr(args, "max_chars", P.DEFAULT_MAX_CHARS) or 0)
    try:
        return (
            P.build(
                start,
                workspace=str(getattr(args, "workspace", "") or "") or None,
                max_chars=max_chars,
            ),
            EXIT_OK,
        )
    except OSError as exc:
        return None, blocked(
            f"the context could not be read: {type(exc).__name__}: {exc}",
            hint="check the --dir and --workspace paths",
            reason="unreadable-context",
        )


def _report(payload: dict, context: P.ProjectContext) -> None:
    """Hand the payload and the context's evidence to the shared envelope (when wired)."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "set_data"):
        return
    try:
        _CONTRACT.set_data(payload)
        evidence = context.evidence()
        for source in evidence["sources"]:
            _CONTRACT.add_evidence(
                f"{source['path']} @ {source['sha256'][:12]}", "file", f"level {source['level']}"
            )
        _CONTRACT.add_evidence(
            f"fingerprint {evidence['fingerprint_short']}", "file", P.SCHEMA
        )
        for warning in context.warnings:
            _CONTRACT.warn(str(warning))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _emit_json(args: argparse.Namespace, payload: dict) -> None:
    """Print the payload when we own stdout; the envelope prints it otherwise."""
    if not envelope_mode():
        dump(payload)


def _print_footer(context: P.ProjectContext) -> None:
    """Provenance and warnings to stderr — the block on stdout stays pasteable."""
    evidence = context.evidence()
    print(
        f"# skaldir context · {evidence['schema']} · fingerprint {evidence['fingerprint']}",
        file=sys.stderr,
    )
    print(
        f"# sections {context.budgeted_chars} chars of {evidence['max_chars']} · "
        f"block {evidence['chars']} chars · {len(evidence['sources'])} source(s) · "
        f"{evidence['workspace']} ({context.workspace_reason})",
        file=sys.stderr,
    )
    for source in evidence["sources"]:
        print(
            f"#   {source['sha256'][:12]}  level {source['level']}  {source['path']}",
            file=sys.stderr,
        )
    for item in evidence["refused"]:
        print(f"#   refused: {item['path']} — {item['status']} ({item['reason']})",
              file=sys.stderr)
    for warning in context.warnings:
        print(f"# warning: {warning}", file=sys.stderr)


def _refuse_empty(context: P.ProjectContext, what: str) -> int:
    """Exit 5 with one line on stderr — an empty context must not look like a success.

    Called *after* the payload was emitted, so a machine caller still gets the (empty) block,
    its fingerprint and the warning; only the exit code says "this is a finding".
    """
    print(f"# skaldir context: no project context file found — {what}", file=sys.stderr)
    return not_found(
        f"no project context file found — {what}",
        hint=(
            "create AGENTS.md / SOUL.md / SKALDIR.md in the project root, "
            "or pass --allow-empty to accept an empty block"
        ),
        details={
            "start_dir": str(context.start_dir),
            "workspace": str(context.workspace),
            "checked": list(P.FILENAME_LADDER),
        },
    )


def cmd_show(args: argparse.Namespace) -> int:
    """The block verbatim on stdout, its provenance on stderr."""
    context, code = _build_for(args)
    if context is None:
        return code
    payload = context.to_dict()
    payload["evidence"] = context.evidence()
    _report(payload, context)
    if text_mode(args):
        print(context.block, end="")
        _print_footer(context)
    else:
        _emit_json(args, payload)
    if context.empty and not bool(getattr(args, "allow_empty", False)):
        return _refuse_empty(context, "nothing would be injected")
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    """The full resolution trail — nothing about the injection stays invisible."""
    context, code = _build_for(args)
    if context is None:
        return code
    report = P.explain_report(context)
    report["evidence"] = context.evidence()
    _report(report, context)
    if text_mode(args):
        _print_explain(context, report)
    else:
        _emit_json(args, report)
    if context.empty and not bool(getattr(args, "allow_empty", False)):
        return _refuse_empty(context, "the ladder below found nothing to inject")
    return EXIT_OK


def cmd_fingerprint(args: argparse.Namespace) -> int:
    """The digests only: one block fingerprint and one sha256 per source file."""
    context, code = _build_for(args)
    if context is None:
        return code
    evidence = context.evidence()
    payload = {
        "schema": evidence["schema"],
        "fingerprint": evidence["fingerprint"],
        "fingerprint_short": evidence["fingerprint_short"],
        "scheme": "sha256 over the canonical JSON payload of the rendered sections + sources",
        "chars": evidence["chars"],
        "max_chars": evidence["max_chars"],
        "truncated": evidence["truncated"],
        "empty": context.empty,
        "sources": evidence["sources"],
        "refused": evidence["refused"],
        "warnings": list(context.warnings),
        "workspace": evidence["workspace"],
        "start_dir": evidence["start_dir"],
    }
    _report(payload, context)
    if text_mode(args):
        print(f"fingerprint        {payload['fingerprint']}")
        print(f"fingerprint_short  {payload['fingerprint_short']}")
        print(f"chars              {payload['chars']} of {payload['max_chars']}"
              f"{' (truncated)' if payload['truncated'] else ''}")
        print(f"workspace          {payload['workspace']}")
        for source in payload["sources"]:
            print(f"{source['sha256']}  level {source['level']:<2} {source['path']}")
        for item in payload["refused"]:
            print(f"# refused: {item['path']} — {item['status']} ({item['reason']})")
    else:
        _emit_json(args, payload)
    if context.empty and not bool(getattr(args, "allow_empty", False)):
        return _refuse_empty(context, "there is nothing to fingerprint")
    return EXIT_OK


def _print_explain(context: P.ProjectContext, report: dict) -> None:
    """The human view of ``context explain`` — ladder, levels, sources, budget, overrides."""
    ladder = report["ladder"]
    print(f"ladder      {' > '.join(ladder['checked_in_each_directory'])} (then deeper wins)")
    print(f"workspace   {context.workspace}  ({context.workspace_reason})")
    print(f"start       {context.start_dir}")
    print("levels")
    for row in report["levels"]:
        present = ", ".join(row["present"]) or "—"
        print(f"  level {row['level']:<2} {row['relative']:<28} checked {row['checked']}, "
              f"present: {present}")
    print("sources")
    if not report["sources"]:
        print("  (none)")
    for row in report["sources"]:
        print(f"  used     {row['path']:<32} level {row['level']} rank {row['rank']} "
              f"{row['chars']} chars sha256 {row['sha256'][:12]}")
    for row in report["refused"]:
        print(f"  refused  {row['path']:<32} {row['status']} — {row['reason']}")
    budget = report["budget"]
    planned = ", ".join(f"{name} {chars}" for name, chars in budget["planned"].items())
    print(f"budget      {budget['max_chars']} chars, funded in order "
          f"{' > '.join(budget['funding_order'])}")
    print(f"planned     {planned}")
    print("sections")
    for row in report["sections"]:
        state = "omitted" if row["omitted"] else ("clipped" if row["clipped"] else "ok")
        print(f"  {row['name']:<12} {state:<8} {row['chars']:>5} chars  "
              f"{row['entries_included']}/{row['entries_total']} entries  quota {row['quota']}")
    if report["overrides"]:
        print("overrides (which entry won, and what the loser said)")
        for row in report["overrides"]:
            print(f"  {row['section']} {row['id']}: {row['winner']} wins over "
                  f"{', '.join(row['losers'])}")
            print(f"    kept:    {row['text']}")
            for loser_path, text in zip(row["losers"], row["losing_text"], strict=False):
                print(f"    dropped: {loser_path} said: {text}")
    print(f"fingerprint {context.fingerprint}")
    if report["warnings"]:
        print("warnings")
        for warning in report["warnings"]:
            print(f"  - {warning}")


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #


def _add_context_verbs(sub: Any) -> None:
    """The three verbs, their arguments and their handlers."""
    p_show = sub.add_parser("show", help=SHOW_HELP)
    p_show.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                        help="exit 0 with an empty block instead of 5 when nothing was found")
    _common_args(p_show)
    p_show.set_defaults(func=cmd_show, command_path="context.show")

    p_explain = sub.add_parser("explain", help=EXPLAIN_HELP)
    p_explain.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                           help="exit 0 instead of 5 when nothing was found")
    _common_args(p_explain)
    p_explain.set_defaults(func=cmd_explain, command_path="context.explain")

    p_fingerprint = sub.add_parser("fingerprint", help=FINGERPRINT_HELP)
    p_fingerprint.add_argument("--allow-empty", dest="allow_empty", action="store_true",
                               help="exit 0 instead of 5 when nothing was found")
    _common_args(p_fingerprint)
    p_fingerprint.set_defaults(func=cmd_fingerprint, command_path="context.fingerprint")

    for parser in (p_show, p_explain, p_fingerprint):
        _share_flags(parser)


def build_context_parser(subparsers: Any) -> Any:
    """Add the ``context`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "context",
        help=GROUP_HELP,
        description=(
            "What project context would be injected into a run, and why. The files are read "
            "only: AGENTS.md, SOUL.md, SKALDIR.md, CLAUDE.md and .skaldir/context.md, walked "
            "up from the working directory. A file closer to the working directory wins over "
            "one further up, and inside one directory the order is SKALDIR.md > SOUL.md > "
            "AGENTS.md > CLAUDE.md > .skaldir/context.md. `show` prints the block verbatim, "
            "`explain` the whole resolution trail, `fingerprint` the digests a run records as "
            "evidence."
        ),
    )
    sub = parser.add_subparsers(dest="context_command", required=True)
    _add_context_verbs(sub)
    return parser


def _existing_group(subparsers: Any, name: str) -> Any:
    """The group already registered under that name, or ``None``."""
    return (getattr(subparsers, "choices", None) or {}).get(name)


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent: a second call on the same handle returns the existing ``context`` group instead
    of letting argparse raise ``conflicting subparser``, and the contract entries are installed
    either way, so a wired-in group never ships without them.
    """
    group = _existing_group(subparsers, "context")
    if group is None:
        group = build_context_parser(subparsers)
    register_contract_entries()
    return group


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``python -m``)."""
    parser = argparse.ArgumentParser(prog="skaldir context",
                                     description="project context files")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_context_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir context …`` standalone — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse owns --help and usage errors
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(handler(args))
    except BrokenPipeError:  # `… show | head` must not traceback
        return EXIT_OK


__all__ = [
    "CONTRACT_ENTRIES",
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "build_context_parser",
    "build_parser",
    "cmd_explain",
    "cmd_fingerprint",
    "cmd_show",
    "contract_entries",
    "main",
    "register_cli",
    "register_contract_entries",
]


if __name__ == "__main__":  # pragma: no cover — `python -m skaldir.project_context_cli`
    raise SystemExit(main())
