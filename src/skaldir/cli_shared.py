"""One home for the plumbing every ``skaldir`` command group otherwise copies.

Every ``*_cli.py`` group needs the same handful of small things:

* resolve the product CLI's envelope module (:func:`product_contract`),
* turn the ``--runtime`` flag into a directory (:func:`runtime_dir`),
* resolve runtime / project / DB through the registry (:func:`registry_from_args`),
* put the product CLI's shared flags (``--format`` / ``--quiet`` / ``--no-color``)
  on the group's leaves (:func:`share_common_flags`),
* say whether the caller asked for human text (:func:`text_mode`) and whether the
  product driver is already carrying the envelope (:func:`envelope_mode`),
* print one payload as the one JSON document (:func:`dump`),
* report the three shared refusal codes (:func:`blocked`, :func:`not_found`,
  :func:`verification_failed`) and hand a group's ``COMMANDS`` entries to the
  contract (:func:`install_contract_entries`),
* read the ``--session`` a verb files its evidence under (:func:`session_id`).

Copied per group they drift apart — ``_product_contract`` existed nine times,
``_text_mode`` six, ``_dump`` and ``_share_common_flags`` five, ``_envelope_mode``
five — and a fix applied to one copy silently missed the others. Here each has
exactly one implementation, and ``tests/test_cli_shared.py`` fails when a group
defines one of them again.

Two rules keep it importable from everywhere:

* **No group imports.** Nothing here may import ``runs_cli`` / ``verify_cli`` /
  ``compose_cli`` / ``act_cli`` (or any other group) — that would turn the shared
  helper into a cycle.
* **Lazy, tolerant dependencies.** ``registry`` and ``cli_contract`` are imported
  lazily and their absence is tolerated, so a group keeps running standalone in a
  build that has neither, instead of failing at import time.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "as_subparsers",
    "blocked",
    "client_and_status",
    "driven",
    "dump",
    "envelope_mode",
    "ev",
    "human",
    "install_contract_entries",
    "not_found",
    "product_contract",
    "registry_from_args",
    "report_envelope",
    "runtime_dir",
    "session_id",
    "share_common_flags",
    "text_mode",
    "verification_failed",
]


def product_contract() -> Any:
    """The product CLI's envelope module, or ``None`` when this build has none."""
    try:
        from . import cli_contract
    except Exception:  # noqa: BLE001 — an older build without the contract module
        return None
    return cli_contract


#: Resolved once at import, like every group resolved its own copy of the contract.
_CONTRACT = product_contract()

#: The contract's refusal codes, with the fallbacks the groups carried before.
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))


def runtime_dir(args: argparse.Namespace) -> Path:
    """``--runtime`` when the caller gave one, else the product's runtime directory."""
    explicit = str(getattr(args, "runtime", "") or "")
    if explicit:
        return Path(explicit).expanduser()
    from .registry import default_runtime_dir

    return default_runtime_dir()


def registry_from_args(args: argparse.Namespace) -> Any:
    """The ONE registry truth — the same resolution ``cli.py`` uses (§1.2)."""
    from .registry import resolve_registry

    return resolve_registry(
        runtime=getattr(args, "runtime", "") or None,
        project=getattr(args, "project", "") or None,
        db=getattr(args, "db", None) or None,
    )


def as_subparsers(target: Any) -> Any:
    """Accept the subparsers action (what ``build_parser`` passes) or a parser that has one."""
    if hasattr(target, "add_parser") and hasattr(target, "choices"):
        return target
    action = next(
        (
            item
            for item in getattr(target, "_actions", [])
            if isinstance(item, argparse._SubParsersAction)
        ),
        None,
    )  # argparse exposes no public API for this — walking `_actions` is the way
    if action is None:
        raise TypeError("register_cli needs a subparsers action or a parser that has one")
    return action


def share_common_flags(parser: Any) -> None:
    """Attach the product CLI's shared flags (``--format/--quiet/--no-color``)."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "add_common_flags"):
        return
    try:
        _CONTRACT.add_common_flags(parser)
    except Exception:  # noqa: BLE001 — a flag helper must never break the group
        return


def text_mode(args: argparse.Namespace) -> bool:
    """Human text only when it is explicitly asked for (``--text``/``--format text``)."""
    if bool(getattr(args, "text", False)):
        return True
    return str(getattr(args, "format", "") or "").strip().lower() == "text"


def envelope_mode() -> bool:
    """True while the product driver runs us — its envelope already carries the data."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "current"):
        return False
    try:
        context = _CONTRACT.current()
    except Exception:  # noqa: BLE001 — a context problem must not break a verb
        return False
    return bool(context is not None and getattr(context, "json_mode", False))


