"""``skaldir permission …`` and ``skaldir approval …`` — the two surfaces of the policy.

Two groups in one module, because they are two questions about the same object:

``permission show | explain | set``
    **What am I allowed to do, and is that the same thing the sandbox enforces?** ``show``
    prints the effective :class:`skaldir.permission_profile.PermissionProfile` — the fields the
    argv is built from *and* the block the agent is told; ``explain`` adds the exact bwrap /
    podman argv, the enforcement matrix (which field is enforced by what, and which is
    honestly *unenforced* on this host) and the two hashes side by side, so the non-drift
    claim is checkable from the CLI instead of believed; ``set`` changes one field and reports
    the hash it moved.
``approval list | allow | deny | check | run``
    **May this command run, on whose authority, and what is on the record?** ``list`` prints
    the persisted rules, the banned prefixes and the live grants; ``allow``/``deny`` answer a
    prompt (with the turn/session/rule scope); ``check`` is the judge — ``--dry-run`` answers
    "allowed, prompted or forbidden" for a command string without running anything; ``run``
    is the enforcement path, which judges *and* runs the command inside the profile.

Exit codes — the product CLI's :mod:`skaldir.cli_contract` vocabulary:

    0   ok                  shown / explained / set / judged-allowed / the command ran clean
    1   usage               argparse refused the call, or an unknown field/scope
    2   verification failed the command ran and returned non-zero
    3   blocked             a prompt-required command with no interactive user, a forbidden
                            command, a banned prefix, or a sandbox that is not available
    5   not found           an unknown profile name
    7   internal            a broken policy file, an unreadable runtime, a bug

The group is added by the product CLI through one call (this module never edits ``cli.py``),
and that call installs the group's contract entries, so wiring the group cannot forget them::

    from .approval_cli import register_cli
    register_cli(sub)
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from . import approval as A
from . import permission_profile as P
from .cli_shared import (
    blocked,
    dump,
    envelope_mode,
    install_contract_entries,
    not_found,
    product_contract,
    runtime_dir,
    share_common_flags,
)

#: Resolved once, like every group resolves its own copy of the product envelope.
_PRODUCT = product_contract()

EXIT_OK = int(getattr(_PRODUCT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_PRODUCT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_PRODUCT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_PRODUCT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_PRODUCT, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_PRODUCT, "EXIT_INTERNAL", 7))

GROUP_HELP = "the permission profile and the approval ladder (one policy, two surfaces)"
SHOW_HELP = "the effective permission profile: fields, identity hash and the prompt block"
EXPLAIN_HELP = "where each field comes from, the exact sandbox argv, and both hashes"
SET_HELP = "change one field of a profile and report the hash it moved"
LIST_HELP = "the persisted rules, the banned prefixes and the live grants"
ALLOW_HELP = "answer a prompt with yes (turn / session / a persisted rule)"
DENY_HELP = "answer a prompt with no, and a reason the agent can act on"
CHECK_HELP = "would this command be allowed, prompted or forbidden? (runs nothing)"
RUN_HELP = "judge the command, then run it inside the profile when it is allowed"

#: ``permission set`` keys. ``+`` appends to a list field instead of replacing it.
SET_KEYS: tuple[str, ...] = (
    "name",
    "workspace",
    "network",
    "read-policy",
    "writable",
    "readable",
    "protected",
    "write-deny-glob",
    "allow-command",
    "timeout",
    "output-max-bytes",
    "max-memory-bytes",
    "max-processes",
    "sandbox-backend",
)

_LIST_KEYS: dict[str, str] = {
    "writable": "writable_paths",
    "readable": "readable_roots",
    "protected": "protected_paths",
    "write-deny-glob": "write_deny_globs",
}
_CAP_KEYS: dict[str, str] = {
    "timeout": "timeout_seconds",
    "output-max-bytes": "output_max_bytes",
    "max-memory-bytes": "max_memory_bytes",
    "max-processes": "max_processes",
}


# --------------------------------------------------------------------------- #
# envelope + shared plumbing                                                    #
# --------------------------------------------------------------------------- #


def _publish(
    payload: Any,
    *,
    evidence: Iterable[dict] = (),
    warnings: Iterable[str] = (),
    next_actions: Iterable[tuple[str, str]] = (),
) -> None:
    """Hand this verb's payload to the product envelope, when there is one.

    It never raises: a missing or broken contract module must not turn a judgement into an
    internal error. The judgement is the deliverable; the envelope is transport.
    """
    if _PRODUCT is None:
        return
    try:
        _PRODUCT.set_data(payload)
        for item in evidence:
            _PRODUCT.add_evidence(
                str(item["ref"]),
                str(item.get("kind") or "verification"),
                str(item.get("note", "")),
            )
        for line in warnings:
            _PRODUCT.warn(str(line))
        for command, reason in next_actions:
            _PRODUCT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _refuse(code: int, message: str, *, hint: str = "", details: Any = None) -> int:
    """Exit with ``code`` through the contract, and say why when we are the whole CLI.

    The product driver builds the envelope and prints it, so printing there would duplicate the
    message; standalone (``python -m skaldir.approval_cli …``) nothing else would ever say why
    the call was refused, and a silent exit 3 is the worst possible answer.
    """
    result = int(code)
    if code == EXIT_BLOCKED:
        result = blocked(message, hint=hint, reason=message, details=details)
    elif code == EXIT_NOT_FOUND:
        result = not_found(message, hint=hint, details=details)
    elif code == EXIT_VERIFICATION_FAILED and _PRODUCT is not None:
        try:
            result = int(_PRODUCT.verification_failed(message, hint=hint, details=details))
        except Exception:  # noqa: BLE001 — the verdict outlives a broken envelope
            result = EXIT_VERIFICATION_FAILED
    elif _PRODUCT is not None and hasattr(_PRODUCT, "fail"):
        try:
            result = int(_PRODUCT.fail(code, message, hint=hint, details=details))
        except Exception:  # noqa: BLE001 — the verdict outlives a broken envelope
            result = int(code)
    if not envelope_mode():
        print(f"skaldir: {message}", file=sys.stderr)
        if hint:
            print(f"  hint: {hint}", file=sys.stderr)
    return result


def _emit(args: argparse.Namespace, payload: Any, human: Sequence[str]) -> int:
    """One report: the envelope's data when the driver runs us, else JSON, else text."""
    if envelope_mode():
        return EXIT_OK
    if bool(getattr(args, "json", False)):
        dump(payload)
        return EXIT_OK
    for line in human:
        if line:
            print(line)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# inputs                                                                        #
