"""``skaldir prompt …`` — assemble, explain and fingerprint the agent's system prompt.

Three verbs over :mod:`skaldir.prompt_builder` (the composer that owns the sections,
their budgets and the stability contract)::

====================  ==================================================================
``prompt build``      compose the prompt from the real inputs and print it **verbatim**
                      (human) or carry it in the envelope (``--json``). Reports the
                      per-section sizes and the total against ``--budget``.
``prompt explain``    the section-by-section answer to "what is in here, and where did
                      it come from": size, budget, truncation, content hash, source and
                      the detail line of every provider — including the omitted ones.
``prompt fingerprint`` the sha256 of the composed bytes, compared against the frozen
                      prompt of a live conversation.
====================  ==================================================================

The stability contract, as the CLI enforces it:

* ``--freeze`` records the current fingerprint for ``--conversation`` in
  ``<runtime>/prompt-freeze.json`` — the deliberate act that defines a conversation.
* A later build whose fingerprint differs is a **prompt cache break**. It is reported
  with the sections that moved and **refused with exit 6** unless the caller asks for it
  (``--accept-change``, which re-freezes and marks the break accepted).
* A prompt cache is keyed on those exact bytes, so this is a cost decision, not a
  cosmetic one: the composer never reads the clock, and neither does this module on the
  path that produces the text.

Exit codes — the product CLI's :mod:`skaldir.cli_contract` vocabulary::

    0   ok                  composed / explained / fingerprint answered
    1   usage               argparse refused the call, or an unknown ``--without`` section
    2   verification failed the assembled prompt is over its token budget
    3   blocked             nothing could be assembled (an empty prompt is not a prompt)
    6   conflict            the frozen prompt of this conversation changed; a decision is
                            required (``--accept-change``)
    7   internal            a broken freeze file, an unreadable store, a bug

Precedence when several apply: ``3`` (nothing to assemble) → ``6`` (a decision is owed)
→ ``2`` (budget) → ``0``.

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``) — and the call installs the group's contract entries, so
wiring the group cannot forget them::

    from .prompt_builder_cli import register_cli
    register_cli(sub)
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import prompt_builder as P
from .cli_shared import (
    dump,
    envelope_mode,
    product_contract,
    runtime_dir,
    share_common_flags,
    text_mode,
)
from .cli_shared import (
    install_contract_entries as _shared_install_contract_entries,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

GROUP_HELP = "assemble / explain / fingerprint the agent system prompt"

BUILD_HELP = "compose the system prompt from the real inputs and print it"
EXPLAIN_HELP = "section by section: size, budget, hash and where each section came from"
FINGERPRINT_HELP = "the sha256 of the composed prompt, against the frozen one"


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

    Every report this group produces is a build result (a fingerprint, a section table,
    a freeze decision), so one writer covers all three verbs. It never raises: a missing
    or broken contract module must not turn a composed prompt into an internal error —
    the prompt is the deliverable, the envelope is transport.
    """
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            _CONTRACT.add_evidence(
                str(item["ref"]),
                str(item.get("kind") or "verification"),
                str(item.get("note", "")),
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break a verb
        return


def _fail_with(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    """Exit through the shared helper that matches ``code``, else by hand."""
    if _CONTRACT is not None and hasattr(_CONTRACT, "fail"):
        try:
            if code == EXIT_BLOCKED and hasattr(_CONTRACT, "blocked"):
                return int(_CONTRACT.blocked(message, hint=hint, details=details, reason=message))
            if code == EXIT_VERIFICATION_FAILED and hasattr(_CONTRACT, "verification_failed"):
                return int(_CONTRACT.verification_failed(message, hint=hint, details=details))
            if code == EXIT_CONFLICT and hasattr(_CONTRACT, "fail"):
                return int(_CONTRACT.fail(code, message, hint=hint, details=details))
            return int(_CONTRACT.fail(code, message, hint=hint, details=details))
        except Exception:  # noqa: BLE001 — a contract problem must not hide the verdict
            return int(code)
    return int(code)


def _warn(lines: Iterable[str]) -> None:
    """Diagnostics go to stderr, so stdout stays exactly the prompt (or the JSON)."""
    for line in lines:
        if line:
            print(line, file=sys.stderr)


# --------------------------------------------------------------------------- #
# arguments → inputs                                                            #
# --------------------------------------------------------------------------- #


def _budget_overrides(raw: list[str] | None) -> dict[str, int]:
    """``--section-budget NAME=CHARS`` → the composer's per-section overrides."""
    budgets: dict[str, int] = {}
    for item in raw or []:
        name, _, value = str(item).partition("=")
        name = name.strip()
        if not name or not value.strip():
            raise ValueError(f"--section-budget wants NAME=CHARS, got {item!r}")
        if name not in P.SECTION_ORDER:
            raise ValueError(
                f"unknown section {name!r} (known: {', '.join(P.SECTION_ORDER)})"
            )
        budgets[name] = int(value)
    return budgets


def _context(args: argparse.Namespace) -> tuple[P.BuildContext | None, str]:
    """Build the one context the composer reads. Returns ``(context, problem)``."""
    try:
        budgets = _budget_overrides(getattr(args, "section_budget", None))
        context = P.context_for(
            project=getattr(args, "project", "") or None,
            runtime=runtime_dir(args),
            db=str(getattr(args, "memory_db", "") or "") or None,
            prompts_root=str(getattr(args, "prompts_root", "") or "") or None,
            total_budget_tokens=int(getattr(args, "budget", 0) or P.DEFAULT_TOTAL_BUDGET_TOKENS),
            budgets=budgets,
        )
    except ValueError as exc:
        return None, str(exc)
    return context, ""


def _assemble(args: argparse.Namespace) -> tuple[P.BuildContext, P.PromptAssembly] | None:
    """The one place the assembly happens, so every verb reads the same prompt.

    A caller's mistake (an unknown ``--without`` section, a malformed ``--section-budget``)
    is a **usage** error and is reported as one; it never escapes as an internal error.
    """
    context, problem = _context(args)
    if context is None:
        print(f"skaldir prompt: {problem}", file=sys.stderr)
        return None
    without = [str(name) for name in (getattr(args, "without", None) or [])]
    try:
        assembly = P.build_prompt(context, without=without)
    except ValueError as exc:
        print(f"skaldir prompt: {exc}", file=sys.stderr)
        return None
    return context, assembly


def _conversation(args: argparse.Namespace) -> str:
    return str(getattr(args, "conversation", "") or P.DEFAULT_CONVERSATION)


def _cache_payload(status: P.CacheStatus) -> dict:
    return status.to_dict()


# --------------------------------------------------------------------------- #
# the verbs                                                                     #
# --------------------------------------------------------------------------- #


def _freeze_if_requested(
    args: argparse.Namespace,
    assembly: P.PromptAssembly,
    runtime: Path,
    conversation: str,
    status: P.CacheStatus,
) -> tuple[P.CacheStatus, bool, bool]:
    """Record the fingerprint when the caller asked for it. Returns ``(status, accepted, frozen)``.

    ``--freeze`` and ``--accept-change`` are the two ways to say "do it deliberately":
    both write the record, and a break that existed is reported and remembered as an
    accepted one (``accepted_from``), so the next read still shows the decision.
    """
    if not (getattr(args, "freeze", False) or getattr(args, "accept_change", False)):
        return status, False, False
    accepted = status.cache_break
    record = P.write_freeze(
        assembly, runtime, conversation, now=time.time(),
        accepted_from=status.previous_fingerprint if accepted else "",
    )
    return (
        P.CacheStatus(
            fingerprint=assembly.fingerprint,
            frozen=True,
            stable=True,
            previous_fingerprint=status.previous_fingerprint or assembly.fingerprint,
            conversation=conversation,
            frozen_at=record.frozen_at,
            changed_sections=status.changed_sections,
            accepted_from=record.accepted_from,
            reason=(
                f"cache break accepted deliberately — the frozen prompt is now "
                f"{assembly.fingerprint[:16]} (it replaced "
                f"{status.previous_fingerprint[:16]})"
                if accepted
                else f"frozen for conversation {conversation!r}"
            ),
            note=status.note,
        ),
        accepted,
        True,
    )


def cmd_build(args: argparse.Namespace) -> int:
    """Compose the prompt, print it verbatim, and answer the budget + freeze questions."""
    assembled = _assemble(args)
    if assembled is None:
        return EXIT_USAGE
    context, assembly = assembled
    runtime, conversation = context.runtime, _conversation(args)
    status = P.cache_status(assembly, runtime, conversation)
    status, accepted, frozen_now = _freeze_if_requested(
        args, assembly, runtime, conversation, status
    )

    payload = {
        **assembly.to_dict(),
        "cache": _cache_payload(status),
        "cache_break_accepted": accepted,
        "frozen_now": frozen_now,
        "conversation": conversation,
        "inputs": context.paths(),
    }
    warnings = list(_build_warnings(assembly, status, _budget_overrides(
        getattr(args, "section_budget", None))))
    evidence = _build_evidence(context, assembly, status)
    next_actions = _next_actions(assembly, status, conversation)
    _publish(payload, evidence=evidence, warnings=warnings, next_actions=next_actions)

    _warn(warnings)
    if text_mode(args):
        if assembly.text:
            # Exactly the fingerprinted bytes: `prompt build --text | sha256sum` equals
            # the fingerprint, with no trailing newline added on the way out.
            sys.stdout.write(assembly.text)
            sys.stdout.flush()
    elif not envelope_mode():
        dump(payload)

    return _verdict(assembly, status)


def _build_warnings(
    assembly: P.PromptAssembly,
    status: P.CacheStatus,
    explicit_budgets: dict[str, int] | None = None,
) -> list[str]:
    warnings: list[str] = []
    # A budget pinned for a section the build does not carry was never applied —
    # say so instead of letting it read as "applied and small".
    overrides = explicit_budgets or {}
    present = {section.name for section in assembly.sections if section.present}
    for name, chars in overrides.items():
        if name not in present:
            warnings.append(
                f"--section-budget {name}={chars} was not applied: "
                f"section {name!r} is absent from this build"
            )
    for section in assembly.sections:
        if section.truncated:
            warnings.append(
                f"section {section.name}: clipped from {section.original_chars} to "
                f"{section.chars} chars (budget {section.budget_chars})"
            )
    if assembly.over_budget:
        warnings.append(
            f"prompt is over budget: {assembly.tokens_estimate} > {assembly.budget_tokens} "
            f"tokens ({assembly.estimate_method})"
        )
    if status.cache_break and not status.stable:
        warnings.append(status.reason)
    return warnings


def _build_evidence(
    context: P.BuildContext, assembly: P.PromptAssembly, status: P.CacheStatus
) -> list[dict]:
    """Evidence that names the bytes: the prompt hash and what was read to make it."""
    entries = [
        {
            "ref": f"sha256:{assembly.fingerprint}",
            "kind": "verification",
            "note": f"composed prompt fingerprint over {assembly.total_bytes} bytes "
                    f"({len(assembly.present)} of {len(assembly.sections)} sections present)",
        }
    ]
    for section in assembly.sections:
        if section.present:
            entries.append({
                "ref": f"{section.name}:sha256:{section.sha256}",
                "kind": "file",
                "note": f"{section.chars} chars from {section.source}",
            })
    entries.append({
        "ref": str(P.freeze_path(context.runtime)),
        "kind": "file",
        "note": status.reason or f"frozen state for conversation {status.conversation!r}",
    })
    return entries


def _next_actions(
    assembly: P.PromptAssembly, status: P.CacheStatus, conversation: str
) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    if not status.frozen:
        actions.append((
            f"skaldir prompt build --freeze --conversation {conversation}",
            "freeze this fingerprint so a later change is reported as a cache break",
        ))
    if status.cache_break:
        actions.append((
            f"skaldir prompt fingerprint --accept-change --conversation {conversation}",
            "accept the change deliberately (and see which sections moved)",
        ))
    for section in assembly.sections:
        if not section.present:
            actions.append((
                f"skaldir prompt explain --without {section.name}",
                f"{section.name} is omitted: {section.detail}",
            ))
            break
    project_block = assembly.section("project_context")
    if project_block is not None and not project_block.present:
        # The shared block owns project context; when it injects nothing, its own
        # diagnostic (and not this group) is what explains which file was seen and why.
        actions.append((
            "skaldir context explain",
            "the shared project-context block found no usable entries — see its own report",
        ))
    return actions


def _verdict(assembly: P.PromptAssembly, status: P.CacheStatus) -> int:
    """The one precedence rule: nothing assembled → a decision owed → budget → ok."""
    if not assembly.text.strip():
        return _fail_with(
            EXIT_BLOCKED,
            "nothing could be assembled — every section is empty",
            hint="check the runtime dir, the memory db and the project directory",
        )
    if status.cache_break:
        return _fail_with(
            EXIT_CONFLICT,
            status.reason,
            hint="re-run with --accept-change to record the new prompt deliberately",
            details={"changed_sections": [dict(item) for item in status.changed_sections]},
        )
    if assembly.over_budget:
        return _fail_with(
            EXIT_VERIFICATION_FAILED,
            f"prompt is over budget: {assembly.tokens_estimate} > {assembly.budget_tokens} tokens",
            hint="raise --budget, or omit a section with --without NAME",
        )
    return EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    """Section by section: size, budget, hash, source — including what was omitted."""
    assembled = _assemble(args)
    if assembled is None:
        return EXIT_USAGE
    context, assembly = assembled
    status = P.cache_status(assembly, context.runtime, _conversation(args))
    payload = {
        **P.explain(assembly, context),
        "cache": _cache_payload(status),
        "conversation": _conversation(args),
        "freeze_path": str(P.freeze_path(context.runtime)),
    }
    warnings = _build_warnings(assembly, status)
    _publish(
        payload,
        evidence=_build_evidence(context, assembly, status),
        warnings=warnings,
        next_actions=_next_actions(assembly, status, _conversation(args)),
    )
    _warn(warnings)
    if text_mode(args):
        _print_explain(payload)
    elif not envelope_mode():
        dump(payload)
    if not assembly.text.strip():
        return _fail_with(EXIT_BLOCKED, "nothing could be assembled — every section is empty")
    if assembly.over_budget:
        return _fail_with(
            EXIT_VERIFICATION_FAILED,
            f"prompt is over budget: {assembly.tokens_estimate} > {assembly.budget_tokens} tokens",
        )
    return EXIT_OK


def _print_explain(payload: dict) -> None:
    present = payload["present"]
    print(
        f"skaldir prompt — {payload['section_count']} sections, {len(present)} present, "
        f"{len(payload['omitted'])} omitted"
    )
    print(
        f"fingerprint sha256:{payload['fingerprint']}\n"
        f"  {payload['chars']} chars / {payload['bytes']} bytes / {payload['tokens_estimate']} "
        f"tokens ({payload['estimate_method']}) vs budget {payload['budget_tokens']} tokens"
        f"{'  OVER BUDGET' if payload['over_budget'] else ''}"
    )
    for section in payload["sections"]:
        mark = "present" if section["present"] else "omitted"
        cut = " clipped" if section["truncated"] else ""
        digest = section["sha256"][:12] if section["sha256"] else "-"
        print(
            f"  {section['name']:<16} {mark:<8} {section['chars']:>6} chars "
            f"{section['tokens_estimate']:>5} tok  budget {section['budget_chars']:>5}{cut}  "
            f"sha256 {digest}  <- {section['source']}"
        )
        print(f"  {'':<16} {section['detail']}")
    cache = payload["cache"]
    if not cache["frozen"]:
        print(f"cache: not frozen — {cache['note'] or cache['reason']}")
    else:
        state = "stable" if cache["stable"] else "CACHE BREAK"
        print(f"cache: frozen ({cache['conversation']}) — {state}: {cache['reason']}")
    for item in cache["changed_sections"]:
        print(f"  {item['kind']:<8} {item['name']}")
    inputs = payload.get("inputs") or {}
    if inputs:
        print("inputs: " + " ".join(f"{key}={value}" for key, value in sorted(inputs.items())))


def cmd_fingerprint(args: argparse.Namespace) -> int:
    """The sha256 of the composed prompt, against the frozen prompt of the conversation."""
    assembled = _assemble(args)
    if assembled is None:
        return EXIT_USAGE
    context, assembly = assembled
    runtime, conversation = context.runtime, _conversation(args)
    status = P.cache_status(assembly, runtime, conversation)
    status, accepted, _frozen_now = _freeze_if_requested(
        args, assembly, runtime, conversation, status
    )
    payload = {
        "fingerprint": assembly.fingerprint,
        "chars": assembly.total_chars,
        "bytes": assembly.total_bytes,
        "tokens_estimate": assembly.tokens_estimate,
        "estimate_method": assembly.estimate_method,
        "sections": assembly.section_map(),
        "present": assembly.present,
        "omitted": assembly.omitted,
        "cache": _cache_payload(status),
        "cache_break_accepted": accepted,
        "conversation": conversation,
        "freeze_path": str(P.freeze_path(runtime)),
        "inputs": context.paths(),
    }
    warnings = [status.reason] if status.cache_break else []
    _publish(
        payload,
        evidence=[
            {
                "ref": f"sha256:{assembly.fingerprint}",
                "kind": "verification",
                "note": status.reason,
            },
            {
                "ref": str(P.freeze_path(runtime)),
                "kind": "file",
                "note": f"frozen state for conversation {conversation!r}",
            },
        ],
        warnings=warnings,
        next_actions=_next_actions(assembly, status, conversation),
    )
    _warn(warnings)
    if text_mode(args):
        _print_fingerprint(payload)
    elif not envelope_mode():
        dump(payload)
    if not assembly.text.strip():
        return _fail_with(EXIT_BLOCKED, "nothing could be assembled — every section is empty")
    if status.cache_break:
        return _fail_with(
            EXIT_CONFLICT,
            status.reason,
            hint="re-run with --accept-change to record the new prompt deliberately",
            details={"changed_sections": [dict(item) for item in status.changed_sections]},
        )
    return EXIT_OK


def _print_fingerprint(payload: dict) -> None:
    cache = payload["cache"]
    print(f"sha256:{payload['fingerprint']}")
    print(
        f"  {payload['chars']} chars / {payload['bytes']} bytes / {payload['tokens_estimate']} "
        f"tokens ({payload['estimate_method']})"
    )
    print(f"  sections present: {', '.join(payload['present']) or '(none)'}")
    if payload["omitted"]:
        print(f"  sections omitted: {', '.join(payload['omitted'])}")
    if not cache["frozen"]:
        print(f"frozen: no — {cache['reason']} ({payload['freeze_path']})")
        return
    previous = cache["previous_fingerprint"][:16]
    print(f"frozen: yes — conversation {cache['conversation']!r}, previous {previous}")
    print(f"stable: {'yes' if cache['stable'] else 'NO'} — {cache['reason']}")
    for item in cache["changed_sections"]:
        print(f"  {item['kind']:<8} {item['name']}")


# --------------------------------------------------------------------------- #
# the contract entries                                                          #
# --------------------------------------------------------------------------- #


def _contract_spec(**kwargs: Any) -> Any:
    """One ``CommandSpec`` when the contract exists, else ``None`` (standalone builds)."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return None
    return _CONTRACT.CommandSpec(**kwargs)


def _evidence(kind: str, ref: str, note: str) -> dict:
    """One evidence reference for this group's contract entries (kind first, on purpose).

    The argument order is the group's own: a reader of these entries asks "what kind of
    artifact backs this command" before "which path", and the freeze record is a *file*
    while the composer is *code*.
    """
    return {"kind": kind, "ref": ref, "note": note}


BUILD_NOTES = (
    "Prints the composed prompt verbatim on stdout (human view); diagnostics — truncation, "
    "budget, a refused cache break — go to stderr, so `skaldir prompt build > prompt.txt` "
    "captures exactly the bytes the fingerprint covers. `--freeze` records the fingerprint "
    "for the conversation; a later build whose fingerprint differs exits 6 (conflict) until "
    "the change is accepted with --accept-change. Exit 2 means the prompt is over --budget."
)
EXPLAIN_NOTES = (
    "The diagnostic view: per section the size, the budget, the clip state, the content "
    "hash, the source and the provider's own detail line — omitted sections included, with "
    "the reason they are absent. `inputs` names every path the assembly read. Over budget "
    "exits 2; a cache break is reported here but not refused (that is `prompt build`)."
)
FINGERPRINT_NOTES = (
    "The stability check: sha256 over the exact UTF-8 bytes of the composed prompt, compared "
    "against the frozen prompt of --conversation. Exits 6 when a frozen prompt exists and the "
    "fingerprint moved, unless --accept-change records the new prompt deliberately."
)

#: The ``data`` payload of each verb, declared for `contract dump` (a GUI renders forms
#: from this; the arrays are summarised rather than itemised — the shape is what matters).
_SECTION_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "title": {"type": "string"},
        "present": {"type": "boolean"},
        "chars": {"type": "integer"},
        "tokens_estimate": {"type": "integer"},
        "budget_chars": {"type": "integer"},
        "over_budget": {"type": "boolean"},
        "truncated": {"type": "boolean"},
        "sha256": {"type": "string"},
        "source": {"type": "string"},
        "detail": {"type": "string"},
    },
    "required": ["name", "present", "chars", "sha256", "source"],
}
_CACHE_SCHEMA = {
    "type": "object",
    "properties": {
        "fingerprint": {"type": "string"},
        "frozen": {"type": "boolean"},
        "stable": {"type": "boolean"},
        "cache_break": {"type": "boolean"},
        "conversation": {"type": "string"},
        "previous_fingerprint": {"type": "string"},
        "accepted_from": {"type": "string"},
        "changed_sections": {"type": "array"},
        "reason": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["fingerprint", "frozen", "stable", "cache_break", "reason"],
}
_MEASURE_SCHEMA = {
    "fingerprint": {"type": "string"},
    "chars": {"type": "integer"},
    "bytes": {"type": "integer"},
    "tokens_estimate": {"type": "integer"},
    "estimate_method": {"type": "string"},
    "budget_tokens": {"type": "integer"},
    "over_budget": {"type": "boolean"},
}
BUILD_DATA_SCHEMA = {
    "type": "object",
    "properties": {
        **_MEASURE_SCHEMA,
        "text": {"type": "string"},
        "sections": {"type": "array", "items": _SECTION_ROW_SCHEMA},
        "present": {"type": "array", "items": {"type": "string"}},
        "omitted": {"type": "array", "items": {"type": "string"}},
        "cache": _CACHE_SCHEMA,
        "cache_break_accepted": {"type": "boolean"},
        "frozen_now": {"type": "boolean"},
        "conversation": {"type": "string"},
        "inputs": {"type": "object"},
    },
    "required": ["fingerprint", "text", "sections", "cache", "budget_tokens", "over_budget"],
}
EXPLAIN_DATA_SCHEMA = {
    "type": "object",
    "properties": {
        **_MEASURE_SCHEMA,
        "section_count": {"type": "integer"},
        "sections": {"type": "array", "items": _SECTION_ROW_SCHEMA},
        "present": {"type": "array", "items": {"type": "string"}},
        "omitted": {"type": "array", "items": {"type": "string"}},
        "cache": _CACHE_SCHEMA,
        "conversation": {"type": "string"},
        "freeze_path": {"type": "string"},
        "inputs": {"type": "object"},
    },
    "required": ["fingerprint", "sections", "present", "omitted", "cache", "inputs"],
}
FINGERPRINT_DATA_SCHEMA = {
    "type": "object",
    "properties": {
        **_MEASURE_SCHEMA,
        "sections": {"type": "object"},
        "present": {"type": "array", "items": {"type": "string"}},
        "omitted": {"type": "array", "items": {"type": "string"}},
        "cache": _CACHE_SCHEMA,
        "cache_break_accepted": {"type": "boolean"},
        "conversation": {"type": "string"},
        "freeze_path": {"type": "string"},
        "inputs": {"type": "object"},
    },
    "required": ["fingerprint", "cache", "conversation", "freeze_path"],
}


CONTRACT_ENTRIES: dict[str, Any] = {
    path: spec
    for path, spec in (
        (
            "prompt.build",
            _contract_spec(
                layer="PERSIST",
                evidence=(
                    _evidence(
                        "code",
                        "src/skaldir/prompt_builder.py",
                        "the composer: seven fixed sections, per-section budgets, one fingerprint",
                    ),
                    _evidence("code", "src/skaldir/prompt_builder_cli.py", "this handler"),
                    _evidence(
                        "file",
                        "<runtime>/prompt-freeze.json",
                        "the frozen fingerprint of the conversation, when one was recorded",
                    ),
                ),
                description="compose the agent system prompt from the real inputs and print it",
                exit_codes=(0, 1, 2, 3, 6, 7),
                mutates=False,
                data_schema=BUILD_DATA_SCHEMA,
                notes=BUILD_NOTES,
            ),
        ),
        (
            "prompt.explain",
            _contract_spec(
                layer="PERSIST",
                evidence=(
                    _evidence("code", "src/skaldir/prompt_builder.py", "prompt_builder.explain"),
                    _evidence("code", "src/skaldir/prompt_builder_cli.py", "this handler"),
                ),
                description="section by section: size, budget, hash and source of each block",
                exit_codes=(0, 1, 2, 3, 7),
                mutates=False,
                data_schema=EXPLAIN_DATA_SCHEMA,
                notes=EXPLAIN_NOTES,
            ),
        ),
        (
            "prompt.fingerprint",
            _contract_spec(
                layer="PERSIST",
                evidence=(
                    _evidence(
                        "code",
                        "src/skaldir/prompt_builder.py",
                        "fingerprint_of + cache_status (the stability contract)",
                    ),
                    _evidence("code", "src/skaldir/prompt_builder_cli.py", "this handler"),
                    _evidence("file", "<runtime>/prompt-freeze.json", "the frozen fingerprint"),
                ),
                description="the sha256 of the composed prompt, against the frozen one",
                exit_codes=(0, 1, 3, 6, 7),
                mutates=False,
                data_schema=FINGERPRINT_DATA_SCHEMA,
                notes=FINGERPRINT_NOTES,
            ),
        ),
    )
    if spec is not None
}


def _short(text: str, limit: int = 60) -> str:
    """One rendered evidence field, clipped so the pasted block stays readable."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


#: The schema constant each rendered entry points at (the schemas stay in this module).
_DATA_SCHEMA_NAMES: dict[str, str] = {
    "prompt.build": "BUILD_DATA_SCHEMA",
    "prompt.explain": "EXPLAIN_DATA_SCHEMA",
    "prompt.fingerprint": "FINGERPRINT_DATA_SCHEMA",
}


def _render_entry(path: str, spec: Any) -> str:
    """One ``COMMANDS`` entry as the source text the CLI owner pastes."""
    refs = "\n".join(
        f'        _ev("{_short(item["ref"])}", "{item["kind"]}", "{_short(item["note"])}"),'
        for item in spec.evidence
    )
    schema = _DATA_SCHEMA_NAMES.get(path, "None")
    return (
        f'    "{path}": _C(\n'
        f'        "{spec.layer}", (\n{refs}\n        ),\n'
        f'        "{spec.description}",\n'
        f'        exit_codes={tuple(spec.exit_codes)},\n'
        f'        mutates={spec.mutates},\n'
        f'        data_schema={schema},\n'
        f'        notes="{spec.notes[:70]}…",\n'
        f'    ),'
    )


def contract_snippet() -> str:
    """The wiring the CLI owner applies, **rendered from the entries themselves**.

    Generated rather than hand-written: a paste-ready block that drifts from what
    ``install_contract_entries`` installs is worse than no block. Note that
    ``register_cli`` already installs the entries, so pasting is optional — the block
    exists for a CLI owner who prefers the entries inline in ``cli_contract.py``.
    """
    header = (
        "# src/skaldir/cli_contract.py — the `prompt` group's entries, rendered from\n"
        "# prompt_builder_cli.CONTRACT_ENTRIES (register_cli installs them on its own).\n"
        "# The data schemas live in prompt_builder_cli (BUILD/EXPLAIN/FINGERPRINT_DATA_SCHEMA).\n"
        "COMMANDS.update({\n"
    )
    body = "\n".join(_render_entry(path, spec) for path, spec in sorted(CONTRACT_ENTRIES.items()))
    footer = (
        "})\n\n"
        "# src/skaldir/cli.py — right after `sub = parser.add_subparsers(...)`\n"
        "from .prompt_builder_cli import register_cli as _register_prompt_cli\n"
        "_register_prompt_cli(sub)\n"
    )
    return header + body + "\n" + footer


#: The rendered wiring block (stable: the entries are module-level constants).
CONTRACT_SNIPPET = contract_snippet()



def install_contract_entries() -> list[str]:
    """Install the group's ``COMMANDS`` entries when they are missing (idempotent)."""
    return _shared_install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)


# --------------------------------------------------------------------------- #
# the parser                                                                    #
# --------------------------------------------------------------------------- #


def _add_context_flags(parser: Any) -> None:
    """The flags every verb shares: what the assembly reads, and how big it may be."""
    parser.add_argument("--project", default="",
                        help="project directory the assembly reads (default: the cwd)")
    parser.add_argument("--runtime", default="",
                        help="runtime dir (default: $SKALDIR_HOME, else ~/.hermes/skaldir)")
    parser.add_argument("--memory-db", default="",
                        help="memory / skill store (default: <runtime>/memory.db)")
    parser.add_argument("--tools-db", default="",
                        help="runtime tool registry (default: <runtime>/tools.db)")
    parser.add_argument("--prompts-root", default="",
                        help="prompts directory for the hard rules (default: the package's)")
    parser.add_argument("--budget", type=int, default=0,
                        help=f"total token budget (default {P.DEFAULT_TOTAL_BUDGET_TOKENS})")
    parser.add_argument("--section-budget", action="append", default=[], metavar="NAME=CHARS",
                        help="override one section's character budget (repeatable)")
    parser.add_argument("--without", action="append", default=[], metavar="SECTION",
                        help=f"omit a section: {', '.join(P.SECTION_ORDER)} (repeatable)")
    parser.add_argument("--conversation", default=P.DEFAULT_CONVERSATION,
                        help=f"the conversation whose frozen prompt is compared "
                             f"(default {P.DEFAULT_CONVERSATION!r})")


def _add_freeze_flags(parser: Any) -> None:
    freezes = parser.add_mutually_exclusive_group()
    freezes.add_argument("--freeze", action="store_true",
                         help="record this fingerprint as the frozen prompt of the conversation")
    freezes.add_argument("--accept-change", action="store_true",
                         help="deliberately accept a reported cache break (re-freezes)")


def _add_build_flags(parser: Any) -> None:
    _add_context_flags(parser)
    _add_freeze_flags(parser)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true",
                        help="print the prompt verbatim (the human default for `build`)")


