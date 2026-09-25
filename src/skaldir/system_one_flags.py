"""System-One feature flags — config-file driven, safe by default (§98/§22).

The flags of §98/JEV-20 (plus the §22 additions) live in the state directory's
``settings.yaml`` under a ``system_one:`` section — the same config-access
pattern the repo already uses for voice strictness
(:func:`skaldir.pause.load_strictness`).

**``goal_completion`` vs ``completion_precheck`` — which one do I set?**
They gate two different things and are deliberately independent:

* ``completion_precheck`` gates the *existing* §91/JEV-13 advisory reading
  (:func:`skaldir.completion.completion_precheck`): structural signals about
  missing/stale completion evidence, which the deterministic
  :class:`~skaldir.completion.GateRunner` already consumes. It needs no provider.
* ``goal_completion`` gates the *new* §9 ledger preflight
  (:func:`skaldir.completion.completion_preflight`): the per-criterion ledger, the
  hard structural checks, the semantic question packs and the sanity pass. It
  spends System-One calls, and it is still **advisory** — the gate certifies.

Set ``completion_precheck`` when you only want the cheap structural reading;
set ``goal_completion`` when you want the semantic preflight; setting both is
the intended full-completion configuration. Neither ever sets completion.

Deliberate: these are **behavioural** settings, so they are never read from
environment variables (the platform rule forbids ``HERMES_*`` env vars for
non-secret configuration). Secrets stay in the Treasury behind handles.

Defaults are all-off; an unreadable, missing or malformed section degrades to
those defaults, and an unknown provider falls back to
:class:`~skaldir.system_one.DisabledSystemOneEvaluator` — deactivation always
returns Voice/Computer Use to the existing path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .system_one import (
    PROVIDER_DISABLED,
    PROVIDER_LLM_ADAPTER,
    PROVIDER_TYPESAFE,
    DisabledSystemOneEvaluator,
    SystemOneContractError,
)

SETTINGS_NAME = "settings.yaml"
SECTION = "system_one"

#: The §98 flags plus the §22 additions, all with safe (off) defaults.
FLAG_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "provider": PROVIDER_DISABLED,
    "shadow_mode": False,
    "voice_triage": False,
    "goal_completion": False,
    "computer_use": False,
    "recovery": False,
    "llm_fallback": False,
    "completion_precheck": False,
}

KNOWN_PROVIDERS = (PROVIDER_DISABLED, PROVIDER_LLM_ADAPTER, PROVIDER_TYPESAFE)


def settings_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / SETTINGS_NAME


@dataclass(frozen=True)
class SystemOneFlags:
    """The rollout flags — one object, no hidden second source of truth.

    ``goal_completion`` = the §9 ledger preflight; ``completion_precheck`` = the
    §91 structural advisory reading; ``recovery`` = the recovery reading;
    ``llm_fallback`` = the authorized LLM System-One fallback when the live
    provider is unavailable (§17). See the module docstring for the two
    completion flags.
    """

    enabled: bool = False
    provider: str = PROVIDER_DISABLED
    shadow_mode: bool = False
    voice_triage: bool = False
    goal_completion: bool = False
    computer_use: bool = False
    recovery: bool = False
    llm_fallback: bool = False
    completion_precheck: bool = False

    @classmethod
    def disabled(cls) -> SystemOneFlags:
        return cls(**FLAG_DEFAULTS)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> SystemOneFlags:
        """Read a ``system_one:`` section; unknown keys are contract errors."""
        if not isinstance(data, Mapping):
            return cls.disabled()
        unknown = sorted(set(data) - set(FLAG_DEFAULTS))
        if unknown:
            raise SystemOneContractError(f"unknown system_one setting(s): {unknown}")
        values = dict(FLAG_DEFAULTS)
        for key, value in data.items():
            if key == "provider":
                values[key] = str(value)
            else:
                values[key] = bool(value)
        provider = str(values["provider"])
        if provider not in KNOWN_PROVIDERS:
            # safe fallback: an unknown provider must not enable anything
            values["provider"] = PROVIDER_DISABLED
        return cls(**values)

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "shadow_mode": self.shadow_mode,
            "voice_triage": self.voice_triage,
            "goal_completion": self.goal_completion,
            "computer_use": self.computer_use,
            "recovery": self.recovery,
            "llm_fallback": self.llm_fallback,
            "completion_precheck": self.completion_precheck,
        }

    def as_dotted(self) -> dict:
        """The §98 spelling: ``system_one.enabled`` …"""
        return {f"{SECTION}.{key}": value for key, value in self.as_dict().items()}

    def with_flag(self, name: str, value: Any) -> SystemOneFlags:
        if name not in FLAG_DEFAULTS:
            raise SystemOneContractError(f"unknown system_one flag: {name!r}")
        return replace(self, **{name: value})


def load_system_one_flags(state_dir: str | Path) -> SystemOneFlags:
    """Current flags, read live from ``<state_dir>/settings.yaml``.

    Nothing is read from the environment; any failure returns the safe
    all-off defaults instead of guessing.
    """
    import yaml

    try:
        data = yaml.safe_load(settings_path(state_dir).read_text(encoding="utf-8")) or {}
    except Exception:
        return SystemOneFlags.disabled()
    if not isinstance(data, Mapping):
        return SystemOneFlags.disabled()
    section = data.get(SECTION)
    if section is None:
        return SystemOneFlags.disabled()
    if not isinstance(section, Mapping):
        return SystemOneFlags.disabled()
    try:
        return SystemOneFlags.from_mapping(section)
    except SystemOneContractError:
        # a malformed section must never enable System One partially
        return SystemOneFlags.disabled()


def save_system_one_flags(state_dir: str | Path, flags: SystemOneFlags) -> SystemOneFlags:
    """Persist the flags, preserving every unrelated settings section."""
    import yaml

    path = settings_path(state_dir)
    data: dict = {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, Mapping):
            data = dict(loaded)
    except Exception:
        data = {}
    data[SECTION] = flags.as_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=True), encoding="utf-8")
    return flags


# --------------------------------------------------------------------------- #
# evaluator construction — the one place flags become behaviour                #
# --------------------------------------------------------------------------- #


def build_evaluator(
    flags: SystemOneFlags,
    *,
    llm=None,
    credential_resolver=None,
    transport=None,
    model: str = "",
    emit=None,
):
    """Build the evaluator the flags ask for, degrading to disabled.

    Falls back to :class:`DisabledSystemOneEvaluator` when: the master flag is
    off, the provider is ``disabled``/unknown, or the provider's dependency is
    missing (no LLM client, no credential resolver). Deactivation must always
    land on the existing path (§98).
    """
    if not flags.enabled or flags.provider == PROVIDER_DISABLED:
        return DisabledSystemOneEvaluator()
    if flags.provider == PROVIDER_LLM_ADAPTER:
        if llm is None:
            return DisabledSystemOneEvaluator()
        from .system_one_adapters import LLMSystemOneAdapter

        return LLMSystemOneAdapter(llm, model=model, emit=emit)
    if flags.provider == PROVIDER_TYPESAFE:
        from .system_one_adapters import TypeSafeJevEvaluator

        return TypeSafeJevEvaluator(
            credential_resolver=credential_resolver,
            transport=transport,
            model=model or "jev-latest",
            emit=emit,
        )
    return DisabledSystemOneEvaluator()  # pragma: no cover — from_mapping folds unknown


__all__ = [
    "FLAG_DEFAULTS",
    "KNOWN_PROVIDERS",
    "SECTION",
    "SystemOneFlags",
    "build_evaluator",
    "load_system_one_flags",
    "save_system_one_flags",
    "settings_path",
]