# --------------------------------------------------------------------------- #


def _workspace(args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "workspace", "") or "")
    return Path(explicit).expanduser().resolve() if explicit else Path.cwd().resolve()


def _profile_name(args: argparse.Namespace) -> str:
    return str(getattr(args, "profile", "") or "default")


def _resolve_profile(args: argparse.Namespace, runtime: Path) -> tuple[P.PermissionProfile, str]:
    """The effective profile plus the path of the stored one (``""`` when none was stored)."""
    name = _profile_name(args)
    stored = P.load_profile(runtime, name)
    profile = P.effective_profile(runtime, _workspace(args), name=name)
    return profile, (str(P.profile_path(runtime, name)) if stored is not None else "")


def _env_value(token: str) -> str:
    from . import env

    return str(env.getenv(token) or "")


def _field_sources(
    profile: P.PermissionProfile,
    *,
    default: P.PermissionProfile,
    stored: P.PermissionProfile | None,
    stored_path: str,
) -> dict[str, str]:
    """For each field: which layer supplied the value — the resolution order, replayed.

    The layers are exactly the ones :func:`skaldir.permission_profile.effective_profile`
    applies (built-in default → stored profile → environment), so this answers "where did
    this come from" by construction instead of through a second, drifting bookkeeping path.
    """
    mine = profile.canonical_payload()
    base = default.canonical_payload()
    kept = stored.canonical_payload() if stored is not None else None
    out: dict[str, str] = {}
    for key, value in mine.items():
        token = P.ENV_FIELD_TOKENS.get(key, "")
        if token and _env_value(token) and value != base.get(key):
            out[key] = f"environment override {token}"
        elif kept is not None and value == kept.get(key):
            out[key] = (
                f"stored profile {stored_path}" if value != base.get(key) else "built-in default"
            )
        elif value != base.get(key):
            out[key] = "built-in default + the workspace this call runs in"
        else:
            out[key] = "built-in default"
    return out


def _effective_without_env(args: argparse.Namespace, runtime: Path) -> P.PermissionProfile:
    """The stored-or-default profile, i.e. the built-in layer this report compares against."""
    return P.effective_profile(runtime, _workspace(args), name=_profile_name(args), use_env=False)


def _profile_report(
    profile: P.PermissionProfile, *, stored_path: str, sources: dict[str, str], probe: bool
) -> dict[str, Any]:
    """The one payload ``permission show``/``explain`` are built from."""
    argv = profile.sandbox_argv("true")
    prompt = profile.prompt_block()
    in_argv = P.permission_profile_hash_from_argv(argv)
    in_prompt = P.permission_profile_hash_from_prompt(prompt)
    payload: dict[str, Any] = {
        "profile": profile.to_dict(),
        "identity_sha256": profile.identity_sha256,
        "stored_path": stored_path,
        "sources": sources,
        "enforcement": profile.enforcement_matrix(),
        "unenforced": profile.unenforced_fields(),
        # Gap 11 (MVP/84): the primitives are Linux-only, so the report says what is really
        # enforced *here* — enforced/degraded/unavailable per primitive, with the reason.
        "platform_coverage": profile.platform_coverage(),
        "platform_verdict": profile.platform_verdict(),
        "prompt_block": prompt,
        "sandbox_argv": argv,
        "sandbox_argv_line": P.shlex_join(argv),
        "podman_argv": profile.podman_argv("true"),
        "podman_argv_line": P.shlex_join(profile.podman_argv("true")),
        "hash_in_argv": in_argv,
        "hash_in_prompt": in_prompt,
        "hashes_agree": bool(
            in_argv and in_prompt and in_argv == in_prompt == profile.identity_sha256
        ),
    }
    if probe:
        payload["probe"] = P.probe_backend(profile)
    return payload


def _render_profile(payload: dict[str, Any], *, full: bool) -> list[str]:
    profile = payload["profile"]
    commands = " ".join(" ".join(item) for item in profile["allowed_subprocess_prefixes"])
    lines = [
        f"profile {profile['name']}  sha256 {payload['identity_sha256']}",
        f"  workspace      {profile['workspace']}",
        f"  read policy    {profile['read_policy']}"
        f"  {' '.join(profile['readable_roots']) or '(/)'}",
        f"  writable       {' '.join(profile['writable_paths']) or '(nothing)'}",
        f"  protected      {' '.join(profile['protected_paths']) or '(none)'}",
        f"  network        {profile['network']}",
        f"  commands       {commands or '(any)'}",
        f"  limits         timeout {profile['caps']['timeout_seconds']:g}s, output "
        f"{profile['caps']['output_max_bytes']} bytes",
        f"  backend        {profile['sandbox_backend']}",
        f"  stored at      {payload['stored_path'] or '(built-in default - nothing stored)'}",
    ]
    if not full:
        return lines
    lines.append("")
    lines.append("enforcement (one row per field; 'unenforced' is reported, never hidden):")
    lines += [
        f"  {row['field']:28} {row['enforced_by']:14} {row['value'][:44]}"
        for row in payload["enforcement"]
    ]
    verdict = payload.get("platform_verdict") or {}
    if verdict:
        lines.append("")
        lines.append(
            "enforcement primitives on this platform "
            f"({verdict.get('platform', {}).get('platform', '?')}): coverage "
            f"{verdict.get('verdict')} — one row per primitive, with the reason when it is not "
            "'enforced':"
        )
        lines += [
            f"  {row['primitive']:14} {row['status']:11} {row['reason'][:70]}"
            for row in payload.get("platform_coverage", [])
        ]
    lines.append("")
    lines.append("field sources:")
    lines += [f"  {key:28} {value}" for key, value in sorted(payload["sources"].items())]
    lines.append("")
    lines.append("sandbox argv (the enforced half):")
    lines.append(f"  {payload['sandbox_argv_line']}")
    lines.append("")
    lines.append("prompt block (the told half):")
    lines += [f"  {line}" for line in payload["prompt_block"].splitlines()]
    lines.append("")
    lines.append("non-drift check:")
    lines.append(f"  hash in the argv   {payload['hash_in_argv'] or '(missing)'}")
    lines.append(f"  hash in the block  {payload['hash_in_prompt'] or '(missing)'}")
    lines.append(f"  agree              {payload['hashes_agree']}")
    probe = payload.get("probe")
    if probe:
        lines.append("")
        lines.append(
            f"sandbox probe: {probe['backend'] or '(none)'} available={probe['available']} "
            f"{probe['probe_output'][:80]}"
        )
    return lines


