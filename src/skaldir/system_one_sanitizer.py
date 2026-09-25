"""State sanitizer — what the System-One provider is allowed to see (§40/JEV-05).

The pipeline is fixed and testable (§40):

    Runtime Snapshot → Scope Filter → Secret Redaction → Project Isolation
                     → State Minimizer → Provider Adapter

Forbidden in the external state unless explicitly required and authorized:
API keys, session tokens, foreign private projects, whole terminal/chat
histories without relevance, raw screenshots the chosen API path does not
support, and unnecessary personal data.

Redaction reuses the repo's single secret-scanning contract
(:mod:`skaldir.secretscan`) instead of inventing a second one (§27); project
isolation uses the caller-supplied project id, so the sanitizer never derives a
competing notion of "current project".
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .secretscan import redact as redact_text
from .system_one import SystemOneContractError, SystemOneState

REDACTED = "[REDACTED:{kind}]"

#: A key whose *name* looks like a credential is redacted wholesale — the value
#: is dropped, never merely truncated.
SECRET_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|apikey|token|secret|password|passwd|authorization|auth|bearer|"
    r"credential|private[_-]?key|session[_-]?token|cookie|client[_-]?secret)"
)

#: Keys that carry per-project state; only the current project survives.
FOREIGN_PROJECT_KEYS = frozenset({"projects", "project_states", "other_projects"})

#: The smallest useful state for the productive question packs. Everything a
#: caller wants to send has to be named here — the default is denial by scope.
DEFAULT_ALLOWED_KEYS = frozenset(
    {
        "goal",
        "goal_revision",
        "non_goals",
        "run_id",
        "session_id",
        "project",
        "project_id",
        "step",
        "signals",
        "errors",
        "recent_events",
        "recent_actions",
        "terminal_history",
        "chat_history",
        "files",
        "commands",
        "plan",
        "progress",
        "loop_count",
        "drift",
        "completion",
        "research",
        "recovery",
        "observation",
        "candidate",
        "window",
        "state",
        "env",
        "headers",
        "note",
    }
)

DEFAULT_MAX_LIST_ITEMS = 20
DEFAULT_MAX_STRING_CHARS = 800
DEFAULT_MAX_DEPTH = 5


@dataclass(frozen=True)
class SanitizerPolicy:
    """What the sanitizer keeps, and how small the result must become."""

    allowed_keys: frozenset[str] = DEFAULT_ALLOWED_KEYS
    project_id: str = ""
    max_list_items: int = DEFAULT_MAX_LIST_ITEMS
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS
    max_depth: int = DEFAULT_MAX_DEPTH


# --------------------------------------------------------------------------- #
# stages (each is individually testable)                                       #
# --------------------------------------------------------------------------- #


def runtime_snapshot(snapshot: Mapping[str, Any]) -> dict:
    """Stage 1 — take a defensive copy of the runtime snapshot."""
    if not isinstance(snapshot, Mapping):
        raise SystemOneContractError("runtime snapshot must be a mapping")
    return dict(snapshot)


def scope_filter(
    snapshot: Mapping[str, Any],
    *,
    allowed_keys: frozenset[str] | set[str],
    keys: tuple[str, ...] | None = None,
) -> dict:
    """Stage 2 — keep only the keys the question pack actually needs.

    With explicit ``keys`` the result is exactly those keys; without them only
    keys that are on the allow-list survive (deny by default).
    """
    if keys is not None:
        unknown = [key for key in keys if key not in snapshot]
        if unknown:
            raise SystemOneContractError(f"scoped keys not present in the snapshot: {unknown}")
        return {key: snapshot[key] for key in keys}
    return {key: value for key, value in snapshot.items() if key in allowed_keys}


def _redact_value(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED.format(kind="key")
                if SECRET_KEY_PATTERN.search(str(key))
                else _redact_value(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        cleaned, findings = redact_text(value)
        return cleaned if findings else value
    return value


def redact_secrets(snapshot: Mapping[str, Any]) -> dict:
    """Stage 3 — redact credential-shaped keys and secret-shaped values."""
    if not isinstance(snapshot, Mapping):
        raise SystemOneContractError("redaction expects a mapping")
    return {str(key): _redact_value(value) for key, value in snapshot.items()}


def isolate_project(snapshot: Mapping[str, Any], *, project_id: str) -> dict:
    """Stage 4 — drop any state that belongs to a foreign project.

    Two shapes are handled: a mapping of per-project states under a known key,
    and lists whose entries carry a ``project_id``. Anything that cannot be
    attributed to the current project under those keys is left untouched — the
    scope filter and the minimizer, not guesswork, decide the rest.
    """

    def _walk(value: Any, *, key: str = "") -> Any:
        if isinstance(value, Mapping):
            if key in FOREIGN_PROJECT_KEYS and project_id:
                return {
                    entry_key: _walk(entry_value, key="")
                    for entry_key, entry_value in value.items()
                    if str(entry_key) == project_id
                }
            return {str(k): _walk(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            filtered = []
            for item in value:
                if isinstance(item, Mapping) and item.get("project_id"):
                    if str(item.get("project_id")) != project_id:
                        continue
                filtered.append(_walk(item, key=""))
            return filtered
        return value

    return _walk(dict(snapshot))


def minimize(
    snapshot: Mapping[str, Any],
    *,
    max_list_items: int = DEFAULT_MAX_LIST_ITEMS,
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict:
    """Stage 5 — shrink lists, strings and depth (no relevance, no payload)."""

    def _walk(value: Any, *, depth: int) -> Any:
        if depth > max_depth:
            return "[TRUNCATED:depth]"
        if isinstance(value, Mapping):
            return {str(key): _walk(item, depth=depth + 1) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            keep = max(0, max_list_items - 1) if len(value) > max_list_items else max_list_items
            items = [_walk(item, depth=depth + 1) for item in value[:keep]]
            if len(value) > max_list_items:
                # the marker replaces the last slot: the budget is a hard cap
                items.append(f"[TRUNCATED:{len(value) - keep} more]")
            return items
        if isinstance(value, str) and len(value) > max_string_chars:
            return value[:max_string_chars]
        return value

    return _walk(dict(snapshot), depth=0)


# --------------------------------------------------------------------------- #
# the pipeline                                                                 #
# --------------------------------------------------------------------------- #


class StateSanitizer:
    """Runs the §40 pipeline and returns a fingerprint-bound
    :class:`~skaldir.system_one.SystemOneState`."""

    def __init__(self, *, policy: SanitizerPolicy | None = None) -> None:
        self.policy = policy or SanitizerPolicy()

    def sanitize(
        self,
        snapshot: Mapping[str, Any],
        *,
        scope: str = "task",
        project_id: str | None = None,
        keys: tuple[str, ...] | None = None,
        workspace_fingerprint: str = "",
        source_event_refs: Iterable[str] = (),
    ) -> SystemOneState:
        """Run the pipeline and stamp the §7 state identity.

        The stamp (``workspace_fingerprint``, ``source_event_refs``,
        ``created_at``) is produced here — the state builder — and never
        guessed by the caller. ``created_at`` stays out of the fingerprint, so
        two builds of the same payload remain comparable for dedup (§23).
        """
        isolated = project_id or self.policy.project_id
        payload = runtime_snapshot(snapshot)
        payload = scope_filter(payload, allowed_keys=self.policy.allowed_keys, keys=keys)
        payload = redact_secrets(payload)
        payload = isolate_project(payload, project_id=isolated)
        payload = minimize(
            payload,
            max_list_items=self.policy.max_list_items,
            max_string_chars=self.policy.max_string_chars,
            max_depth=self.policy.max_depth,
        )
        return SystemOneState(
            payload=payload,
            input_scope=scope,
            source_event_refs=tuple(str(ref) for ref in (source_event_refs or ())),
            workspace_fingerprint=str(workspace_fingerprint or ""),
            created_at=time.time(),
        )



__all__ = [
    "DEFAULT_ALLOWED_KEYS",
    "FOREIGN_PROJECT_KEYS",
    "REDACTED",
    "SECRET_KEY_PATTERN",
    "SanitizerPolicy",
    "StateSanitizer",
    "isolate_project",
    "minimize",
    "redact_secrets",
    "runtime_snapshot",
    "scope_filter",
]
