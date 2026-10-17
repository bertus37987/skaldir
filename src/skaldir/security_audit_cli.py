"""``skaldir security audit`` — the security audit as a shipped command.

One verb over :mod:`skaldir.security_audit`:

======================  ==========================================================
``security audit``      walk the CLI's own source and the live runtime, and report
                        every finding of the eight audited defect classes with its
                        ``file:line``, its severity and the fix it expects
======================  ==========================================================

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok                     no finding at or above ``--fail-on`` (default high)
    1   usage                  argparse refused the call, or an unknown class/filter
    2   verification failed    a finding at or above ``--fail-on`` was reported
    3   blocked                nothing was scanned (the roots do not exist), so a
                               "clean" verdict would be a claim about nothing
    7   internal               an envelope problem or an unexpected crash

The audit is **read-only**: it opens no file for writing, spawns nothing and never
prints a secret value (a secret-shaped match is shown masked). ``--list-rules``
prints the rule catalogue with the fix behind every rule, which is what the GUI's
``contract dump`` cannot show.

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``)::

    from .security_audit_cli import register_cli
    register_cli(sub)

and five ``COMMANDS`` entries in ``src/skaldir/cli_contract.py``: ``security``,
``security.audit`` … see the hand-over note in the module docstring of
:mod:`skaldir.security_audit` and ``MVP/18-security.md`` for the exact snippet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import security_audit as A
from .cli_shared import dump, envelope_mode, product_contract, share_common_flags, text_mode

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

_MAX_EVIDENCE_REFS = 12


# --------------------------------------------------------------------------- #
# envelope plumbing                                                             #
# --------------------------------------------------------------------------- #


def _report(
    payload: Any,
    *,
    evidence: list[dict] | None = None,
    warnings: list[str] | None = None,
    next_actions: list[tuple[str, str]] | None = None,
) -> None:
    """Hand the payload and its evidence to the shared envelope (when wired)."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence or []:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")),
                str(item.get("note", "")),
            )
        for text in warnings or []:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions or []:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _blocked(message: str, *, hint: str = "", next_command: str = "",
             details: dict | None = None) -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "blocked"):
        return int(_CONTRACT.blocked(message, hint=hint, next_command=next_command,
                                     details=details))
    _text(message)
    return EXIT_BLOCKED


def _usage_error(message: str, *, hint: str = "") -> int:
    if _CONTRACT is not None and hasattr(_CONTRACT, "usage_error"):
        return int(_CONTRACT.usage_error(message, hint=hint))
    _text(f"usage: {message}")
    return EXIT_USAGE


def _text(message: str) -> None:
    print(message, file=sys.stderr)


# --------------------------------------------------------------------------- #
# arguments → inputs                                                            #
# --------------------------------------------------------------------------- #


def _roots(args: argparse.Namespace, base: Path) -> list[Path]:
    """The roots to scan: ``--path`` as given, else ``src`` + ``integrations`` (or pkg)."""
    explicit = [str(item) for item in (getattr(args, "path", None) or []) if str(item)]
    if explicit:
        return [Path(item).expanduser() for item in explicit]
    defaults = [base / "src", base / "integrations"]
    found = [candidate for candidate in defaults if candidate.exists()]
    if found:
        return found
    pkg_dir = Path(__file__).resolve().parent
    return [pkg_dir] if pkg_dir.is_dir() else []


def _runtime(args: argparse.Namespace) -> Path | None:
    explicit = str(getattr(args, "runtime", "") or "")
    if explicit:
        return Path(explicit).expanduser()
    return None  # the audit resolves the product runtime dir itself


def _severity_of(args: argparse.Namespace) -> str:
    return str(getattr(args, "min_severity", "info") or "info").lower()


# --------------------------------------------------------------------------- #
# verbs                                                                         #
# --------------------------------------------------------------------------- #