def session_id(args: argparse.Namespace) -> str:
    """The ``--session`` a verb files its evidence under (empty when none was given)."""
    return str(getattr(args, "session", "") or "")


def dump(payload: Any) -> None:
    """Print one payload as the one JSON document."""
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def install_contract_entries(entries: Mapping[str, Any], contract: Any) -> list[str]:
    """Add one group's ``COMMANDS`` entries (idempotent); returns the paths added.

    ``contract`` is the module :func:`product_contract` returned — passed in rather than
    read from here, so the group's own (re-resolved) handle is the one that gets filled.
    """
    if not entries or contract is None:
        return []
    added: list[str] = []
    for path, entry in entries.items():
        if path not in contract.COMMANDS:
            contract.COMMANDS[path] = entry
            added.append(path)
    return added


def blocked(
    message: str, *, hint: str = "", next_command: str = "", reason: str = "", details: Any = None
) -> int:
    """Exit 3 through the shared helper when present, else by hand."""
    if _CONTRACT is not None and hasattr(_CONTRACT, "blocked"):
        return int(
            _CONTRACT.blocked(
                message, hint=hint, next_command=next_command, reason=reason, details=details
            )
        )
    return EXIT_BLOCKED


def not_found(message: str, *, hint: str = "", details: Any = None) -> int:
    """Exit 5 through the shared helper when present, else by hand."""
    if _CONTRACT is not None and hasattr(_CONTRACT, "not_found"):
        return int(_CONTRACT.not_found(message, hint=hint, details=details))
    return EXIT_NOT_FOUND


def verification_failed(message: str, *, hint: str = "", details: Any = None) -> int:
    """Exit 2 through the shared helper when present, else by hand."""
    if _CONTRACT is not None and hasattr(_CONTRACT, "verification_failed"):
        return int(_CONTRACT.verification_failed(message, hint=hint, details=details))
    return EXIT_VERIFICATION_FAILED


# --------------------------------------------------------------------------- #
# the envelope plumbing every group needs (one home, not a copy per group)      #
# --------------------------------------------------------------------------- #


def driven() -> bool:
    """True when a command runner owns the envelope (so we only *add* to it)."""
    if _CONTRACT is None or not callable(getattr(_CONTRACT, "current", None)):
        return False
    try:
        return _CONTRACT.current() is not None
    except Exception:  # noqa: BLE001 — never let reporting break a command
        return False


def report_envelope(data: Any, evidence: list[dict[str, str]] | None = None) -> None:
    """Put one group's payload (and its evidence) into the contract's envelope."""
    if _CONTRACT is None or not driven():
        return
    try:
        _CONTRACT.set_data(data)
        for item in evidence or []:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")),
                str(item.get("kind", "code")),
                note=str(item.get("note", "")),
            )
    except Exception:  # noqa: BLE001 — reporting must never break the command
        return


def ev(ref: str, kind: str = "code", note: str = "") -> dict[str, Any]:
    """Evidence entry in ``cli_contract``'s shape (built here, not imported)."""
    return {"ref": str(ref), "kind": str(kind), "note": str(note)}


def human(text: str = "") -> None:
    """Human line (stdout; in ``--json`` mode the runner moves it to stderr)."""
    print(text)


def client_and_status(args: argparse.Namespace) -> tuple[Any, dict[str, Any]]:
    """Resolve the real provider client (never a scripted stand-in).

    ``--base-url``/``--model`` are explicit overrides; everything else follows the
    documented ``SKALDIR_LLM_*`` / ``godsv.llm`` precedence. Raises
    :class:`skaldir.llm_provider.ProviderConfigError` when nothing usable is
    configured — the caller turns that into exit 4 with the exact exports.
    """
    from .llm_provider import provider_for, provider_status

    explicit: dict[str, Any] = {}
    if str(getattr(args, "base_url", "") or "").strip():
        explicit["base_url"] = str(args.base_url).strip()
    if str(getattr(args, "model", "") or "").strip():
        explicit["model"] = str(args.model).strip()
    client = provider_for(explicit or None)
    cfg = getattr(client, "config", None)
    status = cfg.redacted() if cfg is not None else {
        "provider": type(client).__name__,
        "model": "",
        "base_url": "",
        "endpoint": "",
        "api_key": "n/a",  # a client without a ProviderConfig holds no key to report
        "ready": False,
    }
    try:  # the full, redacted view is what a machine consumer wants
        status = provider_status(cfg if cfg is not None else explicit or None)
    except Exception:  # noqa: BLE001 — the redacted view above is the fallback
        pass
    return client, status