# --------------------------------------------------------------------------- #
# permission verbs                                                              #
# --------------------------------------------------------------------------- #


def _split_list(value: str) -> tuple[str, ...]:
    """A comma-separated list value, or a JSON array when it starts with ``[``."""
    text = value.strip()
    if text.startswith("["):
        loaded = json.loads(text)
        if not isinstance(loaded, list):
            raise ValueError(f"a JSON list value must be a list, got {text!r}")
        return tuple(str(item) for item in loaded)
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _settings_from(
    args: argparse.Namespace, base: P.PermissionProfile
) -> tuple[dict[str, Any], list[str]]:
    """``--set KEY=VALUE`` pairs → the ``with_changes`` keyword arguments, plus a readable log.

    An appending key (``+``) and a single cap key both seed from the **stored** profile, not
    from this call: ``--set writable+=/b`` must extend the declared set, and ``--set timeout=5``
    must not reset the other caps to their defaults on the way past.
    """
    changes: dict[str, Any] = {}
    notes: list[str] = []
    for item in getattr(args, "set", None) or []:
        raw = str(item)
        key, sep, value = raw.partition("=")
        if not sep:
            raise ValueError(f"--set wants KEY=VALUE, got {raw!r}")
        key, value = key.strip(), value.strip()
        append = key.endswith("+")
        if append:
            key = key[:-1]
        if key not in SET_KEYS:
            raise ValueError(f"unknown field {key!r} (known: {', '.join(SET_KEYS)})")

        if key in _LIST_KEYS:
            field_name = _LIST_KEYS[key]
            entries = _split_list(value)
            if append:
                seed = changes.get(field_name)
                if seed is None:
                    seed = tuple(getattr(base, field_name))
                entries = tuple(seed) + entries
            changes[field_name] = entries
            notes.append(f"{key}{'+' if append else ''} = {list(entries)}")
        elif key in _CAP_KEYS:
            cap = _CAP_KEYS[key]
            caps = dict(changes.get("caps") or base.caps.to_dict())
            caps[cap] = float(value) if cap == "timeout_seconds" else int(value)
            changes["caps"] = caps
            notes.append(f"{key} = {caps[cap]}")
        elif key == "allow-command":
            prefixes = tuple(
                tuple(shlex.split(part)) for part in value.split(";") if part.strip()
            )
            if append:
                seed = changes.get("allowed_subprocess_prefixes")
                if seed is None:
                    seed = tuple(base.allowed_subprocess_prefixes)
                prefixes = tuple(seed) + prefixes
            changes["allowed_subprocess_prefixes"] = prefixes
            notes.append(f"allow-command{' +' if append else ''} = {[list(i) for i in prefixes]}")
        elif key == "read-policy":
            if value not in P.READ_POLICIES:
                raise ValueError(f"read-policy must be one of {', '.join(P.READ_POLICIES)}")
            changes["read_policy"] = value
            notes.append(f"read-policy = {value}")
        elif key == "network":
            if value not in P.NETWORK_MODES:
                raise ValueError(f"network must be one of {', '.join(P.NETWORK_MODES)}")
            changes["network"] = value
            notes.append(f"network = {value}")
        elif key == "sandbox-backend":
            if value not in P.BACKENDS:
                raise ValueError(f"sandbox-backend must be one of {', '.join(P.BACKENDS)}")
            changes["sandbox_backend"] = value
            notes.append(f"sandbox-backend = {value}")
        else:
            changes[key] = value
            notes.append(f"{key} = {value}")
    return changes, notes


