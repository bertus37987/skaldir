"""The first-run setup flow — **exactly two mandatory keys**, proven by a real call.

The product must be usable with two credentials and no more:

1. **the TypeSafe/Jev provider** — the OpenAI-compatible endpoint the always-armed
   Jev decision layer (ROUTE) evaluates through. Asked for as *endpoint + model +
   the NAME of the environment variable holding the key*.
2. **one LLM provider** — picked from :data:`skaldir.provider_catalog.CATALOG`
   (``deepseek`` / ``openrouter`` / ``openai`` / ``local``), projected into
   ``godsv.llm`` — the block the runtime actually reads.

Nothing here is a promise. Every claim in the report is a number this process
measured: which model the endpoint said it ran, how long the answer took, whether
the endpoint streams, and which structured-output mode answered. The flow is:

    detect  →  ask (flags/env or prompts)  →  validate live  →  write atomically
            →  re-read  →  verify (one real Jev decision + one real completion)

Two rules are structural, not stylistic:

* **a literal key is refused, never stored, never printed.** The config file holds
  ``api_key_env: <NAME>`` and nothing else; a value that looks like a key is
  rejected before any request, and a file that already carries one is refused
  rather than rewritten (see :mod:`skaldir.provider_config`).
* **``--check`` re-validates and touches nothing.** It exits ``0`` only when both
  keys answer, ``2`` when a key is present but does not work, ``3`` when a key is
  missing, ``4`` when a remote endpoint has no credential, and ``8`` on a timeout.

The module is headless by construction: every question the interactive flow would
ask has a flag or an environment variable (:data:`ROLE_ENV`), so a container or a
CI job runs the identical path with no TTY.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import llm_provider as lp
from . import provider_catalog as catalog
from . import provider_config as pc
from . import provider_registry_data as registry
from . import secretscan
from . import structured as st
from . import system_one_adapters as soa
from .env import first_env, getenv

__all__ = [
    "CHECK_EXIT_CODES",
    "DEFAULT_KEY_ENV",
    "DEFAULT_PROVIDER_ID",
    "ENTRY_NAME",
    "ENV_FILE_ENV",
    "ENV_FILE_MODE",
    "ENV_FILE_NAME",
    "EnvFile",
    "KEY_VALUE_ENV",
    "MANDATORY_ROLES",
    "PROBE_PROMPT",
    "PROBE_PROMPT_ID",
    "PROVIDER_ENV",
    "ProviderChoice",
    "QUESTION_ORDER",
    "Question",
    "ROLE_ENV",
    "ROLE_LLM",
    "ROLE_TYPESAFE",
    "SCHEMA_VERSION",
    "SETUP_SECTION",
    "KeySpec",
    "SetupError",
    "SetupIncomplete",
    "SetupRefused",
    "build_document",
    "check_setup",
    "command_tree",
    "configured_provider",
    "credentials_report",
    "degraded_status",
    "detect_state",
    "default_provider",
    "env_file_path",
    "env_names_for_fields",
    "key_available",
    "key_env_for",
    "key_flags_for",
    "key_from_env",
    "key_spec",
    "key_value_from_env",
    "key_value_from_stream",
    "measure_key",
    "missing_fields",
    "missing_flags",
    "next_steps",
    "provider_choice",
    "provider_choices",
    "provider_defaults",
    "provider_id_for_url",
    "provider_report",
    "questions",
    "read_env_file",
    "read_roles",
    "reachable_commands",
    "refuse_key_value",
    "render_env_line",
    "run_setup",
    "typesafe_provider_config",
    "write_document",
    "write_env_file",
]

SCHEMA_VERSION = "1.0"

#: The two mandatory roles, in the order the flow asks for them. There is no
#: third: a key that is not one of these is configuration, not a requirement.
ROLE_TYPESAFE = "typesafe"
ROLE_LLM = "llm"
MANDATORY_ROLES: tuple[str, ...] = (ROLE_TYPESAFE, ROLE_LLM)

ROLE_LABEL: dict[str, str] = {
    ROLE_TYPESAFE: "TypeSafe / Jev provider (the decision layer)",
    ROLE_LLM: "LLM provider (the general model)",
}

#: The provider-entry name each role is stored under in
#: ``godsv.providers.entries``. Fixed, so a re-run cannot accumulate variants.
ENTRY_NAME: dict[str, str] = {ROLE_TYPESAFE: "jev", ROLE_LLM: "llm"}

#: The flag prefix each role's fields are spelled with on the command line. It is
#: the *provider-entry* name (``--jev-*``), not the role name (``typesafe``), so a
#: message can never point an owner at a flag that does not exist.
FLAG_PREFIX: dict[str, str] = {ROLE_TYPESAFE: "--jev", ROLE_LLM: "--llm"}

#: The ``godsv`` sub-block the TypeSafe/Jev provider is stored in. ``godsv.llm``
#: is the general provider (the block the runtime reads); the decision layer's own
#: endpoint lives beside it so the two credentials never overwrite each other.
JEV_BLOCK = "jev"

#: The key ``godsv.jev`` carries to say **which side of the line the decision role is
#: on**. Before it existed, choosing the real TypeSafe endpoint still wrote
#: ``provider: openai-compatible`` — the store's only kind — and the runtime then built
#: an OpenAI-shaped client against ``/v1/systemone``, which is how "TypeSafe" silently
#: became whichever chat model happened to be configured. Two values, both honest:
#:
#: ``typesafe``
#:     the System One endpoint itself (Jev, ``api.typesafe.ai/v1/systemone``): typed
#:     questions in, calibrated probabilities out, credential by Treasury handle.
#: ``llm``
#:     a **compatibility backend**: an ordinary chat model asked Jev's questions through
#:     :class:`skaldir.system_one_adapters.LLMSystemOneAdapter`. Fully supported — and
#:     never presented as a TypeSafe call.
SYSTEM_ONE_KEY = "system_one"
SYSTEM_ONE_NATIVE = "typesafe"
SYSTEM_ONE_COMPATIBILITY = "llm"
SYSTEM_ONE_VALUES: tuple[str, ...] = (SYSTEM_ONE_NATIVE, SYSTEM_ONE_COMPATIBILITY)

#: The output budget written into the ``godsv.jev`` block (MVP/87). Measured on 2026-09-19:
#: the backend behind ``deepseek-chat`` reasons before it answers. At the provider default
#: (900) and at 4096 the whole allowance went to ``reasoning_content`` (4056 reasoning
#: tokens) and ``content`` came back empty or truncated, so the ROUTE probe's ``advise()``
#: returned no evaluation and ``skaldir selftest`` exited 2. 8192 answers the ROUTE pack in
#: 22 questions / ~2.3 s, one attempt. A written budget is what keeps a later migration from
#: silently reintroducing the failure.
JEV_DEFAULT_MAX_TOKENS = 8192
SETUP_SECTION = "setup"

#: Headless contract: for every role, the flag spelling of each field resolves
#: from these variables, in order. A container sets them and runs the same path.
#: The names are written in full (``SKALDIR_*``); :func:`skaldir.env.first_env`
#: adds the pre-rename ``GODSV_*`` twin of each one automatically, so an older
#: deployment that exports the legacy spelling keeps working.
ROLE_ENV: dict[str, dict[str, tuple[str, ...]]] = {
    ROLE_TYPESAFE: {
        "provider": ("SKALDIR_SETUP_JEV_PROVIDER",),
        "base_url": ("SKALDIR_SETUP_JEV_BASE_URL",),
        "model": ("SKALDIR_SETUP_JEV_MODEL",),
        "api_key_env": ("SKALDIR_SETUP_JEV_API_KEY_ENV",),
    },
    ROLE_LLM: {
        "provider": ("SKALDIR_SETUP_LLM_PROVIDER",),
        "base_url": ("SKALDIR_SETUP_LLM_BASE_URL", "SKALDIR_LLM_BASE_URL"),
        "model": ("SKALDIR_SETUP_LLM_MODEL", "SKALDIR_LLM_MODEL"),
        "api_key_env": ("SKALDIR_SETUP_LLM_API_KEY_ENV",),
        "catalog": ("SKALDIR_SETUP_LLM_CATALOG",),
    },
}

#: The probe call. One fixed id, one fixed sentence, so a test can assert the
#: exact request that left the machine.
PROBE_PROMPT_ID = "setup.validate"
PROBE_PROMPT = "Reply with the single word: pong"
PROBE_TIER = "small"
#: The Jev decision probe runs on the tier the System-One adapter itself uses.
JEV_PROBE_TIER = "mid"
JEV_PROBE_KIND = "tool_routing"

#: The product contract's exit codes (``skaldir.cli_contract``), spelled here so
#: this module also runs in a build without the CLI.
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VERIFICATION_FAILED = 2
EXIT_BLOCKED = 3
EXIT_NEEDS_CREDENTIALS = 4
EXIT_NOT_FOUND = 5
EXIT_INTERNAL = 7
EXIT_TIMEOUT = 8

#: What ``--check`` may return, documented so a caller does not guess.
CHECK_EXIT_CODES: dict[int, str] = {
    EXIT_OK: "both mandatory keys answered a real call",
    EXIT_USAGE: "the check could not be asked (bad arguments)",
    EXIT_VERIFICATION_FAILED: "both keys are configured, at least one does not work",
    EXIT_BLOCKED: "a mandatory key is not configured — run `skaldir setup`",
    EXIT_NEEDS_CREDENTIALS: "a remote endpoint has no credential in the environment",
    EXIT_TIMEOUT: "a provider did not answer in time",
}


# --------------------------------------------------------------------------- #
# refusals                                                                    #
# --------------------------------------------------------------------------- #


class SetupError(pc.ProviderStoreError):
    """Anything this flow refuses to do. The CLI maps subclasses to exit codes.

    Extends the provider store's own refusal family (:class:`skaldir.provider_config.
    ProviderStoreError`) rather than ``RuntimeError`` directly, so a caller that
    already handles "the store refused this" keeps working: the two are the same
    kind of answer — the configuration is not usable, and no request was made.
    """


class SetupRefused(SetupError):
    """The caller's input is not usable (a literal key, a malformed URL)."""


class SetupIncomplete(SetupError):
    """A mandatory key is absent — the product is not usable yet."""


# --------------------------------------------------------------------------- #
# the two keys                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KeySpec:
    """One of the two mandatory keys — a *named* credential and an endpoint.

    The credential is never a field: ``api_key_env`` is a variable NAME, and the
    value only ever exists in the environment.
    """

    role: str
    base_url: str
    model: str
    api_key_env: str = ""
    catalog_id: str = ""
    #: which side of the line this key is (see :data:`SYSTEM_ONE_KEY`): written for the
    #: decision role, never for the general one. An empty value is derived — from the
    #: endpoint and the catalog row — so an older caller keeps working.
    decision_layer: str = ""
    tiers: Mapping[str, str] = field(default_factory=dict)
    timeout_s: float | None = None
    #: The output budget written into the block (MVP/87): ``None`` = the provider default.
    #: The decision layer's role needs a *stated* budget — a reasoning-capable backend
    #: spends the whole allowance on ``reasoning_content`` and returns an empty ``content``
    #: (measured 2026-09-19: `deepseek-chat` → 4056 reasoning tokens of a 4096 budget, empty
    #: answer, ROUTE blocked). The JEV role therefore always writes its own budget; see
    #: :data:`JEV_DEFAULT_MAX_TOKENS`.
    max_tokens: int | None = None

    @property
    def name(self) -> str:
        """The ``godsv.providers.entries`` name this key is stored under."""
        return ENTRY_NAME[self.role]

    @property
    def label(self) -> str:
        return ROLE_LABEL[self.role]

    def system_one(self) -> str:
        """``typesafe`` / ``llm`` for this key — explicit, else derived, never guessed."""
        kind = ""
        spec = registry.REGISTRY.get(str(self.catalog_id or "").strip().lower())
        if spec is not None:
            kind = spec.primary_endpoint.kind
        return decision_layer_for(self.base_url, kind=kind, explicit=self.decision_layer)

    def explicit_config(self) -> dict[str, Any]:
        """The mapping :func:`skaldir.llm_provider.resolve_provider_config` accepts."""
        block: dict[str, Any] = {
            "provider": pc.DEFAULT_KIND,
            "base_url": self.base_url,
            "model": self.model,
        }
        if self.api_key_env:
            block["api_key_env"] = self.api_key_env
        if self.tiers:
            block["tiers"] = {str(k): str(v) for k, v in self.tiers.items()}
        if self.timeout_s is not None:
            block["timeout_s"] = float(self.timeout_s)
        if self.max_tokens is not None:
            block["max_tokens"] = int(self.max_tokens)
        return block

    def entry(self) -> pc.ProviderEntry:
        """The registry entry this key writes."""
        return pc.ProviderEntry(
            name=self.name,
            kind=pc.DEFAULT_KIND,
            base_url=self.base_url,
            model=self.model,
            api_key_env=self.api_key_env,
            tiers={str(k): str(v) for k, v in self.tiers.items()},
            timeout_s=self.timeout_s,
            max_tokens=self.max_tokens,
            # the named registry entry carries the same marker as the projection: which
            # decision provider this installation chose (``extra`` keys are preserved)
            extra={SYSTEM_ONE_KEY: self.system_one()}
            if self.role == ROLE_TYPESAFE
            else {},
        )

    def redacted(self, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        """JSON-safe view — the credential state, never the credential."""
        entry = self.entry()
        return {
            "role": self.role,
            "label": self.label,
            "name": self.name,
            "base_url": self.base_url,
            "endpoint": lp.endpoint_for(self.base_url) if self.base_url else "",
            "model": self.model,
            "tiers": dict(self.tiers),
            "api_key_env": self.api_key_env,
            "credential": entry.credential_state(env),
            "local": entry.is_local(),
            "catalog_id": self.catalog_id,
        }


def validate_endpoint(url: str) -> str:
    """An endpoint must be http(s); the refused value is never echoed raw."""
    text = str(url or "").strip()
    if not text:
        raise SetupRefused("base_url is required (e.g. https://api.deepseek.com/v1)")
    if not text.startswith(("http://", "https://")):
        raise SetupRefused("base_url must start with http:// or https://")
    return text


def validate_key_name(value: str) -> str:
    """``api_key_env`` is a variable NAME — a literal key is refused, not printed.

    Delegates to the store's own validator, so the setup flow and
    ``skaldir provider add`` cannot drift apart on what a variable name is, and
    re-raises its refusal as this flow's own type so the CLI maps it to exit 3.
    """
    try:
        return pc.validate_env_name(value)
    except pc.ProviderStoreError as exc:
        raise SetupRefused(str(exc)) from exc


def key_spec(
    role: str,
    *,
    base_url: str,
    model: str,
    api_key_env: str = "",
    catalog_id: str = "",
    decision_layer: str = "",
    tiers: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    max_tokens: int | None = None,
) -> KeySpec:
    """Build one key, refusing anything that would become a stored secret.

    ``decision_layer`` records which side of the line this key is (see
    :data:`SYSTEM_ONE_KEY`); it is derived from the endpoint when the caller does not say,
    so a hand-written call still writes an honest block. A value that is neither
    ``typesafe`` nor ``llm`` is refused rather than stored.
    """
    if role not in MANDATORY_ROLES:
        raise SetupRefused(
            f"unknown role {role!r} — the two mandatory keys are {list(MANDATORY_ROLES)}"
        )
    url = validate_endpoint(base_url)
    text_model = str(model or "").strip()
    if not text_model:
        raise SetupRefused(
            f"{ROLE_LABEL[role]}: a model is required (e.g. --model deepseek-chat)"
        )
    env_name = validate_key_name(api_key_env) if str(api_key_env or "").strip() else ""
    # the general role cannot be a decision endpoint (no /chat/completions, no chat) —
    # refused here, before a credential is asked for, and never after a failed live call
    refuse_unspeakable(role, catalog_id)
    layer = str(decision_layer or "").strip()
    if layer and layer not in SYSTEM_ONE_VALUES:
        raise SetupRefused(
            f"unknown decision layer {layer!r} (known: {list(SYSTEM_ONE_VALUES)})"
        )
    tier_map = {str(k): str(v) for k, v in dict(tiers or {}).items() if str(v or "").strip()}
    token_budget = (
        JEV_DEFAULT_MAX_TOKENS
        if role == ROLE_TYPESAFE and int(max_tokens or 0) <= 0
        else (int(max_tokens) if max_tokens and int(max_tokens) > 0 else None)
    )
    return KeySpec(
        role=role,
        base_url=url,
        model=text_model,
        api_key_env=env_name,
        catalog_id=str(catalog_id or "").strip(),
        decision_layer=layer,
        tiers=tier_map,
        timeout_s=timeout_s,
        max_tokens=token_budget,
    )


def key_from_env(
    role: str,
    *,
    provider: str = "",
    base_url: str = "",
    model: str = "",
    api_key_env: str = "",
    catalog_id: str = "",
    tiers: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    env: Mapping[str, str] | None = None,
) -> KeySpec:
    """Build one key from flags, falling back to the headless variables.

    A flag always wins; a variable is used only where the flag is empty. Naming a
    ``provider`` (or the legacy ``catalog_id``) supplies the endpoint, the
    credential-variable name and — when the table knows one — the model, so
    ``--llm-provider deepseek`` is a complete answer and ``--llm-provider
    openrouter`` is an answer plus exactly one question.
    """
    if role not in MANDATORY_ROLES:
        raise SetupRefused(
            f"unknown role {role!r} — the two mandatory keys are {list(MANDATORY_ROLES)}"
        )
    names = ROLE_ENV[role]
    env_map = dict(os.environ if env is None else env)

    def look(field_name: str, override: str) -> str:
        if str(override or "").strip():
            return str(override).strip()
        found = first_env(names.get(field_name, ()), "", environ=env_map)
        return str(found or "").strip()

    picked = look("provider", provider) or look("catalog", catalog_id)
    resolved_url = look("base_url", base_url)
    resolved_model = look("model", model)
    resolved_env = look("api_key_env", api_key_env)
    resolved_tiers = dict(tiers or {})

    if picked:
        supplied = provider_defaults(picked)
        if not resolved_url and supplied["base_url"]:
            resolved_url = supplied["base_url"]
        if not resolved_model:
            resolved_model = supplied["model"] or first_env(
                catalog.expand(catalog.ENV_MODEL_FALLBACKS), "", environ=env_map
            ) or ""
        if not resolved_env and supplied["api_key_env"]:
            resolved_env = supplied["api_key_env"]

    return key_spec(
        role,
        base_url=resolved_url,
        model=resolved_model,
        api_key_env=resolved_env,
        catalog_id=picked,
        tiers=resolved_tiers,
        timeout_s=timeout_s,
    )


def missing_flags(role: str, *, env: Mapping[str, str] | None = None) -> list[str]:
    """Which fields of one key a headless caller still has to provide.

    Reported instead of guessed: the flow never invents an endpoint or a model.
    """
    if role not in MANDATORY_ROLES:
        raise SetupRefused(f"unknown role {role!r}")
    names = ROLE_ENV[role]
    env_map = os.environ if env is None else env
    missing: list[str] = []
    for field_name in ("base_url", "model"):
        if not str(first_env(names[field_name], "", environ=env_map) or "").strip():
            missing.append(field_name)
    return missing


# --------------------------------------------------------------------------- #
# the provider picker — the catalog, with the endpoint already filled in       #
# --------------------------------------------------------------------------- #


#: The provider a first run offers when nothing was chosen. It exists in **both**
#: tables and carries its own model, so a bare Enter is a complete answer.
DEFAULT_PROVIDER_ID = "deepseek"

#: The two *questions* this flow asks, as data. ``ROLE_ENV`` already owns the
#: variable names; this adds the provider pick and the key itself, and
#: :func:`questions` renders one table from both — a new question cannot ship
#: without its flag and its variable.
QUESTION_ORDER: tuple[str, ...] = ("provider", "base_url", "model", "api_key_env", "api_key")

PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    ROLE_TYPESAFE: ROLE_ENV[ROLE_TYPESAFE]["provider"],
    ROLE_LLM: ROLE_ENV[ROLE_LLM]["provider"],
}

#: Where the *value* of a key comes from in a headless run. Never a flag that
#: takes the value: an argv value is in the shell history by definition.
KEY_VALUE_ENV: dict[str, tuple[str, ...]] = {
    ROLE_TYPESAFE: ("SKALDIR_SETUP_JEV_API_KEY",),
    ROLE_LLM: ("SKALDIR_SETUP_LLM_API_KEY",),
}

PROMPTS: dict[str, str] = {
    "provider": "provider (number or id)",
    "base_url": "endpoint",
    "model": "model",
    "api_key_env": "the NAME of the variable that holds the key",
    "api_key": "the key",
}


@dataclass(frozen=True)
class Question:
    """One question, its flag and its variable — the headless contract as data."""

    field: str
    flag: str
    env: tuple[str, ...]
    prompt: str
    secret: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "flag": self.flag,
            "env": list(self.env),
            "prompt": self.prompt,
            "secret": self.secret,
        }


def questions(role: str, *, prefix: str | None = None) -> tuple[Question, ...]:
    """Every question the flow asks one role, with the flag and variable that skip it.

    A missing field is therefore always answerable headlessly, and the *text* of
    the prompt is only ever a rendering of this table.
    """
    if role not in MANDATORY_ROLES:
        raise SetupRefused(f"unknown role {role!r}")
    word = prefix or FLAG_PREFIX[role].lstrip("-")
    out: list[Question] = []
    for field_name in QUESTION_ORDER:
        if field_name == "provider":
            out.append(
                Question(field_name, f"--{word}-provider", PROVIDER_ENV[role], PROMPTS[field_name])
            )
        elif field_name == "api_key":
            out.append(
                Question(
                    field_name,
                    f"--{word}-key-stdin",
                    KEY_VALUE_ENV[role],
                    PROMPTS[field_name],
                    secret=True,
                )
            )
        else:
            env_names = ROLE_ENV[role].get(field_name, ())
            out.append(
                Question(
                    field_name,
                    f"--{word}-{field_name.replace('_', '-')}",
                    tuple(env_names),
                    PROMPTS[field_name],
                )
            )
    return tuple(out)


@dataclass(frozen=True)
class ProviderChoice:
    """One pickable provider: the name, a one-line note, and the endpoint to pre-fill.

    The endpoint is part of the *row*, which is why accepting a provider is one
    keypress: the flow never asks a human to retype a URL the catalog already
    knows. A local server carries its own loopback URL for the same reason.

    A provider with several plan SKUs (Xiaomi pay-as-you-go vs Token Plan, Z.ai
    paas vs coding-plan) yields **one row per endpoint** — ``id`` is then
    ``<provider>#<sku>`` so the plan is a first-class pick, not a silent default.
    """

    id: str
    label: str
    note: str
    base_url: str
    api_key_env: str = ""
    model: str = ""
    local: bool = False
    verification: str = ""
    source: str = "registry"
    docs: str = ""
    speakable: bool = True
    #: The plan/SKU this row is (``pay-as-you-go`` / ``token-plan`` / ``coding-plan`` /
    #: ``subscription`` / ``chatgpt-plan``). Empty = the provider's only/default SKU.
    sku: str = ""
    #: The catalog id without the ``#sku`` suffix (what ``plan_for`` writes as provider).
    provider_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "note": self.note,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "model": self.model,
            "local": self.local,
            "verification": self.verification,
            "source": self.source,
            "docs": self.docs,
            "speakable": self.speakable,
            "sku": self.sku,
            "provider_id": self.provider_id or self.id.split("#", 1)[0],
        }

    def prompt_line(self, number: int) -> str:
        """The picker row as it is printed — a URL and a note, never a credential."""
        mark = " (local)" if self.local else ""
        if self.sku:
            mark += f" [{self.sku}]"
        out = [f"  {number:>2}  {self.label}{mark}", f"      {self.base_url}"]
        if self.note:
            out.append(f"      {_one_line(self.note, 96)}")
        if not self.speakable:
            out.append("      the OpenAI-shaped client cannot speak this endpoint natively")
        return "\n".join(out)


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join(str(text or "").split())[:limit]


def _choice_from_registry(
    entry_id: str, *, endpoint: Any | None = None, multi: bool = False
) -> ProviderChoice:
    spec = registry.REGISTRY[entry_id]
    endpoint = endpoint or spec.primary_endpoint
    note = spec.note or (spec.quirks[0] if spec.quirks else "")
    sku = str(getattr(endpoint, "sku", "") or "")
    # a #sku suffix whenever the provider really has several plan rows and this
    # row is not the primary — never two rows with the same id
    planish = bool(sku) and sku != registry.SKU_PAY_AS_YOU_GO
    if multi and (planish or endpoint is not spec.primary_endpoint):
        suffix = sku or str(endpoint.base_url).rstrip("/").rsplit("/", 1)[-1] or "endpoint"
        label = f"{spec.label} · {suffix}"
        row_id = f"{spec.id}#{suffix}"
    else:
        label = spec.label
        row_id = spec.id
    return ProviderChoice(
        id=row_id,
        label=label,
        note=note,
        base_url=endpoint.base_url,
        api_key_env=endpoint.api_key_env[0] if endpoint.api_key_env else "",
        model=spec.default_model,
        local=endpoint.kind == registry.KIND_LOCAL or pc.endpoint_is_local(endpoint.base_url),
        verification=spec.verification,
        source="registry",
        docs=spec.docs,
        speakable=endpoint.openai_compatible,
        sku=sku,
        provider_id=spec.id,
    )


def _choice_from_catalog(entry: Any) -> ProviderChoice:
    return ProviderChoice(
        id=str(entry.id),
        label=str(entry.label),
        note=str(entry.notes),
        base_url=str(entry.base_url),
        api_key_env=str(entry.api_key_env[0]) if getattr(entry, "api_key_env", ()) else "",
        model=str(getattr(entry, "default_model", "") or ""),
        local=pc.endpoint_is_local(str(entry.base_url)),
        verification="documented",
        source="catalog",
        docs=str(getattr(entry, "docs", "")),
        speakable=str(getattr(entry, "kind", "")) != catalog.KIND_NOT_COMPATIBLE,
    )


def provider_choices(*, include_catalog_only: bool = False) -> tuple[ProviderChoice, ...]:
    """Every provider a first run may pick — the breadth registry, in its own order.

    One row per **plan endpoint** when a provider has several SKUs (Xiaomi
    pay-as-you-go vs Token Plan, Z.ai paas vs coding-plan, Codex API vs ChatGPT
    plan): the plan is a first-class pick, never a silent default. ``include_catalog_only``
    appends the catalog's own ids that the registry does not carry (the generic
    loopback ``local``).
    """
    rows: list[ProviderChoice] = []
    for entry_id in registry.REGISTRY_ORDER:
        spec = registry.REGISTRY[entry_id]
        shaped = spec.plan_endpoints()
        multi = len(shaped) > 1
        if not multi:
            rows.append(_choice_from_registry(entry_id))
        else:
            for endpoint in shaped:
                rows.append(_choice_from_registry(entry_id, endpoint=endpoint, multi=True))
    if include_catalog_only:
        for key in catalog.CATALOG_ORDER:
            if key not in registry.REGISTRY:
                rows.append(_choice_from_catalog(catalog.CATALOG[key]))
    return tuple(rows)


def _norm_url(url: Any) -> str:
    """One endpoint spelling: trimmed, no trailing slash, lower-case (for comparison only)."""
    return str(url or "").strip().rstrip("/").lower()


def provider_id_for_url(base_url: str, *, include_catalog_only: bool = True) -> str:
    """The catalog id whose endpoint is ``base_url`` — ``""`` when no row matches.

    A role is stored as a *provider entry* (endpoint · model · the variable that names the
    credential), never as a catalog id, so the only honest way to say "this is the provider you
    already have" is to match the stored endpoint against the catalog's own list.
    """
    wanted = _norm_url(base_url)
    if not wanted:
        return ""
    for choice in provider_choices(include_catalog_only=include_catalog_only):
        if _norm_url(choice.base_url) == wanted:
            return choice.id
    return ""


def configured_provider(
    role: str, *, path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> str:
    """The catalog id this role is **already** configured with, or ``""``.

    This is the provider question's pre-selection. Without it a re-run offers the hard-coded
    default — or, worse, the *other* role's provider — while the banner tells the user to press
    Enter to accept: one Enter then moves a configured role to an endpoint nobody chose.
    """
    try:
        payload = read_roles(path=path, env=env)
    except Exception:  # noqa: BLE001 — an unreadable config is simply "nothing configured"
        return ""
    slot = (payload.get("roles") or {}).get(role) or {}
    entry = slot.get("entry") or {}
    base = str(entry.get("base_url") or "").strip()
    if not base:
        # the LLM role also has the flat projection the runtime resolves
        base = str((slot.get("projection") or {}).get("base_url") or "").strip()
    return provider_id_for_url(base)


def provider_choice(provider_id: str) -> ProviderChoice:
    """One provider by id (``xiaomi-mimo`` or a plan row ``xiaomi-mimo#token-plan``)."""
    raw = str(provider_id or "").strip().lower()
    if not raw:
        raise SetupRefused(
            f"no provider named — pick one of {list(registry.REGISTRY_ORDER)} "
            "or pass the endpoint yourself"
        )
    base, _, sku = raw.partition("#")
    sku = sku.strip()
    if base in registry.REGISTRY:
        spec = registry.REGISTRY[base]
        if sku:
            for endpoint in spec.plan_endpoints():
                if str(endpoint.sku or "").strip().lower() == sku:
                    return _choice_from_registry(base, endpoint=endpoint, multi=True)
            known = [str(ep.sku or "?") for ep in spec.plan_endpoints()]
            raise SetupRefused(
                f"{base} has no plan SKU {sku!r} (known: {known})"
            )
        return _choice_from_registry(base)
    if raw in catalog.CATALOG:
        return _choice_from_catalog(catalog.CATALOG[raw])
    if base in catalog.CATALOG:
        return _choice_from_catalog(catalog.CATALOG[base])
    known = list(registry.REGISTRY_ORDER) + [
        item for item in catalog.CATALOG_ORDER if item not in registry.REGISTRY
    ]
    raise SetupRefused(f"unknown provider {provider_id!r} (known: {known})")


def provider_defaults(provider_id: str) -> dict[str, str]:
    """The endpoint, model and variable a provider *supplies* — never invented.

    When the credential is already present and its prefix names a plan SKU
    (``tp-`` → Xiaomi Token Plan), that plan's endpoint is chosen automatically.
    """
    choice = provider_choice(provider_id)
    base = choice.provider_id or choice.id.split("#", 1)[0]
    key = ""
    if choice.api_key_env:
        key = str(first_env((choice.api_key_env,), "", environ=None) or "").strip()
    if key and base in registry.REGISTRY:
        spec = registry.REGISTRY[base]
        picked = spec.pick_endpoint(key=key, base_url=choice.base_url)
        if picked.base_url != choice.base_url:
            choice = _choice_from_registry(base, endpoint=picked, multi=True)
    return {
        "provider": base,
        "base_url": choice.base_url,
        "model": choice.model,
        "api_key_env": choice.api_key_env,
        "label": choice.label,
        "sku": choice.sku,
    }


def decision_layer_for(base_url: str, *, kind: str = "", explicit: str = "") -> str:
    """``typesafe`` for the System One endpoint, ``llm`` for a stand-in — never guessed.

    An explicitly written value wins (that is what a re-read of the file is for); else the
    endpoint's kind decides; else the endpoint itself is compared against the one endpoint
    the adapter knows. A model *name* never decides this: ``deepseek-chat`` is not evidence
    of anything, which is exactly why the marker is written down.
    """
    if explicit in SYSTEM_ONE_VALUES:
        return explicit
    if str(kind or "").strip().lower() == registry.KIND_NATIVE:
        return SYSTEM_ONE_NATIVE
    if _norm_url(base_url) == _norm_url(soa.TYPESAFE_ENDPOINT):
        return SYSTEM_ONE_NATIVE
    return SYSTEM_ONE_COMPATIBILITY


def refuse_unspeakable(role: str, catalog_id: str) -> None:
    """The general role needs an OpenAI-shaped chat endpoint — refuse anything else.

    A decision endpoint (``typesafe``) has no ``/chat/completions`` beside it, so a key
    written for the LLM role against it could only ever fail the live check — after the
    user had already typed a key. Refused by name here instead, with the role the row
    belongs to, so the mistake costs one message rather than one credential.
    """
    wanted = str(catalog_id or "").strip()
    if role != ROLE_LLM or not wanted:
        return
    try:
        choice = provider_choice(wanted)
    except SetupRefused:
        return  # an unknown id is the store's own refusal; this rule is about the wire shape
    if choice.speakable:
        return
    extra = ""
    if "#" in wanted or getattr(choice, "sku", ""):
        base = wanted.split("#", 1)[0]
        extra = (
            f" The chat-capable SKU of {base} is `{base}` "
            f"(or `{base}#<plan>` with an OpenAI-shaped base URL)."
        )
    raise SetupRefused(
        f"{ROLE_LABEL[ROLE_LLM]}: {wanted} ({choice.base_url}) is not an OpenAI-shaped chat "
        "endpoint — it cannot answer chat, so it cannot be the general model."
        + (
            f" It belongs to the other role: `skaldir model --role typesafe --provider {wanted}`."
            if wanted == "typesafe"
            else ""
        )
        + f"{extra} Give the LLM role a chat provider instead (e.g. --provider deepseek)"
    )


