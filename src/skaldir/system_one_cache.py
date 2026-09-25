"""System-One evaluation cache — dedup that can never serve a stale answer (§23).

The cache key is the contract (§23): ``state_fingerprint``,
``question_pack_id``, ``question_pack_version``, ``goal_revision``,
``policy_revision``, ``provider_model`` and ``threshold_profile``. Everything
the §23 rule names as a *reuse blocker* is represented as well —
``workspace_fingerprint`` plus an epoch per mutating source (``evidence``,
``gui_state``, ``tool``), because "no reuse after a workspace/evidence/GUI-state/
tool change" has to be mechanically true, not a comment.

Two rules are structural, not conventional:

* an entry is stored and looked up under the *whole* key, so any change at all
  is a miss;
* a lookup whose version part no longer matches the live version is **refused**
  and recorded — it is never "refreshed" silently, and the stored entry is left
  exactly as it was (a refusal is evidence, not a mutation).

Nothing here calls a provider, and nothing here is a second runtime: the cache
holds evaluations the :class:`~skaldir.system_one_service.SystemOneService`
already produced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from .secretscan import scan
from .system_one import SystemOneEvaluation

#: §23 invalidation reasons — one per source whose change forbids a reuse.
CACHE_REFUSAL_REASONS: tuple[str, ...] = (
    "goal",
    "policy",
    "workspace",
    "evidence",
    "gui_state",
    "tool",
    "pack",
)

#: The seven fields §23 requires, plus what a reuse would be wrong across.
CACHE_KEY_FIELDS: tuple[str, ...] = (
    "state_fingerprint",
    "question_pack_id",
    "question_pack_version",
    "goal_revision",
    "policy_revision",
    "provider_model",
    "threshold_profile",
    "workspace_fingerprint",
    "evidence_epoch",
    "gui_state_epoch",
    "tool_epoch",
)

#: The key fields that describe a *version* of the world (everything but the
#: state itself). Two evaluations may only share a cache if these match.
CACHE_VERSION_FIELDS: tuple[str, ...] = tuple(
    name for name in CACHE_KEY_FIELDS if name != "state_fingerprint"
)


@dataclass(frozen=True)
class CacheKey:
    """The §23 cache key. ``state_fingerprint`` is the state part; the rest is
    the version part that a goal/policy/workspace/evidence/GUI/tool/pack change
    moves out from under the entry."""

    state_fingerprint: str
    question_pack_id: str
    question_pack_version: str
    goal_revision: int = 0
    policy_revision: int = 0
    provider_model: str = ""
    threshold_profile: str = ""
    workspace_fingerprint: str = ""
    evidence_epoch: int = 0
    gui_state_epoch: int = 0
    tool_epoch: int = 0

    def __post_init__(self) -> None:
        for name in (
            "goal_revision",
            "policy_revision",
            "evidence_epoch",
            "gui_state_epoch",
            "tool_epoch",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must not be negative: {value}")
            object.__setattr__(self, name, value)
        # A key is persisted and logged; a credential-shaped value in it would
        # be a leak. Refuse it at construction instead of carrying it around.
        findings = scan(repr(self.version_part()))
        if findings:
            raise ValueError("cache key carries secret-shaped material — refusing it")

    def version_part(self) -> tuple:
        """The version part of the key — what a change invalidates."""
        return tuple(getattr(self, name) for name in CACHE_VERSION_FIELDS)

    def as_dict_key(self) -> tuple:
        """The storage identity: the whole key, in field order."""
        return tuple(getattr(self, name) for name in CACHE_KEY_FIELDS)

    @classmethod
    def from_context(
        cls,
        context,
        *,
        provider_model: str = "",
        threshold_profile: str = "",
        evidence_epoch: int = 0,
        gui_state_epoch: int = 0,
        tool_epoch: int = 0,
    ) -> CacheKey:
        """Build the key from an :class:`~skaldir.system_one.EvaluationContext`.

        The state fingerprint is left empty on purpose: the state builder fills
        it once the sanitizer produced the payload (§7/§23) — guessing it here
        would be exactly the "reuse a different state" bug the key prevents.
        """
        return cls(
            state_fingerprint="",
            question_pack_id=str(getattr(context, "question_pack_id", "") or ""),
            question_pack_version=str(getattr(context, "question_pack_version", "") or ""),
            goal_revision=int(getattr(context, "goal_revision", 0) or 0),
            policy_revision=int(getattr(context, "policy_revision", 0) or 0),
            provider_model=str(provider_model),
            threshold_profile=str(threshold_profile),
            workspace_fingerprint=str(getattr(context, "workspace_fingerprint", "") or ""),
            evidence_epoch=int(evidence_epoch),
            gui_state_epoch=int(gui_state_epoch),
            tool_epoch=int(tool_epoch),
        )

    def with_state(self, state_fingerprint: str) -> CacheKey:
        """The same version, bound to one concrete state."""
        return replace(self, state_fingerprint=str(state_fingerprint))

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in CACHE_KEY_FIELDS}


@dataclass
class CacheEntry:
    """One stored evaluation plus the key it belongs to. Never mutated in place."""

    key: CacheKey
    evaluation: SystemOneEvaluation
    stored_at: float = field(default_factory=time.time)


class SystemOneCache:
    """In-memory, version-fenced evaluation cache (§23).

    ``live`` is the key of the *current* world (version part only; its state
    fingerprint is ignored). :meth:`update_live` moves the world on — every
    stored entry becomes unreachable, and a lookup that still uses the old key
    is refused and recorded instead of being served or silently refreshed.
    """

    def __init__(self, *, live: CacheKey, max_entries: int = 128) -> None:
        self.live = live
        self.max_entries = int(max_entries)
        self._entries: dict[tuple, CacheEntry] = {}
        self.hits = 0
        self.misses = 0
        self.refusals: list[dict] = []
        self.invalidations: dict[str, int] = {}
        self._order: list[tuple] = []

    # -- the live world ----------------------------------------------------- #

    @property
    def live_version(self) -> tuple:
        return self.live.version_part()

    def update_live(self, **changes) -> CacheKey:
        """Adopt a new version of the world (goal/policy/workspace/pack/…)."""
        unknown = sorted(set(changes) - set(CACHE_KEY_FIELDS))
        if unknown:
            raise ValueError(f"unknown cache key field(s): {unknown}")
        self.live = replace(self.live, **changes)
        return self.live

    def invalidate(self, reason: str) -> int:
        """Drop every entry because a verdict-relevant source changed (§23).

        ``reason`` is one of :data:`CACHE_REFUSAL_REASONS`; an unknown reason is
        refused (a silent catch-all would hide which source moved).
        """
        if reason not in CACHE_REFUSAL_REASONS:
            raise ValueError(
                f"unknown invalidation reason: {reason!r} (known: {list(CACHE_REFUSAL_REASONS)})"
            )
        cleared = len(self._entries)
        self._entries.clear()
        self._order.clear()
        self.invalidations[reason] = self.invalidations.get(reason, 0) + 1
        return cleared

    def bump(self, reason: str) -> CacheKey:
        """Move the epoch of one source and drop the now-unreachable entries."""
        epoch_field = {
            "evidence": "evidence_epoch",
            "gui_state": "gui_state_epoch",
            "tool": "tool_epoch",
            "goal": "goal_revision",
            "policy": "policy_revision",
            "workspace": "workspace_fingerprint",
            "pack": "question_pack_version",
        }.get(str(reason))
        if epoch_field is None:
            raise ValueError(f"unknown bump reason: {reason!r}")
        self.invalidate(reason)
        if epoch_field in ("evidence_epoch", "gui_state_epoch", "tool_epoch"):
            return self.update_live(**{epoch_field: int(getattr(self.live, epoch_field)) + 1})
        return self.live

    # -- lookups ------------------------------------------------------------ #

    def _refuse(self, *, kind: str, reason: str, key: CacheKey) -> None:
        self.refusals.append(
            {
                "kind": kind,
                "reason": reason,
                "key": key.as_dict(),
                "at": time.time(),
            }
        )

    def get(self, key: CacheKey) -> SystemOneEvaluation | None:
        """The stored evaluation, or ``None``.

        ``None`` has three distinct meanings, and they are recorded apart:

        * ``stale_key``/``stale_put`` refusals — the entry belongs to another
          version of the world (goal/policy/workspace/evidence/GUI/tool/pack),
          so it must **not** be reused;
        * ``miss`` — this exact state was never evaluated under this version;
        * ``empty`` — an entry exists but carries no evaluation.
        """
        if key.version_part() != self.live_version:
            self._refuse(kind="stale_key", reason=self._stale_reason(key), key=key)
            return None
        entry = self._entries.get(key.as_dict_key())
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return entry.evaluation

    def _stale_reason(self, key: CacheKey) -> str:
        """Which §23 source moved the key out from under the entry."""
        live = self.live
        if key.goal_revision != live.goal_revision:
            return "goal"
        if key.policy_revision != live.policy_revision:
            return "policy"
        if key.workspace_fingerprint != live.workspace_fingerprint:
            return "workspace"
        if key.evidence_epoch != live.evidence_epoch:
            return "evidence"
        if key.gui_state_epoch != live.gui_state_epoch:
            return "gui_state"
        if key.tool_epoch != live.tool_epoch:
            return "tool"
        if (key.question_pack_id, key.question_pack_version) != (
            live.question_pack_id,
            live.question_pack_version,
        ):
            return "pack"
        return "version"

    def contains(self, key: CacheKey) -> bool:
        """Whether an entry exists — without counting a hit or a refusal."""
        return _hash_key(key) in self._entries

    def entry(self, key: CacheKey) -> CacheEntry | None:
        """The raw entry (for evidence/inspection, never for reuse decisions)."""
        return self._entries.get(_hash_key(key))

    # -- writes ------------------------------------------------------------- #

    def put(self, key: CacheKey, evaluation: SystemOneEvaluation) -> CacheEntry | None:
        """Store an evaluation under ``key``, or refuse it.

        A key that is not the live version is refused with ``stale_put``: caching
        a result computed against an older world would make the cache itself the
        stale-evidence source.
        """
        if key.version_part() != self.live_version:
            self._refuse(kind="stale_put", reason=self._stale_reason(key), key=key)
            return None
        entry = CacheEntry(key=key, evaluation=evaluation)
        hashed = _hash_key(key)
        if hashed not in self._entries:
            self._order.append(hashed)
        self._entries[hashed] = entry
        while len(self._order) > self.max_entries:
            oldest = self._order.pop(0)
            self._entries.pop(oldest, None)
        return entry

    # -- reporting ---------------------------------------------------------- #

    def report(self) -> dict:
        """Measured numbers only: counts and sums, no estimates (§26)."""
        return {
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
            "refusals": len(self.refusals),
            "invalidations": dict(self.invalidations),
            "live_version": list(self.live_version),
        }


def _hash_key(key: CacheKey) -> tuple:
    """The storage identity of a key: the whole key, in field order."""
    return key.as_dict_key()


def cache_key_for(
    *,
    state,
    context,
    provider_model: str = "",
    threshold_profile: str = "",
    epochs: dict[str, int] | None = None,
) -> CacheKey:
    """The one helper that builds a full key from a state + context (§23).

    Used by :class:`~skaldir.system_one_service.SystemOneService` so no caller
    assembles a key by hand and forgets a field.
    """
    values: dict[str, Any] = dict(epochs or {})
    return CacheKey.from_context(
        context,
        provider_model=provider_model,
        threshold_profile=threshold_profile,
        evidence_epoch=int(values.get("evidence", 0)),
        gui_state_epoch=int(values.get("gui_state", 0)),
        tool_epoch=int(values.get("tool", 0)),
    ).with_state(getattr(state, "fingerprint", ""))


__all__ = [
    "CACHE_KEY_FIELDS",
    "CACHE_REFUSAL_REASONS",
    "CACHE_VERSION_FIELDS",
    "CacheEntry",
    "CacheKey",
    "SystemOneCache",
    "cache_key_for",
]