def cmd_permission_show(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    profile, stored_path = _resolve_profile(args, runtime)
    payload = _profile_report(
        profile,
        stored_path=stored_path,
        sources=_field_sources(
            profile,
            default=_effective_without_env(args, runtime),
            stored=P.load_profile(runtime, profile.name),
            stored_path=stored_path,
        ),
        probe=bool(getattr(args, "probe", False)),
    )
    _publish(
        payload,
        evidence=(
            {
                "ref": f"profile:{profile.name}",
                "kind": "file",
                "note": f"permission profile sha256 {profile.identity_sha256}",
            },
        ),
    )
    return _emit(args, payload, _render_profile(payload, full=False))


def cmd_permission_explain(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    profile, stored_path = _resolve_profile(args, runtime)
    payload = _profile_report(
        profile,
        stored_path=stored_path,
        sources=_field_sources(
            profile,
            default=_effective_without_env(args, runtime),
            stored=P.load_profile(runtime, profile.name),
            stored_path=stored_path,
        ),
        probe=True,
    )
    warnings: list[str] = []
    if payload["unenforced"]:
        warnings.append(
            "not enforced on this host (reported, not hidden): " + ", ".join(payload["unenforced"])
        )
    if not payload["hashes_agree"]:
        warnings.append(
            "the profile hash in the sandbox argv differs from the one in the prompt block"
        )
    _publish(
        payload,
        evidence=(
            {
                "ref": "src/skaldir/permission_profile.py",
                "kind": "code",
                "note": "the one object both consumers are rendered from",
            },
            {
                "ref": f"profile:{profile.name}",
                "kind": "file",
                "note": f"sha256 {profile.identity_sha256}",
            },
        ),
        warnings=warnings,
    )
    return _emit(args, payload, _render_profile(payload, full=True))


def cmd_permission_set(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    name = _profile_name(args)
    before = P.effective_profile(runtime, _workspace(args), name=name)
    try:
        changes, notes = _settings_from(args, before)
    except (ValueError, json.JSONDecodeError) as exc:
        return _refuse(
            EXIT_USAGE, f"permission set: {exc}", hint="skaldir permission set --help"
        )
    if not changes:
        return _refuse(
            EXIT_USAGE,
            "permission set: nothing to change (pass --set KEY=VALUE, repeatable)",
            hint="skaldir permission set --help",
        )
    if "name" in changes and str(changes["name"]) != name:
        name = str(changes["name"])
    try:
        after = before.with_changes(name=name, **changes)
    except (ValueError, TypeError) as exc:
        return _refuse(EXIT_USAGE, f"permission set: {exc}")
    path = P.save_profile(after, runtime)
    moved = before.identity_sha256 != after.identity_sha256
    payload = {
        "applied": notes,
        "path": str(path),
        "profile": after.to_dict(),
        "previous_sha256": before.identity_sha256,
        "identity_sha256": after.identity_sha256,
        "hash_moved": moved,
        "changed_fields": before.diff(after),
        "prompt_block": after.prompt_block(),
        "sandbox_argv": after.sandbox_argv("true"),
    }
    _publish(
        payload,
        evidence=({"ref": str(path), "kind": "file", "note": f"sha256 {after.identity_sha256}"},),
        warnings=(
            []
            if moved
            else ["the profile was written but its identity hash did not move — nothing was set"]
        ),
    )
    human = [
        f"profile {after.name}: {after.identity_sha256}",
        *[f"  {line}" for line in notes],
        f"  written to {path}",
        f"  hash {'moved' if moved else 'unchanged'}: {before.identity_sha256} -> "
        f"{after.identity_sha256}",
    ]
    return _emit(args, payload, human)


# --------------------------------------------------------------------------- #
# approval verbs                                                                #
# --------------------------------------------------------------------------- #


def suggested_commands(verdict: A.Verdict) -> list[tuple[str, str]]:
    """The copy-pasteable follow-ups of a block, as ``(command, reason)`` pairs.

    ``what_would_allow`` is written for a human ("allow it once, for the next turn: …"), and a
    ``next_action`` is a command: only the lines that carry a real ``skaldir …`` command become
    one. Suggesting prose as if it were a command is how a follow-up list becomes noise.
    """
    out: list[tuple[str, str]] = []
    for line in verdict.what_would_allow():
        _label, separator, tail = line.partition(": ")
        command = (tail if separator else line).strip()
        if command.startswith("skaldir "):
            out.append((command, "run this to change the verdict"))
    return out


def _command_argument(parser: Any, help_text: str) -> None:
    """The command of a verb, as the tokens that follow it (flags included).

    ``REMAINDER`` on purpose: ``skaldir approval run pytest -q`` is how a user types it, and a
    ``nargs="+"`` positional would refuse ``-q`` as an unknown option. The trade-off is
    documented in the help: options belong *before* the command.
    """
    parser.add_argument("command", nargs=argparse.REMAINDER, help=help_text)


def _command_of(args: argparse.Namespace, verb: str) -> str:
    tokens = [str(item) for item in getattr(args, "command", None) or []]
    if not tokens:
        raise ValueError(f"{verb} needs a command")
    return " ".join(tokens)


def _persisting(args: argparse.Namespace) -> bool:
    return not bool(getattr(args, "no_persist", False))


def _store_for(runtime: Path, args: argparse.Namespace) -> A.RuleStore:
    return A.RuleStore(
        runtime=runtime, profile=_profile_name(args), persist=_persisting(args)
    )


def _session_id(args: argparse.Namespace) -> str:
    """The session a grant is filed under. ``default`` when the caller named none.

    Without a default, ``approval allow --scope session`` would have nowhere to persist to and
    the very next ``approval check`` would ask again — a "session" grant that cannot survive the
    next command is not a scoped grant, it is a no-op.
    """
    return str(getattr(args, "session", "") or "") or "default"


def _ledger_for(runtime: Path, args: argparse.Namespace, store: A.RuleStore) -> A.GrantLedger:
    return A.GrantLedger(
        runtime=runtime,
        session=_session_id(args),
        banned=store.banned,
        persist=_persisting(args),
    )


def _engine_for(args: argparse.Namespace, runtime: Path) -> A.ApprovalEngine:
    store = _store_for(runtime, args)
    ledger = _ledger_for(runtime, args, store)
    return A.ApprovalEngine(
        P.effective_profile(runtime, _workspace(args), name=_profile_name(args)),
        runtime=runtime,
        session=_session_id(args),
        store=store,
        grants=ledger,
        policy=str(getattr(args, "policy", "") or "on_request"),
        interactive=bool(getattr(args, "interactive", False)),
    )


def cmd_approval_list(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    store = _store_for(runtime, args)
    ledger = _ledger_for(runtime, args, store)
    evidence = A.EvidenceLedger(runtime)
    payload = A.policy_summary(store, ledger)
    payload["events"] = {
        "path": str(A.events_path(runtime)),
        "count": evidence.count(),
        "tail": evidence.tail(int(getattr(args, "tail", 10) or 10)),
    }
    payload["profile"] = P.effective_profile(
        runtime, _workspace(args), name=_profile_name(args)
    ).to_dict()
    counts = payload["policy"]["counts"]
    _publish(
        payload,
        evidence=(
            {
                "ref": str(A.policy_path(runtime, _profile_name(args))),
                "kind": "file",
                "note": f"{counts['rules']} rule(s), {counts['banned']} banned prefix(es)",
            },
        ),
    )
    human = [
        f"rules ({counts['rules']}):",
        *[f"  {rule.rule_id:32} {rule.rule_text()}" for rule in store.rules],
        f"banned prefixes ({counts['banned']}) - never auto-approved:",
        "  " + "; ".join(A.render_prefix(item) for item in store.banned),
        f"grants for session {ledger.session or '(none)'}:",
        f"  turn    {len(ledger.turn)}",
        f"  session {len(ledger.session_grants)}",
        f"evidence: {payload['events']['count']} event(s) in {payload['events']['path']}",
    ]
    return _emit(args, payload, human)


def cmd_approval_allow(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    engine = _engine_for(args, runtime)
    try:
        command = _command_of(args, "approval allow")
    except ValueError as exc:
        return _refuse(EXIT_USAGE, str(exc))
    try:
        scope = A.grant_scope_from(str(getattr(args, "scope", "") or A.GrantScope.TURN.value))
    except A.PolicyError as exc:
        return _refuse(EXIT_USAGE, f"approval allow: {exc}")
    try:
        grant, rule, record = engine.approve(
            command,
            scope=scope.value,
            persist_rule=bool(getattr(args, "rule", False)),
            reason=str(getattr(args, "reason", "") or ""),
        )
    except A.BannedPrefixRefusal as exc:
        return _refuse(
            EXIT_BLOCKED,
            f"approval allow: {exc}",
            hint=(
                "banned prefixes can only be approved in the foreground, one command at a time; "
                "run it yourself or rewrite the command"
            ),
            details={"command": command, "banned": True},
        )
    except A.PolicyError as exc:
        return _refuse(EXIT_USAGE, f"approval allow: {exc}")
    payload = {
        "command": command,
        "scope": grant.scope if grant else "persisted_rule",
        "prefix": list(grant.prefix) if grant else list(rule.prefix) if rule else [],
        "rule": rule.to_dict() if rule else {},
        "grant": grant.to_dict() if grant else {},
        "evidence_ref": record.get("evidence_ref", ""),
        "review_decision": record.get("review_decision", ""),
    }
    _publish(
        payload,
        evidence=(
            {
                "ref": str(record.get("evidence_ref", "")),
                "kind": "run",
                "note": f"approval recorded: {record.get('review_decision', '')}",
            },
        ),
    )
    human = [
        f"approved: {command}",
        f"  scope   {payload['scope']}",
        f"  prefix  {A.render_prefix(payload['prefix'])}",
        f"  record  {payload['evidence_ref']}",
    ]
    if payload["scope"] == A.GrantScope.SESSION.value:
        human.append("  note    persisted for this session id only - a new session starts clean")
    return _emit(args, payload, human)


def cmd_approval_deny(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    engine = _engine_for(args, runtime)
    try:
        command = _command_of(args, "approval deny")
    except ValueError as exc:
        return _refuse(EXIT_USAGE, str(exc))
    abort = bool(getattr(args, "abort", False))
    rule, record = engine.deny(
        command,
        reason=str(getattr(args, "reason", "") or ""),
        persist_rule=bool(getattr(args, "rule", False)),
        abort=abort,
    )
    payload = {
        "command": command,
        "reason": str(record.get("reason", "")),
        "persisted_rule": rule.to_dict() if rule else {},
        "review_decision": record.get("review_decision", ""),
        "evidence_ref": record.get("evidence_ref", ""),
        "continues_turn": not abort,
    }
    _publish(
        payload,
        evidence=(
            {
                "ref": str(record.get("evidence_ref", "")),
                "kind": "run",
                "note": f"denial recorded: {payload['reason'][:80]}",
            },
        ),
    )
    human = [
        f"denied: {command}",
        f"  reason  {payload['reason']}",
        "  told    the agent receives this reason; it is not a dead end",
        f"  turn    {'aborted' if abort else 'continues'}",
        f"  record  {payload['evidence_ref']}",
    ]
    return _emit(args, payload, human)


def cmd_approval_check(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    engine = _engine_for(args, runtime)
    try:
        command = _command_of(args, "approval check")
    except ValueError as exc:
        return _refuse(EXIT_USAGE, str(exc))
    interactive = bool(getattr(args, "interactive", False))
    verdict = engine.decide(command, interactive=interactive)
    payload = verdict.to_dict()
    payload["dry_run"] = True
    payload["ran"] = False
    head_tokens = verdict.segments[0] if verdict.segments else ()
    payload["rules_considered"] = [
        rule.to_dict() for rule in engine.store.rules_for(head_tokens)
    ]
    payload["banned_prefixes_considered"] = len(engine.store.banned)
    _publish(
        payload,
        evidence=(
            {
                "ref": str(verdict.evidence_ref),
                "kind": "run",
                "note": f"judgement: {verdict.decision} ({verdict.reason_kind})",
            },
        ),
        warnings=[f"{verdict.decision}: {verdict.reason}"],
        next_actions=suggested_commands(verdict),
    )
    human = [
        f"{verdict.decision.upper():7} {command}",
        f"  because  {verdict.reason}",
        f"  rule     {verdict.rule_id or '(none)'} {verdict.rule_text or ''}".rstrip(),
        f"  segments {[list(item) for item in verdict.segments]}",
        f"  eligible {verdict.rule_eligible}",
        f"  exit     {verdict.exit_code}",
    ]
    if verdict.exit_code:
        human.append("  to allow it:")
        human += [f"    {line}" for line in verdict.what_would_allow()]
    _emit(args, payload, human)
    return verdict.exit_code


def cmd_approval_run(args: argparse.Namespace) -> int:
    runtime = runtime_dir(args)
    engine = _engine_for(args, runtime)
    try:
        command = _command_of(args, "approval run")
    except ValueError as exc:
        return _refuse(EXIT_USAGE, str(exc))
    interactive = bool(getattr(args, "interactive", False))
    dry_run = bool(getattr(args, "dry_run", False))
    verdict = engine.decide(command, interactive=interactive)
    # the same shape ``approval.check`` answers with: the verdict at the top level, plus what
    # actually ran. Two verbs that answer the same question must not need two readers.
    payload: dict[str, Any] = verdict.to_dict()
    payload["dry_run"] = dry_run
    if verdict.blocked or dry_run:
        payload["ran"] = False
        would_run = (not verdict.blocked) and dry_run
        human = [
            f"would run: {command}" if would_run else f"blocked ({verdict.exit_code}): {command}",
            f"  because  {verdict.reason}",
            f"  rule     {verdict.rule_id or '(none)'} {verdict.rule_text or ''}".rstrip(),
        ]
        if not would_run:
            if not interactive:
                human.append(
                    "  headless  no interactive user: this returned blocked instead of waiting, "
                    "and nothing was auto-allowed"
                )
            if verdict.exit_code:
                human.append("  the user would need to allow:")
                human += [f"    {line}" for line in verdict.what_would_allow()]
        _publish(
            payload,
            evidence=(
                {
                    "ref": str(verdict.evidence_ref),
                    "kind": "run",
                    "note": (
                        f"would run ({verdict.decision})"
                        if would_run
                        else f"not run: {verdict.decision} ({verdict.reason_kind})"
                    ),
                },
            ),
            warnings=[] if would_run else [verdict.reason],
            next_actions=suggested_commands(verdict),
        )
        _emit(args, payload, human)
        return EXIT_BLOCKED if verdict.blocked else EXIT_OK
    run = P.run_in_sandbox(engine.profile, command, runtime=runtime)
    payload["run"] = run.to_dict()
    payload["ran"] = not run.blocked
    exit_code = EXIT_OK
    if run.blocked:
        exit_code = EXIT_BLOCKED
    elif run.exit_code != 0:
        exit_code = EXIT_VERIFICATION_FAILED
    _publish(
        payload,
        evidence=(
            {
                "ref": str(verdict.evidence_ref),
                "kind": "run",
                "note": f"allowed by {verdict.reason_kind} ({verdict.rule_id or 'no rule'})",
            },
            {
                "ref": f"profile:{engine.profile.name}",
                "kind": "verification",
                "note": f"ran inside sha256 {run.profile_sha256} (backend {run.backend})",
            },
        ),
        warnings=([run.blocked_reason] if run.blocked else []),
    )
    human = [
        f"ran ({run.exit_code}) under profile sha256 {run.profile_sha256}",
        f"  argv     {P.shlex_join(run.argv)}",
        f"  allowed  {verdict.reason_kind}: {verdict.reason}",
        f"  caps     enforced {run.caps_enforced} unenforced {run.caps_unenforced}",
    ]
    if run.output.strip():
        human.append("  output:")
        human += [f"    {line}" for line in run.output.strip().splitlines()]
    _emit(args, payload, human)
    return exit_code


# --------------------------------------------------------------------------- #
# the parser                                                                    #
# --------------------------------------------------------------------------- #


def _add_policy_flags(parser: Any) -> None:
    parser.add_argument("--runtime", default="", help="runtime dir (default: $SKALDIR_HOME)")
    parser.add_argument("--profile", default="default", help="profile name (default: default)")
    parser.add_argument("--workspace", default="", help="workspace the profile is rooted at")
    parser.add_argument("--json", action="store_true")


def _add_approval_flags(parser: Any) -> None:
    _add_policy_flags(parser)
    parser.add_argument(
        "--session", default="", help="session id grants are filed under (default: default)"
    )
    parser.add_argument(
        "--policy",
        default="on_request",
        choices=list(A.APPROVAL_POLICIES),
        help="on_request | unless_trusted (persisted rules only) | never (no prompt is possible)",
    )
    parser.add_argument(
        "--headless",
        dest="interactive",
        action="store_false",
        help="no interactive user (the default): a prompt returns blocked, never a hang",
    )
    parser.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        help="an interactive user is present and would be shown the prompt",
    )
    parser.set_defaults(interactive=False)
    parser.add_argument("--no-persist", action="store_true", help="do not write any state")


def _add_permission_verbs(sub: Any) -> Any:
    show = sub.add_parser("show", help=SHOW_HELP)
    _add_policy_flags(show)
    show.add_argument("--probe", action="store_true", help="also probe the sandbox for real")
    show.set_defaults(func=cmd_permission_show, command_path="permission.show")

    explain = sub.add_parser("explain", help=EXPLAIN_HELP)
    _add_policy_flags(explain)
    explain.set_defaults(func=cmd_permission_explain, command_path="permission.explain")

    setter = sub.add_parser("set", help=SET_HELP)
    _add_policy_flags(setter)
    setter.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=f"repeatable; append with KEY+=VALUE. keys: {', '.join(SET_KEYS)}",
    )
    setter.set_defaults(func=cmd_permission_set, command_path="permission.set")

    for parser in (show, explain, setter):
        share_common_flags(parser)
    return sub


def _add_approval_verbs(sub: Any) -> Any:
    listing = sub.add_parser("list", help=LIST_HELP)
    _add_approval_flags(listing)
    listing.add_argument("--tail", type=int, default=10, help="how many evidence events to show")
    listing.set_defaults(func=cmd_approval_list, command_path="approval.list")

    allow = sub.add_parser("allow", help=ALLOW_HELP)
    _add_approval_flags(allow)
    _command_argument(allow, "the command or prefix being approved (options come first)")
    allow.add_argument(
        "--scope",
        default=A.GrantScope.TURN.value,
        choices=[item.value for item in A.GrantScope],
        help="turn = allow once (default); session = for this session id only",
    )
    allow.add_argument(
        "--rule", action="store_true", help="persist a prefix rule instead of a grant"
    )
    allow.add_argument("--reason", default="", help="why it is allowed (recorded)")
    allow.set_defaults(func=cmd_approval_allow, command_path="approval.allow")

    deny = sub.add_parser("deny", help=DENY_HELP)
    _add_approval_flags(deny)
    _command_argument(deny, "the command being denied (options come first)")
    deny.add_argument("--reason", default="", help="what the agent should do instead")
    deny.add_argument("--rule", action="store_true", help="persist a forbid rule")
    deny.add_argument("--abort", action="store_true", help="stop the turn instead of continuing")
    deny.set_defaults(func=cmd_approval_deny, command_path="approval.deny")

    check = sub.add_parser("check", help=CHECK_HELP)
    _add_approval_flags(check)
    _command_argument(check, "the command to judge; nothing runs (options come first)")
    check.add_argument(
        "--dry-run",
        action="store_true",
        help="answer 'allowed / prompted / forbidden' without running anything (the default)",
    )
    check.set_defaults(func=cmd_approval_check, command_path="approval.check")

    runner = sub.add_parser("run", help=RUN_HELP)
    _add_approval_flags(runner)
    _command_argument(runner, "the command to judge and run (options come first)")
    runner.add_argument("--dry-run", action="store_true", help="judge only, never run")
    runner.set_defaults(func=cmd_approval_run, command_path="approval.run")

    for parser in (listing, allow, deny, check, runner):
        share_common_flags(parser)
    return sub


def build_permission_parser(subparsers: Any) -> Any:
    """Add the ``permission`` group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "permission",
        help=GROUP_HELP,
        description=(
            "One serializable permission profile is the source of truth for two consumers: "
            "the sandbox invocation and the block the agent is told. `permission show` prints "
            "the profile and its identity hash, `explain` adds the exact bwrap/podman argv, "
            "the per-field enforcement matrix and both hashes side by side, and `set` changes "
            "one field and reports the hash it moved."
        ),
    )
    sub = parser.add_subparsers(dest="permission_command", required=True)
    _add_permission_verbs(sub)
    return parser


def build_approval_parser(subparsers: Any) -> Any:
    """Add the ``approval`` group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "approval",
        help=GROUP_HELP,
        description=(
            "The approval ladder: a three-value rule language (allow / prompt / forbid), "
            "turn- and session-scoped grants, a banned-prefix list that can never be "
            "auto-approved, and the rule that a command which cannot be decomposed safely "
            "must prompt. `check --dry-run` answers what would happen to a command without "
            "running it; `run` judges and then runs it inside the profile. With no interactive "
            "user a prompt-required command returns blocked (exit 3) - it never hangs and is "
            "never auto-allowed."
        ),
    )
    sub = parser.add_subparsers(dest="approval_command", required=True)
    _add_approval_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> tuple[Any, Any]:
    """The CLI envelope contract: ``register_cli(subparsers) -> (permission, approval)``.

    Installs the group's contract entries as part of the wiring, so a CLI that adds the group
    cannot leave ``COMMANDS`` behind (``tests/test_cli_contract_dump.py`` would otherwise fail
    the moment the group is reachable).
    """
    install_entries()
    return build_permission_parser(subparsers), build_approval_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just these groups' verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir permission|approval", description=GROUP_HELP)
    sub = parser.add_subparsers(dest="group", required=True)
    _add_permission_verbs(sub.add_parser("permission", help=GROUP_HELP).add_subparsers(
        dest="permission_command", required=True
    ))
    _add_approval_verbs(sub.add_parser("approval", help=GROUP_HELP).add_subparsers(
        dest="approval_command", required=True
    ))
    return parser


def main(argv: list[str] | None = None) -> int:
    """``python -m skaldir.approval_cli permission|approval …`` — the same verbs, exit codes.

    The out-of-process entry point for the two groups this module owns: it exists so they can be
    exercised (and the wiring snippet proven) without the product CLI, and it deliberately does
    not build the envelope — through the product CLI the contract's ``run_command`` owns that.

    Its own shape, unlike a single-group entry point: the group name comes from the parser (not
    from a hand-written prog), the help goes to stderr so a pipe stays clean, and a broken pipe
    from ``| head`` is an ordinary end rather than a traceback. That is why a sub-argument is
    required: without one there is no group to be in, and that is a usage error.
    """
    parser = build_parser()
    raw = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    try:
        namespace = parser.parse_args(raw)
    except SystemExit as exc:  # argparse owns --help and every usage error
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(namespace, "func", None)
    if handler is None:
        parser.print_help(file=sys.stderr)
        return EXIT_USAGE
    try:
        return int(handler(namespace))
    except BrokenPipeError:  # `skaldir approval list | head` must not traceback
        return EXIT_OK


# --------------------------------------------------------------------------- #
# contract entries                                                              #
# --------------------------------------------------------------------------- #


def _ev3(kind: str, ref: str, note: str = "") -> dict:
    """One evidence reference, from a ``(kind, ref, note)`` triple in that order."""
    return {"kind": str(kind), "ref": str(ref), "note": str(note)}


def _spec(evidence: Sequence[tuple[str, str, str]], **rest: Any) -> Any:
    """One ``CommandSpec`` from a list of triples plus the remaining fields."""
    if _PRODUCT is None or not hasattr(_PRODUCT, "CommandSpec"):
        return None
    return _PRODUCT.CommandSpec(evidence=tuple(_ev3(*item) for item in evidence), **rest)


_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"type": "object"},
        "identity_sha256": {"type": "string"},
        "hash_in_argv": {"type": "string"},
        "hash_in_prompt": {"type": "string"},
        "hashes_agree": {"type": "boolean"},
        "enforcement": {"type": "array", "items": {"type": "object"}},
        "unenforced": {"type": "array", "items": {"type": "string"}},
        "sandbox_argv": {"type": "array", "items": {"type": "string"}},
        "prompt_block": {"type": "string"},
        "sources": {"type": "object"},
    },
    "required": ["profile", "identity_sha256", "hashes_agree", "prompt_block"],
}

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string"},
        "decision": {"type": "string", "enum": ["allow", "prompt", "forbid"]},
        "reason_kind": {"type": "string"},
        "reason": {"type": "string"},
        "rule_id": {"type": "string"},
        "rule_text": {"type": "string"},
        "blocked": {"type": "boolean"},
        "exit_code": {"type": "integer"},
        "would_allow": {"type": "array", "items": {"type": "string"}},
        "profile": {"type": "object"},
    },
    "required": ["command", "decision", "blocked", "exit_code"],
}

