"""The Jev runtime binding — flags, the real provider, and one wired service.

`skaldir selftest --json` reported the ROUTE layer **blocked** for two reasons,
both of which live outside the façade:

1. *"System-One is off in ~/.hermes/godsv/settings.yaml (enabled=false)"* — the
   flag file did not exist, so the substrate's safe default (everything off) was
   in force;
2. *"no provider adapter is wired"* — the CLI's ROUTE probe builds its service
   with :func:`skaldir.system_one_service.build_service` and **no client**, so a
   service whose flags name the LLM adapter degrades to the disabled evaluator.

This module closes both gaps without touching the probe (the CLI owns
``cli_contract.py``) and without a second provider edge:

* :func:`enable` / :func:`disable` — the settings file gets a documented,
  **atomic** and **reversible** edit (backup + dotted-key diff, see
  :mod:`skaldir.settings_service`), so "System-One is on" becomes a fact on disk
  and ``--restore`` puts the previous file back byte for byte.
* :func:`service_for` — the wired service: the flags are read, and when they name
  the LLM adapter the real client comes from the **provider config**, resolved in this
  order (see :func:`provider_resolution`):

  1. ``godsv.jev`` — the TypeSafe/Jev provider block ``skaldir setup`` writes through
     :func:`skaldir.setup_flow.typesafe_provider_config`;
  2. ``godsv.providers.entries.jev`` — the same provider in the named registry;
  3. ``godsv.llm`` + the ``SKALDIR_LLM_*`` environment — the general provider, which is
     what this runtime resolved alone before this wiring, so an installation that never
     ran ``skaldir setup`` behaves exactly as before.

  Every payload and evidence row names which of the three decided (``config_source`` /
  ``provider_config``), because "a decision happened" without the config it happened on
  is not evidence. A TypeSafe block that is *present but unusable* blocks by name — it
  never silently falls back, which is also what keeps an inherited ``SKALDIR_LLM_*``
  from redirecting a Jev decision to the general endpoint.

  The construction itself stays in :mod:`skaldir.jev.decide`, the one module allowed to
  import the service facade.
* :func:`status` — the same two probes the CLI's ``_probe_route`` runs, but
  through the wired service, plus the *honest* verdict: an evaluation with no
  answers (or ``provider_mode == "disabled"``) is **not** reachability, while the
  stock probe only checks that ``advise()`` returned something. It also prints
  :func:`integration_change` — the exact lines the integration pass must apply to
  ``cli_contract.py`` so ``skaldir selftest --json`` agrees.

Standalone CLI (no product CLI needed, this is the proof that ROUTE is reachable)::

    python -m skaldir.jev.runtime status --json                 # settings-driven
    python -m skaldir.jev.runtime status --probe fixture --json # offline, no network
    python -m skaldir.jev.runtime enable --json                 # atomic + backup + diff
    python -m skaldir.jev.runtime decide --kind tool_routing --json

Exit codes follow the product contract (``skaldir.cli_contract``): ``0`` ok,
``1`` usage, ``3`` blocked/no advisory (that includes an answer the provider sent
that violates the contract — refused, never repaired), ``7`` internal.

Never printed, never stored: any credential. :func:`provider_report` returns the
provider's *redacted* state (``"set"``/``"unset"`` plus the variable name), which
is all a report needs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__, settings_service
from ..registry import default_runtime_dir
from ..system_one import (
    PROVIDER_DISABLED,
    PROVIDER_LLM_ADAPTER,
    PROVIDER_TYPESAFE,
    EvaluationContext,
    SystemOneContractError,
    SystemOneState,
)
from ..system_one_flags import (
    SECTION as SYSTEM_ONE_SECTION,
)
from ..system_one_flags import (
    SystemOneFlags,
    load_system_one_flags,
    settings_path,
)
from ..system_one_questions import compose_questions, default_question_registry
from . import contracts as C
from .decide import decide
from .decide import service_for as _decide_service_for
from .fixture import FixtureJevEvaluator
from .kinds import DECISION_KINDS
from .selftest import SELFTEST_CASES, case_request

SCHEMA_VERSION = "1.0"

#: The product contract's exit codes (``skaldir.cli_contract``), spelled here so
#: this module also runs in a build without the CLI (same numbers, one meaning).
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_INTERNAL = 7

#: Which provider the status/decide probe asks for.
#: ``settings`` = the runtime's own flags (the product path),
#: ``live`` = force the LLM adapter, ``fixture`` = the offline evaluator,
#: ``disabled`` = the honest blocked path (no provider, no fabricated answer).
PROBE_CHOICES = ("settings", "live", "fixture", "disabled")

#: The ``settings`` provider, spelled once: the runtime's own flags, and — when those arm
#: nothing — the decision block the config file carries. This is what a bare
#: ``skaldir jev decide`` runs under, because a decision layer that is configured but
#: answers ``provider_disabled`` is a door that only looks open.
PROVIDER_SETTINGS = "settings"

DEFAULT_KIND = "tool_routing"

#: The command that fixes cause 1.
ENABLE_COMMAND = "python -m skaldir.jev.runtime enable --json"
STATUS_COMMAND = "python -m skaldir.jev.runtime status --json"


# --------------------------------------------------------------------------- #
# the runtime directory and the provider edge                                   #
# --------------------------------------------------------------------------- #


def runtime_dir(runtime: str | Path | None = None) -> Path:
    """The runtime state directory — the project's one default rule."""
    return Path(runtime) if runtime else default_runtime_dir()


# --------------------------------------------------------------------------- #
# the provider edge: TypeSafe (godsv.jev) first, the general block second        #
# --------------------------------------------------------------------------- #

#: ``godsv.jev`` — the decision layer's own provider block. ``skaldir setup`` writes it
#: through :func:`skaldir.setup_flow.typesafe_provider_config`, which reads the very same
#: block; before this wiring the runtime resolved ``godsv.llm`` alone, so the key the user
#: configured *for TypeSafe* was read by exactly one caller (the setup flow).
TYPESAFE_BLOCK = "jev"

#: ``godsv.providers.entries.jev`` — the same provider in the named registry the setup
#: flow writes alongside the projection.
TYPESAFE_ENTRY = "jev"

#: ``godsv.llm`` — the general provider, and the fallback for the Jev path.
GENERAL_BLOCK = "llm"

#: The provenance strings every report names: *which* config decided this.
SOURCE_TYPESAFE = f"godsv.{TYPESAFE_BLOCK}"
SOURCE_TYPESAFE_ENTRY = f"godsv.providers.entries.{TYPESAFE_ENTRY}"
SOURCE_GENERAL = f"godsv.{GENERAL_BLOCK}"
#: Nothing was consulted — the probe brings its own evaluator (fixture) or is disabled.
SOURCE_NOT_CONSULTED = "not_consulted"

CONFIG_SOURCES = (
    SOURCE_TYPESAFE,
    SOURCE_TYPESAFE_ENTRY,
    SOURCE_GENERAL,
    SOURCE_NOT_CONSULTED,
)


def _compact_problem(exc: Exception, limit: int = 240) -> str:
    """One line, bounded — the shape every blocker string in this module has."""
    return " ".join(str(exc).split())[:limit]


def _config_document(config_path: Any = None) -> tuple[dict, str, str]:
    """The provider config file as a mapping, its path, and a problem (never raises).

    Read directly instead of through ``llm_provider._load_block`` because that helper
    only ever extracts ``godsv.llm``; the decision layer's own block needs the sibling.
    """
    from ..llm_provider import default_config_file

    path = Path(config_path).expanduser() if config_path else default_config_file()
    if not path.is_file():
        return {}, str(path), ""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, str(path), f"cannot read {path}: {type(exc).__name__}"
    try:
        import yaml
    except ImportError:  # pragma: no cover — PyYAML is a runtime dependency
        return {}, str(path), "PyYAML is required to read the provider config file"
    try:
        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 — a YAML error is a report, never a crash
        return {}, str(path), f"{path} is not valid YAML: {type(exc).__name__}"
    if not isinstance(data, Mapping):
        return {}, str(path), f"{path} must contain a mapping"
    return {str(key): value for key, value in data.items()}, str(path), ""