def cmd_audit(args: argparse.Namespace) -> int:
    """``security audit`` — run the audit, report it, exit by ``--fail-on``."""
    if bool(getattr(args, "list_rules", False)):
        payload = A.rules_payload()
        _report(
            payload,
            evidence=[{"ref": "src/skaldir/security_audit.py:1", "kind": "code",
                       "note": f"{len(A.RULES)} rules over {len(A.CLASSES)} classes"}],
        )
        if not envelope_mode() or text_mode(args):
            dump(payload)
        return EXIT_OK

    root_arg = str(getattr(args, "root", "") or "")
    base = Path(root_arg).expanduser() if root_arg else None
    resolved_base = (base or Path(__file__).resolve().parents[2]).resolve()
    roots = _roots(args, resolved_base)
    if not roots:
        return _blocked(
            f"no source root to audit under {resolved_base}",
            hint="pass --path <dir> (e.g. --path src)",
            next_command="security audit --path src",
            details={"root": str(resolved_base)},
        )

    classes = [str(item) for item in (getattr(args, "class_", None) or []) if str(item)]
    unknown = sorted(set(classes) - set(A.CLASSES))
    if unknown:
        return _usage_error(
            f"unknown class(es): {', '.join(unknown)}",
            hint=f"known classes: {', '.join(A.CLASSES)}",
        )

    min_severity = _severity_of(args)
    if min_severity not in A.SEVERITIES:
        return _usage_error(
            f"unknown severity: {min_severity}",
            hint=f"known severities: {', '.join(A.SEVERITIES)}",
        )

    report = A.audit(
        [Path(item) for item in roots],
        base=resolved_base,
        runtime=_runtime(args),
        include_tests=bool(getattr(args, "include_tests", False)),
        live=not bool(getattr(args, "no_live", False)),
        classes=classes or None,
        min_severity=min_severity,
        max_file_bytes=int(getattr(args, "max_file_bytes", A.MAX_FILE_BYTES) or A.MAX_FILE_BYTES),
        argv=list(sys.argv),
    )

    if int(report.get("files_scanned", 0)) <= 0:
        return _blocked(
            "the audit scanned no file — a clean verdict would be a claim about nothing",
            hint=f"checked roots: {', '.join(report.get('scan_roots') or [])}",
            next_command="security audit --path src",
            details={"roots": report.get("scan_roots") or []},
        )

    fail_on = str(getattr(args, "fail_on", "high") or "high").lower()
    blocking = [
        item for item in report["findings"]
        if fail_on != "none" and A.rank(str(item["severity"])) >= A.rank(fail_on)
    ]
    report["fail_on"] = fail_on
    report["blocking_at_fail_on"] = len(blocking)

    evidence = [
        {
            "ref": f"{resolved_base}",
            "kind": "file",
            "note": f"{report['files_scanned']} files scanned against {len(A.RULES)} rules",
        }
    ]
    for item in report["findings"][:_MAX_EVIDENCE_REFS]:
        evidence.append({
            "ref": str(item["location"]),
            "kind": "code",
            "note": f"{item['rule']} ({item['severity']}) — {item['message']}",
        })
    warnings: list[str] = []
    if report["totals"]["findings"] and not report["findings"]:
        warnings.append("every finding is filtered out by --class/--min-severity")
    if not report.get("environment"):
        warnings.append("live runtime checks were skipped (--no-live or no runtime dir)")
    next_actions = []
    if blocking:
        first = blocking[0]
        next_actions.append((
            f"security audit --class {first['class']}",
            f"review the {len(blocking)} finding(s) at or above {fail_on}",
        ))
    _report(report, evidence=evidence, warnings=warnings, next_actions=next_actions)

    text = A.render_text(report)
    if not envelope_mode() or text_mode(args):
        _text(text)
    else:
        # the driver redirects a handler's stdout to stderr in --json mode; keep it there
        print(text, file=sys.stderr)

    if blocking:
        message = (
            f"{len(blocking)} security finding(s) at or above {fail_on}: "
            + ", ".join(sorted({f"{item['rule']}@{item['location']}" for item in blocking}))
        )
        if _CONTRACT is not None and hasattr(_CONTRACT, "verification_failed"):
            return int(_CONTRACT.verification_failed(
                message,
                hint="each finding's fix is in data.findings[].fix",
                details={"blocking": len(blocking), "fail_on": fail_on},
            ))
        _text(message)
        return EXIT_VERIFICATION_FAILED
    return EXIT_OK


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


