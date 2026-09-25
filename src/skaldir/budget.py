"""Shared cross-role budget (design 04 §6).

One account per goal with subaccounts for main_agent, voice, workers, tools
and evaluators. Reservations commit capacity before parallel expensive work so
the same remaining capacity cannot be double-planned; actual spend records
against a reservation (or up to a small per-role floor without one); an
exhausted role degrades to a structured ``budget_wait`` — never a silent
fallback to unlimited. Per-role token buckets are reporting-only; backoff
policy stays with the caller. Accounting is thread-safe (single lock).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from .llm import BudgetExceeded as _LLMBudgetExceeded

ROLES = ("main_agent", "voice", "workers", "tools", "evaluators")

DEFAULT_LIMITS: dict[str, int] = {
    "main_agent": 100_000,
    "voice": 20_000,
    "workers": 150_000,
    "tools": 50_000,
    "evaluators": 50_000,
}


DEFAULT_UNRESERVED_FRACTION = 0.05  # 5% of the role limit per budget cycle


class BudgetError(RuntimeError):
    """Raised when budget accounting cannot be performed as requested."""


class BudgetExceeded(BudgetError, _LLMBudgetExceeded):
    """Raised when a reservation would exceed the account capacity.

    Also subclasses ``llm.BudgetExceeded`` so per-module budget guarding
    (``llm.BudgetTracker``) and the shared account share one exception type.
    """


DEFAULT_UNRESERVED_FRACTION = 0.05  # 5% of the role limit per budget cycle


@dataclass
class Reservation:
    reservation_id: str
    role: str
    amount: int


@dataclass
class BudgetWait:
    """Structured degraded-mode reason — never silently unlimited."""

    role: str
    requested: int
    available: int
    reason: str = "budget_wait"
    granted: bool = False


class TokenBucket:
    """Reporting-only rate limiter: capacity + refill per second."""

    def __init__(self, *, capacity: float, refill_per_second: float, now: float = 0.0) -> None:
        if capacity <= 0:
            raise BudgetError("bucket capacity must be positive")
        self.capacity = float(capacity)
        self.refill_per_second = float(refill_per_second)
        self.tokens = float(capacity)
        self.last_refill = float(now)
        self._lock = threading.Lock()

    def take(self, amount: float = 1.0, *, now: float | None = None) -> bool:
        with self._lock:
            if now is not None:
                self.tokens = min(
                    self.capacity,
                    self.tokens + (float(now) - self.last_refill) * self.refill_per_second,
                )
                self.last_refill = float(now)
            if self.tokens >= amount:
                self.tokens -= amount
                return True
            return False


class _RoleBudget:
    """Internal per-role state: limits, reserved, spend, rate bucket."""

    def __init__(self, *, limit: int, bucket: TokenBucket | None = None) -> None:
        self.limit = int(limit)
        self.reserved = 0
        self._reservations: dict[str, int] = {}
        self.spent = 0
        self._unreserved_spent = 0  # GV2-SEC-05: cumulative reservation-less spend
        self.bucket = bucket
        self._lock = threading.Lock()

    def remaining(self) -> int:
        return max(0, self.limit - self.reserved - self.spent)


class BudgetAccount:
    """One shared budget account for a goal; every mutation takes the lock."""

    def __init__(
        self,
        *,
        goal_id: str,
        limits: dict[str, int] | None = None,
        rate_buckets: dict[str, TokenBucket] | None = None,
        small_spend_floor: int = 200,
        unchanged_floor: int | None = None,
        unreserved_cap_fraction: float = DEFAULT_UNRESERVED_FRACTION,
    ) -> None:
        self.goal_id = str(goal_id)
        self._small_floor = int(
            unchanged_floor if unchanged_floor is not None else small_spend_floor
        )
        # GV2-SEC-05: per-role CAP on total reservation-less spend. Beyond this
        # fraction of the role limit, floor spends are refused (structured
        # budget_wait) so many small spends can't silently drain the account.
        self._unreserved_cap_fraction = float(unreserved_cap_fraction)
        if not 0.0 <= self._unreserved_cap_fraction <= 1.0:
            raise BudgetError("unreserved_cap_fraction must be within [0, 1]")
        limits = dict(DEFAULT_LIMITS if limits is None else limits)
        self._roles: dict[str, _RoleBudget] = {}
        for role in ROLES:
            bucket = (rate_buckets or {}).get(role)
            self._roles[role] = _RoleBudget(
                limit=int(limits.get(role, 0)),
                bucket=bucket
                if bucket is not None
                else TokenBucket(capacity=60.0, refill_per_second=10.0),
            )
        self._ledger: list[dict] = []
        self._lock = threading.Lock()

    # -- accounting --------------------------------------------------------

    def _role(self, role: str) -> _RoleBudget:
        if role not in self._roles:
            raise BudgetError(f"unknown role: {role!r} (known: {ROLES})")
        return self._roles[role]

    def reserve(
        self, role: str, amount: int, *, reservation_id: str = ""
    ) -> Reservation:
        """Commit capacity for expensive work before it starts.

        Two concurrent reserves of the same remaining capacity: the second
        raises :class:`BudgetExceeded` — never a double commitment.
        """
        amount = int(amount)
        rid = reservation_id or f"res-{len(self._ledger) + 1}-{role}"
        with self._lock:
            st = self._role(role)
            if st.reserved + amount > st.limit or st.spent + amount > st.limit:
                raise BudgetExceeded(
                    f"role '{role}' budget exceeded while reserving: "
                    f"{st.reserved + amount} > {st.limit} tokens"
                )
            st.reserved += amount
            st._reservations[rid] = st._reservations.get(rid, 0) + amount
            entry = {"action": "reserve", "role": role, "amount": amount, "reservation_id": rid}
            self._ledger.append(entry)
        return Reservation(reservation_id=rid, role=role, amount=amount)

    def release_reservation(self, reservation_id: str, *, cancel: bool = False) -> bool:
        """Release a reservation (after commit or as a rollback if cancelled)."""
        with self._lock:
            found: tuple[str, int] | None = None
            for role, st in self._roles.items():
                amount = st._reservations.pop(reservation_id, None)
                if amount is not None:
                    st.reserved = max(0, st.reserved - amount)
                    if cancel:
                        # rolled back: the capacity was consumed with no actual spend
                        st.spent = max(0, st.spent - amount) if False else st.spent
                    found = (role, amount)
                    break
            if found is None:
                return False
            role, amount = found
            entry = {
                "action": "release",
                "role": role,
                "amount": amount,
                "reservation_id": reservation_id,
            }
            self._ledger.append(entry)
            return True

    def rollback(self, reservation_id: str) -> bool:
        """Explicit rollback of an unused reservation before it is closed."""
        return self.release_reservation(reservation_id, cancel=True)

    def spend(
        self,
        role: str,
        amount: int,
        *,
        reservation: str = "",
        note: str = "",
    ) -> None:
        """Record actual spend against a reservation (or the small-spend floor).

        Actuals never push a role over its global account limit; the caller is
        expected to have reserved capacity for expensive work.

        GV2-SEC-06: a supplied reservation id that is UNKNOWN to this account,
        or one already fully consumed, is a hard ``BudgetExceeded`` — never a
        silent fall-through to the floor path. Spending beyond the amount a
        reservation still holds is likewise refused (over-tap must be planned
        with a real reservation, not taken silently).
        """
        amount = int(amount)
        with self._lock:
            st = self._role(role)
            reserved_amount = st._reservations.get(str(reservation)) if reservation else None
            if reservation and reserved_amount is None:
                raise BudgetExceeded(
                    f"role '{role}': unknown or exhausted reservation id "
                    f"{str(reservation)!r} — reservation-less path refused "
                    f"(reserve first or spend without a reservation id)"
                )
            if reserved_amount is not None:
                if amount > reserved_amount:
                    # over-tap: the extra tonnage was never committed
                    raise BudgetExceeded(
                        f"role '{role}': spend of {amount} exceeds the remaining "
                        f"hold of {reserved_amount} on reservation {str(reservation)!r} "
                        f"(reserve the difference first)"
                    )
                # the reservation already holds this capacity — convert actuals
                used = min(reserved_amount, amount)
                st.reserved -= used
                st._reservations[str(reservation)] = reserved_amount - used
            else:
                if amount > self._small_floor:
                    raise BudgetExceeded(
                        f"role '{role}': spend of {amount} needs a reservation "
                        f"(floor for reservation-less spend: {self._small_floor})"
                    )
                cap = int(st.limit * self._unreserved_cap_fraction)
                if (st._unreserved_spent + amount) > cap:
                    raise BudgetExceeded(
                        f"role '{role}': cumulative reservation-less spend "
                        f"{st._unreserved_spent + amount} exceeds the unreserved cap "
                        f"{cap} ({self._unreserved_cap_fraction:.0%} of {st.limit}); "
                        f"reserve capacity instead"
                    )
                if st.spent + st.reserved + amount > st.limit:
                    raise BudgetExceeded(
                        f"role '{role}' budget exceeded: {st.spent + st.reserved + amount}"
                        f" > {st.limit} tokens"
                    )
                st._unreserved_spent += amount
            st.spent += amount
            entry = {
                "action": "spend",
                "role": role,
                "amount": amount,
                "reservation_id": str(reservation) if reservation else "",
                "note": note,
            }
            self._ledger.append(entry)

    # -- degraded mode ------------------------------------------------------

    def budget_wait(self, role: str, requested: int) -> BudgetWait:
        """Structured degraded availability — a caller-facing reason, never
        a silent unlimited fallback."""
        requested = int(requested)
        with self._lock:
            st = self._role(role)
            available = st.remaining()
        wait = BudgetWait(role=role, requested=requested, available=available)
        # never grant: exhausted capacity is a wait, backoff policy stays with callers
        return wait

    # -- state / report ------------------------------------------------------

    def remaining(self, role: str) -> int:
        with self._lock:
            return self._role(role).remaining()

    def reserved(self, role: str) -> int:
        with self._lock:
            return self._role(role).reserved

    def spent(self, role: str) -> int:
        with self._lock:
            return self._role(role).spent

    def rate_report(self) -> dict[str, dict]:
        """Reporting-only token-bucket state per role."""
        with self._lock:
            return {
                role: {
                    "capacity": st.bucket.capacity,
                    "refill_per_second": st.bucket.refill_per_second,
                    "tokens": st.bucket.tokens,
                }
                for role, st in self._roles.items()
            }

    def dump_ledger(self, path: str | Path) -> int:
        """Optional JSONL ledger dump (no persistence requirement)."""
        entries = list(self._ledger)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return len(entries)


def bound_budget_tracker(
    account: BudgetAccount, role: str, *, shared_limit: int | None = None
) -> object:
    """Return an llm.BudgetTracker bound to a shared account/role subaccount.

    Delegated: the tracker reserves around the shared account when a shared limit
    is given, and always records actual spend on the role subaccount.
    """
    # imported here to avoid a circular import at module load
    from .llm import TIERS, BudgetTracker

    reservation: Reservation | None = None
    if shared_limit is not None:
        reservation = account.reserve(role, int(shared_limit))
    tracker = BudgetTracker(
        limits={tier: int(shared_limit) for tier in TIERS} if shared_limit is not None else None,
        shared_account=account,
        shared_role=role,
    )
    tracker.shared_reservation = reservation  # type: ignore[attr-defined]
    tracker.shared_reservation_id = reservation.reservation_id if reservation else ""
    return tracker