def default_provider(role: str, *, env: Mapping[str, str] | None = None) -> str:
    """The provider offered when Enter is pressed — the variable wins over the default."""
    found = str(first_env(PROVIDER_ENV[role], "", environ=env) or "").strip()
    return found or DEFAULT_PROVIDER_ID


def provider_report(*, include_catalog_only: bool = True) -> dict[str, Any]:
    """The picker as JSON: name, one-line note, pre-filled base URL, per row.

    ``count`` is the breadth registry (16 rows: the list a first run shows).
    ``extras`` are the catalog's own ids the registry does not carry — reachable by
    id (``--llm-provider local``), never smuggled into the count.
    """
    rows = provider_choices()
    known = {row.id for row in rows}
    extras = (
        tuple(row for row in provider_choices(include_catalog_only=True) if row.id not in known)
        if include_catalog_only
        else ()
    )
    return {
        "count": len(rows),
        "registry_count": len(registry.REGISTRY_ORDER),
        "order": [row.id for row in rows],
        "default": DEFAULT_PROVIDER_ID,
        "providers": [row.to_dict() for row in rows],
        "extra_count": len(extras),
        "extras": [row.to_dict() for row in extras],
        "local": [row.id for row in (*rows, *extras) if row.local],
        "note": (
            "every row carries the base URL the flow pre-fills, so accepting a provider "
            "is one Enter; a local row carries its own loopback URL"
        ),
    }