def _audit_args(parser: Any) -> None:
    parser.add_argument(
        "--path", action="append", default=[],
        help="a directory (or file) to scan; repeatable (default: src, integrations)",
    )
    parser.add_argument("--root", default="", help="the repository root (default: this tree)")
    parser.add_argument(
        "--class", dest="class_", action="append", default=[],
        help=f"only report this class; repeatable ({', '.join(A.CLASSES)})",
    )
    parser.add_argument(
        "--min-severity", default="info", choices=A.SEVERITIES,
        help="drop findings below this severity (default: info)",
    )
    parser.add_argument(
        "--fail-on", default="high", choices=(*A.SEVERITIES, "none"),
        help="exit 2 when a finding at or above this severity is reported (default: high)",
    )
    parser.add_argument("--runtime", default="", help="runtime dir for the live checks")
    parser.add_argument("--include-tests", action="store_true",
                        help="also scan tests/ (their fixtures carry fake secrets)")
    parser.add_argument("--no-live", action="store_true",
                        help="skip the live runtime checks (permissions, leftovers, manifests)")
    parser.add_argument("--max-file-bytes", type=int, default=A.MAX_FILE_BYTES,
                        help=f"skip files larger than this (default: {A.MAX_FILE_BYTES})")
    parser.add_argument("--list-rules", action="store_true",
                        help="print the rule catalogue with the fix behind every rule")
    parser.add_argument("--json", action="store_true", help="machine-readable envelope")
    parser.add_argument("--text", action="store_true", help="human text only")


def cmd_coverage(args: argparse.Namespace) -> int:
    """Expose measured platform coverage without upgrading degraded controls."""
    from .security_linux import STATUS_ENFORCED, platform_report

    report = platform_report()
    payload = {
        **report,
        "assessment_scope": "host_capability_probe",
        "execution_observed": False,
        "primitives": [
            {**row, "capability_available": row["status"] == STATUS_ENFORCED,
             "applied": None}
            for row in report["primitives"]
        ],
        "notes": [*report.get("notes", []),
                  "Capability detection is not proof of enforcement in a child process. "
                  "Use permission explain for profile coverage and run evidence for application."],
    }
    _report(payload)
    if not envelope_mode() or text_mode(args):
        dump(payload)
    return EXIT_OK if payload["verdict"] == STATUS_ENFORCED else EXIT_BLOCKED


def _add_security_verbs(sub: Any) -> Any:
    """Add the ``security`` verbs to a subparsers handle (one definition)."""
    parser = sub.add_parser(
        "audit",
        help="audit the CLI surface for the eight classic defect classes",
        description=(
            "Walk this tree and the live runtime and report command injection, path "
            "traversal, secrets, unsafe deserialisation, permission bypass, TOCTOU, "
            "unbounded resources and error leakage — each with file:line and the fix. "
            "Read-only; a run that scanned nothing exits blocked, never ok."
        ),
    )
    _audit_args(parser)
    share_common_flags(parser)
    parser.set_defaults(func=cmd_audit, command_path="security.audit")
    coverage = sub.add_parser("coverage", help="show measured enforcement coverage per primitive")
    share_common_flags(coverage)
    coverage.set_defaults(func=cmd_coverage, command_path="security.coverage")
    return sub


def build_security_parser(subparsers: Any) -> Any:
    """Add the ``security`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "security",
        help="security audit of the CLI surface (audit)",
        description="One re-runnable audit of the classes the owner asked to be safe against.",
    )
    sub = parser.add_subparsers(dest="security_command", required=True)
    _add_security_verbs(sub)
    return sub


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    return build_security_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir security",
                                     description="security audit of the CLI surface")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_security_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir security …`` — returns the exit code, never raises for a request.

    Standalone (``python -m skaldir.security_audit_cli``) this goes through the product
    contract's ``run_command`` when it is importable, so ``--json`` prints the real
    envelope here too; inside the product CLI the driver calls ``run_command`` itself
    and this function is not used.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is not None and hasattr(_CONTRACT, "run_command") and not envelope_mode():
        try:
            return int(_CONTRACT.run_command(args, func))
        except Exception:  # noqa: BLE001 — fall back to the verb's own exit code
            return int(func(args))
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover — convenience for `python -m`
    raise SystemExit(main(sys.argv[1:]))


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_parser",
    "build_security_parser",
    "cmd_audit",
    "main",
    "register_cli",
]
