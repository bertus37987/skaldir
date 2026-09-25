"""``skaldir prompt cache`` — where the cache markers land, and what was measured.

Two verbs over :mod:`skaldir.prompt_cache` (the module that owns the breakpoints,
the byte-stable prefix and the one-word verdict)::

====================  ==================================================================
``prompt cache status`` reports the prompt-cache surface of the *configured* provider:
                      the strategy it needs (``explicit`` markers or an automatic
                      prefix), the usage fields a verdict is read from, the
                      documentation that claim came from, and the ledger of requests
                      that were actually measured. It never calls the provider — a
                      verdict nobody observed stays ``unknown``.
``prompt cache plan``  shows where ``cache_control`` would land on a given prompt:
                      the blocks of the system message, every breakpoint with its
                      path, the targets that were skipped **and why**, and the sha256
                      of the byte-stable prefix. It builds a plan, it does not send one.
====================  ==================================================================

The rules this group keeps visible:

* **A prediction is not an observation.** The plan reports what a byte-compare says
  *should* be reusable; the status reports what the provider's usage payload said.
  They are printed as separate fields with separate names.
* **``unknown`` is a real answer.** A provider that reports no cache counter gets
  ``unknown`` — never a zero, never a "probably a hit".
* **Nothing is sent.** ``plan`` only explains; ``status`` only reads. Sending a plan
  is the provider client's job (``OpenAICompatibleLLM.chat``), and it only does so for
  a provider whose documented mode is ``explicit``.

Exit codes — the product CLI's :mod:`skaldir.cli_contract` vocabulary::

    0   ok                  a strategy and/or a plan was produced
    1   usage               argparse refused the call, or an unknown --provider/--ttl
    3   blocked             nothing could be answered (no provider config, no prompt)
    7   internal            a broken ledger line count is reported, never raised

The group is added by the product CLI through one call (the CLI owner wires it; this
module never edits ``cli.py``). ``register_cli`` is **create-or-extend**: it merges
into an existing ``prompt`` group (``prompt_builder_cli`` owns the same one) instead of
failing with ``conflicting subparser: prompt``, and the other order works too::

    from .prompt_cache_cli import register_cli
    register_cli(sub)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import llm_provider as lp
from . import prompt_cache as C
from .cli_shared import (
    as_subparsers,
    blocked,
    envelope_mode,
    runtime_dir,
    share_common_flags,
    text_mode,
)
from .cli_shared import (
    install_contract_entries as _install_contract_entries,
)
from .cli_shared import (
    product_contract as _product_contract,
)

__all__ = [
    "CONTRACT_ENTRIES",
    "GROUP_HELP",
    "PLAN_HELP",
    "STATUS_HELP",
    "build_cache_parser",
    "build_parser",
    "cmd_plan",
    "cmd_status",
    "install_contract_entries",
    "main",
    "register_cli",
]

_CONTRACT = _product_contract()

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3

GROUP_HELP = "prompt caching: the breakpoints, the stable prefix, and what was measured"
STATUS_HELP = "the cache surface of the configured provider + what the ledger measured"
PLAN_HELP = "where cache_control would land on a prompt (built, never sent)"

STATUS_NOTES = (
    "`status` answers two different questions in one payload and keeps them apart: "
    "`strategy` is what the provider documents (mode, usage fields, reference), "
    "`summary`/`last` are what its usage payloads reported. A provider that sent no "
    "cache counter counts as `unknown` here, never as a hit — the counter is the only "
    "thing that may say `hit`, and it comes from the answer, not from our prefix."
)

PLAN_NOTES = (
    "`plan` places `cache_control` markers on the last stable block of the system "
    "message, on the last tool, and inside the system prompt at the end of the "
    "project-context section when the composer's own section list is supplied "
    "(`--assembly`). A target that does not exist is reported in `skipped` with its "
    "reason instead of being invented, and the plan is never sent by this command."
)


def _contract_entries() -> dict[str, Any]:
    """The group's ``COMMANDS`` entries (built once, at import)."""
    contract = _CONTRACT
    if contract is None:  # pragma: no cover - a build without the contract module
        return {}
    return {
        "prompt.cache.status": contract.CommandSpec(
            "PERSIST",
            (
                {"ref": "src/skaldir/prompt_cache.py", "kind": "code",
                 "note": "the strategy table and the usage-field reader"},
                {"ref": "src/skaldir/llm_provider.py", "kind": "code",
                 "note": "OpenAICompatibleLLM._finish_cache: the per-request verdict"},
                {"ref": "<runtime>/prompt-cache.jsonl", "kind": "file",
                 "note": "one line per measured request, when a ledger is enabled"},
            ),
            "the prompt-cache surface of the configured provider",
            exit_codes=(0, 1, 3, 7),
            data_schema={"type": "object", "additionalProperties": True},
            notes=STATUS_NOTES,
        ),
        "prompt.cache.plan": contract.CommandSpec(
            "PERSIST",
            (
                {"ref": "src/skaldir/prompt_cache.py", "kind": "code",
                 "note": "explicit_breakpoints + section_blocks"},
                {"ref": "src/skaldir/prompt_cache_cli.py", "kind": "code", "note": "this handler"},
            ),
            "where cache_control would land on a prompt (never sent)",
            exit_codes=(0, 1, 3),
            data_schema={"type": "object", "additionalProperties": True},
            notes=PLAN_NOTES,
        ),
    }


