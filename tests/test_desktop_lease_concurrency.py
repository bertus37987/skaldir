"""The desktop lease under real concurrency, corruption and owner changes.

The requirement audit for GOAL §2 says a thread lock and a docstring mentioning ``fcntl``
are not enough: ownership must be atomic across *processes* and across *store instances*,
and stale state must never be mistaken for a free desktop. These tests measure that.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from skaldir.desktop_lease import (
    DesktopLeaseStore,
    LeaseRefused,
    LeaseState,
    LeaseStateCorrupt,
    StaleFence,
)

SRC = str(Path(__file__).resolve().parents[1] / "src")

ACQUIRE_WORKER = textwrap.dedent("""
    import json, os, sys, time
    import skaldir.desktop_lease as _dl
    from skaldir.desktop_lease import DesktopLeaseStore, LeaseRefused

    # test-only knob: lets a *proof* run demonstrate that this test actually detects a
    # missing cross-process lock. Production code never reads it.
    if os.environ.get("SKALDIR_LEASE_TEST_DISABLE_FCNTL") == "1":
        _dl.fcntl = None

    path, run_id, gate, ready = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    store = DesktopLeaseStore(path)
    # A real barrier: announce ready, then wait for the gate. Without the ready phase the
    # parent releases workers that are still importing Python, so they take turns instead
    # of colliding and the race never happens.
    open(ready, "w").close()
    while not os.path.exists(gate):
        time.sleep(0.0005)
    try:
        lease = store.acquire(run_id, task="t", now=1.0)
        print(json.dumps({"won": True, "token": lease.owner_token, "generation": lease.generation}))
    except LeaseRefused as exc:
        print(json.dumps({"won": False, "reason": str(exc)[:120]}))
""")


def _start_worker(path: Path, run_id: str, gate: Path, ready: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", ACQUIRE_WORKER, str(path), run_id, str(gate), str(ready)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": SRC},
    )


def test_two_processes_cannot_both_take_the_desktop(tmp_path):
    """GOAL §2: atomarer Besitz über Prozesse hinweg.

    Each worker is a *separate process* with its *own* store instance doing a
    read-modify-write acquire, and every worker is released by one shared gate file so
    they actually collide instead of taking turns. Exactly one may win. Without a real
    cross-process lock they all read "free", all write, and all report owning the desktop.

    A previous version of this test ran the workers one after another and still passed
    with the lock disabled — it proved nothing. The gate is what makes it a race.
    """
    state = tmp_path / "state" / "desktop-lease.json"
    gate = tmp_path / "gate"
    ready_dir = tmp_path / "ready"
    ready_dir.mkdir()
    workers = [ _start_worker(state, f"run-{i}", gate, ready_dir / f"{i}") for i in range(4) ]

    # wait until every worker has finished importing and is spinning on the gate
    for _ in range(6000):
        if len(list(ready_dir.iterdir())) == len(workers):
            break
        time.sleep(0.005)
    assert len(list(ready_dir.iterdir())) == len(workers), "workers did not reach the barrier"
    gate.touch()                               # ... and this releases them together

    results = []
    for proc in workers:
        out, err = proc.communicate(timeout=90)
        assert proc.returncode == 0, (err or out)[-800:]
        results.append(json.loads(out.strip().splitlines()[-1]))
    winners = [r for r in results if r["won"]]
    losers = [r for r in results if not r["won"]]

    assert len(winners) == 1, f"four processes, {len(winners)} winners: {results}"
    assert len(losers) == 3
    assert all("is queued as waiter" in r["reason"] for r in losers)

    # and the persisted state agrees with exactly one owner
    data = json.loads(state.read_text())
    assert data["lease"]["run_id"] == winners[0]["token"].rsplit(":", 1)[0]
    assert data["lease"]["state"] == LeaseState.CONTROLLING


def test_two_store_instances_in_one_process_do_not_both_take_it(tmp_path):
    """flock is per descriptor — the thread lock must cover the second instance too."""
    state = tmp_path / "desktop-lease.json"
    a, b = DesktopLeaseStore(state), DesktopLeaseStore(state)
    lease = a.acquire("run-a", task="t", now=1.0)
    assert lease.owner_token == "run-a:1"

    with pytest.raises(LeaseRefused):
        b.acquire("run-b", task="t", now=1.0)
    current = a.current()
    assert current is not None and current.run_id == "run-a"


def test_a_corrupt_state_file_is_never_treated_as_free(tmp_path):
    """A damaged file must not hand the desktop to a second run (GOAL §2)."""
    state = tmp_path / "desktop-lease.json"
    state.write_text("{ this is not json", encoding="utf-8")

    store = DesktopLeaseStore(state)
    with pytest.raises(LeaseStateCorrupt):
        store.acquire("run-a", task="t", now=1.0)
    with pytest.raises(LeaseStateCorrupt):
        store.current()

    # and a truncated / non-object payload likewise
    state.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(LeaseStateCorrupt):
        DesktopLeaseStore(state).current()


def test_a_stale_teardown_cannot_free_the_new_owner(tmp_path):
    """GOAL §2: no release by a stale teardown after an owner change."""
    state = tmp_path / "desktop-lease.json"
    store = DesktopLeaseStore(state)
    store.acquire("run-a", task="t", now=1.0)

    # the deliberate owner change: run-b takes over, generation moves on
    new = store.acquire("run-b", task="t", now=2.0, force=True)
    assert new.generation == 2

    # run-a's late teardown must do nothing at all
    assert store.release("run-a", reason="late teardown", now=3.0) is False
    current = store.current()
    assert current is not None
    assert current.run_id == "run-b", "a stale teardown freed the new owner"
    assert current.state == LeaseState.CONTROLLING


def test_the_generation_is_never_reused_so_the_fence_survives_a_release(tmp_path):
    """DESK-02: release must not recycle the token that input actions carry."""
    state = tmp_path / "desktop-lease.json"
    store = DesktopLeaseStore(state)
    first = store.acquire("run-a", task="t", now=1.0)
    assert store.release("run-a", now=2.0) is True

    second = store.acquire("run-a", task="t", now=3.0)
    assert second.generation > first.generation
    assert second.owner_token != first.owner_token
    # an action queued with the old token is refused even by the same run: the store's
    # fence check is what the input adapter calls before every host action
    with pytest.raises(StaleFence):
        store.fence("run-a", first.generation)
    store.fence("run-a", second.generation)      # the current token passes


def test_a_crashed_owner_leaves_a_real_lock_file_but_no_dead_lock(tmp_path):
    """The lock file is not the state: a leftover must not block the next run forever."""
    state = tmp_path / "desktop-lease.json"
    store = DesktopLeaseStore(state)
    store.acquire("run-a", task="t", now=1.0)
    lock = store.lock_path
    assert lock.exists()

    # the kernel releases flock on close, so a later acquire must be able to wait on
    # (and then take) the same lock file rather than dead-locking on it
    store.release("run-a", now=2.0)
    again = DesktopLeaseStore(state)
    lease = again.acquire("run-b", task="t", now=3.0)
    assert lease.run_id == "run-b"
