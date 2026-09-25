"""God's Voice model roles — per-role provider/model for the voice pipeline.

Reuses Hermes' provider / auth / model infrastructure end to end. This module
stores only *names* (provider + model); credentials, live model lists and
runtime resolution stay where they already live (Hermes config + provider
model cache). The plugin hands a role's provider/model to the host-owned
side-LLM (``ctx.llm``) through the capability-gated overrides
(``plugins.entries.godsv.llm.allow_*_override``) — if the gate is closed, the
call simply runs without an override (fail-open, traced).

Roles (subset relationships are fine — several roles may share one model):

    main          the watched main agent (mirrors Hermes' default; reference)
    watcher       cheap/fast triage — anomaly detection runs here
    investigator  research/redirect building
    judge         verification (guard LLM channel, claim review)
    fallback      used when a role's primary provider call fails
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

ROLES = ("main", "watcher", "investigator", "judge", "fallback")

# call-site → role. Prompt ids are godsv prompt-registry ids; tiers are the
# llm.py tiers (small/mid/main).
_PROMPT_ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("triage", "watcher"),
    ("investigate", "investigator"),
    ("research", "investigator"),
    ("voice_redirect", "investigator"),
    ("judge", "judge"),
    ("verify", "judge"),
)
_TIER_ROLE = {"small": "watcher", "mid": "investigator", "main": "main"}

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")
_REASONING_LEVELS = ("low", "medium", "high")


def roles_file() -> Path:
    from .env import host_getenv  # local: this module stays importable on its own

    home = host_getenv("HERMES_HOME") or os.path.expanduser("~/.hermes")
    try:
        from .registry import runtime_dir_for

        return runtime_dir_for(home) / "models.yaml"
    except Exception:  # noqa: BLE001 — standalone use has no registry
        return Path(home) / "skaldir" / "models.yaml"


# --------------------------------------------------------------------------- #
# storage (tiny YAML subset — no dependency on pyyaml for a name/value file)  #
# --------------------------------------------------------------------------- #


def _parse_yaml_min(text: str) -> dict[str, dict[str, str]]:
    """Parse the exact shape we write: ``roles:`` → 2-space role → 4-space keys."""
    roles: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if indent == 0:
            current = None  # top-level container (e.g. "roles:")
            continue
        if indent <= 2 and not value:
            current = key
            roles.setdefault(current, {})
            continue
        if indent >= 4 and current and value:
            roles[current][key] = value
    return roles


def load_roles() -> dict[str, dict[str, str]]:
    """Roles as configured; missing file → {} (callers fall back to Hermes)."""
    path = roles_file()
    try:
        return _parse_yaml_min(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_role(
    role: str, *, provider: str, model: str, reasoning: str | None = None
) -> dict[str, dict[str, str]]:
    """Validate + persist one role. Raises ValueError on invalid input."""
    if role not in ROLES:
        raise ValueError(f"unknown role: {role!r} (expected one of {ROLES})")
    provider = (provider or "").strip()
    model = (model or "").strip()
    if not _NAME_RE.fullmatch(provider):
        raise ValueError("provider must be a plain provider slug")
    if not _NAME_RE.fullmatch(model):
        raise ValueError("model must be a plain model name")
    reasoning = (reasoning or "").strip() or None
    if reasoning is not None and reasoning not in _REASONING_LEVELS:
        raise ValueError(f"reasoning must be one of {_REASONING_LEVELS}")
    roles = load_roles()
    entry = {"provider": provider, "model": model}
    if reasoning:
        entry["reasoning"] = reasoning
    roles[role] = entry
    _write_roles(roles)
    return roles


def clear_role(role: str) -> dict[str, dict[str, str]]:
    roles = load_roles()
    roles.pop(role, None)
    _write_roles(roles)
    return roles


def _write_roles(roles: dict[str, dict[str, str]]) -> None:
    path = roles_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# God's Voice model roles — provider/model names only (no secrets).",
        "# Resolution, credentials and live model lists stay with Hermes.",
        "roles:",
    ]
    for role in ROLES:
        entry = roles.get(role)
        if not entry:
            continue
        lines.append(f"  {role}:")
        lines.append(f"    provider: {entry['provider']}")
        lines.append(f"    model: {entry['model']}")
        if entry.get("reasoning"):
            lines.append(f"    reasoning: {entry['reasoning']}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# resolution                                                                  #
# --------------------------------------------------------------------------- #


def role_for_call(prompt_id: str, tier: str) -> str:
    """Which role serves this call (prompt-id hints win over the tier)."""
    pid = (prompt_id or "").lower()
    for hint, role in _PROMPT_ROLE_HINTS:
        if hint in pid:
            return role
    return _TIER_ROLE.get(tier, "main")


def resolve_for_call(prompt_id: str, tier: str) -> tuple[str, str, str | None] | None:
    """(provider, model, reasoning) override for a call, or None (Hermes default)."""
    roles = load_roles()
    role = role_for_call(prompt_id, tier)
    entry = roles.get(role)
    if role == "main" or not entry:
        return None  # role unset / main mirrors Hermes — no override requested
    return entry["provider"], entry["model"], entry.get("reasoning")


def roles_payload() -> dict[str, Any]:
    """Read-only view for the panel/CLI (names only, never secrets)."""
    return {
        "roles": load_roles(),
        "roles_file": str(roles_file()),
        "known_roles": list(ROLES),
        "reasoning_levels": list(_REASONING_LEVELS),
    }


# --------------------------------------------------------------------------- #
# Hermes-side setup: enable the (non-default) override capability             #
# --------------------------------------------------------------------------- #


def overrides_enabled(hermes_binary: str | None = None) -> bool:
    """True when Hermes allows the godsv plugin to request provider/model."""
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore[import-not-found]
    except Exception:
        return False
    cfg: Any = None
    try:
        cfg = (load_config_readonly() or {}).get("plugins")
    except Exception:
        return False
    for key in ("entries", "godsv", "llm"):
        cfg = cfg.get(key) if isinstance(cfg, dict) else None
    if not isinstance(cfg, dict):
        return False
    return bool(cfg.get("allow_model_override")) and bool(cfg.get("allow_provider_override"))


def ensure_overrides_enabled(hermes_binary: str | None = None, *, timeout: float = 30.0) -> dict:
    """Apply the two capability flags through the safe config writer (best effort)."""
    binary = hermes_binary or _hermes_binary()
    result: dict[str, Any] = {"hermes": binary or "", "applied": [], "ok": False}
    if not binary:
        return result
    for flag in ("allow_model_override", "allow_provider_override"):
        try:
            proc = subprocess.run(  # nosec B603 — fixed argv, no shell
                [binary, "config", "set", f"plugins.entries.godsv.llm.{flag}", "true"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode == 0:
                result["applied"].append(flag)
        except (OSError, subprocess.SubprocessError):
            continue
    result["ok"] = len(result["applied"]) == 2
    result["timestamp"] = time.time()
    return result


def _hermes_binary() -> str | None:
    import shutil

    found = shutil.which("hermes")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "hermes",):
        if candidate.exists():
            return str(candidate)
    return None