#: The group's contract entries — ``register_cli`` installs them on its own.
CONTRACT_ENTRIES: dict[str, Any] = _contract_entries()


def install_contract_entries() -> list[str]:
    """Install the group's ``COMMANDS`` entries when they are missing (idempotent)."""
    return _install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)


def _publish(
    payload: Any,
    *,
    evidence: tuple[dict, ...] = (),
    warnings: tuple[str, ...] = (),
    next_actions: tuple[tuple[str, str], ...] = (),
) -> None:
    """Hand this verb's payload to the product envelope, when there is one.

    An envelope problem must never turn an answered cache status into an internal
    error: the payload is the deliverable, the envelope is transport.
    """
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind") or "code"),
                str(item.get("note", "")),
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 - an envelope problem must not break a verb
        return


# --------------------------------------------------------------------------- #
# verbs                                                                         #
# --------------------------------------------------------------------------- #


def _ledger_flag(args: argparse.Namespace) -> Any:
    """``--ledger`` when given, else the environment rule (``None`` = no reading).

    Without a flag or an environment variable the ledger path is the runtime
    default, *read-only*: ``status`` reads it, and only the provider client ever
    appends to one.
    """
    explicit = str(getattr(args, "ledger", "") or "").strip()
    if explicit:
        return C.CacheLedger(explicit)
    return C.CacheLedger(C.default_ledger_path(runtime_dir(args)))