def _mapping_at(document: Mapping[str, Any], *keys: str) -> dict | None:
    """One nested block, or ``None`` when it is absent or not a non-empty mapping."""
    node: Any = document
    for key in keys:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    if not isinstance(node, Mapping) or not node:
        return None
    return {str(key): value for key, value in node.items()}


def _typesafe_block(document: Mapping[str, Any]) -> tuple[dict | None, str]:
    """The TypeSafe provider block and the name of the config it came from."""
    block = _mapping_at(document, "godsv", TYPESAFE_BLOCK)
    if block is not None:
        return block, SOURCE_TYPESAFE
    block = _mapping_at(document, "godsv", "providers", "entries", TYPESAFE_ENTRY)
    if block is not None:
        return block, SOURCE_TYPESAFE_ENTRY
    return None, SOURCE_NOT_CONSULTED


def _credential_problem(cfg: Any) -> str:
    """The named export a remote endpoint without a credential needs (``""`` when fine).

    The setup flow refuses this case by name (``decision_through_config``) and the
    runtime path must not be the quieter one: a decision sent without a key reads like a
    provider outage instead of a missing export.
    """
    from ..llm_provider import is_local_base_url

    if cfg.api_key or not cfg.api_key_env or is_local_base_url(cfg.base_url):
        return ""
    return (
        f"{cfg.api_key_env} is not set in the environment (it is only named in the config "
        f"file) — export {cfg.api_key_env}=<your-key>"
    )


def _decision_layer(block: Mapping[str, Any] | None) -> str:
    """Which side of the line ``godsv.jev`` is on — the rule lives in ``setup_flow``.

    Imported lazily: this module is on the façade's import path and the marker's own
    helper lives with the document shape it is written into.
    """
    if not block:
        return ""
    from .. import setup_flow as sf

    return sf.decision_layer_for(
        str(block.get("base_url") or ""),
        kind=str(block.get("provider") or block.get("kind") or ""),
        explicit=str(block.get(sf.SYSTEM_ONE_KEY) or ""),
    )


def _native_problem(cfg: Any) -> str:
    """Why the System One endpoint produces no client — named, with the handle it wants."""
    from .. import setup_flow as sf
    from ..system_one_treasury import DEFAULT_HANDLE

    return (
        f"{cfg.base_url} is the System One endpoint "
        f"({sf.SYSTEM_ONE_KEY}={sf.SYSTEM_ONE_NATIVE}), not an OpenAI-compatible chat "
        "endpoint: no chat client is built for it (it has no /chat/completions). The native "
        "evaluator needs an authorized TypeSafe credential — the Treasury handle "
        f"{DEFAULT_HANDLE} — so without one this decision is blocked_external_access rather "
        "than answered by another provider's key"
    )


def _resolution_row(
    source: str,
    path: str,
    problem: str,
    client: Any,
    view: Mapping[str, Any] | None,
    *,
    decision_layer: str = "",
) -> dict:
    """One resolution record: which config, which client, and why not (never a key).

    ``decision_layer`` is ``typesafe`` (the System One endpoint) or ``llm`` (a chat model
    standing in for it) — the fact a caller needs before it can say whether a decision was
    a Jev decision at all. ``""`` means no decision block was consulted.
    """
    return {
        "source": str(source),
        "config_path": str(path),
        "present": bool(path) and Path(path).is_file(),
        "problem": str(problem),
        "client": client,
        "ready": client is not None,
        "block": dict(view or {}),
        "decision_layer": str(decision_layer),
    }


def provider_resolution(*, env: Mapping[str, str] | None = None, config_path: Any = None) -> dict:
    """Which config the Jev provider came from — and the client it built.

    Order, and it is deliberate:

    1. ``godsv.jev`` — the TypeSafe/Jev provider ``skaldir setup`` writes (the block
       :func:`skaldir.setup_flow.typesafe_provider_config` resolves);
    2. ``godsv.providers.entries.jev`` — the same provider in the named registry;
    3. ``godsv.llm`` (+ the ``SKALDIR_LLM_*`` environment) — the general provider.

    A TypeSafe block that is *present* but cannot produce a client is a **blocker that
    names it**: the fallback exists for "no TypeSafe provider is configured", never for
    "the one that is configured failed". Quietly deciding on the general provider's
    endpoint or key would be exactly the substitution this façade refuses elsewhere —
    and it is how an inherited ``SKALDIR_LLM_*`` would redirect a decision away from the
    endpoint the user configured for TypeSafe.

    Never returns a secret: the block view is ``ProviderConfig.redacted()``.
    """
    from ..llm_provider import provider_for, resolve_provider_config

    document, path, doc_problem = _config_document(config_path)
    if doc_problem:
        return _resolution_row(SOURCE_NOT_CONSULTED, path, doc_problem, None, {})

    block, source = _typesafe_block(document)
    layer = _decision_layer(block)
    if block is None:
        # 3. the general provider — unchanged behaviour for an installation that never
        #    ran `skaldir setup` (explicit config > env > file, key from env only).
        try:
            client = provider_for(env=env, config_path=config_path)
            problem = ""
        except Exception as exc:  # noqa: BLE001 — a wiring problem is a blocker, not a bug
            client, problem = None, _compact_problem(exc)
        try:
            view = resolve_provider_config(env=env, config_path=config_path).redacted()
        except Exception:  # noqa: BLE001 — the view is a report, never the answer
            view = {}
        return _resolution_row(SOURCE_GENERAL, path, problem, client, view)

    from .. import setup_flow as sf

    try:
        cfg = resolve_provider_config(dict(block), env=env, config_path=path)
    except Exception as exc:  # noqa: BLE001
        return _resolution_row(
            source, path, _compact_problem(exc), None, {}, decision_layer=layer
        )
    view = cfg.redacted()
    problem = "; ".join(str(item) for item in cfg.problems) or _credential_problem(cfg)
    client = None
    if layer == sf.SYSTEM_ONE_NATIVE:
        # The System One endpoint has no chat path: an OpenAI-shaped client built against
        # it is the substitution this whole rule exists to stop — the decision endpoint
        # would have been POSTed a /chat/completions body. The native evaluator is what
        # answers here, and it blocks by name without an authorized credential; a missing
        # environment variable (if the block names one) is reported beside that, never
        # instead of it.
        native = _native_problem(cfg)
        problem = "; ".join(item for item in (problem, native) if item)
    elif not problem:
        try:
            client = provider_for(dict(block), env=env, config_path=path)
        except Exception as exc:  # noqa: BLE001
            problem = _compact_problem(exc)
    return _resolution_row(source, path, problem, client, view, decision_layer=layer)


