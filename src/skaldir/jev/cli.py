"""``skaldir jev …`` — the machine-first CLI surface of MVP/03 §7.1.

The command group is added by the product CLI through one call:

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), right after
    #     sub = parser.add_subparsers(dest="command", required=False)
    from .jev.cli import register_cli

    register_cli(sub)

``register_cli`` is the whole contract: it takes the CLI's own ``subparsers``
handle, adds the ``jev`` group with its verbs, and returns the parser. Nothing in
this package edits ``cli.py``.

Exit codes — the product CLI's ``cli_contract`` vocabulary is adopted when that
module exists, otherwise MVP/03 §7.1's own codes are used (the product contract
wins; the Jev spelling is in brackets)::

    0                  ok            one decision, status "ok"
    1 [design 2]       usage         argparse refused, request unreadable/malformed
    3                  no advisory   status "refused"/"blocked" — the caller keeps its path
    7 [design 4]       violation     contract error / hard-rule violation (our bug)
    4 [design 3]       credentials   a live-capable provider with no credential
    3 [design 5]       provider      the provider failed structurally = no advisory

stdout carries exactly one JSON object per invocation (``ensure_ascii=False``,
``sort_keys=True``, ``indent=2`` — the product CLI's JSON convention); diagnostics
go to stderr only, and ``--text`` (or ``--format text``) is the only human mode.
When the product driver runs the handler (``cli_contract.run_command``), the
payload and the evidence for it are handed to the shared envelope instead
(``set_data``/``add_evidence``), which is what satisfies its "no success without
evidence" rule; the group is also responsible for its five ``COMMANDS`` entries
there — see the hand-over note in the module docstring of
:mod:`skaldir.jev.selftest`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..system_one import (
    PROVIDER_DISABLED,
    PROVIDER_LLM_ADAPTER,
    PROVIDER_TYPESAFE,
    SystemOneContractError,
)
from . import contracts as C
from .decide import decide
from .kinds import DECISION_KINDS
from .ledger import DecisionLedger
from .rules import JevRuleViolation
from .selftest import kinds_report, packs_report, selftest


def _product_contract():
    """The product CLI's envelope module, when this build has one.

    ``src/skaldir/cli_contract.py`` (owned by the CLI) defines the shared exit-code
    set, the one JSON envelope and the "no success without evidence" rule. The Jev
    group adopts it when it is importable and falls back to the exit codes MVP/03
    §7.1 names otherwise — never the other way round: the product contract wins.
    """
    try:
        from .. import cli_contract
    except Exception:  # noqa: BLE001 — an older build without the module
        return None
    return cli_contract


_CONTRACT = _product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
#: usage: the design's 2, the product contract's 1
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 2))
#: no advisory — refused/blocked: both vocabularies say 3
EXIT_NO_ADVISORY = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
#: a contract/rule violation is our bug, not the caller's problem
EXIT_VIOLATION = int(getattr(_CONTRACT, "EXIT_INTERNAL", 4))
#: a provider that failed structurally has produced no advisory
EXIT_PROVIDER = int(getattr(_CONTRACT, "EXIT_BLOCKED", 5))
#: a live-capable provider with no authorized credential has its own code
EXIT_NEEDS_CREDENTIALS = int(getattr(_CONTRACT, "EXIT_NEEDS_CREDENTIALS", EXIT_NO_ADVISORY))

def _runtime_module() -> Any:
    """The runtime binding (:mod:`skaldir.jev.runtime`), imported lazily.

    The CLI group is imported by the product CLI at start-up; the runtime module pulls the
    settings/substrate layer in, and the façade must stay importable without it. One
    function, one reason: `--provider settings` is the runtime's rule, not the CLI's.
    """
    from . import runtime

    return runtime


#: The providers the CLI may select. The fixture evaluator is deliberately
#: **not** here (it is test/self-audit infrastructure); `selftest` uses it
#: in-process and labels its decisions `replay_fixture`.
#:
#: ``settings`` is the **default**, and it is what an owner means by "always armed":
#: the runtime's own flags (``<runtime>/settings.yaml``) when they name a provider, else
#: the decision block the config file carries — the System One endpoint as
#: ``typesafe_jev``, a stand-in chat model as ``llm_system_one_adapter``. ``disabled``
#: stays selectable and stays honest; it is just no longer what an unconfigured verb
#: silently does to a configured installation.
PROVIDER_SETTINGS = _runtime_module().PROVIDER_SETTINGS
PROVIDER_CHOICES = (PROVIDER_SETTINGS, PROVIDER_DISABLED, PROVIDER_LLM_ADAPTER, PROVIDER_TYPESAFE)

DEFAULT_STATE_DIR = ".godsv"


# --------------------------------------------------------------------------- #
# JSON in / JSON out                                                            #
# --------------------------------------------------------------------------- #


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _dump_line(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _text_mode(args: argparse.Namespace) -> bool:
    """Human text only when it is explicitly asked for (``--text``/``--format text``).

    Jev is machine-first (MVP/03 §7.1: ``--json`` is the default mode), so the
    shared ``--format`` flag can only *narrow* to text — never the other way
    round, and never implicitly.
    """
    if bool(getattr(args, "text", False)):
        return True
    return str(getattr(args, "format", "") or "").strip().lower() == "text"


def _report(
    payload: Any, *, evidence: tuple[dict, ...] = (), warnings: tuple[str, ...] = ()
) -> None:
    """Feed the product envelope: the data payload and the evidence for it.

    Required when wired into ``skaldir``: the shared envelope refuses to report
    success it cannot back (``build_envelope`` flips a success without evidence to
    ``EXIT_INTERNAL``), so every successful Jev verb declares where its answer
    comes from. Harmless when the group runs standalone.
    """
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


def _decision_evidence(decision: C.JevDecision) -> tuple[dict, ...]:
    return (
        {
            "ref": decision.decision_id,
            "kind": "code",
            "note": (
                f"{decision.kind} {decision.status} via {decision.provider_mode} "
                f"(pack {decision.question_pack_id or '-'} {decision.question_pack_version or '-'})"
            ),
        },
        {
            "ref": "src/skaldir/jev/rules.py",
            "kind": "code",
            "note": "R1-R4 re-checked at construction with the request in hand",
        },
    )


def _read_request(args: argparse.Namespace) -> tuple[dict | None, int]:
    """The request object from ``--request FILE`` or stdin — JSON only."""
    path = getattr(args, "request", None) or getattr(args, "transcript", None)
    try:
        if path and str(path) != "-":
            text = Path(path).read_text(encoding="utf-8")
        elif not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print("jev decide: no request on stdin and no --request FILE", file=sys.stderr)
            return None, EXIT_USAGE
    except OSError as exc:
        print(f"jev decide: cannot read the request: {exc}", file=sys.stderr)
        return None, EXIT_USAGE
    if not text.strip():
        print("jev decide: the request is empty", file=sys.stderr)
        return None, EXIT_USAGE
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"jev decide: the request is not JSON: {exc}", file=sys.stderr)
        return None, EXIT_USAGE
    if not isinstance(payload, dict):
        print("jev decide: the request must be one JSON object", file=sys.stderr)
        return None, EXIT_USAGE
    return payload, EXIT_OK


def _exit_for(decision: C.JevDecision) -> int:
    if decision.status == "ok":
        return EXIT_OK
    if decision.status == "blocked" and _needs_credentials(decision, None):
        return EXIT_NEEDS_CREDENTIALS
    return EXIT_NO_ADVISORY


def _needs_credentials(decision: C.JevDecision, args: argparse.Namespace | None) -> bool:
    """Was the block a missing authorized credential rather than "no provider"?

    True when the caller asked for a live-capable provider (anything but
    ``disabled``) and the block names the credential or the live-access refusal —
    the two honest spellings of "Jev is not authorized here". Everything else
    (disabled provider, stalled provider, deadline) is a plain no-advisory.
    """
    provider = str(getattr(args, "provider", "") or "") if args is not None else "typesafe_jev"
    if provider in ("", PROVIDER_DISABLED):
        # only a live-capable provider can be "missing credentials"; decide from
        # the decision's own reason when no namespace is at hand
        provider = "typesafe_jev" if "credential" in decision.refusal_reason.lower() else ""
    if not provider:
        return False
    reason = decision.refusal_reason.lower()
    return "credential" in reason or reason.startswith("provider_contract_refusal")


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def cmd_kinds(args: argparse.Namespace) -> int:
    """The seven kinds with their pack ref, risk class, flag and models."""
    rows = kinds_report()
    payload = {"kinds": rows, "count": len(rows)}
    _report(
        payload,
        evidence=(
            {"ref": "src/skaldir/jev/kinds.py", "kind": "code", "note": "kind -> pack map"},
            {
                "ref": "src/skaldir/jev/contracts.py",
                "kind": "code",
                "note": "JEV_REQUESTS/JEV_ANSWERS",
            },
        ),
    )
    if not _text_mode(args):
        _dump(payload)
        return EXIT_OK
    for row in rows:
        print(
            f"{row['kind']:<26} {row['pack_ref']:<24} {row['risk_class']:<3} "
            f"{row['flag'] or '-':<24} {row['answer_model']}"
        )
    return EXIT_OK


def cmd_packs(args: argparse.Namespace) -> int:
    """Every pack the Jev registry view serves (the substrate's + the five new ones)."""
    rows = packs_report()
    payload = {"packs": rows, "count": len(rows)}
    _report(
        payload,
        evidence=(
            {"ref": "src/skaldir/jev/packs.py", "kind": "code", "note": "pack definitions"},
            {
                "ref": "src/skaldir/system_one_questions.py",
                "kind": "code",
                "note": "the registry the view is built from (voice_triage_v1 is reused)",
            },
        ),
    )
    if not _text_mode(args):
        _dump(payload)
        return EXIT_OK
    for row in rows:
        print(f"{row['ref']:<24} {row['owner']:<12} {row['risk_class']:<3} {row['purpose'][:60]}")
    return EXIT_OK


def cmd_selftest(args: argparse.Namespace) -> int:
    """The R1–R4 table + per-kind reachability; exits 1 when a rule is untested."""
    report = selftest()
    evidence = tuple(
        {
            "ref": f"tests/test_jev_facade_rules.py::{node.split('::')[-1]}",
            "kind": "test",
            "note": f"{rule['rule']} enforcement",
        }
        for rule in report["rules"]
        for node in rule["tests"]
    ) + tuple(
        {
            "ref": f"kind:{row['kind']}",
            "kind": "code",
            "note": f"{row['status']} via {row['provider_mode']} ({row['answer_model']})",
        }
        for row in report["kinds"]
    )
    warnings = tuple(
        f"rule without its pinned tests: {rule}" for rule in report["untested_rules"]
    )
    _report({"selftest": report}, evidence=evidence, warnings=warnings)
    if _text_mode(args):
        for entry in report["kinds"]:
            mark = "reachable" if entry["reachable"] else "NOT REACHABLE"
            print(
                f"{entry['kind']:<26} {entry['status']:<8} "
                f"{entry['provider_mode']:<22} {mark}"
            )
        for rule in report["rules"]:
            mark = "pinned" if rule["test_present"] else "UNTESTED"
            print(f"{rule['rule']:<26} {mark:<9} {rule['statement']}")
        if report["untested_rules"]:
            print(
                "untested rule(s): " + ", ".join(report["untested_rules"]),
                file=sys.stderr,
            )
    else:
        _dump(report)
    return int(report["exit_code"])


def cmd_decide(args: argparse.Namespace) -> int:
    """One decision: request JSON in, exactly one decision JSON out."""
    if getattr(args, "apply", False):
        # Applying a compaction decision to a transcript is the compaction port's
        # job (``src/skaldir/jevcomp``): it owns the staged fitting, the batching and
        # the deterministic rebuild. Jev returns the typed decision; it never
        # rewrites a transcript, so a silent no-op here would be a lie.
        print(
            "jev decide: --apply rebuilds a transcript and belongs to the compaction "
            "port (src/skaldir/jevcomp); Jev only produces the decision — omit --apply "
            "and pass the decision to that port",
            file=sys.stderr,
        )
        return EXIT_USAGE
    payload, code = _read_request(args)
    if payload is None:
        return code
    kind = str(getattr(args, "kind", "") or payload.get("kind") or "")
    if kind not in DECISION_KINDS:
        print(
            f"jev decide: --kind must be one of {list(DECISION_KINDS)}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if payload.get("kind") and str(payload["kind"]) != kind:
        print(
            f"jev decide: the request's kind {payload['kind']!r} contradicts --kind {kind!r}",
            file=sys.stderr,
        )
        return EXIT_VIOLATION
    payload.setdefault("kind", kind)
    try:
        request = C.JEV_REQUESTS[kind](**payload)
    except Exception as exc:  # noqa: BLE001 — a malformed request is the caller's bug
        print(f"jev decide: the request is not a valid {kind} request: {exc}", file=sys.stderr)
        return EXIT_VIOLATION

    flags = _flags_for(args)
    try:
        decision = decide(
            request,
            flags=flags,
            service=None,
            evaluator=None,
            shadow=bool(getattr(args, "shadow", False)),
        )
    except JevRuleViolation as exc:
        print(f"jev decide: hard-rule violation {exc.rule}: {exc}", file=sys.stderr)
        return EXIT_VIOLATION
    except SystemOneContractError as exc:
        print(f"jev decide: contract error: {exc}", file=sys.stderr)
        return EXIT_VIOLATION

    document = decision.model_dump(mode="json")
    out = getattr(args, "out", None)
    if out:
        try:
            Path(out).write_text(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"jev decide: cannot write --out: {exc}", file=sys.stderr)
            return EXIT_USAGE
    if getattr(args, "dry_run", False):
        ledger_path = ""
    else:
        ledger = DecisionLedger(Path(getattr(args, "state", DEFAULT_STATE_DIR)))
        ledger_path = ledger.append(document)
        ledger.close()
    _report(
        document,
        evidence=_decision_evidence(decision),
        warnings=(
            (f"no advisory ({decision.refusal_reason}); the caller keeps its own path",)
            if decision.status != "ok" and decision.refusal_reason
            else ()
        ),
    )
    if _text_mode(args):
        print(
            f"{decision.kind:<26} {decision.status:<8} {decision.provider_mode:<22} "
            f"caution={decision.caution_level} fallback={decision.fallback or '-'}"
        )
        if ledger_path:
            print(f"ledger: {ledger_path}")
    else:
        _dump(document)
    if decision.status == "ok":
        return EXIT_OK
    if _needs_credentials(decision, args):
        return EXIT_NEEDS_CREDENTIALS
    return _exit_for(decision)


def cmd_history(args: argparse.Namespace) -> int:
    """What Jev decided and which fallback fired — the decision ledger."""
    ledger = DecisionLedger(Path(getattr(args, "state", DEFAULT_STATE_DIR)))
    limit = int(getattr(args, "limit", 20) or 20)
    run_id = str(getattr(args, "run", "") or "")
    try:
        rows = ledger.history(run_id=run_id, limit=limit)
    finally:
        ledger.close()
    payload = {"decisions": rows, "count": len(rows), "ledger": str(ledger.path)}
    _report(
        payload,
        evidence=(
            {
                "ref": str(ledger.path),
                "kind": "file",
                "note": f"{len(rows)} recorded decision(s)"
                + (f" for run {run_id}" if run_id else ""),
            },
        ),
    )
    if not _text_mode(args):
        _dump(payload)
        return EXIT_OK
    for row in rows:
        print(
            f"{row.get('kind', ''):<26} {row.get('status', ''):<8} "
            f"{row.get('provider_mode', ''):<22} {row.get('fallback', '') or '-'}"
        )
    return EXIT_OK


def _flags_for(args: argparse.Namespace):
    """The flags the invocation asks for — never off when the installation is armed.

    ``settings`` (the default) asks the runtime: its ``settings.yaml`` flags when they name
    a provider, else the configured decision block, else an honest ``disabled``. Every other
    value is taken literally, including ``disabled`` — this is a switch, not a policy.
    """
    from ..system_one_flags import SystemOneFlags

    provider = str(getattr(args, "provider", PROVIDER_SETTINGS) or PROVIDER_SETTINGS)
    if provider == PROVIDER_SETTINGS:
        flags, _provenance = _runtime_module().configured_provider_flags(
            runtime=str(getattr(args, "runtime", "") or "") or None,
            config_path=str(getattr(args, "config", "") or "") or None,
        )
        if bool(getattr(args, "shadow", False)):
            return SystemOneFlags(
                enabled=True,
                provider=flags.provider,
                shadow_mode=True,
                voice_triage=flags.voice_triage,
                goal_completion=flags.goal_completion,
                computer_use=flags.computer_use,
                recovery=flags.recovery,
                llm_fallback=flags.llm_fallback,
                completion_precheck=flags.completion_precheck,
            )
        return flags
    if provider == PROVIDER_DISABLED:
        return SystemOneFlags(enabled=True, provider=PROVIDER_DISABLED)
    return SystemOneFlags(
        enabled=True,
        provider=provider,
        shadow_mode=bool(getattr(args, "shadow", False)),
    )


# --------------------------------------------------------------------------- #
# the parser contract                                                           #
# --------------------------------------------------------------------------- #


def _share_common_flags(parser: Any) -> None:
    """Attach the product CLI's shared flags (``--format/--quiet/--no-color``).

    Done through ``cli_contract.add_common_flags`` when that module exists so the
    group speaks the same flag vocabulary as the rest of ``skaldir``; the helper
    skips option strings a parser already defines, so it is safe to call twice and
    safe to call before or after the CLI's own ``annotate()`` pass.
    """
    if _CONTRACT is None or not hasattr(_CONTRACT, "add_common_flags"):
        return
    try:
        _CONTRACT.add_common_flags(parser)
    except Exception:  # noqa: BLE001 — a flag helper must never break the group
        return


def _add_jev_verbs(sub: Any) -> Any:
    """Add the five ``jev`` verbs to a subparsers handle (one definition)."""
    p_kinds = sub.add_parser("kinds", help="list the six decision kinds + pack refs")
    p_kinds.add_argument("--json", action="store_true")
    p_kinds.add_argument("--text", action="store_true")
    p_kinds.set_defaults(func=cmd_kinds)

    p_packs = sub.add_parser("packs", help="print the question-registry manifest")
    p_packs.add_argument("--json", action="store_true")
    p_packs.add_argument("--text", action="store_true")
    p_packs.set_defaults(func=cmd_packs)

    p_selftest = sub.add_parser(
        "selftest", help="hard-rule table + per-kind reachability (exit 1 if untested)"
    )
    p_selftest.add_argument("--json", action="store_true")
    p_selftest.add_argument("--text", action="store_true")
    p_selftest.set_defaults(func=cmd_selftest)

    p_decide = sub.add_parser(
        "decide", help="one decision: request JSON on stdin (or --request FILE)"
    )
    p_decide.add_argument("--kind", choices=list(DECISION_KINDS), default="")
    p_decide.add_argument("--request", default="", help="request JSON file ('-' for stdin)")
    p_decide.add_argument(
        "--transcript",
        default="",
        help="alias for --request: the transcript-derived request document (compaction)",
    )
    p_decide.add_argument(
        "--apply",
        action="store_true",
        help="rebuild the transcript from the decisions (owned by the compaction port)",
    )
    p_decide.add_argument("--out", default="", help="also write the decision JSON here")
    p_decide.add_argument(
        "--provider",
        choices=list(PROVIDER_CHOICES),
        default=PROVIDER_SETTINGS,
        help=(
            "which provider answers: 'settings' (default) = the runtime's own flags, or the "
            "configured decision block when they name none; 'typesafe_jev' = the System One "
            "endpoint (needs an authorized TypeSafe credential); 'llm_system_one_adapter' = "
            "an LLM standing in for Jev; 'disabled' = refuse, by design"
        ),
    )
    p_decide.add_argument("--shadow", action="store_true")
    p_decide.add_argument("--dry-run", action="store_true", help="do not append to the ledger")
    p_decide.add_argument("--state", default=DEFAULT_STATE_DIR)
    p_decide.add_argument(
        "--runtime",
        default="",
        help="the runtime state directory holding settings.yaml (default: the product's)",
    )
    p_decide.add_argument(
        "--config", default="", help="the provider config file to resolve (default: the product's)"
    )
    p_decide.add_argument("--json", action="store_true")
    p_decide.add_argument("--text", action="store_true")
    p_decide.set_defaults(func=cmd_decide)

    p_history = sub.add_parser("history", help="the decision ledger")
    p_history.add_argument("--run", default="")
    p_history.add_argument("--limit", type=int, default=20)
    p_history.add_argument("--state", default=DEFAULT_STATE_DIR)
    p_history.add_argument("--json", action="store_true")
    p_history.add_argument("--text", action="store_true")
    p_history.set_defaults(func=cmd_history)
    for parser in (p_kinds, p_packs, p_selftest, p_decide, p_history):
        _share_common_flags(parser)
    return sub


def build_jev_parser(subparsers: Any) -> Any:
    """Add the ``jev`` command group to a CLI's ``subparsers`` handle and return it."""
    parser = subparsers.add_parser(
        "jev",
        help="Jev decision façade (typed advisories: routing, memory, compaction)",
        description=(
            "Jev is a fast decision/routing layer under the agent. It answers one of six "
            "typed questions, always advisory: never a goal PASS, never a permission, "
            "never a security exception, never a goal override."
        ),
    )
    sub = parser.add_subparsers(dest="jev_command")
    _add_jev_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``."""
    return build_jev_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (``python -m skaldir.jev`` / tests).

    Same verbs as ``build_jev_parser`` minus the wrapping ``jev`` word, so
    ``main(["selftest", "--json"])`` and the product's
    ``skaldir jev selftest --json`` run exactly the same handler.
    """
    parser = argparse.ArgumentParser(prog="skaldir jev", description="Jev decision façade")
    sub = parser.add_subparsers(dest="command")
    _add_jev_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir jev …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already chose a code
        # ``--help``/``--version`` exit 0; a usage error exits 2, which the product
        # contract spells 1.
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


__all__ = [
    "EXIT_NO_ADVISORY",
    "EXIT_OK",
    "EXIT_PROVIDER",
    "EXIT_USAGE",
    "EXIT_VIOLATION",
    "PROVIDER_CHOICES",
    "build_jev_parser",
    "build_parser",
    "cmd_decide",
    "cmd_history",
    "cmd_kinds",
    "cmd_packs",
    "cmd_selftest",
    "main",
    "register_cli",
]

# `--request -` reads stdin: an explicit spelling of the default.