def cmd_status(args: argparse.Namespace) -> int:
    """Report the configured provider's cache strategy and the measured requests."""
    payload = lp.prompt_cache_surface(
        config_path=str(getattr(args, "config", "") or "") or None,
        ledger=_ledger_flag(args),
        limit=int(getattr(args, "limit", 0) or 0),
    )
    payload["support"] = C.support_table()
    payload["runtime"] = str(runtime_dir(args))
    strategy = payload.get("strategy") or {}
    if payload.get("strategy") is None:
        message = "; ".join(payload.get("problems") or ["no provider configuration"])
        _publish(payload, warnings=(message,), next_actions=(("skaldir provider status --json",
                                                             "resolve the provider first"),))
        if text_mode(args) or not envelope_mode():
            print(f"prompt cache: {message}", file=sys.stderr)
        return blocked(message, hint="export SKALDIR_LLM_BASE_URL/_MODEL/_API_KEY",
                       reason="no strategy can be claimed without a configured provider")

    summary = payload.get("summary") or {}
    counts = summary.get("counts") or {}
    evidence = (
        {"ref": "src/skaldir/prompt_cache.py", "kind": "code",
         "note": "strategy table + cache_report (usage fields only)"},
    )
    if strategy.get("reference"):
        evidence += (
            {"ref": str(strategy["reference"]), "kind": "code",
             "note": f"the {strategy['provider']} documentation this strategy came from"},
        )
    if int(payload.get("entries") or 0):
        evidence += (
            {"ref": str(payload.get("ledger") or ""), "kind": "file",
             "note": f"{payload['entries']} measured request(s)"},
        )
    warnings: tuple[str, ...] = ()
    if not int(payload.get("entries") or 0):
        warnings = (
            "no request has been measured yet: the ledger is opt-in "
            "(SKALDIR_PROMPT_CACHE_LEDGER=<path>) — nothing is simulated here",
        )
    _publish(payload, evidence=evidence, warnings=warnings)

    if text_mode(args) or not envelope_mode():
        print(
            f"provider {strategy.get('provider')} mode {strategy.get('mode')} "
            f"({strategy.get('confidence')}, matched by {strategy.get('matched_by') or 'nothing'})"
        )
        print(f"  reads: {', '.join(strategy.get('read_fields') or []) or '-'}")
        print(f"  writes: {', '.join(strategy.get('write_fields') or []) or '-'}")
        print(f"  misses: {', '.join(strategy.get('miss_fields') or []) or '-'}")
        if strategy.get("reference"):
            print(f"  reference: {strategy['reference']}")
        if strategy.get("note"):
            print(f"  note: {strategy['note']}")
        print(
            f"ledger {payload.get('ledger')}: {counts.get(C.CACHE_HIT, 0)} hit / "
            f"{counts.get(C.CACHE_MISS, 0)} miss / {counts.get(C.CACHE_UNKNOWN, 0)} unknown "
            f"({summary.get('hit_tokens', 0)} hit tokens, "
            f"{summary.get('read_tokens', 0)} read from cache, "
            f"{summary.get('write_tokens', 0)} written)"
        )
        if not int(payload.get("entries") or 0):
            print("  no measured request yet — the ledger is opt-in (SKALDIR_PROMPT_CACHE_LEDGER)")
    return EXIT_OK


def _assembly_from_file(path: str) -> Any:
    """A composer ``PromptAssembly`` read from JSON: ``{text, sections:[…]}``.

    The composer's own section list is what makes the in-prompt breakpoint exact:
    :func:`skaldir.prompt_cache.section_blocks` only accepts a split that
    reconstructs the prompt **byte for byte**, so a hand-written section list that
    does not add up is refused instead of placing a marker in the wrong place.
    """
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        raise ValueError(
            "an assembly file is {text: str, sections: [{name, title, text, present}]}"
        )
    sections = [
        SimpleNamespace(
            name=str(item.get("name") or ""),
            title=str(item.get("title") or ""),
            text=str(item.get("text") or ""),
            present=bool(item.get("present", True)),
        )
        for item in data["sections"]
        if isinstance(item, dict)
    ]
    return SimpleNamespace(text=str(data.get("text") or ""), sections=sections)


def _prompt_text(args: argparse.Namespace) -> tuple[str, str]:
    """``(prompt, source)`` from ``--prompt`` / ``--prompt-file`` / stdin."""
    literal = str(getattr(args, "prompt", "") or "")
    if literal:
        return literal, "argument"
    path = str(getattr(args, "prompt_file", "") or "")
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8"), f"file:{path}"
    if not sys.stdin.isatty():
        return sys.stdin.read(), "stdin"
    return "", ""