# --------------------------------------------------------------------------- #
# the .env — one 0600 file, one line per credential, never printed             #
# --------------------------------------------------------------------------- #

ENV_FILE_NAME = ".env"
ENV_FILE_MODE = 0o600

#: The variable that moves the credential file (a project `.env`, a container path).
ENV_FILE_ENV: tuple[str, ...] = ("SKALDIR_SETUP_ENV_FILE",)

#: A line we can read back (``NAME=value``), with or without ``export``.
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")
#: A value that has to be quoted for a shell to read it back as one token.
_ENV_NEEDS_QUOTES = re.compile(r"""[\s#"'`$\\]""")


@dataclass(frozen=True)
class EnvFile:
    """The credential file as it is on disk — its names and mode, never its values.

    ``values`` exists so the flow can *use* the key it just wrote; every export
    goes through :meth:`redacted`, which drops it. A report is not a secret store.
    """

    path: Path
    exists: bool
    mode: str
    lines: tuple[str, ...]
    values: Mapping[str, str] = field(default_factory=dict)
    duplicate_names: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.values)

    def has(self, name: str) -> bool:
        return bool(str(self.values.get(name) or "").strip())

    def value(self, name: str) -> str:
        return str(self.values.get(name) or "")

    def redacted(self) -> dict[str, Any]:
        """JSON-safe view: the path, the mode and the *names* — never a value."""
        return {
            "path": str(self.path),
            "exists": self.exists,
            "mode": self.mode,
            "lines": len(self.lines),
            "names": list(self.names),
            "duplicates": list(self.duplicate_names),
            "values": "redacted",
        }


def _mode_str(target: Path) -> str:
    """``0600`` (octal, four digits) for a path that exists, ``""`` when it does not."""
    try:
        info = target.stat()
    except OSError:
        return ""
    if not stat.S_ISREG(info.st_mode):
        return ""
    return format(stat.S_IMODE(info.st_mode), "04o")


def env_file_path(
    *,
    config_path: str | Path | None = None,
    explicit: str = "",
    env: Mapping[str, str] | None = None,
) -> Path:
    """Where the credentials live: the flag, then the variable, then beside the config.

    The default is the config file's own directory on purpose — the two files
    reference each other (``config.yaml`` names the variable it reads), so they
    stay a pair. A project ``.env`` is one flag or one variable away.
    """
    given = str(explicit or "").strip()
    if given:
        return Path(given).expanduser()
    from_var = str(first_env(ENV_FILE_ENV, "", environ=env) or "").strip()
    if from_var:
        return Path(from_var).expanduser()
    return pc.config_path(config_path).parent / ENV_FILE_NAME


def _unquote(text: str) -> str:
    body = str(text or "").strip()
    if len(body) >= 2 and body[0] == body[-1] and body[0] in ("'", '"'):
        inner = body[1:-1]
        if body[0] == '"':
            for old, new in (("\\\\", "\\"), ('\\"', '"'), ("\\`", "`"), ("\\$", "$")):
                inner = inner.replace(old, new)
        return inner
    return body


def read_env_file(path: str | Path) -> EnvFile:
    """Read the credential file: names and values, comments and order preserved.

    A file that cannot be read is reported as absent rather than raised — reading
    a *hint* must never be why a setup fails.
    """
    target = Path(path).expanduser()
    if not target.is_file():
        return EnvFile(path=target, exists=False, mode="", lines=())
    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return EnvFile(path=target, exists=False, mode="", lines=())
    lines = tuple(raw.splitlines())
    values: dict[str, str] = {}
    duplicates: list[str] = []
    for line in lines:
        found = _ENV_LINE.match(line)
        if found is None:
            continue
        name = found.group("name")
        if name in values:
            duplicates.append(name)
            continue
        values[name] = _unquote(found.group("value"))
    return EnvFile(
        path=target,
        exists=True,
        mode=_mode_str(target),
        lines=lines,
        values=values,
        duplicate_names=tuple(dict.fromkeys(duplicates)),
    )


def render_env_line(name: str, value: str) -> str:
    """``NAME=value`` as one line a shell reads back as exactly this value.

    Quoted when it has to be (whitespace, ``#``, quotes, ``$``, a backslash), and
    **refused** rather than escaped when it contains a newline: a value that could
    break out of its own line would let a pasted key rewrite the file.
    """
    text = str(value)
    if any(char in text for char in ("\n", "\r", "\x00")):
        raise SetupRefused(
            f"{name}: a credential value must be a single line "
            "(a newline would rewrite the file)"
        )
    if not text or _ENV_NEEDS_QUOTES.search(text):
        escaped = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("`", "\\`")
            .replace("$", "\\$")
        )
        return f'{name}="{escaped}"'
    return f"{name}={text}"


