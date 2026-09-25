"""``skaldir edit`` — edit transactions: ``apply`` / ``dry-run`` / ``undo`` / ``show``.

Four verbs over :mod:`skaldir.edit_tx` (the engine). One transaction document describes a
batch of targeted replacements; the group either plans it (``dry-run``), plans and
publishes it (``apply``), restores it (``undo``) or explains it (``show``)::

    skaldir edit dry-run --tx tx.json --workspace DIR
    skaldir edit apply   --tx tx.json --workspace DIR --verify tests --verify-tests tests/x.py
    skaldir edit undo    last --state DIR
    skaldir edit show    last

What the group is *for*: making a multi-file change that either happens completely or not
at all, seeing the diff **before** it happens, having a declared check run after it and a
byte-identical way back. The engine's module docstring carries the design, the reference
patterns and the honest limits; this module carries the command surface, the exit codes
and the evidence.

Exit codes — the product CLI's ``cli_contract`` vocabulary::

    0   ok                 the plan applies / the transaction was applied (and verified)
    1   usage              a bad document, conflicting flags, a no-op edit
    2   verification       the declared check failed (rolled back) — or, for ``dry-run``,
                           a plan that does not apply
    3   blocked            the workspace, the path, the file's shape or a cap refused it
    5   not found          the file / the needle / the transaction id does not exist
    6   conflict           ambiguous match, an occurrence the caller did not name, a
                           cascade edit, a tree that moved after the plan, or an undo
                           whose files changed since the apply
    7   internal           a publish failed and the rollback ran (or an envelope problem)

``dry-run`` and ``apply`` share :func:`skaldir.edit_tx.exit_code_for`, so **a dry run exits
with the code the apply would exit with** (0 / 1 / 2 / 3 / 5 / 6).

``--json`` is the product envelope when the group is wired into ``cli.py`` (one JSON
document on stdout, the human view on stderr) and the transaction record itself when the
module runs standalone (``python -m skaldir.edit_cli edit dry-run … --json``), which is
also how it is tested without touching ``cli.py``.

Hand-over (the only change ``cli.py`` needs — 3 lines, insert next to the other group
registrations)::

    # Edit transactions (atomic multi-file edits): the group owns its four verbs and their
    # `cli_contract.COMMANDS` entries — see the hand-over note in
    # src/skaldir/edit_cli.py.
    from .edit_cli import register_cli as _register_edit_cli

    _register_edit_cli(sub)

The four ``COMMANDS`` entries are installed by ``register_cli`` through
``cli_shared.install_contract_entries`` (never overwriting an existing entry), and
:data:`CONTRACTS_SNIPPET` carries the same lines for a CLI owner who prefers them inline.
``tests/test_edit_tx_cli.py`` proves the hand-over on a **scratch copy** of ``src/``: the
snippet applied there makes ``skaldir edit …`` reachable, ``contract dump`` list the four
leaves and ``tests/test_cli_contract_dump.py`` stay green.

Credits: the reference patterns (Claude Code's unique-``old_string`` + read-before-edit +
cascade protection, Cline's shadow snapshots and restore-files, OpenCode's workspace
boundary + post-write diagnostics) are described in ``skaldir.edit_tx``'s docstring and in
``MVP/36-edit-transactions.md``; the matching ladder and the path policy are this
repository's own ``tools_core.patch_tool`` / ``tools_core.sandbox``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import edit_tx as ET
from .cli_shared import (
    as_subparsers,
    blocked,
    dump,
    envelope_mode,
    install_contract_entries,
    not_found,
    product_contract,
    runtime_dir,
    share_common_flags,
    verification_failed,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

GROUP_HELP = "edit transactions: atomic multi-file edits with a dry-run, a snapshot and undo"
APPLY_HELP = "plan *and* publish a transaction, then run the declared verification"
DRY_RUN_HELP = "resolve every edit, print the diffs and the failure reasons, write nothing"
UNDO_HELP = "restore every file of a transaction, byte-identically"
SHOW_HELP = "the transaction record (or the list of records in this state directory)"

_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")


# --------------------------------------------------------------------------- #
# envelope + refusals                                                           #
# --------------------------------------------------------------------------- #


def _publish(
    payload: Any,
    *,
    evidence: Iterable[dict] = (),
    warnings: Iterable[str] = (),
    next_actions: Iterable[tuple[str, str]] = (),
) -> None:
    """Hand this verb's record to the product envelope (a no-op when standalone).

    ``register_cli`` installs the contract entries, so the envelope's *default* evidence
    is already declared; this adds the per-call evidence on top (the transaction's own
    files, hashes and the verification argv). A failure here never breaks a verb.
    """
    contract = _CONTRACT
    if contract is None:
        return
    for command, why in next_actions:
        try:
            contract.next_action(str(command), str(why))
        except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
            continue
    for text in warnings:
        try:
            contract.warn(str(text))
        except Exception:  # noqa: BLE001
            continue
    try:
        for item in evidence:
            kind = str(item.get("kind") or "file")
            contract.add_evidence(
                str(item.get("ref") or ""),
                kind if kind in _EVIDENCE_KINDS else "file",
                str(item.get("note") or ""),
            )
        contract.set_data(payload)
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _refuse(
    code: int,
    message: str,
    *,
    hint: str = "",
    details: Any = None,
    next_command: str = "",
    reason: str = "",
) -> int:
    """The contract's own refusal for ``code`` (the tree was never touched)."""
    if _CONTRACT is None:
        return int(code)
    if code == EXIT_BLOCKED:
        return blocked(
            message, hint=hint, next_command=next_command, reason=reason, details=details
        )
    if code == EXIT_NOT_FOUND:
        return not_found(message, hint=hint, details=details)
    if code == EXIT_VERIFICATION_FAILED:
        return verification_failed(message, hint=hint, details=details)
    if code == EXIT_USAGE:
        return int(_CONTRACT.usage_error(message, hint=hint))
    return int(_CONTRACT.fail(code, message, hint=hint, details=details))