def configured_provider_flags(
    *,
    runtime: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
) -> tuple[SystemOneFlags, dict]:
    """``(flags, provenance)`` for a bare invocation — armed whenever the install is.

    The order, and it is the product's own:

    1. **the runtime's flags** (``<runtime>/settings.yaml``) when they name a provider —
       this is the substrate's switch and it wins;
    2. otherwise **the decision block** (``godsv.jev`` → ``godsv.providers.entries.jev``):
       a configured decision provider means ``typesafe_jev`` for the System One endpoint
       and ``llm_system_one_adapter`` for the compatibility backend — *not* ``disabled``.
       Before this, a fully configured installation answered exit 3 with
       ``provider_disabled`` because nothing read either one;
    3. otherwise **disabled**, with the reason spelled out (no provider is configured at
       all), never a fabricated answer.

    ``provenance`` carries ``source`` (``settings`` / ``godsv.jev`` /
    ``godsv.providers.entries.jev`` / ``godsv.llm`` / ``not_consulted``),
    ``decision_layer`` and ``reason``.
    """
    from dataclasses import replace as _replace

    settings = load_system_one_flags(runtime_dir(runtime))
    resolution = provider_resolution(env=env, config_path=config_path)
    layer = str(resolution.get("decision_layer") or "")
    block_source = str(resolution.get("source") or "")
    if settings.enabled and settings.provider != PROVIDER_DISABLED:
        return settings, {
            "source": "settings",
            "config_source": block_source,
            "decision_layer": layer,
            "reason": "",
        }
    if block_source in (SOURCE_TYPESAFE, SOURCE_TYPESAFE_ENTRY):
        from .. import setup_flow as sf

        provider = (
            PROVIDER_TYPESAFE if layer == sf.SYSTEM_ONE_NATIVE else PROVIDER_LLM_ADAPTER
        )
        flags = _replace(settings, enabled=True, provider=provider)
        return flags, {
            "source": block_source,
            "config_source": block_source,
            "decision_layer": layer,
            "reason": (
                f"the runtime flags name no provider; {block_source} does "
                f"({sf.SYSTEM_ONE_KEY}={layer or 'unmarked'})"
            ),
        }
    return settings, {
        "source": block_source or SOURCE_NOT_CONSULTED,
        "config_source": block_source,
        "decision_layer": layer,
        "reason": (
            "no decision provider is configured: <runtime>/settings.yaml names none and no "
            "godsv.jev block exists — run `skaldir setup` (the refusal is honest, not a "
            "missing feature)"
        ),
    }


def provider_client(*, env: Mapping[str, str] | None = None, config_path: Any = None) -> Any:
    """The real LLM client for this environment: the TypeSafe config first.

    Imported lazily: this is the only place the Jev side reaches the HTTP adapter, and a
    façade import must not cost a transport at import time. Raises the adapter's own
    ``ProviderConfigError`` when neither block can produce a client — a half-configured
    provider fails loudly instead of degrading to a fabricated answer.
    """
    from ..llm_provider import ProviderConfigError

    resolution = provider_resolution(env=env, config_path=config_path)
    if resolution["client"] is None:
        raise ProviderConfigError(f"{resolution['source']}: {resolution['problem']}"[:400])
    return resolution["client"]


def provider_report(*, env: Mapping[str, str] | None = None, config_path: Any = None) -> dict:
    """Redacted provider state for the report — never a key, never a header.

    Carries the provenance the decision path needs: ``config_source`` names the block the
    Jev provider came from (``godsv.jev`` / ``godsv.providers.entries.jev`` / ``godsv.llm``
    / ``not_consulted``) and ``jev_problem`` says why that block produced no client.
    """
    from ..llm_provider import provider_status

    resolution = provider_resolution(env=env, config_path=config_path)
    try:
        report = dict(provider_status(env=env, config_path=config_path))
    except Exception as exc:  # noqa: BLE001 — a report must report, not raise
        report = {"ready": False, "problems": [f"{type(exc).__name__}: {exc}"[:200]]}
    report.setdefault("api_key", "unset")
    report.setdefault("hint", "")
    report["config_source"] = resolution["source"]
    report["config_path"] = resolution["config_path"]
    report["config_present"] = resolution["present"]
    report["config_block"] = {
        key: resolution["block"].get(key)
        for key in ("provider", "base_url", "endpoint", "model", "api_key", "api_key_env")
        if resolution["block"]
    }
    report["jev_ready"] = bool(resolution["ready"])
    report["jev_problem"] = resolution["problem"]
    # which side of the line decided this: ``typesafe`` (the System One endpoint) or
    # ``llm`` (a chat model answering Jev's questions). A decision that cannot say this
    # is a decision nobody can audit.
    report["decision_layer"] = str(resolution.get("decision_layer") or "")
    return report


def _provider_note(flags: SystemOneFlags | None, resolution: Mapping[str, Any]) -> dict:
    """The provenance row a payload carries: which config this call was decided on."""
    if flags is None or str(getattr(flags, "provider", "")) != PROVIDER_LLM_ADAPTER:
        return {
            "source": SOURCE_NOT_CONSULTED,
            "config_path": "",
            "present": False,
            "problem": "",
            "block": {},
            "reason": (
                "the flags name no LLM adapter — the fixture evaluator or the disabled "
                "provider answered, so no provider config was read"
            ),
        }
    return {
        "source": str(resolution.get("source") or ""),
        "config_path": str(resolution.get("config_path") or ""),
        "present": bool(resolution.get("present")),
        "problem": str(resolution.get("problem") or ""),
        "block": dict(resolution.get("block") or {}),
        "reason": "",
    }


def _runtime_provider(
    flags: SystemOneFlags | None,
    *,
    llm: Any | None = None,
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
) -> tuple[Any | None, str, dict]:
    """``(client, problem, resolution)`` — the client the flags ask for, TypeSafe first.

    The flags decide *whether* a config is read at all: only ``llm_system_one_adapter``
    wants a client. A caller-supplied ``llm`` and the fixture evaluator own their own
    provider edge (exactly as :func:`skaldir.jev.decide._provider_client` promises).
    """
    resolution = provider_resolution(env=env, config_path=config_path)
    if llm is not None:
        return llm, "", resolution
    if flags is None or str(getattr(flags, "provider", "")) != PROVIDER_LLM_ADAPTER:
        return None, "", resolution
    return resolution["client"], resolution["problem"], resolution


def service_for(
    flags: SystemOneFlags | None = None,
    *,
    evaluator: Any | None = None,
    llm: Any | None = None,
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
):
    """The wired :class:`~skaldir.system_one_service.SystemOneService` (see decide.py).

    Public here as well so a caller that must *patch a probe* has the runtime's
    construction path at hand under the name the probe already calls.
    """
    return _decide_service_for(
        flags, evaluator=evaluator, llm=llm, env=env, config_path=config_path
    )


def provider_problem(flags: SystemOneFlags | None, llm: Any | None = None, **kwargs: Any) -> str:
    """Why the provider the flags name could not be built (``""`` when it could).

    Goes through the same TypeSafe-first resolution the decision path uses, so a blocker
    names the block the runtime really tried (``godsv.jev`` before ``godsv.llm``).
    """
    _client, problem, _resolution = _runtime_provider(flags, llm=llm, **kwargs)
    return problem


def flags_for_probe(
    runtime: str | Path | None = None, probe: str = "settings"
) -> SystemOneFlags | None:
    """The flags one probe runs under (``None`` = ``decide()`` decides, fixture path)."""
    if probe == "fixture":
        # The fixture is selected by handing `decide()` an evaluator; the flags
        # play no part at all (exactly like `skaldir jev selftest`).
        return None
    if probe == "settings":
        return load_system_one_flags(runtime_dir(runtime))
    if probe == "live":
        shadow = load_system_one_flags(runtime_dir(runtime)).shadow_mode
        return SystemOneFlags(enabled=True, provider=PROVIDER_LLM_ADAPTER, shadow_mode=shadow)
    if probe == "disabled":
        return SystemOneFlags(enabled=True, provider=PROVIDER_DISABLED)
    raise ValueError(f"unknown probe {probe!r} (known: {list(PROBE_CHOICES)})")


def _evaluator_for_probe(probe: str, evaluator: Any | None) -> Any | None:
    if evaluator is not None:
        return evaluator
    return FixtureJevEvaluator() if probe == "fixture" else None


# --------------------------------------------------------------------------- #
# the settings edit: atomic, backed up, reported, reversible                    #
# --------------------------------------------------------------------------- #


