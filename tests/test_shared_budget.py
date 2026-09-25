"""Shared cross-role budget (design 04 §6): one account per goal, subaccounts
for main_agent / voice / workers / tools / evaluators.
"""

import threading

import pytest

from skaldir import budget as bud


def account(**over):
    limits = {
        "main_agent": 1000,
        "voice": 500,
        "workers": 800,
        "tools": 400,
        "evaluators": 300,
    }
    limits.update(over.pop("limits", {}))
    return bud.BudgetAccount(goal_id="g-1", limits=limits, **over)


# --- reservation / spend / rollback ----------------------------------------------


def test_reserve_spend_rollback_roundtrip():
    acc = account()
    res = acc.reserve("workers", 400, reservation_id="r-1")
    assert res.reservation_id == "r-1"
    assert acc.reserved("workers") == 400
    acc.spend("workers", 250, reservation="r-1")
    assert acc.spent("workers") == 250
    assert acc.reserved("workers") == 150  # spend converted that part of the hold
    assert acc.remaining("workers") == 800 - 400  # capacity still fully committed
    assert acc.release_reservation("r-1") is True  # commit: drop the remainder
    assert acc.reserved("workers") == 0
    assert acc.remaining("workers") == 800 - 250
    # rollback of a live reservation restores full capacity
    acc.reserve("workers", 400, reservation_id="r-2")
    assert acc.rollback("r-2") is True
    assert acc.reserved("workers") == 0
    assert acc.release_reservation("r-2") is False  # unknown after release


def test_reserve_and_release_restores_capacity():
    acc = account()
    acc.reserve("workers", 400, reservation_id="r-1")
    assert acc.remaining("workers") == 400
    acc.release_reservation("r-1")
    assert acc.remaining("workers") == 800


def test_spend_without_reservation_up_to_floor():
    # SEC-05: unreserved cap is 5% of the 400-token tools limit = 20 lifetime.
    acc = account(unchanged_floor=100, unreserved_cap_fraction=0.25)
    acc.spend("tools", 80)  # under cap(100) AND under the floor
    assert acc.spent("tools") == 80
    with pytest.raises(bud.BudgetExceeded):
        acc.spend("tools", 300)  # over the floor and over capacity without reservation


# --- double-commitment -------------------------------------------------------------


def test_double_reservation_of_same_capacity_rejected():
    acc = account()
    acc.reserve("workers", 500, reservation_id="r-1")
    with pytest.raises(bud.BudgetExceeded):
        acc.reserve("workers", 400, reservation_id="r-2")


def test_two_concurrent_reserves_only_one_wins():
    acc = account()
    results: list[object] = []
    lock = threading.Lock()

    def try_reserve(rid: str, amount: int):
        outcome = None
        try:
            outcome = acc.reserve("workers", amount, reservation_id=rid)
            value = outcome
        except bud.BudgetExceeded as exc:
            value = exc
        with lock:
            results.append(value)

    threads = [
        threading.Thread(target=try_reserve, args=("r-1", 600)),
        threading.Thread(target=try_reserve, args=("r-2", 600)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    kinds = sorted(type(r).__name__ for r in results)
    assert kinds == ["BudgetExceeded", "Reservation"]


# --- degraded mode ------------------------------------------------------------------


def test_exhausted_role_returns_structured_wait_reason():
    acc = account()
    acc.reserve("evaluators", 300, reservation_id="r-1")
    acc.spend("evaluators", 300, reservation="r-1")
    acc.release_reservation("r-1")
    wait = acc.budget_wait("evaluators", 100)
    assert wait.reason == "budget_wait"
    assert wait.role == "evaluators"
    assert wait.available == 0
    with pytest.raises(bud.BudgetExceeded):
        acc.reserve("evaluators", 100, reservation_id="r-2")  # 300 of 300 spent


def test_budget_wait_reports_remaining_not_requested():
    acc = account()
    acc.reserve("voice", 400, reservation_id="r-1")
    wait = acc.budget_wait("voice", 50)
    assert wait.reason == "budget_wait"
    assert not wait.granted


def test_budget_wait_never_falls_back_silently():
    acc = account()
    acc.reserve("voice", 500, reservation_id="r-1")
    wait = acc.budget_wait("voice", 50)
    assert wait.reason == "budget_wait"
    assert not wait.granted


# --- token bucket -------------------------------------------------------------------


def test_token_bucket_capacity_and_refill():
    bucket = bud.TokenBucket(capacity=2, refill_per_second=1.0, now=1000.0)
    assert bucket.take(1, now=1000.0)
    assert bucket.take(1, now=1000.0)
    assert not bucket.take(1, now=1000.0)  # drained
    assert bucket.take(1, now=1001.0)  # one token refilled after 1s
    assert not bucket.take(1, now=1001.0)


def test_account_reports_bucket_state_per_role():
    acc = account()
    report = acc.rate_report()
    assert set(report) == {"main_agent", "voice", "workers", "tools", "evaluators"}
    for state in report.values():
        assert "capacity" in state and "tokens" in state


# --- ledger dump ---------------------------------------------------------------------


def test_jsonl_ledger_dump(tmp_path):
    acc = account()
    acc.reserve("workers", 100, reservation_id="r-1")
    acc.spend("workers", 60, reservation="r-1", note="first worker call")
    acc.release_reservation("r-1")
    path = tmp_path / "ledger.jsonl"
    n = acc.dump_ledger(path)
    assert n == 3
    import json

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [entry["action"] for entry in lines] == ["reserve", "spend", "release"]
    assert lines[1]["amount"] == 60


# --- llm.BudgetTracker binding -------------------------------------------------------


def test_bound_budget_tracker_delegates_reservations():
    acc = account()
    tracker = bud.bound_budget_tracker(acc, "main_agent", shared_limit=400)
    with pytest.raises(bud.BudgetExceeded):
        tracker.spend("small", "x" * 4400, "y")  # est. 1100 tokens > 400 shared limit
    ok = bud.bound_budget_tracker(acc, "voice", shared_limit=400)
    ok.spend("small", "x" * 400, "y")  # est. 100+1 tokens, within the shared limit
    assert ok.shared_reservation.reservation_id
    assert acc.spent("voice") == 101


def test_unbound_budget_tracker_still_works():
    from skaldir.llm import BudgetTracker

    tracker = BudgetTracker(limits={"small": 10})
    tracker.spend("small", "abcd", "efgh")
    assert tracker.total() == 2