def _refusal_message(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The one message a refused record has to offer, plus the detail behind it."""
    for key in ("blocked", "undo_error", "error"):
        issue = record.get(key) or {}
        if issue.get("message"):
            return str(issue["message"]), {
                "reason": issue.get("reason", ""),
                "details": issue.get("detail", {}),
            }
    for item in record.get("files") or []:
        if not item.get("ok"):
            return (
                f"{item.get('path')}: {item.get('message')}",
                {"reason": item.get("reason", ""), "detail": item.get("detail", {})},
            )
    return str(record.get("message") or "the transaction was refused"), {}


def _refusal_hint(record: dict[str, Any]) -> str:
    """A refusal that already knows the fix names it; otherwise a generic next step."""
    for item in record.get("files") or []:
        hint = str((item.get("detail") or {}).get("hint") or "")
        if hint:
            return hint
    if record.get("verdict") == ET.VERDICT_ROLLED_BACK:
        return "the change was rolled back; read data.rollback for what was restored"
    return "run `skaldir edit dry-run` against the current tree to see the per-file reason"


# --------------------------------------------------------------------------- #
# arguments → inputs                                                            #
# --------------------------------------------------------------------------- #


def _is_json_out(args: argparse.Namespace) -> bool:
    """The record as JSON — unless the product driver is already emitting the envelope."""
    if envelope_mode():
        return False
    if _CONTRACT is not None and hasattr(_CONTRACT, "json_requested"):
        try:
            return bool(_CONTRACT.json_requested(args))
        except Exception:  # noqa: BLE001 — a broken helper must not break the verb
            return False
    if bool(getattr(args, "json", False)):
        return True
    return str(getattr(args, "format", "") or "").strip().lower() == "json"


def _emit(args: argparse.Namespace, record: dict[str, Any], *, evidence: Iterable[dict] = (),
          warnings: Iterable[str] = (), next_actions: Iterable[tuple[str, str]] = ()) -> None:
    """Print the record (JSON or the human block), then publish it to the envelope."""
    if _is_json_out(args):
        dump(record)
    else:
        print(ET.render_text(record))
    _publish(record, evidence=tuple(evidence), warnings=tuple(warnings),
             next_actions=tuple(next_actions))


def _state_dir(args: argparse.Namespace) -> Path:
    """``--state`` when given, else ``<runtime>/edit`` (the product's own runtime)."""
    explicit = str(getattr(args, "state", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return runtime_dir(args) / ET.RECORD_DIRNAME


def _workspace(args: argparse.Namespace) -> Path:
    """``--workspace`` when given, else the current directory (resolved by the engine)."""
    raw = str(getattr(args, "workspace", "") or "").strip()
    return Path(raw).expanduser() if raw else Path.cwd()


def _caps(args: argparse.Namespace) -> ET.EditCaps:
    """The caps in force: the environment, then every flag that was actually given."""
    caps = ET.EditCaps.from_env()
    overrides: dict[str, Any] = {}
    for flag, field, factor in (
        ("max_file_mb", "max_file_bytes", 1024 * 1024),
        ("max_tx_mb", "max_tx_bytes", 1024 * 1024),
        ("max_files", "max_files", 1),
        ("max_edits", "max_edits", 1),
        ("max_diff_kb", "max_diff_bytes", 1024),
    ):
        value = getattr(args, flag, None)
        if value is None:
            continue
        overrides[field] = int(value) * factor
    return dataclasses.replace(caps, **overrides) if overrides else caps


def _load_document(args: argparse.Namespace) -> tuple[dict[str, Any] | None, str]:
    """The transaction document: ``--tx FILE|-`` (stdin) or the inline ``--tx-json``."""
    inline = str(getattr(args, "tx_json", "") or "")
    source = str(getattr(args, "tx", "") or "").strip()
    if inline:
        text, where = inline, "<--tx-json>"
    elif source:
        if source == "-":
            text, where = sys.stdin.read(), "<stdin>"
        else:
            path = Path(source).expanduser()
            if not path.is_file():
                return None, f"the transaction document does not exist: {path}"
            where = str(path)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                return None, f"cannot read {path}: {exc}"
    else:
        return None, "edit apply/dry-run needs --tx FILE (or --tx-json JSON)"
    try:
        document = json.loads(text)
    except ValueError as exc:
        return None, f"{where} is not valid JSON: {exc}"
    return ({"document": document, "source": where}, "")


def _verification_override(args: argparse.Namespace) -> tuple[dict[str, Any] | None, str]:
    """The CLI's verification declaration, when a flag asked for one (else the document's)."""
    kind = str(getattr(args, "verify", "") or "").strip().lower()
    argv_json = str(getattr(args, "verify_argv", "") or "").strip()
    command = str(getattr(args, "verify_command", "") or "").strip()
    targets = [str(item) for item in (getattr(args, "verify_tests", None) or [])]
    timeout = getattr(args, "verify_timeout", None)
    expect = int(getattr(args, "verify_expect_exit", 0) or 0)
    if argv_json:
        try:
            argv = json.loads(argv_json)
        except ValueError as exc:
            return None, f"--verify-argv is not valid JSON: {exc}"
        if not isinstance(argv, list):
            return None, "--verify-argv must be a JSON list of strings"
        declaration: dict[str, Any] = {"kind": "command", "argv": argv}
    elif command:
        declaration = {"kind": "command", "command": command}
    elif targets:
        declaration = {"kind": "tests", "targets": targets}
    elif kind:
        declaration = {"kind": kind}
    else:
        return None, ""
    if timeout is not None:
        declaration["timeout_s"] = float(timeout)
    declaration["expect_exit"] = expect
    return declaration, ""


def _plan_inputs(
    args: argparse.Namespace,
) -> tuple[dict[str, Any] | None, str]:
    """The document with the CLI's verification override applied, plus its source label."""
    loaded, problem = _load_document(args)
    if loaded is None:
        return None, problem
    document = loaded["document"]
    if not isinstance(document, dict):
        return None, "the transaction document must be a JSON object"
    override, problem = _verification_override(args)
    if problem:
        return None, problem
    if override is not None:
        document = dict(document)
        document["verification"] = override
    spec, problem = ET.parse_spec(document)
    if spec is None:
        return None, problem
    return {"spec": spec, "source": loaded["source"], "document": document}, ""


# --------------------------------------------------------------------------- #
# evidence                                                                      #
# --------------------------------------------------------------------------- #


def _plan_evidence(record: dict[str, Any], source: str) -> tuple[dict, ...]:
    """The evidence a plan/applied transaction stands on (never empty on success)."""
    items: list[dict] = [
        {
            "ref": f"edit {record.get('mode')} --tx {source} --workspace {record.get('workspace')}",
            "kind": "run",
            "note": f"{len(record.get('files') or [])} file(s), verdict {record.get('verdict')}, "
                    f"{record.get('duration_ms')} ms",
        }
    ]
    for item in record.get("files") or []:
        if not item.get("ok"):
            continue
        items.append(
            {
                "ref": str(item.get("abs_path") or item.get("path")),
                "kind": "file",
                "note": f"{item.get('strategy')} match at {item.get('matches')} occurrence(s); "
                        f"before {str(item.get('before_sha256'))[:12]}, "
                        f"after {str(item.get('after_sha256'))[:12]}",
            }
        )
    for entry in (record.get("snapshot") or {}).get("pre") or []:
        digest = str(entry.get("before_sha256"))[:12]
        items.append(
            {
                "ref": str(entry.get("pre_file")),
                "kind": "file",
                "note": f"pre-image of {entry.get('path')} "
                        f"({entry.get('bytes')} bytes, sha256 {digest})",
            }
        )
    verification = record.get("verification") or {}
    if verification.get("argv_line"):
        items.append(
            {
                "ref": str(verification["argv_line"]),
                "kind": "run",
                "note": f"the declared verification: executed "
                        f"{verification.get('executed')}, passed {verification.get('passed')}",
            }
        )
    if record.get("record_path"):
        items.append(
            {
                "ref": str(record["record_path"]),
                "kind": "file",
                "note": "the transaction record: per-file results, diff, verification, snapshot",
            }
        )
    return tuple(items)


def _undo_evidence(record: dict[str, Any]) -> tuple[dict, ...]:
    """What a restore stands on: the pre-image and the restored sha256 per file."""
    items: list[dict] = []
    for entry in (record.get("undo") or {}).get("restored") or []:
        items.append(
            {
                "ref": str(record.get("workspace", "")) + "/" + str(entry.get("path")),
                "kind": "file",
                "note": f"restored sha256 {str(entry.get('restored_sha256'))[:12]} == "
                        f"pre-apply {str(entry.get('expected_sha256'))[:12]} "
                        f"(byte_identical {entry.get('byte_identical')})",
            }
        )
    if record.get("record_path"):
        items.append(
            {"ref": str(record["record_path"]), "kind": "file",
             "note": "the record now carries data.undo (what was restored, with hashes)"}
        )
    if not items:
        items.append(
            {"ref": f"edit undo {record.get('tx_id')}", "kind": "run",
             "note": f"verdict {record.get('verdict')}, nothing restored"}
        )
    return tuple(items)


# --------------------------------------------------------------------------- #
# verbs                                                                         #
# --------------------------------------------------------------------------- #


def _plan_verb(args: argparse.Namespace, *, dry_run: bool) -> int:
    """``apply`` and ``dry-run``: one engine call, one exit code, one record."""
    loaded, problem = _plan_inputs(args)
    if loaded is None:
        return _refuse(EXIT_USAGE, problem, hint="skaldir edit dry-run --tx tx.json --json")
    record = ET.run_transaction(
        loaded["spec"],
        _workspace(args),
        state_dir=_state_dir(args),
        caps=_caps(args),
        dry_run=dry_run,
        context_lines=int(getattr(args, "context_lines", 3) or 0),
        keep_on_failure=bool(getattr(args, "keep_on_failure", False)),
        allow_near_duplicates=bool(getattr(args, "allow_near_duplicates", False)),
    )
    code = ET.exit_code_for(record)
    warnings: list[str] = list(record.get("warnings") or [])
    if record.get("diff_truncated"):
        warnings.append(
            "the diff in this payload was truncated to the diff cap; the full aggregate is "
            "in the record's diff.patch"
        )
    if code != 0:
        message, details = _refusal_message(record)
        details["verdict"] = record.get("verdict")
        details["tx_id"] = record.get("tx_id")
        _emit(args, record, evidence=_plan_evidence(record, loaded["source"]),
              warnings=warnings)
        if code == EXIT_VERIFICATION_FAILED:
            return _refuse(
                code, message, hint=_refusal_hint(record), details=details,
                next_command=f"skaldir edit show {record.get('tx_id')} --json",
                reason="the record names which files were restored",
            )
        return _refuse(code, message, hint=_refusal_hint(record), details=details)
    if record.get("verdict") == ET.VERDICT_APPLIED_UNVERIFIED:
        warnings.append(
            "no check executed: the change is in the tree but nothing verified it "
            "(declare a command or a test target)"
        )
    _emit(args, record, evidence=_plan_evidence(record, loaded["source"]), warnings=warnings,
          next_actions=(("skaldir edit undo " + str(record.get("tx_id")), "restore it"),)
          if record.get("record_path") else ())
    return code


def cmd_apply(args: argparse.Namespace) -> int:
    """``skaldir edit apply`` — plan, publish, verify (and roll back on a failed check)."""
    return _plan_verb(args, dry_run=False)


def cmd_dry_run(args: argparse.Namespace) -> int:
    """``skaldir edit dry-run`` — the diff, the failure reason, and nothing written."""
    return _plan_verb(args, dry_run=True)


def cmd_undo(args: argparse.Namespace) -> int:
    """``skaldir edit undo`` — restore the transaction's files byte-identically."""
    state = _state_dir(args)
    tx_id = str(getattr(args, "tx_id", "") or "last")
    record = ET.undo_transaction(
        tx_id, state_dir=state, caps=_caps(args), force=bool(getattr(args, "force", False))
    )
    code = ET.exit_code_for(record)
    if code != 0:
        message, details = _refusal_message(record)
        _emit(args, record, evidence=_undo_evidence(record))
        if code == EXIT_NOT_FOUND:
            return _refuse(
                code, message, hint="list the records with `skaldir edit show --list --json`",
                details=details,
            )
        return _refuse(code, message, hint=_refusal_hint(record), details=details)
    _emit(args, record, evidence=_undo_evidence(record))
    return code


def cmd_show(args: argparse.Namespace) -> int:
    """``skaldir edit show`` — one record, or the list of records in this state directory."""
    state = _state_dir(args)
    if bool(getattr(args, "list_records", False)):
        transactions = ET.list_transactions(state)
        payload = {
            "state_dir": str(state),
            "count": len(transactions),
            "transactions": transactions,
            "caps": _caps(args).as_dict(),
        }
        if _is_json_out(args):
            dump(payload)
        else:
            print(f"edit records in {state}: {len(transactions)}")
            for item in transactions:
                print(
                    f"  {item['tx_id']}  {item['verdict']:<18} "
                    f"{len(item['files'])} file(s)  {item['created_at']}"
                )
        _publish(
            payload,
            evidence=tuple(
                {"ref": str(item.get("record_path") or item["tx_id"]), "kind": "file",
                 "note": f"{item['verdict']} · {len(item['files'])} file(s)"}
                for item in transactions
            )
            or ({"ref": str(state), "kind": "file", "note": "no transaction records yet"},),
        )
        return EXIT_OK
    tx_id = str(getattr(args, "tx_id", "") or "last")
    resolved = ET.resolve_tx_id(state, tx_id)
    record = ET.load_transaction(state, resolved)
    if record is None:
        message = f"no transaction {tx_id!r} in {state}"
        payload = {
            "schema_version": ET.SCHEMA_VERSION,
            "tx_id": tx_id,
            "verdict": ET.VERDICT_NOT_APPLICABLE,
            "reason": ET.REASON_NOT_FOUND,
            "message": message,
            "files": [],
            "state_dir": str(state),
        }
        _emit(args, payload)
        return _refuse(
            EXIT_NOT_FOUND, message,
            hint="list the records with `skaldir edit show --list --json`",
        )
    _emit(args, record, evidence=(
        {"ref": str(record.get("record_path") or resolved), "kind": "file",
         "note": f"{record.get('verdict')} · {len(record.get('files') or [])} file(s) · "
                 f"snapshot {(record.get('snapshot') or {}).get('dir') or '(none)'}"},
    ), next_actions=(
        (f"skaldir edit undo {resolved}", "restore exactly these files"),
    ) if record.get("in_tree") else ())
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser                                                                        #
# --------------------------------------------------------------------------- #


def _add_state_flags(parser: Any) -> None:
    """Where the transaction reads and writes: the workspace, its state, its runtime."""
    parser.add_argument("--workspace", default="",
                        help="the root every path in the document is resolved against "
                             "(default: the current directory)")
    parser.add_argument("--state", default="",
                        help="where records and snapshots live (default: <runtime>/edit)")
    parser.add_argument("--runtime", default="",
                        help="the Skaldir runtime directory (default: the installation's)")


def _add_cap_flags(parser: Any) -> None:
    """The caps, overridable per call (the environment is the other override)."""
    default_file_mb = ET.EditCaps.max_file_bytes // (1024 * 1024)
    default_tx_mb = ET.EditCaps.max_tx_bytes // (1024 * 1024)
    default_diff_kb = ET.EditCaps.max_diff_bytes // 1024
    parser.add_argument("--max-file-mb", type=int, default=None,
                        help=f"one file, in MiB (default {default_file_mb})")
    parser.add_argument("--max-tx-mb", type=int, default=None,
                        help=f"the whole transaction's bytes, in MiB (default {default_tx_mb})")
    parser.add_argument("--max-files", type=int, default=None,
                        help=f"files in one transaction (default {ET.EditCaps.max_files})")
    parser.add_argument("--max-edits", type=int, default=None,
                        help=f"edits in one transaction (default {ET.EditCaps.max_edits})")
    parser.add_argument("--max-diff-kb", type=int, default=None,
                        help=f"the diff inside the payload, in KiB (default {default_diff_kb})")


def _add_verification_flags(parser: Any) -> None:
    """The declared check that runs *after* the publish (the caller declares it)."""
    parser.add_argument("--verify", default="", choices=("", *ET.VERIFY_KINDS),
                        help="the check to run after apply (default: the document's, else "
                             "'syntax')")
    parser.add_argument("--verify-command", default="",
                        help="a command string for --verify command (split without a shell)")
    parser.add_argument("--verify-argv", default="",
                        help="a JSON list of strings: the argv to run (no shell)")
    parser.add_argument("--verify-tests", nargs="+", default=None, metavar="TARGET",
                        help="pytest targets, run through this interpreter (kind 'tests')")
    parser.add_argument("--verify-timeout", type=float, default=None,
                        help=f"seconds for the verification command "
                             f"(default {ET.EditCaps.verify_timeout_s:g})")
    parser.add_argument("--verify-expect-exit", type=int, default=0,
                        help="the exit code that means 'verified' (default 0)")


def _add_plan_args(parser: Any) -> None:
    """Everything ``apply`` and ``dry-run`` share (they are the same call, one writes)."""
    parser.add_argument("--tx", default="",
                        help="the transaction document: a file path, or '-' for stdin")
    parser.add_argument("--tx-json", default="",
                        help="the transaction document inline as JSON (instead of --tx)")
    parser.add_argument("--context-lines", type=int, default=3,
                        help="diff context lines (default 3)")
    parser.add_argument("--keep-on-failure", action="store_true",
                        help="keep the change when the verification fails (the transaction "
                             "is then reported applied_unverified, never verified)")
    parser.add_argument("--allow-near-duplicates", action="store_true",
                        help="accept a needle whose whitespace/indentation-normalised form "
                             "appears elsewhere (strict transactions refuse it)")


def _add_text_flag(parser: Any) -> None:
    """``--text`` asks for the human rendering (the product adds ``--format`` itself)."""
    parser.add_argument("--text", action="store_true",
                        help="human-readable output instead of the JSON record")


def _add_edit_verbs(sub: Any) -> Any:
    """The four verbs, defined once (the product group and the standalone parser share them)."""
    p_apply = sub.add_parser("apply", help=APPLY_HELP,
                             description="Plan the whole transaction, publish every file, then "
                                         "run the declared check. A failed check rolls the "
                                         "transaction back unless --keep-on-failure is given.")
    _add_plan_args(p_apply)
    _add_verification_flags(p_apply)
    _add_cap_flags(p_apply)
    _add_state_flags(p_apply)
    _add_text_flag(p_apply)
    p_apply.set_defaults(func=cmd_apply, command_path="edit.apply")

    p_dry = sub.add_parser("dry-run", help=DRY_RUN_HELP,
                           description="Resolve every edit against the current tree, print the "
                                       "per-file diff and the exact failure reason "
                                       "(not_found / ambiguous_match / "
                                       "would_change_other_occurrences / cascade_edit), and "
                                       "write nothing. Exits with the code apply would exit "
                                       "with for the same document.")
    _add_plan_args(p_dry)
    _add_verification_flags(p_dry)
    _add_cap_flags(p_dry)
    _add_state_flags(p_dry)
    _add_text_flag(p_dry)
    p_dry.set_defaults(func=cmd_dry_run, command_path="edit.dry-run")

    p_undo = sub.add_parser("undo", help=UNDO_HELP,
                            description="Restore the files a transaction touched from its "
                                        "snapshot and verify each restore byte-for-byte. "
                                        "Refuses when a file changed after the apply unless "
                                        "--force is given.")
    p_undo.add_argument("tx_id", nargs="?", default="last",
                        help="the transaction id, or 'last' (default: last)")
    p_undo.add_argument("--force", action="store_true",
                        help="restore even when a touched file changed after the apply")
    _add_cap_flags(p_undo)
    _add_state_flags(p_undo)
    _add_text_flag(p_undo)
    p_undo.set_defaults(func=cmd_undo, command_path="edit.undo")

    p_show = sub.add_parser("show", help=SHOW_HELP,
                            description="One transaction record — verdict, per-file results, "
                                        "diff, verification, snapshot, undo — or the list of "
                                        "records in this state directory.")
    p_show.add_argument("tx_id", nargs="?", default="last",
                        help="the transaction id, or 'last' (default: last)")
    p_show.add_argument("--list", dest="list_records", action="store_true",
                        help="list every record in the state directory instead")
    _add_cap_flags(p_show)
    _add_state_flags(p_show)
    _add_text_flag(p_show)
    p_show.set_defaults(func=cmd_show, command_path="edit.show")

    for parser in (p_apply, p_dry, p_undo, p_show):
        share_common_flags(parser)
    return sub


def build_edit_parser(subparsers: Any) -> Any:
    """Add the ``edit`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "edit",
        help=GROUP_HELP,
        description=(
            "Edit transactions: a batch of targeted file edits that either happens "
            "completely or not at all. Every edit is resolved against a staged copy first "
            "(fuzzy-tolerant, one unique occurrence, refusals instead of guesses), a "
            "dry-run prints the diffs and the failure reasons without writing, apply "
            "publishes the files and then runs the check the caller declared, and undo "
            "restores the pre-images byte-identically. Workspace containment, binary "
            "refusals and size caps come from tools_core.sandbox; the matching ladder "
            "from tools_core.patch_tool."
        ),
    )
    sub = parser.add_subparsers(dest="edit_command", required=True)
    _add_edit_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """Wire the group in once: ``register_cli(subparsers) -> parser`` (idempotent)."""
    sub = as_subparsers(subparsers)
    existing = getattr(sub, "choices", None) or {}
    if "edit" in existing:
        install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
        return existing["edit"]
    parser = build_edit_parser(sub)
    install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir edit", description=GROUP_HELP)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_edit_verbs(sub)
    return parser


GROUP = "edit"


def _argv_without_group(argv: list[str] | None) -> list[str]:
    """``python -m skaldir.edit_cli edit dry-run …`` and ``… dry-run …`` both work."""
    tokens = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    return tokens[1:] if tokens[:1] == [GROUP] else tokens


def main(argv: list[str] | None = None) -> int:
    """``skaldir edit …`` — returns the exit code, never raises for a request.

    Standalone the group installs its own contract entries and runs through the product's
    ``run_command`` when that module is present, so ``--json`` emits the **same one
    envelope** here as it does once the group is wired into ``cli.py``.
    """
    tokens = _argv_without_group(argv)
    install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
    parser = build_parser()
    try:
        parsed = parser.parse_args(tokens)
    except SystemExit as exc:  # argparse already chose the code (--help exits 0)
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(parsed, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is not None and hasattr(_CONTRACT, "run_command"):
        return int(_CONTRACT.run_command(parsed, handler))
    return int(handler(parsed))


# --------------------------------------------------------------------------- #
# the CLI contract                                                              #
# --------------------------------------------------------------------------- #

_RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "tx_id": {"type": "string"},
        "created_at": {"type": "string"},
        "workspace": {"type": "string"},
        "mode": {"type": "string", "enum": ["apply", "dry_run"]},
        "verdict": {"type": "string", "enum": list(ET.VERDICTS)},
        "applied": {"type": "boolean"},
        "in_tree": {"type": "boolean"},
        "verified": {"type": ["boolean", "null"]},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "abs_path": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "message": {"type": "string"},
                    "strategy": {"type": "string"},
                    "score": {"type": "number"},
                    "matches": {"type": "integer"},
                    "occurrence_lines": {"type": "array", "items": {"type": "integer"}},
                    "before_sha256": {"type": "string"},
                    "after_sha256": {"type": "string"},
                    "bytes_before": {"type": "integer"},
                    "bytes_after": {"type": "integer"},
                    "diff": {"type": "string"},
                    "diff_truncated": {"type": "boolean"},
                },
                "required": ["path", "ok", "reason"],
            },
        },
        "diff": {"type": "string"},
        "diff_truncated": {"type": "boolean"},
        "verification": {"type": "object"},
        "rollback": {"type": ["object", "null"]},
        "undo": {"type": ["object", "null"]},
        "record_path": {"type": "string"},
        "caps": {"type": "object"},
        "caps_hit": {"type": "array", "items": {"type": "string"}},
        "limits": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["schema_version", "verdict", "files"],
}

_SHOW_DATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "transactions": {"type": "array"},
        "count": {"type": "integer"},
        "tx_id": {"type": "string"},
        "verdict": {"type": "string"},
        "files": {"type": "array"},
        "record_path": {"type": "string"},
    },
    "additionalProperties": True,
}

_CAP_NOTE = (
    "every cap is declared in data.caps and overridable through "
    "SKALDIR_EDIT_MAX_FILE_BYTES / _MAX_TX_BYTES / _MAX_FILES / _MAX_EDITS / "
    "_MAX_DIFF_BYTES (the legacy GODSV_* spelling is accepted)"
)


def _entries() -> dict[str, Any]:
    """The four ``COMMANDS`` entries, built from the contract module itself."""
    make = getattr(_CONTRACT, "_C", None)
    evidence = getattr(_CONTRACT, "_ev", None)
    if make is None or evidence is None:  # pragma: no cover — a contract-less build
        return {}
    engine = evidence("src/skaldir/edit_tx.py")
    cli = evidence("src/skaldir/edit_cli.py")
    ladder = evidence("src/skaldir/tools_core/patch_tool.py", "code",
                      "the matching ladder this layer runs the edits through")
    return {
        "edit.apply": make(
            "ENFORCE", (engine, cli, ladder),
            "plan and publish an atomic multi-file edit batch, then run the declared check",
            exit_codes=(0, 1, 2, 3, 5, 6, 7),
            mutates=True,
            data_schema=_RECORD_SCHEMA,
            notes="all-or-nothing: a refused plan publishes nothing; a failed verification "
                  "rolls back and exits 2; " + _CAP_NOTE,
        ),
        "edit.dry-run": make(
            "ENFORCE", (engine, cli, ladder),
            "resolve every edit, print the diffs and the exact failure reason, write nothing",
            exit_codes=(0, 1, 2, 3, 5, 6, 7),
            data_schema=_RECORD_SCHEMA,
            notes="exits with the code apply would exit with for the same document; failures "
                  "are not_found / ambiguous_match / would_change_other_occurrences / "
                  "cascade_edit; " + _CAP_NOTE,
        ),
        "edit.undo": make(
            "PERSIST", (engine, cli),
            "restore every file of a transaction from its snapshot, byte-identically",
            exit_codes=(0, 1, 3, 5, 6, 7),
            mutates=True,
            data_schema=_RECORD_SCHEMA,
            notes="refuses (6) when a touched file changed after the apply unless --force is "
                  "given; every restore carries byte_identical + both sha256s",
        ),
        "edit.show": make(
            "PERSIST", (engine, cli),
            "one transaction record (verdict, diffs, verification, snapshot, undo) or the list",
            exit_codes=(0, 1, 5, 7),
            data_schema=_SHOW_DATA_SCHEMA,
            notes="--list returns {count, transactions[]}; a single record is returned as-is "
                  "(data.undo.undone_at is what marks it undone)",
        ),
    }


CONTRACT_ENTRIES: dict[str, Any] = _entries()

CONTRACTS_SNIPPET = '''\
# src/skaldir/cli_contract.py — the `edit` group's entries (register_cli installs these
# itself, so pasting is optional). The data schemas live in edit_cli (record/show).
"edit.apply": _C("ENFORCE", (_ev("src/skaldir/edit_tx.py"),), "atomic multi-file edit "
                "batch, then the declared verification",
                exit_codes=(0, 1, 2, 3, 5, 6, 7), mutates=True,
                data_schema={"type": "object", "additionalProperties": True},
                notes="all-or-nothing; a failed verification rolls back and exits 2"),
"edit.dry-run": _C("ENFORCE", (_ev("src/skaldir/edit_tx.py"),), "the diffs and the exact "
                   "failure reasons, nothing written",
                   exit_codes=(0, 1, 2, 3, 5, 6, 7),
                   data_schema={"type": "object", "additionalProperties": True},
                   notes="exits with the code apply would exit with"),
"edit.undo": _C("PERSIST", (_ev("src/skaldir/edit_tx.py"),), "restore a transaction's files "
               "byte-identically", exit_codes=(0, 1, 3, 5, 6, 7), mutates=True,
               data_schema={"type": "object", "additionalProperties": True},
               notes="refuses when a touched file changed after the apply (--force overrides)"),
"edit.show": _C("PERSIST", (_ev("src/skaldir/edit_tx.py"),), "one record, or the list",
                exit_codes=(0, 1, 5, 7),
                data_schema={"type": "object", "additionalProperties": True},
                notes="--list returns {count, transactions[]}"),
'''


__all__ = [
    "CONTRACT_ENTRIES",
    "CONTRACTS_SNIPPET",
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "build_edit_parser",
    "build_parser",
    "cmd_apply",
    "cmd_dry_run",
    "cmd_show",
    "cmd_undo",
    "main",
    "register_cli",
]


if __name__ == "__main__":  # pragma: no cover — a standalone run, not a product path
    raise SystemExit(main())