def enable(
    runtime: str | Path | None = None,
    *,
    provider: str = PROVIDER_LLM_ADAPTER,
    overrides: Mapping[str, Any] | None = None,
    dry_run: bool = False,
) -> dict:
    """Turn System-One on in ``<runtime>/settings.yaml`` — atomically.

    Writes the substrate's own flag vocabulary (``enabled: true`` plus the
    provider); every other flag keeps its current value, so an owner who had
    ``voice_triage: true`` keeps it. The section is validated through
    :meth:`skaldir.system_one_flags.SystemOneFlags.from_mapping` *before* the file
    is touched: a section the loader would refuse is never written.

    ``dry_run`` computes the diff and writes nothing — no backup either, so the
    directory stays byte-identical.

    Note (honest gap): the per-kind flags of MVP/03 §7.4
    (``memory_injection``, ``tool_routing``, …) are **not** fields of
    :class:`~skaldir.system_one_flags.SystemOneFlags` yet, and ``decide()`` does not
    read them; writing them would be an unknown-key contract error. The two keys
    that are read today are the two this writes.
    """
    base = runtime_dir(runtime)
    path = settings_path(base)
    if provider not in (PROVIDER_DISABLED, PROVIDER_LLM_ADAPTER, PROVIDER_TYPESAFE):
        raise ValueError(f"unknown provider {provider!r}")
    current = settings_service.section_of(settings_service.read_document(path), SYSTEM_ONE_SECTION)
    requested = {"enabled": True, "provider": provider, **dict(overrides or {})}
    try:
        merged = SystemOneFlags.from_mapping({**current, **requested})
    except Exception as exc:  # noqa: BLE001 — a refused section must not be written
        raise settings_service.SettingsError(
            f"refusing to write a system_one section the loader would reject: {exc}"
        ) from exc
    loader_verified = SystemOneFlags.from_mapping(merged.as_dict()) == merged
    report = settings_service.apply_section(
        path, SYSTEM_ONE_SECTION, merged.as_dict(), dry_run=bool(dry_run)
    )
    report.update(
        {
            "command": "jev.runtime enable",
            "flags": merged.as_dotted(),
            "loader_verified": bool(loader_verified),
            "read_back": load_system_one_flags(base).as_dotted() if report["wrote"] else None,
            "per_kind_flags": {
                "state": "not in SystemOneFlags",
                "detail": (
                    "the MVP/03 §7.4 per-kind flags (memory_injection, tool_routing, "
                    "capability_ranking, context_compaction, adversarial_planning) do not "
                    "exist in skaldir.system_one_flags and are not read by decide(); "
                    "writing them would be an unknown-key contract error"
                ),
            },
            "reversible_by": "python -m skaldir.jev.runtime disable --restore --json",
            "next": STATUS_COMMAND,
        }
    )
    return report


def disable(
    runtime: str | Path | None = None, *, restore: bool = False, dry_run: bool = False
) -> dict:
    """Turn System-One off again — either the all-off defaults or the exact backup.

    ``restore`` puts the newest backup of the settings file back byte for byte,
    which is the true reversal of :func:`enable` (including "the file did not
    exist before" — the backup is then simply absent and nothing is written).
    Without ``restore`` the section is set to the substrate's all-off defaults.
    """
    base = runtime_dir(runtime)
    path = settings_path(base)
    if restore:
        backup = settings_service.latest_backup(path)
        if backup is None:
            return _restore_without_backup(path, dry_run=bool(dry_run))
        previous = settings_service.section_of(
            settings_service.read_document(backup), SYSTEM_ONE_SECTION
        )
        report = {
            "command": "jev.runtime disable",
            "path": str(path),
            "before": settings_service.section_of(
                settings_service.read_document(path), SYSTEM_ONE_SECTION
            ),
            "after": previous,
            "changed": settings_service.diff_sections(
                settings_service.section_of(
                    settings_service.read_document(path), SYSTEM_ONE_SECTION
                ),
                previous,
                section=SYSTEM_ONE_SECTION,
            ),
        }
        if dry_run:
            report.update({"dry_run": True, "wrote": False, "restored_from": str(backup)})
            return report
        report.update(settings_service.restore(path, backup))
        return report
    report = settings_service.apply_section(
        path, SYSTEM_ONE_SECTION, SystemOneFlags.disabled().as_dict(), dry_run=bool(dry_run)
    )
    report.update(
        {
            "command": "jev.runtime disable",
            "read_back": load_system_one_flags(base).as_dotted() if report["wrote"] else None,
            "next": STATUS_COMMAND,
        }
    )
    return report


def _restore_without_backup(path: Path, *, dry_run: bool) -> dict:
    """Reversal for the case "there was no settings file before the enable".

    :func:`enable` cannot back up a file that does not exist, so the true
    reversal of "created from nothing" is to remove the file again — but only
    when the file contains *nothing else*: a settings file that carries an
    unrelated section (``strictness``, …) is never deleted by this command, and
    the caller is told why instead.
    """
    report: dict = {
        "command": "jev.runtime disable",
        "path": str(path),
        "restored_from": "",
        "wrote": False,
        "changed": [],
    }
    try:
        document = settings_service.read_document(path)
    except settings_service.SettingsError as exc:
        report["detail"] = f"no backup, and the file cannot be read: {exc}"
        return report
    if not document:
        report["detail"] = "no settings file and no backup — there is nothing to restore"
        return report
    sections = sorted(document)
    if sections != [SYSTEM_ONE_SECTION]:
        report["detail"] = (
            "no backup of this settings file exists (the enable created it), and it "
            f"carries other section(s) {sections} — left untouched; use `disable` to "
            "switch the flags off, or edit the file to drop the system_one section"
        )
        return report
    report["detail"] = (
        "no backup existed (the enable created this file) and it holds nothing but the "
        "system_one section — removing it restores the previous state exactly"
    )
    report["removed"] = True
    if dry_run:
        report["dry_run"] = True
        return report
    path.unlink()
    report["wrote"] = True
    return report


# --------------------------------------------------------------------------- #
# the CLI's ROUTE probe — the exact integration change                          #
# --------------------------------------------------------------------------- #


def integration_change(contract_path: str | Path | None = None) -> dict:
    """The exact edits ``src/skaldir/cli_contract.py`` still needs, with line numbers.

    Read from the file on every call, so the report cannot drift from the code.
    ``applied`` is measured, never assumed: both spellings are searched for in
    the ``_probe_route`` region (``service_for`` wired, and a verdict that follows
    the probes).
    """
    target = (
        Path(contract_path)
        if contract_path
        else Path(__file__).resolve().parents[1] / "cli_contract.py"
    )
    report: dict[str, Any] = {
        "file": "src/skaldir/cli_contract.py",
        "path": str(target),
        "applied": False,
        "changes": [],
        "after": (
            "skaldir selftest --json reports ROUTE ok:true / status reachable "
            "(exit 0 on that layer)"
        ),
        "verified_here": STATUS_COMMAND,
    }
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        report["error"] = f"cannot read {target}: {exc}"
        return report

    def _find(needle: str, *, start: int = 0) -> int:
        for index in range(start, len(lines)):
            if needle in lines[index]:
                return index
        return -1

    route = _find("def _probe_route(")
    offset = route if route >= 0 else 0
    window = lines[offset:]

    import_line = _find("from .system_one_service import build_service", start=offset)
    call_line = _find("service = build_service(flags=flags)", start=offset)
    verdict_line = _find('return False, " ".join(blocked), probes', start=offset)
    wired_now = _find("service_for", start=offset) >= 0
    verdict_now = _find("return not blocked, ", start=offset) >= 0

    if not wired_now:
        report["changes"].append(
            {
                "line": (import_line if import_line >= 0 else call_line) + 1,
                "kind": "wire the real provider into the ROUTE probe",
                "from": "from .system_one_service import build_service",
                "to": "from .jev.runtime import service_for as build_service",
                "call_site": {
                    "line": call_line + 1,
                    "before": "service = build_service(flags=flags)",
                },
                "why": (
                    "build_service(flags) builds the evaluator with llm=None, so a settings "
                    "file naming the LLM adapter still degrades to the disabled evaluator; "
                    "the import swap is one line and leaves the probe body untouched (its "
                    "call site then resolves the wired builder)"
                ),
                "equivalent": (
                    "or rename the call site: `service = service_for(flags=flags)` plus "
                    "`from .jev.runtime import service_for`"
                ),
            }
        )
    if not verdict_now:
        report["changes"].append(
            {
                "line": verdict_line + 1,
                "kind": "let the layer verdict follow its probes",
                "from": 'return False, " ".join(blocked), probes',
                "to": 'return not blocked, " ".join(blocked), probes',
                "why": (
                    "measured: with the settings file enabled and a working provider both "
                    "ROUTE probes report ok:true while this line still returns False, so the "
                    "layer can never leave `blocked` — exit 3, blocked_reason 'probe failed'"
                ),
            }
        )
    report["probe_region"] = {"start": offset + 1, "lines": len(window)}
    report["applied"] = bool(wired_now and verdict_now and not report["changes"])
    return report