_ANSI_ESCAPE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI: colours + bracketed-paste ESC[200~ / ESC[201~
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[@-Z\\-_]"  # two-byte escapes
)
_CONTROL_KEEP = {"\n", "\r", "\x00"}  # checked below — a real multi-line paste is refused


def _sanitize_key(text: str) -> str:
    """Strip the terminal artifacts a paste can leave on a credential.

    A modern terminal wraps a paste in bracketed-paste markers (``ESC[200~`` …
    ``ESC[201~``). Read raw by ``getpass``, those escape bytes stick to the value and
    the endpoint sees ``ESC[200~<key>ESC[201~`` — never the user's key — so every
    provider answers ``401 Invalid API Key``. Only printable bytes survive: a key is
    printable ASCII, never escapes or control codes.
    """
    text = _ANSI_ESCAPE.sub("", str(text or ""))
    # a credential is printable ASCII — drop non-ASCII (a `·` visual separator copied
    # with the key would otherwise fail httpx's ascii header normalisation) and any
    # remaining control bytes. `_CONTROL_KEEP` are checked afterwards, never sent.
    text = "".join(
        ch for ch in text if (" " <= ch <= "~") or ch in _CONTROL_KEEP
    )
    return text.strip()


def refuse_key_value(value: str, *, name: str = "the key") -> str:
    """A pasted credential: non-empty, single line, and never echoed back."""
    text = _sanitize_key(value)
    if not text:
        raise SetupRefused(f"{name} is empty — nothing was written")
    if any(char in text for char in ("\n", "\r", "\x00")):
        raise SetupRefused(f"{name} contains a line break — that would rewrite the file")
    return text


def write_env_file(
    path: str | Path,
    updates: Mapping[str, str],
    *,
    mode: int = ENV_FILE_MODE,
    create: bool = True,
) -> dict[str, Any]:
    """Append/replace ``NAME=value`` lines — atomically, ``0600``, values unlogged.

    Everything already in the file is preserved **verbatim**: comments, blank
    lines, ordering and every other variable. A name that is already there is
    replaced in place; a second line with the same name (which would shadow the
    first) is removed and reported as ``deduplicated``. A backup is deliberately
    **not** taken: copying a credential file multiplies the copies of the secret,
    and replacing a line by name is already non-destructive for everything else.
    """
    target = Path(path).expanduser()
    plan: list[tuple[str, str]] = []
    for key, value in updates.items():
        name = validate_key_name(key)
        plan.append((name, refuse_key_value(value, name=name)))
    report: dict[str, Any] = {
        "path": str(target),
        "mode": "",
        "created": False,
        "written": False,
        "appended": [],
        "replaced": [],
        "deduplicated": [],
        "preserved_lines": 0,
        "bytes": 0,
    }
    if not plan:
        report["reason"] = "nothing to write"
        return report
    existed = target.is_file()
    if not existed and not create:
        report["reason"] = f"{target} does not exist and creation was not requested"
        return report

    wanted = dict(plan)
    existing = read_env_file(target)
    if existing.exists:
        report["mode_before"] = existing.mode
        report["deduplicated"] = list(existing.duplicate_names)

    lines = list(existing.lines)
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        found = _ENV_LINE.match(line)
        if found is None:
            out.append(line)
            continue
        name = found.group("name")
        if name not in wanted:
            out.append(line)
            continue
        if name in seen:
            continue  # a duplicate would shadow the line we just wrote
        out.append(render_env_line(name, wanted[name]))
        seen.add(name)
        report["replaced"].append(name)
    for name, value in plan:
        if name in seen:
            continue
        out.append(render_env_line(name, value))
        seen.add(name)
        report["appended"].append(name)

    body = "\n".join(out) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".skaldir-env-tmp-{os.getpid()}"
    try:
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            # The mode is set on the descriptor of the file we just opened — never on a
            # *name*. Two things follow, and both are the point: a directory the caller did
            # not name can never lose its mode (the descriptor is a regular file we created
            # with O_CREAT), and the key file is already 0600 *before* `os.replace` installs
            # it, so there is no window in which the secrets sit at the process umask.
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:  # pragma: no cover — a temp file we cannot remove is not fatal here
            pass
        raise SetupError(f"cannot write {target}: {type(exc).__name__}") from exc

    report.update(
        {
            "created": not existed,
            "written": True,
            "mode": _mode_str(target),
            "preserved_lines": len(lines) - len(report["replaced"]),
            "bytes": len(body.encode("utf-8")),
            "replaced": sorted(set(report["replaced"])),
            "appended": sorted(set(report["appended"])),
        }
    )
    return report


def key_env_for(
    env: Mapping[str, str] | None,
    env_file: EnvFile | None,
    *,
    override: bool = False,
) -> dict[str, str]:
    """The environment a validation call runs in: the process, then the credential file.

    ``env`` wins by default — a value the operator exported for *this* shell is the
    more deliberate one — and the file fills in whatever the process does not have.
    """
    merged = dict(env or os.environ)
    if env_file is None or not env_file.exists:
        return merged
    for name, value in env_file.values.items():
        if not str(value or "").strip():
            continue
        if not override and str(merged.get(name) or "").strip():
            continue
        merged[name] = value
    return merged


def key_available(
    role: str,
    api_key_env: str,
    *,
    env: Mapping[str, str] | None = None,
    env_file: EnvFile | None = None,
) -> dict[str, Any]:
    """Is this key already somewhere we can use? — and **where**, named exactly.

    The order is deliberate: a headless variable set for this run, then the
    process environment, then the credential file. The answer says which one it
    was, so a re-run can print "reused" instead of asking again.
    """
    if role not in MANDATORY_ROLES:
        raise SetupRefused(f"unknown role {role!r}")
    env_map = dict(env if env is not None else os.environ)
    for name in KEY_VALUE_ENV[role]:
        if str(env_map.get(name) or "").strip():
            return {"available": True, "source": name, "kind": "environment", "name": api_key_env}
    target = str(api_key_env or "").strip()
    if target and str(env_map.get(target) or "").strip():
        return {"available": True, "source": target, "kind": "environment", "name": target}
    if target and env_file is not None and env_file.has(target):
        return {
            "available": True,
            "source": str(env_file.path),
            "kind": "env-file",
            "name": target,
        }
    if not target and env_file is not None:
        return {"available": False, "source": "", "kind": "", "name": ""}
    return {"available": False, "source": "", "kind": "", "name": target}


def key_value_from_env(role: str, *, env: Mapping[str, str] | None = None) -> str:
    """The headless spelling of the key itself (never a flag: argv is history)."""
    if role not in MANDATORY_ROLES:
        raise SetupRefused(f"unknown role {role!r}")
    return str(first_env(KEY_VALUE_ENV[role], "", environ=env) or "").strip()


def key_value_from_stream(stream: Iterable[str] | None = None) -> str:
    """One line from a stream — the scripted equivalent of the masked prompt."""
    source = stream
    if source is None:
        import sys

        source = [sys.stdin.readline()]
    for line in source:
        return refuse_key_value(str(line or "").rstrip("\r\n"), name="the key read from stdin")
    return ""


def _key_name_for(
    role: str, *, provider_id: str = "", api_key_env: str = "", env: Mapping[str, str] | None = None
) -> str:
    """The variable NAME a key would be stored under — resolved, or refused."""
    given = str(api_key_env or "").strip()
    if given:
        return validate_key_name(given)
    wanted = str(provider_id or "").strip() or default_provider(role, env=env)
    return validate_key_name(provider_defaults(wanted)["api_key_env"])


def missing_fields(
    role: str,
    *,
    values: Mapping[str, str] | None = None,
    provider_id: str = "",
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """What a headless caller still has to provide, **after** flags and catalog.

    Reported instead of guessed, and it knows the difference between "no endpoint
    at all" and "the provider supplied the endpoint but not a model" — the second
    is one flag, not three.
    """
    if role not in MANDATORY_ROLES:
        raise SetupRefused(f"unknown role {role!r}")
    given = {str(k): str(v or "").strip() for k, v in dict(values or {}).items()}
    picked = str(provider_id or "").strip()
    supplied = provider_defaults(picked) if picked else {}
    out: list[str] = []
    base_url = given.get("base_url") or supplied.get("base_url", "")
    if not base_url:
        out.append("provider" if not picked else "base_url")
    if not (given.get("model") or supplied.get("model", "")):
        out.append("model")
    key_name = given.get("api_key_env") or supplied.get("api_key_env", "")
    if not key_name and not _is_local_url(base_url):
        out.append("api_key_env")
    return out


def _is_local_url(url: str) -> bool:
    return bool(url) and pc.endpoint_is_local(str(url))


def key_flags_for(role: str, missing: Sequence[str]) -> str:
    """The exact flag spellings for the fields a caller still owes us."""
    word = FLAG_PREFIX[role].lstrip("-")
    table = {item.field: item.flag for item in questions(role)}
    fields = list(missing) or ["provider", "base_url", "model", "api_key_env"]
    return ", ".join(table.get(field, f"--{word}-{field.replace('_', '-')}") for field in fields)


def env_names_for_fields(role: str, missing: Sequence[str]) -> str:
    """The exact variable spellings for the fields a caller still owes us."""
    table = {item.field: item.env for item in questions(role)}
    wanted = missing or ["provider", "base_url", "model"]
    fields = [field for field in wanted if field != "api_key"]
    return ", ".join(table[field][0] for field in fields if table.get(field))


# --------------------------------------------------------------------------- #
# what happens next — the commands, checked against the real command tree      #
# --------------------------------------------------------------------------- #

#: Preferred first commands, in the owner's own words first. The line printed to
#: the user is a *lookup*, not a template: `skaldir chat` is printed the day the
#: command exists, and `skaldir shell` is printed while it does not.
TALK_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("chat", "talk to the model in a session"),
    ("shell", "talk to the model in a session"),
)
TASK_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("run", "give it a task and watch it work"),
    ("agent.run", "give it a task and watch it work"),
)

_REACHABLE_CACHE: dict[str, Any] = {}


def reachable_commands() -> tuple[str, ...]:
    """Every leaf command the *real* tree has, as dotted paths, or the contract's list.

    The command tree is the authority (`cli.build_parser()`); the contract's
    ``COMMANDS`` table is the fallback for a build that cannot import the CLI. An
    unimportable tree is reported as an empty tuple — never as a guess.
    """
    return tuple(command_tree()["commands"])