def cmd_plan(args: argparse.Namespace) -> int:
    """Place the breakpoints on a prompt and report every one (never sent)."""
    text, source = _prompt_text(args)
    if not text.strip():
        return blocked(
            "no prompt to plan",
            hint="pass --prompt, --prompt-file or pipe one on stdin",
            reason="a breakpoint plan without bytes would be an invention",
        )
    try:
        requested = str(getattr(args, "assembly", "") or "")
        assembly = _assembly_from_file(requested) if requested else None
    except (OSError, ValueError) as exc:
        print(f"skaldir prompt cache: {exc}", file=sys.stderr)
        return EXIT_USAGE
    tools: list[dict[str, Any]] = []
    if getattr(args, "tools_file", ""):
        try:
            loaded = json.loads(Path(str(args.tools_file)).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"skaldir prompt cache: tools file: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if not isinstance(loaded, list):
            print("skaldir prompt cache: the tools file must hold a JSON array", file=sys.stderr)
            return EXIT_USAGE
        tools = [dict(item) for item in loaded if isinstance(item, dict)]

    strategy = C.strategy_for(
        str(getattr(args, "provider", "") or ""),
        base_url=str(getattr(args, "base_url", "") or ""),
        model=str(getattr(args, "model", "") or ""),
    )
    try:
        plan = C.explicit_breakpoints(
            [{"role": "system", "content": text}],
            tools=tools,
            strategy=strategy,
            assembly=assembly,
            ttl=str(getattr(args, "ttl", "") or ""),
        )
    except C.CachePlanError as exc:
        print(f"skaldir prompt cache: {exc}", file=sys.stderr)
        return EXIT_USAGE

    payload = plan.to_dict()
    payload["prompt"] = {
        "source": source,
        "bytes": len(text.encode("utf-8")),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "assembly": bool(assembly),
        "tools": len(tools),
    }
    payload["preview"] = [
        {
            "index": position,
            "chars": len(str(block.get("text") or "")),
            "cache_control": dict(block.get("cache_control") or {}),
        }
        for position, block in enumerate(plan.messages[0]["content"])
        if isinstance(block, dict)
    ]
    _publish(
        payload,
        evidence=(
            {"ref": "src/skaldir/prompt_cache.py", "kind": "code",
             "note": "explicit_breakpoints: the markers and their paths"},
            {"ref": f"prompt:{source}", "kind": "verification",
             "note": f"{len(text.encode('utf-8'))} bytes planned"},
        ),
        warnings=tuple(str(item["reason"]) for item in plan.skipped),
        next_actions=(("skaldir prompt cache status --json",
                           "see whether the provider actually reported a hit"),),
    )
    if text_mode(args) or not envelope_mode():
        print(
            f"breakpoints {plan.placed} on {len(text.encode('utf-8'))} bytes "
            f"(provider {strategy.provider or 'unknown'}, mode {strategy.mode})"
        )
        for item in plan.breakpoints:
            print(f"  {item.target:<16} {item.block:<8} {item.path}  <- {item.source}")
        for item in plan.skipped:
            print(f"  {item['target']:<16} skipped  {item['reason']}")
        prefix = payload.get("prefix") or {}
        print(
            f"  stable prefix sha256:{str(prefix.get('sha256') or '')[:16]} "
            f"({prefix.get('bytes', 0)} bytes, {prefix.get('messages', 0)} message(s), "
            f"{prefix.get('tools', 0)} tool(s))"
        )
        if not assembly:
            print("  note: pass --assembly to place the project-context marker inside the prompt")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# the parser                                                                    #
# --------------------------------------------------------------------------- #


def _add_status_flags(parser: Any) -> None:
    parser.add_argument("--runtime", default="",
                        help="runtime dir (default: $SKALDIR_HOME, else ~/.hermes/skaldir)")
    parser.add_argument("--config", default="",
                        help="provider config file (default: the resolved godsv.llm block)")
    parser.add_argument("--ledger", default="",
                        help="the measured-request ledger (default: <runtime>/prompt-cache.jsonl)")
    parser.add_argument("--limit", type=int, default=0,
                        help="consider only the newest N ledger lines (0 = all)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")


def _add_plan_flags(parser: Any) -> None:
    parser.add_argument("--prompt", default="", help="the system prompt text to plan")
    parser.add_argument("--prompt-file", default="", help="a file holding the prompt")
    parser.add_argument("--tools-file", default="",
                        help="a JSON array of tool schemas (the request's tools)")
    parser.add_argument("--assembly", default="",
                        help='a composer assembly as JSON: {"text": …, "sections": […]}')
    parser.add_argument("--provider", default="",
                        help="a catalog provider id (deepseek, openai, …)")
    parser.add_argument("--base-url", default="", help="match the strategy by endpoint host")
    parser.add_argument("--model", default="", help="match the strategy by model name")
    parser.add_argument("--ttl", default="",
                        help=f"cache ttl {list(C.CACHE_TTLS)} (default: unset)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--text", action="store_true")


def _add_cache_verbs(sub: Any) -> Any:
    """Add the two ``prompt cache`` verbs to a subparsers handle (one definition)."""
    p_status = sub.add_parser("status", help=STATUS_HELP)
    _add_status_flags(p_status)
    p_status.set_defaults(func=cmd_status, command_path="prompt.cache.status")

    p_plan = sub.add_parser("plan", help=PLAN_HELP)
    _add_plan_flags(p_plan)
    p_plan.set_defaults(func=cmd_plan, command_path="prompt.cache.plan")

    for parser in (p_status, p_plan):
        share_common_flags(parser)
    return sub


def build_cache_parser(subparsers: Any) -> Any:
    """Add (or extend) ``prompt cache`` on a CLI's ``subparsers`` handle.

    **Create-or-extend, never return-early**: ``prompt_builder_cli`` owns a
    ``prompt`` group too, and whichever module is wired first creates it — the
    other attaches its own verbs to the same ``argparse._SubParsersAction``. Both
    orders produce the same leaf set, which is what the wiring test proves.
    """
    subs = as_subparsers(subparsers)
    group = subs.choices.get("prompt")
    if group is None:
        group = subs.add_parser(
            "prompt",
            help="assemble, explain and fingerprint the agent's system prompt",
            description=(
                "The system prompt as an artifact: the composer builds it, "
                "`prompt cache` explains the provider-side cache it feeds — where the "
                "breakpoints land, how stable the prefix is, and what the provider "
                "reported back."
            ),
        )
    verbs = next(
        (
            action
            for action in getattr(group, "_actions", [])
            if isinstance(action, argparse._SubParsersAction)
        ),
        None,
    )
    if verbs is None:
        verbs = group.add_subparsers(dest="prompt_command", required=True)
    if "cache" in verbs.choices:
        return verbs.choices["cache"]  # idempotent: a second call adds nothing
    cache = verbs.add_parser("cache", help=GROUP_HELP, description=GROUP_HELP)
    sub = cache.add_subparsers(dest="cache_command", required=True)
    _add_cache_verbs(sub)
    return cache


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Installs the group's contract entries as part of the wiring, so a CLI that adds
    the group cannot leave ``COMMANDS`` behind (``tests/test_cli_contract_dump.py``
    would otherwise fail the moment the group becomes reachable).
    """
    install_contract_entries()
    return build_cache_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir prompt cache", description=GROUP_HELP)
    sub = parser.add_subparsers(dest="cache_command", required=True)
    _add_cache_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``python -m skaldir.prompt_cache_cli …`` — the same verbs, the same exit codes.

    This is the out-of-process entry point: it exists so the group can be exercised
    (and its wiring snippet proven) without the product CLI, and it deliberately does
    not build the envelope — through ``skaldir prompt cache`` the contract's
    ``run_command`` owns that.
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
    return int(handler(args))


if __name__ == "__main__":  # pragma: no cover - the module entry point
    raise SystemExit(main())