# --------------------------------------------------------------------------- #
# status — the honest reachability verdict                                      #
# --------------------------------------------------------------------------- #


def _parity_advise(service: Any) -> dict:
    """The substrate call the CLI's ROUTE probe makes — same state, same pack choice.

    One deliberate difference: the question *set* is built with the substrate's
    own :func:`skaldir.system_one_questions.compose_questions` (a ``QuestionSet``)
    instead of handing the raw ``QuestionPack`` to ``advise()``. The stock probe
    passes the pack; measured, that shape raises ``TypeError: 'method' object is
    not iterable`` inside the gate for an evaluator that actually answers, so the
    probe's own call shape would make the wiring look broken. This function
    measures the *wiring*, which is what the status is for.
    """
    registry = default_question_registry()
    packs = list(registry.packs())
    pack = next(
        (item for item in packs if "rout" in str(getattr(item, "id", ""))),
        packs[0] if packs else None,
    )
    if pack is None:
        return {"ok": False, "detail": "no question pack is registered", "answers": 0}
    questions = compose_questions(str(pack.id), registry=registry)
    state = SystemOneState(payload={"task": "runtime status probe", "options": ["a", "b"]})
    context = EvaluationContext(run_id="jev-runtime-status", goal_id="jev-runtime-status")
    evaluation = asyncio.run(
        service.advise(state=state, questions=questions, context=context, use_cache=False)
    )
    if evaluation is None:
        return {
            "ok": False,
            "detail": "one advise() call returned no evaluation (provider or gate refused)",
            "pack": f"{getattr(pack, 'id', '')} {getattr(pack, 'version', '')}",
            "answers": 0,
        }
    answered = len(getattr(evaluation, "answers", {}) or {})
    return {
        "ok": answered > 0,
        "detail": (
            f"advise() answered {answered} question(s), "
            f"provider_mode={evaluation.provider_mode}"
        ),
        "pack": f"{getattr(pack, 'id', '')} {getattr(pack, 'version', '')}",
        "provider_mode": str(evaluation.provider_mode),
        "answers": answered,
        "latency_ms": int(getattr(evaluation, "latency_ms", 0) or 0),
        "evaluation_id": str(getattr(evaluation, "evaluation_id", "")),
    }


def _rules_recheck(decision: C.JevDecision, request: C.JevRequestBase) -> dict:
    """R1–R4, re-measured on the decision the runtime path just produced."""
    echo = int(decision.goal_revision) == int(request.goal_revision)
    grants = tuple(decision.grants)
    advisory = (
        decision.authority == "advisory" and decision.certifies is False and not grants
    )
    return {
        "authority": str(decision.authority),
        "certifies": bool(decision.certifies),
        "grants": list(grants),
        "goal_revision_echo": bool(echo),
        "goal_revision": int(decision.goal_revision),
        "advisory_only": bool(advisory),
        "violations": [
            name
            for name, broken in (
                ("R1_never_pass", decision.certifies is not False),
                ("R2_never_grants", bool(grants)),
                ("R4_goal_revision_echo", not echo),
            )
            if broken
        ],
    }


def status(
    runtime: str | Path | None = None,
    *,
    probe: str = "settings",
    kind: str = DEFAULT_KIND,
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
) -> dict:
    """Is ROUTE reachable, and if not, exactly which cause is still open?

    The two causes of the original report are checked in order and each is
    ``fixed`` or ``pending`` with its fix named. The verdict is deliberately
    stricter than the CLI probe's: a wired provider that answered **no**
    question (the disabled evaluator) is *not* reachability — that is the false
    green :func:`integration_change` warns about.

    Read-only: nothing is written into the runtime directory.
    """
    if probe not in PROBE_CHOICES:
        raise ValueError(f"unknown probe {probe!r} (known: {list(PROBE_CHOICES)})")
    if kind not in DECISION_KINDS:
        raise ValueError(f"unknown kind {kind!r} (known: {list(DECISION_KINDS)})")
    base = runtime_dir(runtime)
    path = settings_path(base)
    document = settings_service.read_document(path)
    section = settings_service.section_of(document, SYSTEM_ONE_SECTION)
    flags = flags_for_probe(base, probe)
    where = str(path) if path.is_file() else f"{path} (absent)"
    causes: list[dict] = []
    warnings: list[str] = []

    if flags is None:  # the fixture probe carries its own evaluator
        causes.append(
            {
                "cause": "system_one_flags",
                "state": "fixed",
                "detail": (
                    "not consulted: the fixture probe hands decide() its own evaluator "
                    "(exactly like `skaldir jev selftest`), so the settings file cannot "
                    "hold it back"
                ),
                "fix": "",
            }
        )
    elif flags.enabled:
        causes.append(
            {
                "cause": "system_one_flags",
                "state": "fixed",
                "detail": f"flags loaded from {where}: provider={flags.provider}",
                "fix": "",
            }
        )
    else:
        causes.append(
            {
                "cause": "system_one_flags",
                "state": "pending",
                "detail": f"System-One is off in {where} (enabled=false)",
                "fix": ENABLE_COMMAND,
            }
        )

    llm = None
    problem = ""
    resolution: dict[str, Any] = {}
    if flags is not None:
        llm, problem, resolution = _runtime_provider(flags, env=env, config_path=config_path)
    evaluator = _evaluator_for_probe(probe, None)
    service = service_for(flags, evaluator=evaluator, llm=llm, env=env, config_path=config_path)

    probe_record: dict | None = None
    decision_document: dict | None = None
    rules: dict | None = None
    if service is None:
        causes.append(
            {
                "cause": "advise",
                "state": "pending",
                "detail": "no service could be built (no flags, no evaluator)",
                "fix": ENABLE_COMMAND,
            }
        )
    else:
        decision = None
        answered = 0
        try:
            probe_record = _parity_advise(service)
            answered = int(probe_record.get("answers", 0) or 0)
            decision = decide(case_request(kind), service=service, flags=flags)
        except SystemOneContractError as exc:
            # The provider answered, but not in the shape the contract allows
            # (measured: deepseek-chat once named a choice that was not the argmax
            # of its own distribution). Jev refuses such an answer — never a
            # fabricated decision — and a *status* must report that, not crash.
            refusal = " ".join(str(exc).split())[:400]
            probe_record = {
                "ok": False,
                "detail": (
                    f"provider_contract_refusal: {refusal}"
                    + (
                        f" (the parity advice itself did answer {answered} question(s))"
                        if answered
                        else ""
                    )
                ),
                "answers": answered,
            }
            warnings.append(
                "the provider's answer did not satisfy the contract and was refused "
                f"(never a fabricated decision): {refusal}"
            )
        if decision is not None:
            decision_document = decision.model_dump(mode="json")
            rules = _rules_recheck(decision, case_request(kind))
        if (
            decision is not None
            and decision.status == "ok"
            and str(decision.provider_mode) == "disabled"
        ):
            warnings.append(
                "the provider labelled this decision 'disabled' — no model ran; the empty "
                "answer is the substrate's honest blocked path, not a decision"
            )
        if probe_record["ok"]:
            causes.append(
                {
                    "cause": "advise",
                    "state": "fixed",
                    "detail": probe_record["detail"],
                    "fix": "",
                }
            )
        elif str(probe_record.get("detail", "")).startswith("provider_contract_refusal"):
            causes.append(
                {
                    "cause": "advise",
                    "state": "pending",
                    "detail": probe_record["detail"],
                    "fix": "re-run " + STATUS_COMMAND,
                }
            )
        elif problem:
            causes.append(
                {
                    "cause": "advise",
                    "state": "pending",
                    "detail": f"provider_unconfigured: {problem}",
                    "fix": (
                        "export SKALDIR_LLM_BASE_URL / SKALDIR_LLM_MODEL / SKALDIR_LLM_API_KEY "
                        "(the key is read from the environment only) and re-run " + STATUS_COMMAND
                    ),
                }
            )
        elif decision is not None and decision.status != "ok":
            causes.append(
                {
                    "cause": "advise",
                    "state": "pending",
                    "detail": (
                        f"decision status={decision.status!r} "
                        f"reason={decision.refusal_reason!r} fallback={decision.fallback!r}"
                    ),
                    "fix": decision.fallback or STATUS_COMMAND,
                }
            )
        else:
            causes.append(
                {
                    "cause": "advise",
                    "state": "pending",
                    "detail": probe_record["detail"],
                    "fix": STATUS_COMMAND,
                }
            )
        if rules is not None and rules["violations"]:
            warnings.append("hard-rule violation(s): " + ", ".join(rules["violations"]))

    pending = [row for row in causes if row["state"] == "pending"]
    reachable = not pending
    if reachable and decision_document is not None and decision_document["status"] != "ok":
        reachable = False
    contract_probe = _contract_probe(base)
    if contract_probe.get("ok") and not reachable:
        warnings.append(
            "the stock ROUTE probe already reports reachable while the runtime verdict is "
            "blocked — see integration_change and the 'disabled provider' warning above"
        )
    return {
        "runtime": str(base),
        "command": "jev.runtime status",
        "probe": probe,
        "settings": {
            "path": str(path),
            "present": path.is_file(),
            "backup": (
                str(settings_service.latest_backup(path))
                if settings_service.latest_backup(path)
                else ""
            ),
            "section": section,
        },
        "flags": flags.as_dotted() if flags is not None else None,
        "provider": provider_report(env=env, config_path=config_path),
        "provider_config": _provider_note(flags, resolution),
        "causes": causes,
        "route": {
            "reachable": bool(reachable),
            "status": "reachable" if reachable else "blocked",
            "exit_code": EXIT_OK if reachable else EXIT_BLOCKED,
            "blocked_reason": "" if reachable else " " .join(row["detail"] for row in pending),
            "provider_mode": (
                str(decision_document["provider_mode"]) if decision_document else "disabled"
            ),
            "live_jev": bool(decision_document["live_jev"]) if decision_document else False,
        },
        "advise": probe_record,
        "decision": decision_document,
        "rules_rechecked": rules,
        "cli_contract_probe": contract_probe,
        "integration_change": integration_change(),
        "warnings": warnings,
    }