def _add_read_flags(parser: Any) -> None:
    _add_context_flags(parser)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")


def _add_prompt_verbs(sub: Any) -> Any:
    """Add the three ``prompt`` verbs to a subparsers handle (one definition)."""
    p_build = sub.add_parser("build", help=BUILD_HELP)
    _add_build_flags(p_build)
    p_build.set_defaults(func=cmd_build, command_path="prompt.build")

    p_explain = sub.add_parser("explain", help=EXPLAIN_HELP)
    _add_read_flags(p_explain)
    p_explain.set_defaults(func=cmd_explain, command_path="prompt.explain")

    p_fingerprint = sub.add_parser("fingerprint", help=FINGERPRINT_HELP)
    _add_context_flags(p_fingerprint)
    _add_freeze_flags(p_fingerprint)
    p_fingerprint.add_argument("--json", action="store_true")
    p_fingerprint.add_argument("--text", action="store_true")
    p_fingerprint.set_defaults(func=cmd_fingerprint, command_path="prompt.fingerprint")

    for parser in (p_build, p_explain, p_fingerprint):
        share_common_flags(parser)
    return sub


def build_prompt_parser(subparsers: Any) -> Any:
    """Add the ``prompt`` command group to a CLI's ``subparsers`` handle."""
    parser = subparsers.add_parser(
        "prompt",
        help=GROUP_HELP,
        description=(
            "The system prompt of an agent turn is seven named sections with a budget each. "
            "`prompt build` composes it from the real inputs (the versioned steering prompt, "
            "the memory and skill stores, the tool catalogue, the project context) and prints "
            "it verbatim; `explain` shows the size, budget, hash and source of every section; "
            "`fingerprint` answers whether those bytes are still the ones a live conversation "
            "was frozen with — because a prompt cache is keyed on the bytes, and changing them "
            "mid-conversation is a cost decision, not a cosmetic one."
        ),
    )
    sub = parser.add_subparsers(dest="prompt_command", required=True)
    _add_prompt_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Installs the group's contract entries as part of the wiring, so a CLI that adds the
    group cannot leave ``COMMANDS`` behind (``tests/test_cli_contract_dump.py`` would
    otherwise fail the moment the group is reachable).
    """
    install_contract_entries()
    return build_prompt_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir prompt", description=GROUP_HELP)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_prompt_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``python -m skaldir.prompt_builder_cli …`` — the same verbs, the same exit codes.

    This is the out-of-process entry point: it exists so the group can be exercised (and
    its wiring snippet proven) without the product CLI, and it deliberately does not build
    the envelope — through ``skaldir prompt`` the contract's ``run_command`` owns that.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:  # argparse owns --help and every usage error
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    code = int(handler(args))
    try:
        sys.stdout.flush()
    except BrokenPipeError:  # `skaldir prompt build | head` must not traceback
        return EXIT_OK
    return code


__all__ = [
    "BUILD_HELP",
    "BUILD_NOTES",
    "CONTRACT_ENTRIES",
    "CONTRACT_SNIPPET",
    "EXPLAIN_HELP",
    "EXPLAIN_NOTES",
    "FINGERPRINT_HELP",
    "FINGERPRINT_NOTES",
    "GROUP_HELP",
    "build_parser",
    "build_prompt_parser",
    "cmd_build",
    "cmd_explain",
    "cmd_fingerprint",
    "contract_snippet",
    "install_contract_entries",
    "main",
    "register_cli",
]


if __name__ == "__main__":  # pragma: no cover — convenience only
    sys.exit(main())
