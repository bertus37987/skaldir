"""LLM client abstraction (tiers small/mid/main) + provider resolution + budgets.

The core never talks to a provider directly: callers get an ``LLMClient``.
Three kinds of client exist:

* :class:`ScriptedLLM` / :class:`CallableLLM` — deterministic offline clients
  for tests and demos (unchanged);
* :class:`skaldir.llm_provider.OpenAICompatibleLLM` — the real HTTP client for any
  ``/v1/chat/completions`` endpoint (OpenAI, OpenRouter, llama.cpp, vLLM, …),
  configured from ``SKALDIR_LLM_*`` environment variables and/or the ``godsv.llm``
  block of a YAML config file. The complete contract — env var names, config
  keys, precedence, retry/timeout policy, error classes — is documented in the
  :mod:`skaldir.llm_provider` module docstring;
* the Hermes plugin's ``ctx.llm``-backed client (gated by ``SKALDIR_LLM=1``),
  which needs the host and is unrelated to the standalone provider above.

:func:`provider_for` is the single entry point into a real client, with the
precedence **explicit config > environment > config file > scripted fallback**.
No provider is created implicitly: an unconfigured run gets a loud
``ProviderConfigError`` naming the exact variables to export. The API key is
read from the environment only — never from a file, never logged, never written
to disk.

Budget: a task-scoped ``BudgetTracker`` accumulates estimated token usage
per tier and fails loud when a limit is exceeded (plan: hard task budgets;
tokens are estimated as chars/4 — budgets are guards, not accounting).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

TIER_SMALL = "small"
TIER_MID = "mid"
TIER_MAIN = "main"
TIERS = (TIER_SMALL, TIER_MID, TIER_MAIN)


class LLMError(RuntimeError):
    """Raised when an LLM call fails or the budget is exceeded."""


class BudgetExceeded(LLMError):
    """Raised when a task would exceed its token budget."""


class LLMProviderError(LLMError):
    """A provider call failed with an HTTP-ish status (401/422/429/529/…).

    The status is carried explicitly so callers can apply the shared
    error/deadline policy (ABNAHME §43) instead of guessing the case from
    message text. ``sdk_retries`` counts retries the provider SDK already did
    internally, so they stay visible in the total attempt count.
    """

    def __init__(self, message: str, *, status: int | None = None, sdk_retries: int = 0) -> None:
        super().__init__(message)
        self.status = None if status is None else int(status)
        self.sdk_retries = max(0, int(sdk_retries))


class LLMClient(Protocol):
    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        """Return the model's raw text response."""
        ...


@dataclass
class ScriptedLLM:
    """Deterministic test/demo client: maps prompt ids to canned responses."""

    responses: dict[str, str]
    calls: list[dict] = field(default_factory=list)
    default: str | None = None

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.calls.append({"prompt_id": prompt_id, "tier": tier, "chars": len(prompt)})
        if prompt_id in self.responses:
            return self.responses[prompt_id]
        if self.default is not None:
            return self.default
        raise LLMError(f"no scripted response for prompt id '{prompt_id}'")


class CallableLLM:
    """Wraps any callable ``fn(*, prompt_id, prompt, tier) -> str`` as a client."""

    def __init__(self, fn: Callable[..., str]) -> None:
        self._fn = fn

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        return self._fn(prompt_id=prompt_id, prompt=prompt, tier=tier)


def provider_for(config: Any = None, **kwargs: Any) -> LLMClient:
    """Build a real provider client for the standalone CLI.

    Thin, documented entry point into :mod:`skaldir.llm_provider` (which owns the
    full contract: env vars, config keys, retry/timeout policy). Precedence is
    **explicit config > environment > config file > scripted fallback**; a
    client object (``ScriptedLLM``/``CallableLLM``) passed as ``config`` is
    returned unchanged. Raises ``LLMError`` (``ProviderConfigError``) when
    nothing usable is configured, naming the exact variables to export.
    """
    try:
        from .llm_provider import provider_for as _provider_for
    except ImportError as exc:  # pragma: no cover - httpx absent from the install
        raise LLMError(
            "the OpenAI-compatible provider requires httpx>=0.27 (pip install httpx); "
            "ScriptedLLM/CallableLLM stay available offline"
        ) from exc
    return _provider_for(config, **kwargs)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4) — budgets are guards, not accounting."""
    return max(1, len(text) // 4)


@dataclass
class BudgetTracker:
    """Task-scoped token budget per tier. ``limits=None`` disables checking.

    Optionally bound to a shared cross-role :class:`skaldir.budget.BudgetAccount`
    via :func:`skaldir.budget.bound_budget_tracker` (design 04 §6): when bound,
    limit violations raise the shared BudgetExceeded and actuals record on the
    role subaccount; unbound, the per-module limits keep working unchanged.
    """

    limits: dict[str, int] | None = None
    usage: dict[str, int] = field(default_factory=lambda: {tier: 0 for tier in TIERS})
    shared_account: Any = None
    shared_role: str = "main_agent"
    shared_reservation_id: str = ""  # set by budget.bound_budget_tracker

    def spend(self, tier: str, prompt: str, response: str) -> int:
        if tier not in self.usage:
            raise LLMError(f"unknown tier: {tier}")
        tokens = estimate_tokens(prompt) + estimate_tokens(response)
        budget_cls: type = BudgetExceeded
        account = self.shared_account
        if account is not None:
            # delegate to the shared cross-role account (design 04 §6)
            from .budget import BudgetExceeded as SharedBudgetExceeded

            account.spend(
                self.shared_role,
                tokens,
                reservation=self.shared_reservation_id,
                note=f"tier={tier}",
            )
            budget_cls = SharedBudgetExceeded
        self.usage[tier] += tokens
        if self.limits is not None:
            limit = self.limits.get(tier)
            if limit is not None and self.usage[tier] > limit:
                raise budget_cls(
                    f"tier '{tier}' budget exceeded: {self.usage[tier]} > {limit} tokens"
                )
        return tokens  # actual token count, for reservation reconciliation

    def reserve_shared(self, amount: int, *, reservation_id: str = "") -> object | None:
        """Delegate a reservation to the bound shared account (None if unbound)."""
        account = self.shared_account
        if account is None:
            return None
        return account.reserve(self.shared_role, amount, reservation_id=reservation_id)

    def total(self) -> int:
        return sum(self.usage.values())