def _contract_probe(base: Path) -> dict:
    """The ROUTE layer as `skaldir selftest` measures it *today* (read-only)."""
    try:
        from .. import cli_contract
    except Exception as exc:  # noqa: BLE001 — a build without the CLI is still valid
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        report = cli_contract.run_selftest(base, layers=["ROUTE"])
    except Exception as exc:  # noqa: BLE001 — the probe must not break the status
        return {"available": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    layer = (report.get("layers") or [{}])[0]
    return {
        "available": True,
        "ok": bool(layer.get("ok")),
        "status": layer.get("status", ""),
        "blocked_reason": layer.get("blocked_reason") or "",
        "probes": layer.get("probes", []),
        "note": (
            "this is the layer verdict of `skaldir selftest --json --layer ROUTE` as the "
            "contract file stands right now; integration_change carries the lines that fix it"
        ),
    }


def decide_through_runtime(
    request: C.JevRequestBase | str | Mapping[str, Any],
    *,
    runtime: str | Path | None = None,
    probe: str = "settings",
    kind: str = "",
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
) -> tuple[C.JevDecision, SystemOneFlags | None, str]:
    """One decision through the wired service. Returns ``(decision, flags, problem)``.

    ``request`` is a kind name (the canonical self-audit request is used), a
    request mapping, or a typed request.
    """
    flags = flags_for_probe(runtime, probe)
    llm, problem = (None, "")
    if flags is not None:
        llm, problem, _resolution = _runtime_provider(flags, env=env, config_path=config_path)
    evaluator = _evaluator_for_probe(probe, None)
    service = service_for(flags, evaluator=evaluator, llm=llm, env=env, config_path=config_path)
    if isinstance(request, str):
        typed: C.JevRequestBase = case_request(request)
    elif isinstance(request, Mapping):
        payload = dict(request)
        selected = str(payload.get("kind") or kind or DEFAULT_KIND)
        typed = C.request_for(selected, payload)
    else:
        typed = request
    return decide(typed, service=service, flags=flags), flags, problem


# --------------------------------------------------------------------------- #
# the standalone CLI                                                            #
# --------------------------------------------------------------------------- #


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _dump(document: dict) -> None:
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _text_mode(args: argparse.Namespace) -> bool:
    if bool(getattr(args, "text", False)):
        return True
    return str(getattr(args, "format", "") or "").strip().lower() == "text"


def _envelope(
    command: str,
    data: dict,
    *,
    ok: bool,
    exit_code: int,
    started: float,
    evidence: tuple[dict, ...] = (),
    warnings: tuple[str, ...] = (),
    next_actions: tuple[tuple[str, str], ...] = (),
    error: dict | None = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(ok),
        "command": command,
        "godsvoice_version": __version__,
        "started_at": _iso_now(),
        "duration_ms": int((time.time() - started) * 1000),
        "exit_code": int(exit_code),
        "data": data,
        "error": error,
        "evidence": [dict(item) for item in evidence],
        "warnings": [str(item) for item in warnings],
        "next_actions": [{"command": cmd, "reason": why} for cmd, why in next_actions],
    }


def _status_evidence(data: dict, runtime: Path) -> tuple[dict, ...]:
    flags = data.get("flags") or {}
    rows = [
        {
            "ref": str(runtime / "settings.yaml"),
            "kind": "file",
            "note": (
                f"runtime flags: enabled={flags.get('system_one.enabled')} "
                f"provider={flags.get('system_one.provider')}"
                if flags
                else "flags not consulted (the fixture probe brings its own evaluator)"
            ),
        },
        {
            "ref": "src/skaldir/jev/runtime.py",
            "kind": "code",
            "note": "flags -> real provider -> one wired service (service_for)",
        },
        {
            "ref": "src/skaldir/llm_provider.py",
            "kind": "code",
            "note": "provider_for() / provider_status(): the real OpenAI-compatible adapter",
        },
    ]
    provider_config = data.get("provider_config") or {}
    if provider_config:
        block = provider_config.get("block") or {}
        where = (
            f"{block.get('model')} at {block.get('base_url')}"
            if block
            else "no provider block resolved"
        )
        rows.append(
            {
                "ref": str(
                    provider_config.get("config_path") or "src/skaldir/jev/runtime.py"
                ),
                "kind": "file",
                "note": (
                    f"the Jev provider config came from {provider_config.get('source')}: "
                    f"{where}"
                    + (
                        f" — {provider_config['problem']}"
                        if provider_config.get("problem")
                        else ""
                    )
                ),
            }
        )
    decision = data.get("decision") or {}
    if decision:
        rows.append(
            {
                "ref": str(decision.get("decision_id", "")),
                "kind": "code",
                "note": (
                    f"{decision.get('kind')} {decision.get('status')} via "
                    f"{decision.get('provider_mode')} ({decision.get('latency_ms')} ms, "
                    f"pack {decision.get('question_pack_id')} "
                    f"{decision.get('question_pack_version')})"
                ),
            }
        )
    return tuple(rows)


def cmd_status(args: argparse.Namespace) -> int:
    started = time.time()
    try:
        data = status(
            args.runtime or None,
            probe=args.probe,
            kind=args.kind,
            env=_probe_env(args),
            config_path=getattr(args, "config_path", None) or None,
        )
    except Exception as exc:  # noqa: BLE001 — a status must report, not raise
        return _fail("jev.runtime status", exc, started, args)
    route = data["route"]
    next_actions: list[tuple[str, str]] = []
    if not route["reachable"]:
        for row in data["causes"]:
            if row["state"] == "pending" and row["fix"]:
                next_actions.append((row["fix"], row["cause"]))
    document = _envelope(
        "jev.runtime status",
        data,
        ok=route["reachable"],
        exit_code=route["exit_code"],
        started=started,
        evidence=_status_evidence(data, Path(data["runtime"])),
        warnings=tuple(data["warnings"]),
        next_actions=tuple(next_actions),
        error=(
            None
            if route["reachable"]
            else {
                "code": "blocked",
                "message": route["blocked_reason"],
                "hint": "run " + ENABLE_COMMAND,
            }
        ),
    )
    if _text_mode(args):
        print(
            f"ROUTE {'reachable' if route['reachable'] else 'blocked'} "
            f"(probe={data['probe']}, provider_mode={route['provider_mode']})",
            file=sys.stderr,
        )
        for row in data["causes"]:
            print(f"  {row['cause']:<18} {row['state']:<8} {row['detail']}", file=sys.stderr)
    else:
        _dump(document)
    return int(route["exit_code"])


def cmd_enable(args: argparse.Namespace) -> int:
    started = time.time()
    overrides: dict[str, Any] = {}
    if bool(getattr(args, "shadow", False)):
        overrides["shadow_mode"] = True
    for item in list(getattr(args, "set", []) or []):
        key, _, value = str(item).partition("=")
        if not key or not _:
            return _usage("enable", f"--set needs KEY=VALUE, got {item!r}", started, args)
        overrides[key.strip()] = value.strip().lower() in ("1", "true", "yes", "on")
    try:
        report = enable(
            args.runtime or None,
            provider=args.provider,
            overrides=overrides,
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    except Exception as exc:  # noqa: BLE001 — a write failure must be reported
        return _fail("jev.runtime enable", exc, started, args)
    changed = report.get("changed", [])
    document = _envelope(
        "jev.runtime enable",
        report,
        ok=True,
        exit_code=EXIT_OK,
        started=started,
        evidence=(
            {
                "ref": str(report["path"]),
                "kind": "file",
                "note": f"{len(changed)} key(s) changed; backup={report.get('backup') or 'none'}",
            },
            {
                "ref": "src/skaldir/settings_service.py",
                "kind": "code",
                "note": "atomic write (tmp + fsync + os.replace) with a byte-exact backup",
            },
        ),
        next_actions=((STATUS_COMMAND, "prove the ROUTE layer is wired now"),),
    )
    if _text_mode(args):
        for row in changed:
            print(f"{row['key']}: {row['before']!r} -> {row['after']!r}", file=sys.stderr)
        print(
            f"{report['path']}: wrote={report['wrote']} backup={report.get('backup') or '-'}",
            file=sys.stderr,
        )
    else:
        _dump(document)
    return EXIT_OK


def cmd_disable(args: argparse.Namespace) -> int:
    started = time.time()
    try:
        report = disable(
            args.runtime or None,
            restore=bool(getattr(args, "restore", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    except Exception as exc:  # noqa: BLE001 — a write failure must be reported
        return _fail("jev.runtime disable", exc, started, args)
    document = _envelope(
        "jev.runtime disable",
        report,
        ok=True,
        exit_code=EXIT_OK,
        started=started,
        evidence=(
            {
                "ref": str(report.get("path", "")),
                "kind": "file",
                "note": (
                    f"restored {report.get('restored_from')}"
                    if report.get("restored_from")
                    else f"{len(report.get('changed', []))} key(s) changed"
                ),
            },
        ),
    )
    if _text_mode(args):
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    else:
        _dump(document)
    return EXIT_OK


def cmd_decide(args: argparse.Namespace) -> int:
    started = time.time()
    payload: Any = None
    if args.request:
        try:
            payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — an unreadable request is usage
            return _usage("decide", f"cannot read --request {args.request}: {exc}", started, args)
    elif _stdin_has_data():
        try:
            text = sys.stdin.read()
        except OSError:  # a captured or closed stdin simply carries no request
            text = ""
        if text.strip():
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                return _usage("decide", f"the request is not JSON: {exc}", started, args)
    try:
        if payload is None:
            decision, flags, problem = decide_through_runtime(
                args.kind,
                runtime=args.runtime or None,
                probe=args.probe,
                env=_probe_env(args),
                config_path=getattr(args, "config_path", None) or None,
            )
        else:
            decision, flags, problem = decide_through_runtime(
                payload,
                runtime=args.runtime or None,
                probe=args.probe,
                kind=args.kind,
                env=_probe_env(args),
                config_path=getattr(args, "config_path", None) or None,
            )
    except SystemOneContractError as exc:
        # The answer the provider sent does not satisfy the contract (or the
        # substrate refused to run): that is "no advisory", not a decision and not
        # a crash — the caller keeps its own path (measured, see status()).
        return _contract_refusal("jev.runtime decide", exc, started, args)
    except Exception as exc:  # noqa: BLE001 — a violated contract is our bug, reported
        return _fail("jev.runtime decide", exc, started, args, code=EXIT_INTERNAL)
    document = decision.model_dump(mode="json")
    request = case_request(args.kind) if payload is None else C.request_for(args.kind, payload)
    warnings: list[str] = []
    if decision.status != "ok":
        warnings.append(
            f"no advisory ({decision.refusal_reason}); the caller keeps its own path"
        )
    if problem:
        warnings.append(f"provider_unconfigured: {problem}")
    if str(decision.provider_mode) == "disabled":
        warnings.append("no model ran: the decision carries the substrate's disabled label")
    evidence = [
        {
            "ref": str(decision.decision_id),
            "kind": "code",
            "note": (
                f"{decision.kind} {decision.status} via {decision.provider_mode} "
                f"({decision.latency_ms} ms, pack {decision.question_pack_id} "
                f"{decision.question_pack_version})"
            ),
        },
        {
            "ref": "src/skaldir/jev/runtime.py",
            "kind": "code",
            "note": (
                "runtime path: settings flags -> provider config (godsv.jev, then "
                "godsv.providers.entries.jev, then godsv.llm) -> service_for() -> decide()"
            ),
        },
    ]
    provider_config = _provider_note(
        flags,
        provider_resolution(
            env=_probe_env(args), config_path=getattr(args, "config_path", None) or None
        ),
    )
    if provider_config["source"] != SOURCE_NOT_CONSULTED:
        block = provider_config.get("block") or {}
        evidence.append(
            {
                "ref": str(
                    provider_config.get("config_path") or "src/skaldir/jev/runtime.py"
                ),
                "kind": "file",
                "note": (
                    f"the provider config this decision used: {provider_config['source']}"
                    + (
                        f" ({block.get('model')} at {block.get('base_url')})"
                        if block
                        else ""
                    )
                    + (
                        f" — {provider_config['problem']}"
                        if provider_config.get("problem")
                        else ""
                    )
                ),
            }
        )
    rules = _rules_recheck(decision, request) if request is not None else None
    if rules is not None and rules["violations"]:
        warnings.append("hard-rule violation(s): " + ", ".join(rules["violations"]))
    exit_code = EXIT_OK if decision.status == "ok" else EXIT_BLOCKED
    envelope = _envelope(
        "jev.runtime decide",
        {
            "decision": document,
            "flags": flags.as_dotted() if flags is not None else None,
            "provider_problem": problem,
            "provider_config": provider_config,
            "rules_rechecked": rules,
        },
        ok=decision.status == "ok",
        exit_code=exit_code,
        started=started,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
        error=(
            None
            if decision.status == "ok"
            else {
                "code": "blocked",
                "message": decision.refusal_reason,
                "hint": decision.fallback,
            }
        ),
    )
    if _text_mode(args):
        print(
            f"{decision.kind} {decision.status} via {decision.provider_mode} "
            f"({decision.latency_ms} ms) fallback={decision.fallback or '-'}",
            file=sys.stderr,
        )
    else:
        _dump(envelope)
    return exit_code


def _stdin_has_data() -> bool:
    """Is there a request on stdin? (a captured or closed stdin has none)."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return False
    except (OSError, ValueError):
        return False
    return True


def _probe_env(args: argparse.Namespace) -> Mapping[str, str] | None:
    """``--env KEY=VALUE`` pins the provider resolution (tests use it, never a key)."""
    rows = list(getattr(args, "env", []) or [])
    if not rows:
        return None
    mapping: dict[str, str] = {}
    for item in rows:
        key, sep, value = str(item).partition("=")
        if not sep:
            raise ValueError(f"--env needs KEY=VALUE, got {item!r}")
        mapping[key.strip()] = value
    return mapping


def _usage(command: str, message: str, started: float, args: argparse.Namespace) -> int:
    document = _envelope(
        command,
        {},
        ok=False,
        exit_code=EXIT_USAGE,
        started=started,
        error={"code": "usage", "message": message, "hint": ""},
    )
    _dump(document) if not _text_mode(args) else print(message, file=sys.stderr)
    return EXIT_USAGE


def _contract_refusal(
    command: str, exc: Exception, started: float, args: argparse.Namespace
) -> int:
    """A refused/blocked answer is exit 3 — no advisory, never a fabricated one."""
    message = " ".join(str(exc).split())[:400]
    document = _envelope(
        command,
        {},
        ok=False,
        exit_code=EXIT_BLOCKED,
        started=started,
        warnings=(
            "the provider's answer was refused as a contract violation — the caller "
            "keeps its own path",
        ),
        error={"code": "blocked", "message": message, "hint": STATUS_COMMAND},
    )
    if _text_mode(args):
        print(f"no advisory: {message}", file=sys.stderr)
    else:
        _dump(document)
    return EXIT_BLOCKED


def _fail(
    command: str,
    exc: Exception,
    started: float,
    args: argparse.Namespace,
    *,
    code: int = EXIT_INTERNAL,
) -> int:
    document = _envelope(
        command,
        {},
        ok=False,
        exit_code=code,
        started=started,
        error={"code": "internal", "message": f"{type(exc).__name__}: {exc}"[:400], "hint": ""},
    )
    if _text_mode(args):
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    else:
        _dump(document)
    return code


def build_parser() -> argparse.ArgumentParser:
    """``python -m skaldir.jev.runtime <verb>`` — the standalone ROUTE binding CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m skaldir.jev.runtime",
        description=(
            "Jev runtime binding: enable System-One in the runtime settings (atomic + "
            "reversible), wire the real provider and report whether the ROUTE layer is "
            "really reachable."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="is ROUTE reachable, and which cause is still open")
    p_status.add_argument("--runtime", default="", help="runtime state dir (default: SKALDIR_HOME)")
    p_status.add_argument("--probe", choices=list(PROBE_CHOICES), default="settings")
    p_status.add_argument("--kind", choices=list(DECISION_KINDS), default=DEFAULT_KIND)
    p_status.add_argument(
        "--env", action="append", default=[], metavar="KEY=VALUE",
        help="pin one provider env variable (tests; never a secret in a report)",
    )
    p_status.add_argument("--config-path", default="", help="provider config file to read")
    p_status.add_argument("--json", action="store_true", help="JSON output (the default)")
    p_status.add_argument("--text", action="store_true")
    p_status.add_argument("--format", default="", choices=["", "json", "text"])
    p_status.set_defaults(func=cmd_status)

    p_enable = sub.add_parser("enable", help="enable System-One (atomic, backed up, reversible)")
    p_enable.add_argument("--runtime", default="")
    p_enable.add_argument(
        "--provider",
        choices=[PROVIDER_DISABLED, PROVIDER_LLM_ADAPTER, PROVIDER_TYPESAFE],
        default=PROVIDER_LLM_ADAPTER,
    )
    p_enable.add_argument("--shadow", action="store_true", help="also set shadow_mode: true")
    p_enable.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE", help="one more system_one flag"
    )
    p_enable.add_argument("--dry-run", action="store_true", help="report the diff, write nothing")
    p_enable.add_argument("--json", action="store_true")
    p_enable.add_argument("--text", action="store_true")
    p_enable.add_argument("--format", default="", choices=["", "json", "text"])
    p_enable.set_defaults(func=cmd_enable)

    p_disable = sub.add_parser("disable", help="turn System-One off again (or restore the backup)")
    p_disable.add_argument("--runtime", default="")
    p_disable.add_argument("--restore", action="store_true", help="put the newest backup back")
    p_disable.add_argument("--dry-run", action="store_true")
    p_disable.add_argument("--json", action="store_true")
    p_disable.add_argument("--text", action="store_true")
    p_disable.add_argument("--format", default="", choices=["", "json", "text"])
    p_disable.set_defaults(func=cmd_disable)

    p_decide = sub.add_parser("decide", help="one real decision through the runtime path")
    p_decide.add_argument("--kind", choices=list(DECISION_KINDS), default=DEFAULT_KIND)
    p_decide.add_argument("--probe", choices=list(PROBE_CHOICES), default="settings")
    p_decide.add_argument("--runtime", default="")
    p_decide.add_argument("--request", default="", help="request JSON file (default: stdin)")
    p_decide.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    p_decide.add_argument("--config-path", default="")
    p_decide.add_argument("--json", action="store_true")
    p_decide.add_argument("--text", action="store_true")
    p_decide.add_argument("--format", default="", choices=["", "json", "text"])
    p_decide.set_defaults(func=cmd_decide)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one verb; returns the exit code and never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(func(args))
    except ValueError as exc:  # a bad flag value is the caller's bug
        return _usage(str(getattr(args, "command", "")), str(exc), time.time(), args)


__all__ = [
    "CONFIG_SOURCES",
    "DEFAULT_KIND",
    "ENABLE_COMMAND",
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_OK",
    "EXIT_USAGE",
    "GENERAL_BLOCK",
    "PROBE_CHOICES",
    "SCHEMA_VERSION",
    "SOURCE_GENERAL",
    "SOURCE_NOT_CONSULTED",
    "SOURCE_TYPESAFE",
    "SOURCE_TYPESAFE_ENTRY",
    "STATUS_COMMAND",
    "TYPESAFE_BLOCK",
    "TYPESAFE_ENTRY",
    "build_parser",
    "decide_through_runtime",
    "disable",
    "enable",
    "flags_for_probe",
    "integration_change",
    "main",
    "provider_client",
    "provider_problem",
    "provider_report",
    "provider_resolution",
    "runtime_dir",
    "service_for",
    "status",
    "SELFTEST_CASES",
    "case_request",
]


if __name__ == "__main__":  # pragma: no cover — exercised by the runtime tests
    sys.exit(main())