def command_tree() -> dict[str, Any]:
    """``{"commands": (...), "source": ...}`` — where the command list came from.

    ``source`` is the honest provenance of the list: ``cli.build_parser()``, the
    contract's own table, or ``"unavailable"`` with the reason. A caller that
    prints a command name must be able to say what it checked it against.
    """
    if "tree" in _REACHABLE_CACHE:
        return dict(_REACHABLE_CACHE["tree"])
    payload: dict[str, Any] = {"commands": (), "source": "unavailable", "reason": ""}
    try:
        from .cli import build_parser

        payload["commands"] = tuple(sorted(_walk_commands(build_parser(), "")))
        payload["source"] = "cli.build_parser()"
    except Exception as exc:  # noqa: BLE001 — a build without the CLI falls back to the contract
        payload["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        try:
            from . import cli_contract

            payload["commands"] = tuple(sorted(cli_contract.COMMANDS))
            payload["source"] = "cli_contract.COMMANDS"
        except Exception:  # noqa: BLE001 — and a build without either says so
            payload["commands"] = ()
    _REACHABLE_CACHE["tree"] = dict(payload)
    return payload


def _walk_commands(parser: Any, prefix: str) -> set[str]:
    import argparse

    out: set[str] = set()
    for action in getattr(parser, "_actions", []):
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001 — argparse has no public API
            continue
        for name, child in action.choices.items():
            path = f"{prefix}.{name}" if prefix else str(name)
            out.add(path)
            out.update(_walk_commands(child, path))
    return out


def next_steps(
    *, tree: Sequence[str] | None = None, env_file: EnvFile | None = None
) -> dict[str, Any]:
    """The first commands, each one **looked up** in the tree that would run it.

    A preferred spelling that this build does not have is reported under
    ``preferred_missing`` instead of being printed as if it existed, so a line a
    user copies is a line that runs.
    """
    known = command_tree()
    present = set(tree if tree is not None else known["commands"])
    steps: list[dict[str, str]] = [
        {
            "command": "skaldir setup --check",
            "why": "re-validate both keys in one call (writes nothing)",
        }
    ]
    missing: list[str] = []
    for kind, candidates in (("talk", TALK_CANDIDATES), ("task", TASK_CANDIDATES)):
        for name, why in candidates:
            if name not in present:
                missing.append(name)
                continue
            tail = ' "…"' if kind == "task" else ""
            steps.append({"command": f"skaldir {name}{tail}", "why": why})
            break
        else:
            steps.append(
                {
                    "command": "",
                    "why": f"no {kind} command in this build (looked for: "
                    + ", ".join(name for name, _ in candidates)
                    + ")",
                }
            )
    if env_file is not None and env_file.exists:
        steps.append(
            {
                "command": f"set -a; . {env_file.path}; set +a",
                "why": "put the credentials from the .env into a shell (the file is mode 0600)",
            }
        )
    return {
        "commands": steps,
        "verified_against": known["source"],
        "tree_size": len(present),
        "preferred_missing": missing,
        "env_file": str(env_file.path) if env_file is not None else "",
    }


# --------------------------------------------------------------------------- #
# detection — the honest state, first                                          #
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _runtime(runtime: str | Path | None) -> tuple[Path, str]:
    from .registry import default_runtime_dir, runtime_reason

    if runtime:
        path = Path(str(runtime)).expanduser()
        return path, "explicit --runtime"
    path = default_runtime_dir()
    return path, runtime_reason(path)


def _docker_state() -> dict[str, Any]:
    """Is a docker CLI on PATH? (Reported, never started — setup runs no container.)"""
    found = shutil.which("docker")
    return {"available": bool(found), "cli": str(found or "")}


def _settings_state(runtime: Path) -> dict[str, Any]:
    """The System-One flags as they sit on disk, or why they could not be read."""
    from .system_one_flags import SETTINGS_NAME, load_system_one_flags

    path = runtime / SETTINGS_NAME
    state: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    try:
        flags = load_system_one_flags(runtime)
        state.update(
            {
                "readable": True,
                "enabled": bool(flags.enabled),
                "provider": str(flags.provider),
            }
        )
    except Exception as exc:  # noqa: BLE001 — a report must report, not raise
        state.update({"readable": False, "error": f"{type(exc).__name__}: {exc}"[:200]})
    return state


def _tools_state(runtime: Path, tools_db: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "db": str(tools_db),
        "exists": tools_db.is_file(),
        "count": 0,
        "names": [],
    }
    if not tools_db.is_file():
        return state
    try:
        from .capability import load_tool_entries
        from .registry import _read_specs  # noqa: SLF001 — the registry's own reader

        specs, error = _read_specs(tools_db)
        state["count"] = len(specs)
        state["names"] = [str(spec["name"]) for spec in specs]
        state["readable"] = error is None
        if error:
            state["error"] = str(error)[:200]
        state["generated"] = len(load_tool_entries(runtime))
    except Exception as exc:  # noqa: BLE001 — a report must report, not raise
        state["readable"] = False
        state["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return state


def _config_state(path: Path, env: Mapping[str, str] | None) -> dict[str, Any]:
    """The config file as the store reads it — refusals included, never a crash."""
    state: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "bytes": 0,
    }
    if path.is_file():
        try:
            state["bytes"] = path.stat().st_size
        except OSError:  # pragma: no cover — an unreadable stat is reported as unknown
            state["bytes"] = -1
    try:
        store = pc.load_store(path, env=env)
    except pc.ProviderStoreError as exc:
        state["readable"] = False
        state["refused"] = f"{type(exc).__name__}: {exc}"[:300]
        return state
    except Exception as exc:  # noqa: BLE001 — a malformed file is a state, not a crash
        state["readable"] = False
        state["refused"] = f"{type(exc).__name__}: {exc}"[:300]
        return state
    state.update(
        {
            "readable": True,
            "default": store.default,
            "providers": store.report(env)["providers"],
            "count": len(store.providers),
            "warnings": list(store.warnings),
            "blocks": sorted(
                str(key) for key in (store.document.get("godsv") or {}) if isinstance(key, str)
            )
            if isinstance(store.document.get("godsv"), Mapping)
            else [],
        }
    )
    return state


def read_roles(
    *, path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Re-read the config and report both key slots as they really are.

    This is what makes the verification step a *re-read* rather than a re-use of
    what the flow believes it wrote: the two blocks are read back out of the file,
    resolved through the provider layer, and their credential state is taken from
    the environment.
    """
    target = pc.config_path(path)
    payload: dict[str, Any] = {
        "config_file": str(target),
        "config_exists": target.is_file(),
        "roles": {},
    }
    try:
        store = pc.load_store(target, env=env)
    except Exception as exc:  # noqa: BLE001 — reported as a slot state, never raised
        for role in MANDATORY_ROLES:
            payload["roles"][role] = {
                "present": False,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            }
        payload["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return payload

    godsv = store.document.get("godsv")
    godsv_block = dict(godsv) if isinstance(godsv, Mapping) else {}
    jev_raw = godsv_block.get(JEV_BLOCK)
    jev_block = dict(jev_raw) if isinstance(jev_raw, Mapping) else None
    secret_keys = sorted(
        {str(key).strip().lower() for key in (jev_block or {})}.intersection(lp.SECRET_FILE_KEYS)
    )

    for role in MANDATORY_ROLES:
        name = ENTRY_NAME[role]
        entry = store.providers.get(name)
        slot: dict[str, Any] = {
            "name": name,
            "present": entry is not None,
            "block": "godsv.llm" if role == ROLE_LLM else f"godsv.{JEV_BLOCK}",
        }
        if role == ROLE_LLM:
            # the LLM provider has TWO spellings on disk and both must agree:
            # the named entry and the flat projection the runtime resolves.
            active = lp.resolve_provider_config(env=dict(env or os.environ), config_path=target)
            slot["projection"] = {
                "base_url": active.base_url,
                "model": active.model,
                "api_key_env": active.api_key_env,
                "ready": active.ready(),
                "problems": list(active.problems),
            }
            slot["drift"] = [
                f"godsv.llm.{key} != godsv.providers.entries.{name}.{key}"
                for key in ("base_url", "model", "api_key_env")
                if entry is not None
                and str(getattr(entry, key) or "").strip()
                and str(getattr(entry, key) or "").strip()
                != str(({"base_url": active.base_url, "model": active.model,
                         "api_key_env": active.api_key_env})[key] or "").strip()
            ]
        if role == ROLE_TYPESAFE:
            slot["block_present"] = jev_block is not None
            slot["literal_secret_keys"] = secret_keys
            if jev_block is not None:
                slot["literal_secret_keys"] = secret_keys or sorted(
                    {str(key).strip().lower() for key in jev_block}.intersection(
                        lp.SECRET_FILE_KEYS
                    )
                )
                slot["api_key_env"] = str(jev_block.get("api_key_env") or "")
                # which decision provider this block is — derived when the file predates
                # the marker, so a hand-written install is reported honestly too
                slot[SYSTEM_ONE_KEY] = decision_layer_for(
                    str(jev_block.get("base_url") or ""),
                    kind=str(jev_block.get("provider") or ""),
                    explicit=str(jev_block.get(SYSTEM_ONE_KEY) or ""),
                )
                local = pc.endpoint_is_local(str(jev_block.get("base_url") or ""))
                slot["credential"] = (
                    "set"
                    if str((dict(env or os.environ)).get(slot["api_key_env"]) or "").strip()
                    else ("not-required" if local else "unset")
                )
        if entry is not None:
            slot["entry"] = entry.redacted(env)
        payload["roles"][role] = slot

    payload["complete"] = all(
        bool(payload["roles"][role].get("entry", {}).get("ready")) for role in MANDATORY_ROLES
    )
    return payload


def credentials_report(
    env_file: EnvFile, *, written: bool = False, reason: str = ""
) -> dict[str, Any]:
    """The credential file in a report: where it is, its mode, its names, who reads it.

    The last two fields exist because a file nobody reads is a lie waiting to
    happen: they state which commands honour it today and the one command that
    puts it into a shell — so a reader is never left guessing.
    """
    payload = dict(env_file.redacted())
    payload["written"] = written
    if reason:
        payload["reason"] = reason
    payload["read_by"] = ["skaldir setup", "skaldir setup --check"]
    payload["into_a_shell"] = f"set -a; . {env_file.path}; set +a"
    return payload


def detect_state(
    *,
    runtime: str | Path | None = None,
    config_path: str | Path | None = None,
    project: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """What is already configured — measured, never guessed.

    Runs no provider call and writes nothing. Every section is guarded, because a
    report that raises is worse than a report that says "unreadable".
    """
    runtime_dir, reason = _runtime(runtime)
    tools_db = runtime_dir / "tools.db"
    target = pc.config_path(config_path)
    credential_path = env_file_path(config_path=target, env=env)
    state: dict[str, Any] = {
        "checked_at": _now(),
        "runtime_dir": str(runtime_dir),
        "runtime_reason": reason,
        "runtime_exists": runtime_dir.is_dir(),
        "config": _config_state(target, env),
        "credentials_file": credentials_report(read_env_file(credential_path)),
        "settings": _settings_state(runtime_dir),
        "tools": _tools_state(runtime_dir, tools_db),
        "docker": _docker_state(),
        "roles": {},
        "configured": False,
        "missing": [],
    }

    try:
        roles = read_roles(path=target, env=env)
        state["roles"] = roles["roles"]
    except Exception as exc:  # noqa: BLE001 — a report must report, not raise
        state["roles_error"] = f"{type(exc).__name__}: {exc}"[:200]

    missing: list[str] = []
    for role in MANDATORY_ROLES:
        slot = state["roles"].get(role) or {}
        entry = slot.get("entry") or {}
        if not entry:
            missing.append(f"{ROLE_LABEL[role]} — not configured")
            continue
        if not entry.get("ready"):
            for item in entry.get("missing") or []:
                missing.append(f"{ROLE_LABEL[role]} — {item}")
    if not state["settings"].get("enabled"):
        missing.append(
            "System-One (the Jev layer) is off in settings.yaml — run `skaldir jev enable`"
        )
    state["missing"] = missing
    # ``configured`` means exactly "both mandatory keys are present and usable";
    # the Jev flag is a *next step*, not a third key, so it does not downgrade it.
    state["configured"] = all(
        bool((state["roles"].get(role) or {}).get("entry", {}).get("ready"))
        for role in MANDATORY_ROLES
    )
    state["next_steps"] = [
        item
        for item in missing
        if not any(item.startswith(ROLE_LABEL[role]) for role in MANDATORY_ROLES)
    ]
    state["degraded"] = degraded_status(runtime=runtime, config_path=config_path, env=env)
    return state


# --------------------------------------------------------------------------- #
# the live validation call                                                     #
# --------------------------------------------------------------------------- #


class _Recorder:
    """A real ``httpx.Client`` wrapper that keeps request bodies and answers.

    Only JSON bodies and JSON answers are kept — **never** headers, so an
    ``Authorization`` value cannot reach a report. The transport is the real one;
    this is an observer, not a mock.
    """

    def __init__(self, inner: Any = None) -> None:
        import httpx

        self._inner = inner if inner is not None else httpx.Client(follow_redirects=False)
        self.requests: list[dict[str, Any]] = []
        self.payloads: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        self.requests.append({"url": str(url), "body": kwargs.get("json")})
        response = self._inner.post(url, **kwargs)
        self._capture(response)
        return response

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        self.requests.append({"url": str(url), "body": kwargs.get("json")})
        return self._inner.stream(method, url, **kwargs)

    def _capture(self, response: Any) -> None:
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 — an unreadable body is the client's problem
            return
        if isinstance(payload, Mapping):
            self.payloads.append({str(k): v for k, v in payload.items()})

    @property
    def last_body(self) -> dict[str, Any]:
        for entry in reversed(self.requests):
            body = entry.get("body")
            if isinstance(body, Mapping):
                return dict(body)
        return {}

    @property
    def last_payload(self) -> dict[str, Any]:
        return dict(self.payloads[-1]) if self.payloads else {}

    def close(self) -> None:
        self._inner.close()


def _model_of(payload: Mapping[str, Any] | None) -> str:
    return str((payload or {}).get("model") or "")


def _finish_reason_of(payload: Mapping[str, Any] | None) -> str:
    choices = (payload or {}).get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    return str(first.get("finish_reason") or "")


def _preview(answer: str, limit: int = 200) -> tuple[str, list[str]]:
    text = " ".join(str(answer or "").split())[:limit]
    redacted, findings = secretscan.redact(text)
    return redacted, sorted({finding.kind for finding in findings})


def _config_for(key: KeySpec, *, env: Mapping[str, str], timeout_s: float | None) -> Any:
    explicit = key.explicit_config()
    if timeout_s is not None:
        explicit["timeout_s"] = float(timeout_s)
    cfg = lp.resolve_provider_config(explicit, env=dict(env), config_path=None)
    return cfg


def _failure(message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "problem": message}
    payload.update(extra)
    return payload


def measure_key(
    key: KeySpec,
    *,
    env: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    measure_streaming: bool = True,
    measure_structured: bool = True,
) -> dict[str, Any]:
    """One real call per measurement, with the numbers it produced.

    Reports exactly four measured facts — ``model_echo`` (what the endpoint said
    it ran, not what we asked for), ``latency_ms``, ``streaming`` and
    ``structured`` — plus the credential state and every problem found. A remote
    endpoint without a credential raises :class:`SetupRefused` **before** any
    request is made: a key that was never sent cannot be validated.

    Nothing is coerced: a refused streaming attempt is reported as ``false`` with
    the endpoint's own message, and a structured answer that breaks the schema is
    reported with its raw text and the validation error.
    """
    env_map = dict(os.environ if env is None else env)
    try:
        cfg = _config_for(key, env=env_map, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 — a bad configuration is a refusal
        raise SetupRefused(f"{key.label}: {type(exc).__name__}: {exc}"[:300]) from exc

    entry = key.entry()
    if entry.needs_credential(env_map):
        export = key.api_key_env or lp.ENV_API_KEY
        raise SetupRefused(
            f"{key.label} points at {key.base_url} and has no credential in the "
            f"environment: export {export}=<your-key> (the value is never stored)"
        )
    if cfg.problems:
        raise SetupRefused(f"{key.label}: provider config refused: {'; '.join(cfg.problems)}")
    if not cfg.ready():
        raise SetupRefused(f"{key.label}: {lp.not_configured_message(cfg)}")

    report: dict[str, Any] = {
        "ok": False,
        "role": key.role,
        "label": key.label,
        "name": key.name,
        "base_url": key.base_url,
        "endpoint": lp.endpoint_for(key.base_url),
        "model": key.model,
        "tier": PROBE_TIER,
        "model_for_tier": cfg.model_for_tier(PROBE_TIER),
        "client": "OpenAICompatibleLLM",
        "resolved_by": "skaldir.llm_provider.provider_for",
        "credential": entry.credential_state(env_map),
        "credential_env": entry.credential_env(env_map),
        "catalog_id": key.catalog_id,
        "measurements": [],
    }

    # -- 1. one real completion: model echo + latency ----------------------- #
    recorder = _Recorder()
    client = lp.OpenAICompatibleLLM(cfg, client=recorder)
    started = time.monotonic()
    try:
        answer = client.complete(prompt_id=PROBE_PROMPT_ID, prompt=PROBE_PROMPT, tier=PROBE_TIER)
    except Exception as exc:  # noqa: BLE001 — a failed validation is a measured failure
        latency_ms = int((time.monotonic() - started) * 1000)
        recorder.close()
        report.update(
            _failure(
                f"{key.label}: the endpoint did not answer: {type(exc).__name__}: {exc}"[:300],
                latency_ms=latency_ms,
                status=getattr(exc, "status", None),
                phase="completion",
            )
        )
        report["model_echo"] = _model_of(recorder.last_payload)
        report["finish_reason"] = _finish_reason_of(recorder.last_payload)
        report["measurements"].append(
            {"name": "completion", "ok": False, "latency_ms": latency_ms,
             "error": f"{type(exc).__name__}: {exc}"[:200]}
        )
        report["streaming"] = {"supported": False, "measured": False,
                               "reason": "the completion failed, streaming was not tried"}
        report["structured"] = {"measured": False,
                                "reason": "the completion failed, structured was not tried"}
        return report
    latency_ms = int((time.monotonic() - started) * 1000)
    last_body = recorder.last_body
    last_payload = recorder.last_payload
    preview, redacted_kinds = _preview(answer)
    recorder.close()

    report.update(
        {
            "ok": True,
            "latency_ms": latency_ms,
            "model_echo": _model_of(last_payload),
            "finish_reason": _finish_reason_of(last_payload),
            "answer_chars": len(answer),
            "answer_preview": preview,
            "redacted": bool(redacted_kinds),
            "redacted_kinds": redacted_kinds,
            "usage": _usage_of(last_payload),
            "request": {
                "model": last_body.get("model"),
                "messages": len(last_body.get("messages") or []),
                "stream": bool(last_body.get("stream")),
            },
        }
    )
    report["measurements"].append(
        {"name": "completion", "ok": True, "latency_ms": latency_ms,
         "model_echo": report["model_echo"]}
    )

    # -- 2. streaming: asked for, and only then believed -------------------- #
    if measure_streaming:
        report["streaming"] = _measure_streaming(cfg, key=key, env=env_map)
    else:
        report["streaming"] = {"measured": False, "reason": "not requested"}

    # -- 3. structured output mode ------------------------------------------ #
    if measure_structured:
        report["structured"] = _measure_structured(cfg, key=key)
    else:
        report["structured"] = {"measured": False, "reason": "not requested"}

    return report


def _usage_of(payload: Mapping[str, Any] | None) -> dict[str, int]:
    usage = (payload or {}).get("usage")
    if not isinstance(usage, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[str(key)] = int(value)
    return out


def _first_byte_ms(client: Any) -> int | None:
    """Time to first streamed content token, if the client measured one.

    ``OpenAICompatibleLLM.first_byte_ms`` is a *method* (it returns ``None`` until a
    streamed call runs), so it is called — and a client that has no such accessor
    reports ``None`` instead of a bound method a report could not serialize.
    """
    accessor = getattr(client, "first_byte_ms", None)
    if not callable(accessor):
        return None
    try:
        value = accessor()
    except Exception:  # noqa: BLE001 — a report must report, not raise
        return None
    return int(value) if isinstance(value, (int, float)) else None


def _measure_streaming(cfg: Any, *, key: KeySpec, env: Mapping[str, str]) -> dict[str, Any]:
    """Ask for a streamed answer and report whether one really arrived."""
    stream_cfg = lp.config_with(cfg, stream=True)
    recorder = _Recorder()
    client = lp.OpenAICompatibleLLM(stream_cfg, client=recorder)
    started = time.monotonic()
    try:
        text = client.complete(prompt_id=PROBE_PROMPT_ID, prompt=PROBE_PROMPT, tier=PROBE_TIER)
    except Exception as exc:  # noqa: BLE001 — a refusal is the measurement
        elapsed = int((time.monotonic() - started) * 1000)
        recorder.close()
        return {
            "supported": False,
            "measured": True,
            "latency_ms": elapsed,
            "reason": f"{type(exc).__name__}: {exc}"[:200],
            "request_stream_flag": True,
        }
    elapsed = int((time.monotonic() - started) * 1000)
    first_byte = _first_byte_ms(client)
    recorder.close()
    return {
        "supported": True,
        "measured": True,
        "latency_ms": elapsed,
        "first_byte_ms": first_byte,
        "answer_chars": len(text),
        "reason": "",
        "request_stream_flag": True,
    }


def _measure_structured(cfg: Any, *, key: KeySpec) -> dict[str, Any]:
    """Ask for a schema-constrained answer and name the mode that answered."""
    recorder = st.RecordingClient()
    client = lp.OpenAICompatibleLLM(cfg, client=recorder)
    started = time.monotonic()
    try:
        outcome = st.structured_call(
            client,
            dict(st.PROBE_SCHEMA),
            prompt=st.PROBE_PROMPT,
            tier=PROBE_TIER,
            mode=st.MODE_AUTO,
            name=st.PROBE_SCHEMA_NAME,
        )
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed
        elapsed = int((time.monotonic() - started) * 1000)
        recorder.close()
        return {
            "measured": True,
            "ok": False,
            "mode_used": "",
            "native_json_schema": None,
            "latency_ms": elapsed,
            "reason": f"{type(exc).__name__}: {exc}"[:200],
        }
    elapsed = int((time.monotonic() - started) * 1000)
    sent = recorder.last_body.get("response_format")
    recorder.close()
    native = next(
        (item for item in outcome.mode_trail if item.get("mode") == st.MODE_NATIVE), None
    )
    native_supported: bool | None = None
    if outcome.mode == st.MODE_NATIVE:
        native_supported = True
    elif native is not None:
        if native.get("reason") == st.REASON_UNSUPPORTED:
            native_supported = False
        elif native.get("status") == "answered":
            native_supported = True
    return {
        "measured": True,
        "ok": bool(outcome.ok),
        "mode_used": str(outcome.mode),
        "native_json_schema": native_supported,
        "requested_mode": st.MODE_AUTO,
        "response_format_sent": sent if isinstance(sent, Mapping) else None,
        "raw_answer": str(outcome.raw)[:300],
        "validator": str(outcome.validator),
        "validation_error": str(outcome.validation_error or "")[:300],
        "mode_trail": [dict(item) for item in outcome.mode_trail],
        "latency_ms": elapsed,
        "reason": "",
    }


# --------------------------------------------------------------------------- #
# the config document                                                          #
# --------------------------------------------------------------------------- #


def _catalog_note(catalog_id: str) -> dict[str, Any]:
    """The catalog's *claims* for one entry — with their source, never as a measurement."""
    if not catalog_id:
        return {}
    try:
        entry = catalog.catalog_entry(catalog_id)
    except KeyError:
        return {"id": catalog_id, "known": False}
    payload = entry.to_dict() if hasattr(entry, "to_dict") else {}
    payload["known"] = True
    return payload


def build_document(
    *,
    typesafe: KeySpec,
    llm: KeySpec,
    env: Mapping[str, str] | None = None,
    existing: Mapping[str, Any] | None = None,
    env_file: EnvFile | None = None,
) -> dict[str, Any]:
    """The config file the two keys produce — placeholders only, no secret.

    The LLM provider is written twice on purpose: as ``godsv.providers.entries.llm``
    (the registry) and as the **projection** in ``godsv.llm`` — the flat block
    :func:`skaldir.llm_provider.resolve_provider_config` actually reads. The
    TypeSafe/Jev provider lives in the sibling ``godsv.jev`` block, so a second
    credential never overwrites the first.
    """
    document: dict[str, Any] = dict(existing or {})
    godsv = document.get("godsv")
    godsv_block: dict[str, Any] = dict(godsv) if isinstance(godsv, Mapping) else {}
    document["godsv"] = godsv_block

    # 1. the general provider: the block the runtime resolves
    godsv_block["llm"] = llm.entry().projection()
    # 2. the decision layer's own endpoint — and *which* decision provider it is
    jev_block = typesafe.entry().projection()
    jev_block["stream"] = False
    jev_block[SYSTEM_ONE_KEY] = typesafe.system_one()
    godsv_block[JEV_BLOCK] = jev_block
    # 3. the named registry: both keys, the LLM one as the default
    jev_entry = typesafe.entry().config_block()
    jev_entry[SYSTEM_ONE_KEY] = typesafe.system_one()
    godsv_block[pc.REGISTRY_BLOCK] = {
        pc.REGISTRY_DEFAULT: llm.name,
        pc.REGISTRY_ENTRIES: {
            llm.name: llm.entry().config_block(),
            typesafe.name: jev_entry,
        },
    }
    # 4. the receipt: which two keys this file satisfies, and the measured host
    godsv_block[SETUP_SECTION] = {
        "contract_version": SCHEMA_VERSION,
        "completed_at": _now(),
        "mandatory_keys": list(MANDATORY_ROLES),
        "keys": {
            ROLE_TYPESAFE: {
                "entry": typesafe.name,
                "block": f"godsv.{JEV_BLOCK}",
                "base_url": typesafe.base_url,
                "model": typesafe.model,
                "api_key_env": typesafe.api_key_env,
                "catalog_id": typesafe.catalog_id,
                SYSTEM_ONE_KEY: typesafe.system_one(),
            },
            ROLE_LLM: {
                "entry": llm.name,
                "block": "godsv.llm",
                "base_url": llm.base_url,
                "model": llm.model,
                "api_key_env": llm.api_key_env,
                "catalog_id": llm.catalog_id,
            },
        },
        "catalog_claim": _catalog_note(llm.catalog_id),
    }
    if env_file is not None:
        # the receipt names *where* the credentials live and under which NAMES —
        # so the pair (config + .env) can be checked without opening either twice
        wanted = {name for name in (typesafe.api_key_env, llm.api_key_env) if name}
        entry = {
            "file": str(env_file.path),
            "mode": env_file.mode or "0600",
            "names": [name for name in env_file.names if name in wanted],
        }
        godsv_block[SETUP_SECTION]["credentials"] = entry
    return document


def write_document(
    document: Mapping[str, Any],
    path: str | Path | None = None,
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """Write atomically with a backup — and refuse anything secret-shaped.

    The store's own writer does the work, so ``skaldir setup`` inherits the exact
    guarantees ``skaldir provider add`` has: temp file + ``fsync`` + ``os.replace``,
    a timestamped backup of the file being replaced, and a secret scan of the
    ``godsv`` subtree before a byte is written.
    """
    target = pc.config_path(path)
    report = pc.write_document(dict(document), target, backup=backup)
    report["secret_scan"] = "clean"
    return report


def typesafe_provider_config(
    *, path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> Any:
    """Resolve ``godsv.jev`` into a :class:`ProviderConfig` — the Jev provider slot.

    Refuses a literal secret in the block (the store refuses it for ``godsv.llm``;
    the decision layer's block gets the same rule here), and refuses an absent
    block with a named :class:`SetupIncomplete` instead of an empty config.
    """
    target = pc.config_path(path)
    store = pc.load_store(target, env=env)
    godsv = store.document.get("godsv")
    block = dict(godsv).get(JEV_BLOCK) if isinstance(godsv, Mapping) else None
    if not isinstance(block, Mapping) or not block:
        raise SetupIncomplete(
            f"{ROLE_LABEL[ROLE_TYPESAFE]} is not configured in {target} "
            f"(godsv.{JEV_BLOCK}) — run `skaldir setup`"
        )
    keys = sorted(
        {str(key).strip().lower() for key in block}.intersection(lp.SECRET_FILE_KEYS)
    )
    if keys:
        names = ", ".join(f"'{key}:'" for key in keys)
        raise pc.ProviderSecretRefused(
            f"config file {target} carries a literal {names} value in godsv.{JEV_BLOCK} — "
            "a config file is not a secret store. Keep only 'api_key_env: <NAME>' and "
            "export the credential in the environment"
        )
    cfg = lp.resolve_provider_config(dict(block), env=dict(env or os.environ), config_path=target)
    if cfg.problems:
        raise SetupRefused(
            f"{ROLE_LABEL[ROLE_TYPESAFE]}: godsv.{JEV_BLOCK} refused: {'; '.join(cfg.problems)}"
        )
    return cfg


# --------------------------------------------------------------------------- #
# verification — one real decision, one real completion                        #
# --------------------------------------------------------------------------- #


def completion_through_config(
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    prompt: str = PROBE_PROMPT,
    tier: str = PROBE_TIER,
) -> dict[str, Any]:
    """One real completion through the **written** config (re-read, not remembered).

    Uses the provider store's own probe, so this measures the same path a normal
    run takes: the store's default entry, the real client, the real endpoint.
    """
    try:
        payload = pc.probe_provider(path=path, env=env, prompt=prompt, tier=tier)
    except Exception as exc:  # noqa: BLE001 — a failed verification is reported
        return {
            "ok": False,
            "problem": f"{type(exc).__name__}: {exc}"[:300],
            "status": getattr(exc, "status", None),
            "phase": "completion",
        }
    return {
        "ok": True,
        "name": payload.get("name"),
        "endpoint": payload.get("endpoint"),
        "model": payload.get("model"),
        "model_echo": payload.get("model_echo"),
        "finish_reason": payload.get("finish_reason"),
        "latency_ms": payload.get("latency_ms"),
        "credential": payload.get("credential"),
        "answer_preview": payload.get("answer_preview"),
        "usage": payload.get("usage"),
        "resolved_by": payload.get("resolved_by"),
    }


def decision_through_config(
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    kind: str = JEV_PROBE_KIND,
) -> dict[str, Any]:
    """One real Jev decision through the **written** ``godsv.jev`` provider.

    This is the decision layer's own path: the ``LLMSystemOneAdapter`` asks the
    configured client one question set, the substrate validates every answer
    against the versioned registry, and :func:`skaldir.jev.decide.decide` returns a
    decision — or an honest blocked outcome naming the provider error.
    """
    from .system_one import PROVIDER_LLM_ADAPTER
    from .system_one_flags import SystemOneFlags

    env_map = dict(os.environ if env is None else env)
    try:
        jev_cfg = typesafe_provider_config(path=path, env=env_map)
    except Exception as exc:  # noqa: BLE001 — reported as the verification result
        return {
            "ok": False,
            "problem": f"{type(exc).__name__}: {exc}"[:300],
            "phase": "jev_provider",
        }
    entry = pc.load_store(path, env=env_map).providers.get(ENTRY_NAME[ROLE_TYPESAFE])
    if entry is not None and entry.needs_credential(env_map):
        export = entry.api_key_env or lp.ENV_API_KEY
        return {
            "ok": False,
            "problem": (
                f"the TypeSafe/Jev provider has no credential in the environment: "
                f"export {export}=<your-key>"
            ),
            "phase": "jev_credential",
            "credential": "unset",
        }

    try:
        from .jev import contracts as C
        from .jev.decide import decide
        from .jev.decide import service_for as jev_service_for
        from .jev.selftest import case_request
    except Exception as exc:  # noqa: BLE001 — a build without the Jev layer says so
        return {
            "ok": False,
            "problem": (
                f"the Jev layer is not available in this build: "
                f"{type(exc).__name__}: {exc}"
            )[:300],
            "phase": "jev_import",
        }

    flags = SystemOneFlags(enabled=True, provider=PROVIDER_LLM_ADAPTER)
    started = time.monotonic()
    try:
        client = lp.provider_for(jev_cfg, env=env_map, config_path=pc.config_path(path))
        service = jev_service_for(flags, llm=client)
        typed: C.JevRequestBase = case_request(kind)
        decision = decide(typed, service=service, flags=flags)
    except Exception as exc:  # noqa: BLE001 — a provider failure is the measurement
        return {
            "ok": False,
            "problem": f"the decision call failed: {type(exc).__name__}: {exc}"[:300],
            "status": getattr(exc, "status", None),
            "phase": "jev_decision",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    latency_ms = int((time.monotonic() - started) * 1000)

    raw_payload = decision.model_dump() if hasattr(decision, "model_dump") else {}
    payload: dict[str, Any] = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    status = str(payload.get("status") or getattr(decision, "status", "") or "")
    answer_raw = payload.get("answer")
    answer: dict[str, Any] = dict(answer_raw) if isinstance(answer_raw, Mapping) else {}
    usage_raw = payload.get("usage")
    usage: dict[str, Any] = dict(usage_raw) if isinstance(usage_raw, Mapping) else {}
    route_raw = answer.get("route")
    route: dict[str, Any] = dict(route_raw) if isinstance(route_raw, Mapping) else {}
    chosen = answer.get("chosen_kind") or answer.get("choice") or ""
    if not chosen and route:
        chosen = route.get("candidate_id") or route.get("name") or ""
    provider_mode = str(
        payload.get("provider_mode")
        or usage.get("provider_mode")
        or getattr(decision, "provider_mode", "")
        or ""
    )
    # ``Status`` is ``ok`` | ``refused`` | ``blocked`` (MVP/03 §4.2): ``ok`` means an
    # advisory exists. ``blocked`` is the honest "no provider was available" — it is
    # never success, so it is never reported as one.
    return {
        "ok": status == "ok",
        "kind": kind,
        "status": status,
        "decision_id": str(payload.get("decision_id") or getattr(decision, "decision_id", "")),
        "authority": str(payload.get("authority") or ""),
        "certifies": bool(payload.get("certifies")),
        "provider_mode": provider_mode,
        "live_jev": bool(payload.get("live_jev")),
        "question_pack_id": str(payload.get("question_pack_id") or ""),
        "confidence": payload.get("confidence"),
        "caution_level": payload.get("caution_level"),
        "attempts": payload.get("attempts"),
        "usage": usage,
        "answer_kind": str(answer.get("kind") or ""),
        "answer_chosen": str(chosen or ""),
        "requires_approval": bool(answer.get("requires_approval")),
        "refusal_reason": str(payload.get("refusal_reason") or "")[:200],
        "reason": str(payload.get("refusal_reason") or payload.get("fallback") or "")[:200],
        "latency_ms": latency_ms or int(payload.get("latency_ms") or 0),
        "endpoint": lp.endpoint_for(jev_cfg.base_url),
        "model": jev_cfg.model,
        "raw_status": status,
    }


# --------------------------------------------------------------------------- #
# degraded-but-honest mode                                                     #
# --------------------------------------------------------------------------- #


def degraded_status(
    *,
    runtime: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """What works, what does not, and what would work after setup — no crash.

    This is the answer the CLI gives when the product is run before ``skaldir
    setup``. It never claims a capability: every entry names the two keys.
    """
    runtime_dir, _reason = _runtime(runtime)
    target = pc.config_path(config_path)
    env_map = dict(os.environ if env is None else env)
    roles: dict[str, Any] = {}
    try:
        roles = read_roles(path=target, env=env_map)["roles"]
    except Exception as exc:  # noqa: BLE001 — a report must report, not raise
        roles = {role: {"present": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
                 for role in MANDATORY_ROLES}

    usable: dict[str, bool] = {}
    for role in MANDATORY_ROLES:
        entry = (roles.get(role) or {}).get("entry") or {}
        usable[role] = bool(entry.get("ready"))

    return {
        "setup_complete": all(usable.values()),
        "missing_keys": [ROLE_LABEL[role] for role in MANDATORY_ROLES if not usable[role]],
        "needs_setup": not all(usable.values()),
        "runtime_dir": str(runtime_dir),
        "config_file": str(target),
        "credentials_file": credentials_report(
            read_env_file(env_file_path(config_path=target, env=env_map))
        ),
        "works_now": [
            "skaldir --version",
            "skaldir --help / the command tree",
            "skaldir contract dump --json",
            "skaldir provider list --json (the honest empty registry)",
            "skaldir setup --check (says exactly which key is missing)",
        ],
        "blocked_until_setup": [
            "skaldir agent run … (needs the LLM provider)",
            "skaldir jev decide … (needs the TypeSafe/Jev provider)",
            "any command with requires_credentials=true",
        ],
        "after_setup": [
            "skaldir setup --llm-provider deepseek --jev-provider deepseek "
            "(both keys, the endpoints pre-filled; the key is typed hidden)"
        ],
        "next_command": "skaldir setup --json",
        "next_steps": next_steps(),
        "roles": roles,
    }


# --------------------------------------------------------------------------- #
# the flow                                                                     #
# --------------------------------------------------------------------------- #


def _role_report(
    key: KeySpec | None,
    *,
    role: str,
    env: Mapping[str, str],
    measure_streaming: bool,
    timeout_s: float | None,
    missing: Sequence[str] | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if key is None:
        fields = list(missing) if missing is not None else missing_flags(role, env=env)
        spellings = key_flags_for(role, fields)
        variables = env_names_for_fields(role, fields)
        return {
            "role": role,
            "label": ROLE_LABEL[role],
            "ok": False,
            "present": False,
            "problem": (
                f"{ROLE_LABEL[role]} was not given — pass {spellings} or set {variables}"
            ),
            "missing_fields": fields,
        }
    try:
        measured = measure_key(key, env=env, timeout_s=timeout_s,
                               measure_streaming=measure_streaming)
    except SetupRefused as exc:
        return {
            "role": role,
            "label": ROLE_LABEL[role],
            "ok": False,
            "present": True,
            "spec": key.redacted(env),
            "problem": str(exc)[:300],
            "refused": True,
            "key_source": dict(source or {}),
        }
    measured["present"] = True
    measured["spec"] = key.redacted(env)
    measured["key_source"] = dict(source or {})
    return measured


def _with_key_name(key: KeySpec, name: str) -> KeySpec:
    """The same key under a named credential variable (a frozen KeySpec, rebuilt)."""
    return key_spec(
        key.role,
        base_url=key.base_url,
        model=key.model,
        api_key_env=name,
        catalog_id=key.catalog_id,
        tiers=dict(key.tiers),
        timeout_s=key.timeout_s,
    )


#: The variable a key is written under when neither the config nor the provider
#: table names one — so a pasted credential always has a line to live on.
DEFAULT_KEY_ENV: dict[str, str] = {ROLE_TYPESAFE: "SKALDIR_JEV_API_KEY", ROLE_LLM: lp.ENV_API_KEY}


def credential_name(key: KeySpec | None, role: str) -> str:
    if key is not None and key.api_key_env:
        return key.api_key_env
    return DEFAULT_KEY_ENV[role]


def run_setup(
    *,
    typesafe: KeySpec | None = None,
    llm: KeySpec | None = None,
    path: str | Path | None = None,
    runtime: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check_only: bool = False,
    write: bool = True,
    backup: bool = True,
    timeout_s: float | None = None,
    measure_streaming: bool = True,
    env_file: str | Path | None = None,
    write_env: bool = True,
    key_values: Mapping[str, str] | None = None,
    key_sources: Mapping[str, str | Mapping[str, Any]] | None = None,
    missing_by_role: Mapping[str, Sequence[str]] | None = None,
) -> tuple[dict[str, Any], int]:
    """Run the whole flow. Returns ``(report, exit_code)`` — never raises for a state.

    ``check_only`` re-validates and **writes nothing**: it is the ``--check``
    verb. Without it the flow asks, validates, writes the credential line into the
    ``.env`` (0600), writes the config atomically with a backup, re-reads it and
    verifies with one real Jev decision and one real completion.

    ``key_values`` carries a credential the *caller* collected (a masked prompt, a
    piped line). Its value is written to the credential file and never appears in
    the report; ``key_sources`` is the label of where each one came from. The
    credential file is written **before** the validation call and the config file
    **after** it: a key the user just pasted is not thrown away because an endpoint
    is down, while a config the flow could not validate is still never written.
    """
    env_map = dict(os.environ if env is None else env)
    target = pc.config_path(path)
    keys: dict[str, KeySpec | None] = {ROLE_TYPESAFE: typesafe, ROLE_LLM: llm}
    missing_map = dict(missing_by_role or {})
    source_labels = dict(key_sources or {})
    values: dict[str, str] = {}
    for role, value in dict(key_values or {}).items():
        if role not in MANDATORY_ROLES:
            continue
        if str(value or "").strip():
            values[role] = refuse_key_value(str(value), name=f"{ROLE_LABEL[role]} key")
    credential_path = env_file_path(
        config_path=target, explicit=str(env_file or ""), env=env_map
    )
    seen = read_env_file(credential_path)
    report: dict[str, Any] = {
        "action": "check" if check_only else "setup",
        "schema_version": SCHEMA_VERSION,
        "config_file": str(target),
        "env_file": str(credential_path),
        "mandatory_keys": list(MANDATORY_ROLES),
        "checked_at": _now(),
    }

    if check_only:
        report["state"] = detect_state(
            runtime=runtime, config_path=target, env=env_map
        )
        # a check must be able to read the credential the setup wrote, so the file
        # is part of the environment it validates in — and it still writes nothing
        check_env = key_env_for(env_map, seen)
        roles = {
            role: _role_report_from_config(role, path=target, env=check_env)
            for role in MANDATORY_ROLES
        }
        report["roles"] = roles
        report["key_sources"] = {
            role: {
                "name": _entry_key_name(roles.get(role)),
                **key_available(
                    role, _entry_key_name(roles.get(role)), env=env_map, env_file=seen
                ),
            }
            for role in MANDATORY_ROLES
        }
        report["credentials_file"] = credentials_report(seen)
        code = _check_exit(roles)
        report["ok"] = code == EXIT_OK
        report["verdict"] = CHECK_EXIT_CODES.get(code, "")
        report["exit_meaning"] = CHECK_EXIT_CODES
        report["next_steps"] = next_steps(env_file=seen)
        return report, code

    # -- 0. the credential file: read it, and name every key that lives in it -- #
    for role in MANDATORY_ROLES:
        key = keys[role]
        # a key with a value needs a variable name to be written under; a key with
        # neither (a local endpoint that wants no credential) keeps its empty name
        if key is not None and not key.api_key_env and values.get(role):
            keys[role] = _with_key_name(key, credential_name(key, role))
    validation_env = key_env_for(env_map, seen)
    found: dict[str, Any] = {}
    for role in MANDATORY_ROLES:
        name = credential_name(keys[role], role)
        where = key_available(role, name, env=env_map, env_file=seen)
        handed = source_labels.get(role)
        if isinstance(handed, Mapping):
            # the caller says where *it* got the value (a hidden prompt, stdin, the
            # environment) — that is the provenance a reader wants, not "some value"
            where = {"available": True, "name": name, **dict(handed)}
        elif values.get(role):
            where = {
                "available": True,
                "source": str(handed or "prompt (hidden)"),
                "kind": "typed",
                "name": name,
            }
        found[role] = where
        value = values.get(role) or validation_env.get(name, "")
        if str(value or "").strip():
            validation_env[name] = str(value)
    report["key_sources"] = {
        role: {"name": credential_name(keys[role], role), **dict(found[role])}
        for role in MANDATORY_ROLES
    }

    # -- 1. the detected state ---------------------------------------------- #
    report["state"] = detect_state(runtime=runtime, config_path=target, env=env_map)

    # -- 2. the credential file, written before any call -------------------- #
    # the plan follows the order the values arrived in (the ask order), so the file
    # reads the way the user answered
    plan: dict[str, str] = {}
    for role in values:
        name = credential_name(keys[role], role)
        if name in validation_env and seen.value(name) != values[role]:
            plan[name] = validation_env[name]
    if plan and not write_env:
        report["credentials_file"] = credentials_report(
            seen,
            written=False,
            reason="--no-env-write: the key stays in the environment only",
        )
    elif plan:
        try:
            written_env = write_env_file(credential_path, plan)
        except SetupRefused as exc:
            report["ok"] = False
            report["wrote"] = False
            report["problem"] = str(exc)[:300]
            report["verdict"] = "refused: the credential was not written"
            return report, EXIT_BLOCKED
        except SetupError as exc:
            report["ok"] = False
            report["wrote"] = False
            report["problem"] = str(exc)[:300]
            report["verdict"] = "the credential file could not be written"
            return report, EXIT_INTERNAL
        report["env_write"] = written_env
        seen = read_env_file(credential_path)
        report["credentials_file"] = credentials_report(seen, written=True)
    else:
        report["credentials_file"] = credentials_report(seen, written=False)

    # -- 3. validate both keys live ----------------------------------------- #
    roles = {
        role: _role_report(
            keys[role],
            role=role,
            env=validation_env,
            measure_streaming=measure_streaming,
            timeout_s=timeout_s,
            missing=missing_map.get(role),
            source=report["key_sources"].get(role),
        )
        for role in MANDATORY_ROLES
    }
    report["roles"] = roles
    if not all(bool(item.get("ok")) for item in roles.values()):
        report["ok"] = False
        report["wrote"] = False
        report["verdict"] = "not written — a mandatory key did not validate"
        report["next_command"] = _next_command(keys, env_map)
        report["next_steps"] = next_steps(env_file=seen)
        return report, _setup_exit(roles)

    if not write:
        # a dry run: both keys answered, nothing was written
        report["ok"] = True
        report["wrote"] = False
        report["verdict"] = "both keys validated; nothing written (--no-write)"
        report["next_steps"] = next_steps(env_file=seen)
        return report, EXIT_OK

    # -- 4. write the config atomically, with a backup ---------------------- #
    final_typesafe = keys[ROLE_TYPESAFE]
    final_llm = keys[ROLE_LLM]
    assert final_typesafe is not None and final_llm is not None  # both validated above
    document = build_document(
        typesafe=final_typesafe, llm=final_llm, env=env_map, env_file=seen
    )
    try:
        written = write_document(document, target, backup=backup)
    except pc.ProviderSecretRefused as exc:
        report["ok"] = False
        report["wrote"] = False
        report["problem"] = str(exc)[:300]
        report["verdict"] = "refused: the config would have stored a secret"
        return report, EXIT_BLOCKED
    except pc.ProviderStoreError as exc:
        report["ok"] = False
        report["wrote"] = False
        report["problem"] = str(exc)[:300]
        report["verdict"] = "the config could not be written"
        return report, EXIT_INTERNAL
    report["write"] = written

    # -- 5. re-read, then verify with two real calls ------------------------ #
    # the re-read and both verification calls run in the *same* environment the
    # validation did (process + credential file), so a key that lives only in the
    # .env is proven exactly as a key exported into the shell is
    report["reread"] = read_roles(path=target, env=validation_env)
    report["verify"] = {
        "llm_completion": completion_through_config(path=target, env=validation_env),
        "jev_decision": decision_through_config(path=target, env=validation_env),
    }
    verified = all(bool(item.get("ok")) for item in report["verify"].values())
    report["ok"] = verified
    report["wrote"] = True
    report["verdict"] = (
        "configured and verified: one real decision through Jev, one real completion"
        if verified
        else "the config was written, but verification failed — see `verify`"
    )
    report["next_steps"] = next_steps(env_file=seen)
    if not verified:
        report["next_command"] = "skaldir setup --check --json"
        return report, EXIT_VERIFICATION_FAILED
    return report, EXIT_OK


def _entry_key_name(role_report: Mapping[str, Any] | None) -> str:
    """The credential variable a role's report names (``""`` when it names none)."""
    spec = (role_report or {}).get("spec")
    if not isinstance(spec, Mapping):
        return ""
    return str(spec.get("api_key_env") or "")


def _role_report_from_config(
    role: str, *, path: str | Path | None, env: Mapping[str, str]
) -> dict[str, Any]:
    """``--check``: re-validate a key **out of the file**, not out of the flags.

    ``--check`` re-runs the *validation* and nothing else: one real call per
    measurement through the key as the config file states it. Nothing is written,
    no backup is taken, and a refusal (no credential, an unreachable endpoint) is
    the check's *result* — it is reported, never raised.
    """
    target = pc.config_path(path)
    try:
        store = pc.load_store(target, env=env)
    except Exception as exc:  # noqa: BLE001 — reported as the check result
        return {
            "role": role,
            "label": ROLE_LABEL[role],
            "ok": False,
            "present": False,
            "problem": f"cannot read {target}: {type(exc).__name__}: {exc}"[:300],
        }
    name = ENTRY_NAME[role]
    entry = store.providers.get(name)
    if entry is None and role == ROLE_TYPESAFE:
        # a hand-written install may only carry godsv.jev; accept it as the slot
        try:
            cfg = typesafe_provider_config(path=target, env=env)
        except SetupIncomplete:
            entry = None
        except pc.ProviderSecretRefused as exc:
            return {
                "role": role,
                "label": ROLE_LABEL[role],
                "ok": False,
                "present": True,
                "problem": str(exc)[:300],
                "refused": True,
            }
        except Exception as exc:  # noqa: BLE001 — reported, not raised
            return {
                "role": role,
                "label": ROLE_LABEL[role],
                "ok": False,
                "present": True,
                "problem": str(exc)[:300],
            }
        else:
            entry = pc.ProviderEntry(
                name=name,
                kind=cfg.provider,
                base_url=cfg.base_url,
                model=cfg.model,
                api_key_env=cfg.api_key_env,
                tiers=dict(cfg.tier_models),
            )
    if entry is None:
        return {
            "role": role,
            "label": ROLE_LABEL[role],
            "ok": False,
            "present": False,
            "problem": f"{ROLE_LABEL[role]} is not configured in {target} — run `skaldir setup`",
        }
    spec = KeySpec(
        role=role,
        base_url=entry.base_url,
        model=entry.model,
        api_key_env=entry.api_key_env,
        tiers=dict(entry.tiers),
        timeout_s=entry.timeout_s,
    )
    try:
        measured = measure_key(spec, env=env, measure_streaming=True)
    except SetupRefused as exc:
        return {
            "role": role,
            "label": ROLE_LABEL[role],
            "ok": False,
            "present": True,
            "spec": spec.redacted(env),
            "refused": True,
            "problem": str(exc)[:300],
            "measure": {"measured": False, "reason": "the key was refused before any request"},
        }
    return {
        "role": role,
        "label": ROLE_LABEL[role],
        "ok": bool(measured.get("ok")),
        "present": True,
        "spec": spec.redacted(env),
        "entry": entry.redacted(env),
        "measure": {
            "model_echo": measured.get("model_echo"),
            "latency_ms": measured.get("latency_ms"),
            "streaming": measured.get("streaming"),
            "structured": measured.get("structured"),
            "credential": measured.get("credential"),
        },
        "status": measured.get("status"),
        "phase": measured.get("phase"),
        "problem": "" if measured.get("ok") else str(measured.get("problem") or "")[:300],
    }


def _needs_credentials(role_report: Mapping[str, Any]) -> bool:
    problem = str(role_report.get("problem") or "")
    return bool(role_report.get("refused")) and (
        "no credential" in problem or "credential is unset" in problem
    )


def _is_timeout(role_report: Mapping[str, Any]) -> bool:
    """No answer at all — a timeout or a transport failure that never got a status.

    The contract has no separate "unreachable" code; both mean the same thing to a
    caller: the endpoint produced nothing within the budget. An HTTP refusal
    (``status >= 400``) is *not* this bucket — that is a real answer and maps to
    ``EXIT_VERIFICATION_FAILED``.
    """
    if role_report.get("refused"):
        return False
    status = role_report.get("status")
    if status == 408:
        return True
    problem = str(role_report.get("problem") or "")
    if "Timeout" in problem or "timeout" in problem:
        return True
    return status is None and str(role_report.get("phase") or "") == "completion"


def _check_exit(roles: Mapping[str, Mapping[str, Any]]) -> int:
    """``--check``: 0 only when both keys answered a real call."""
    present = [bool(item.get("present")) for item in roles.values()]
    ok = [bool(item.get("ok")) for item in roles.values()]
    if all(ok):
        return EXIT_OK
    if not all(present):
        return EXIT_BLOCKED
    if any(_needs_credentials(item) for item in roles.values()):
        return EXIT_NEEDS_CREDENTIALS
    if any(_is_timeout(item) for item in roles.values()):
        return EXIT_TIMEOUT
    return EXIT_VERIFICATION_FAILED


def _setup_exit(roles: Mapping[str, Mapping[str, Any]]) -> int:
    """Setup: a missing key is ``BLOCKED``; a present-but-broken one is ``2``/``4``/``8``."""
    if any(not item.get("present") for item in roles.values()):
        return EXIT_BLOCKED
    if any(_needs_credentials(item) for item in roles.values()):
        return EXIT_NEEDS_CREDENTIALS
    if any(_is_timeout(item) for item in roles.values()):
        return EXIT_TIMEOUT
    return EXIT_VERIFICATION_FAILED


def check_setup(
    *,
    path: str | Path | None = None,
    runtime: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    measure_streaming: bool = True,
) -> tuple[dict[str, Any], int]:
    """``skaldir setup --check`` — re-validate both keys and nothing else."""
    return run_setup(
        path=path,
        runtime=runtime,
        env=env,
        env_file=env_file,
        check_only=True,
        write=False,
        measure_streaming=measure_streaming,
    )


def _next_command(keys: Mapping[str, KeySpec | None], env: Mapping[str, str]) -> str:
    """The exact command that finishes setup — built from what worked, not a template."""
    parts = ["skaldir setup"]
    for role in MANDATORY_ROLES:
        key = keys.get(role)
        prefix = "--jev" if role == ROLE_TYPESAFE else "--llm"
        if key is None:
            parts += [
                f"{prefix}-base-url <URL>",
                f"{prefix}-model <MODEL>",
                f"{prefix}-api-key-env <NAME>",
            ]
            continue
        if key.base_url:
            parts.append(f"{prefix}-base-url {key.base_url}")
        if key.model:
            parts.append(f"{prefix}-model {key.model}")
        if key.api_key_env:
            parts.append(f"{prefix}-api-key-env {key.api_key_env}")
    parts.append("--json")
    return " ".join(parts)


def catalog_listing() -> dict[str, Any]:
    """The LLM providers a first run may pick from — the catalog, verbatim."""
    return catalog.catalog_report()


def dump(report: Mapping[str, Any]) -> str:
    """One payload as the one JSON document (same shape the CLI prints)."""
    return json.dumps(dict(report), ensure_ascii=False, indent=2, sort_keys=True, default=str)


def env_names_for(role: str) -> Sequence[str]:
    """Every headless variable one role reads, in order (for help text)."""
    if role not in MANDATORY_ROLES:
        raise SetupRefused(f"unknown role {role!r}")
    out: list[str] = []
    for names in ROLE_ENV[role].values():
        for name in names:
            if name not in out:
                out.append(name)
    return tuple(out)


def scratch_hint() -> str:
    """The one-liner a headless caller can copy (used by the CLI help)."""
    return (
        "SKALDIR_SETUP_JEV_BASE_URL=… SKALDIR_SETUP_JEV_MODEL=… "
        "SKALDIR_SETUP_JEV_API_KEY_ENV=… SKALDIR_SETUP_LLM_BASE_URL=… "
        "SKALDIR_SETUP_LLM_MODEL=… SKALDIR_SETUP_LLM_API_KEY_ENV=… skaldir setup"
    )


def compat_getenv(name: str, default: str = "") -> str:
    """``SKALDIR_``/``GODSV_`` lookup for callers that only have a token."""
    return str(getenv(name, default) or default)