CONTRACT_ENTRIES: dict[str, Any] = {
    path: spec
    for path, spec in (
        (
            "permission.show",
            _spec(
                (
                    ("code", "src/skaldir/permission_profile.py", "the one profile object"),
                    ("code", "src/skaldir/approval_cli.py", "this handler"),
                ),
                layer="ENFORCE",
                description="the effective permission profile, its hash and the prompt block",
                exit_codes=(0, 1, 3, 7),
                mutates=False,
                data_schema=_PROFILE_SCHEMA,
                notes=(
                    "Read-only. Prints the fields the sandbox argv is built from and the block "
                    "the agent is told, both carrying the same identity_sha256."
                ),
            ),
        ),
        (
            "permission.explain",
            _spec(
                (
                    (
                        "code",
                        "src/skaldir/permission_profile.py",
                        "enforcement_matrix / sandbox_argv / prompt_block",
                    ),
                    ("code", "src/skaldir/approval_cli.py", "this handler"),
                ),
                layer="ENFORCE",
                description="per-field source, the exact argv, and both hashes side by side",
                exit_codes=(0, 1, 3, 7),
                mutates=False,
                data_schema=_PROFILE_SCHEMA,
                notes=(
                    "Also probes the sandbox for real (bwrap/podman) and names every field that "
                    "is *not* enforced on this host instead of implying that it is."
                ),
            ),
        ),
        (
            "permission.set",
            _spec(
                (
                    ("code", "src/skaldir/permission_profile.py", "save_profile"),
                    ("file", "<runtime>/permissions/<name>.json", "the stored profile"),
                ),
                layer="ENFORCE",
                description="change one profile field and report the identity hash it moved",
                exit_codes=(0, 1, 3, 7),
                mutates=True,
                data_schema=_PROFILE_SCHEMA,
                notes=(
                    "Writes the named profile. A change that does not move the hash (an "
                    "identical value) is reported as a warning, never as a change."
                ),
            ),
        ),
        (
            "approval.list",
            _spec(
                (
                    ("file", "<runtime>/approval/policy.json", "the persisted prefix rules"),
                    ("file", "<runtime>/approval/grants/<session>.json", "session grants"),
                    ("file", "<runtime>/approval/events.jsonl", "the evidence ledger"),
                ),
                layer="ENFORCE",
                description="rules, banned prefixes, live grants and the evidence tail",
                exit_codes=(0, 1, 7),
                mutates=False,
                data_schema={
                    "type": "object",
                    "properties": {
                        "policy": {"type": "object"},
                        "grants": {"type": "object"},
                        "events": {"type": "object"},
                    },
                    "required": ["policy", "grants"],
                },
                notes="Read-only; the banned list is printed in full so it cannot be forgotten.",
            ),
        ),
        (
            "approval.allow",
            _spec(
                (
                    ("code", "src/skaldir/approval.py", "ApprovalEngine.approve"),
                    ("file", "<runtime>/approval/events.jsonl", "the recorded decision"),
                ),
                layer="ENFORCE",
                description="answer a prompt with yes: turn scope, session scope, or a rule",
                exit_codes=(0, 1, 3, 7),
                mutates=True,
                data_schema=_VERDICT_SCHEMA,
                notes=(
                    "A banned prefix is refused with exit 3 in every scope — that is what the "
                    "banned list means. A session grant is persisted per session id and is not "
                    "inherited by a new one."
                ),
            ),
        ),
        (
            "approval.deny",
            _spec(
                (
                    ("code", "src/skaldir/approval.py", "ApprovalEngine.deny"),
                    ("file", "<runtime>/approval/events.jsonl", "the recorded denial"),
                ),
                layer="ENFORCE",
                description="answer a prompt with no, plus a reason the agent can act on",
                exit_codes=(0, 1, 3, 7),
                mutates=True,
                data_schema=_VERDICT_SCHEMA,
                notes=(
                    "Denied continues the turn with the reason (deny-and-teach-back); --abort "
                    "stops the turn instead. --rule persists a forbid rule."
                ),
            ),
        ),
        (
            "approval.check",
            _spec(
                (
                    ("code", "src/skaldir/approval.py", "ApprovalEngine.judge"),
                    ("code", "src/skaldir/approval.py", "decompose"),
                ),
                layer="ENFORCE",
                description="would this command be allowed, prompted or forbidden?",
                exit_codes=(0, 1, 3, 7),
                mutates=False,
                data_schema=_VERDICT_SCHEMA,
                notes=(
                    "--dry-run: nothing is executed. Exit 0 when it would be allowed, 3 when it "
                    "would be prompted or forbidden — with the deciding rule and what the user "
                    "would need to allow."
                ),
            ),
        ),
        (
            "approval.run",
            _spec(
                (
                    ("code", "src/skaldir/approval.py", "ApprovalEngine.decide"),
                    ("code", "src/skaldir/permission_profile.py", "run_in_sandbox"),
                ),
                layer="ENFORCE",
                description="judge the command, then run it inside the profile when allowed",
                exit_codes=(0, 1, 2, 3, 7),
                mutates=True,
                data_schema=_VERDICT_SCHEMA,
                notes=(
                    "The enforcement path: exit 3 blocked (no user / forbidden / no sandbox), "
                    "2 when the command ran and returned non-zero, 0 when it ran clean. The "
                    "argv that ran carries the same profile hash as the prompt block."
                ),
            ),
        ),
    )
}


def install_entries() -> list[str]:
    """Install this module's ``COMMANDS`` entries when they are missing (idempotent)."""
    return install_contract_entries(CONTRACT_ENTRIES, _PRODUCT)


__all__ = [
    "CONTRACT_ENTRIES",
    "CHECK_HELP",
    "DENY_HELP",
    "EXPLAIN_HELP",
    "GROUP_HELP",
    "LIST_HELP",
    "RUN_HELP",
    "SET_KEYS",
    "SHOW_HELP",
    "build_approval_parser",
    "build_parser",
    "build_permission_parser",
    "cmd_approval_allow",
    "cmd_approval_check",
    "cmd_approval_deny",
    "cmd_approval_list",
    "cmd_approval_run",
    "cmd_permission_explain",
    "cmd_permission_set",
    "cmd_permission_show",
    "install_entries",
    "main",
    "register_cli",
    "suggested_commands",
]


if __name__ == "__main__":  # pragma: no cover — convenience only
    sys.exit(main())
