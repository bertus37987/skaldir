# Skaldir Agent — Run Lifecycle

**Scope:** how a run keeps its execution responsibility across turns, crashes and
restarts. Implementation: `src/godsv/run_supervisor.py` (persistent run records +
continuation queue + watchdog), `tools/gv_crash_recovery.py` (crash probe),
`src/godsv/desktop.py` (GV instance identification).
**Ground truth:** `docs/COMPLETE_AUDIT-2026-09-13.md` (`audit-3` AUTO-03/04/05/07,
`audit-4` R1) · Plan: `docs/IMPLEMENTATION-PLAN-live-control-plane.md` Block 2.

> Legend: **implemented** = code exists and is covered by tests · **wired** = a
> production caller uses it · **open** = named gap, not yet done.

---

## 1. The invariant

```
NON_TERMINAL_RUN  =>  ACTIVE_WORKER_LEASE
                  OR  QUEUED_CONTINUATION
                  OR  EXPLICIT_WAIT_WITH_WAKE_SOURCE
```

A non-terminal run must satisfy **at least one** disjunct. A run that satisfies
none is not "quietly sleeping" — it is a lifecycle error: nobody owns the work,
no queue entry exists, and nobody is waiting for a wake source. `run_supervisor`
detects exactly that and moves the run into a defined state.

| Disjunct | Holds when |
|---|---|
| `ACTIVE_WORKER_LEASE` | `lease_owner` set, `lease_expires_at > now`, and the encoded owner PID is alive (a named owner without a PID is taken at its word) |
| `QUEUED_CONTINUATION` | an open queue row exists for the run's *current* generation; a `claimed` row counts only while the claimant's lease is still valid |
| `EXPLICIT_WAIT_WITH_WAKE_SOURCE` | `wait_reason` **and** `wake_source` are both set — half of it is not a wait |

Terminal runs are exempt (there is no work left to own).

## 2. Run states

| State | Terminal | Meaning |
|---|---|---|
| `QUEUED` | – | work is owed, waiting for a worker to claim it |
| `ACTIVE` | – | a worker holds a valid lease |
| `CONTINUING` | – | a continuation was claimed and is executing |
| `WAITING` | – | explicit wait with a wake source (external dependency, next turn, …) |
| `UNKNOWN_SIDE_EFFECT_STATE` | – | an action may have taken effect and cannot be proven; **operator-owned** |
| `USER_PAUSED` | – | the owner paused the run |
| `SAFETY_BLOCKED` | – | a safety decision blocked the run |
| `APPROVAL_PENDING` | – | the run waits for an approval decision |
| `BUDGET_STOPPED` | – | a budget stop halted the run |
| `LEGACY_ORPHAN` | – | record from an older schema whose state GV cannot classify |
| `COMPLETE` / `FAILED` / `CANCELLED` / `ABANDONED` | ✓ | terminal |

**Transitions (implemented):**

```
QUEUED ──claim──▶ CONTINUING ──commit──▶ ACTIVE
   ▲                  │                    │
   │                  └──orphan────────────┤
   └──────requeue (same generation)◀───────┘
ACTIVE ──orphan/expired lease──▶ QUEUED | UNKNOWN_SIDE_EFFECT_STATE
any    ──hold──▶ USER_PAUSED | SAFETY_BLOCKED | APPROVAL_PENDING | BUDGET_STOPPED
hold   ──wake (matching wake source only)──▶ QUEUED
```

Every transition is one conditional `UPDATE` (`compare-and-swap` on `state` and
`continuation_generation`), so two supervisors cannot both act on one
observation. `RunStore.update()` refuses terminal runs, refuses policy-owned
states unless the caller explicitly opts in, and refuses a mismatched `expect`.

## 3. Persistent run record

`runs` table, exactly these 16 columns (`run_supervisor.RUN_FIELDS`, test-enforced):

```
run_id  session_id  goal_revision  state  next_owner  pending_action
continuation_generation  lease_owner  lease_expires_at  heartbeat_at
wait_reason  wake_source  retry_count  recovery_count  last_progress_at
last_committed_event
```

* Storage follows the existing runtime pattern: `<HERMES_HOME>/godsv/runs.db`,
  SQLite WAL, `RLock`, single writer (`pauses.db` / `events.db` are the models).
  No second runtime, no parallel persistence.
* **Backward compatible (implemented):** `CREATE TABLE IF NOT EXISTS` plus an
  additive `ALTER TABLE … ADD COLUMN` migration adds missing columns to an
  existing `runs` table. Old rows are never rewritten; a record whose `state` is
  unknown to this version is read as `LEGACY_ORPHAN` with wake source
  `operator:classify`, i.e. an explicit wait that the watchdog leaves alone.
  Covered by `test_migration_adds_missing_columns_and_keeps_legacy_rows`.
* `last_committed_event` only ever moves forward; a stale replay cannot rewind it.

## 4. Leases

* A lease is `(lease_owner, lease_expires_at)`; `lease_owner` carries the host
  holder format `pid=<pid>:turn=…` so liveness is derivable.
* `heartbeat(run_id, owner)` extends the lease **only** for the current owner and
  moves `last_progress_at` — progress is heartbeat-driven, not turn-driven.
* An **expired** lease is no ownership even if the process is still alive; a
  **dead** holder is an orphan even if the lease has not expired yet. Both are
  violations.
* A **stale heartbeat on a still-valid lease is a warning, not a violation**
  (`STALE_HEARTBEAT`, reported in `check_invariants()["warnings"]`): ownership is
  intact, so it must not trigger recovery — the clock alone is not evidence.

## 5. Continuation and exactly-once

The crash window **between redirect and continue** is the core risk: the redirect
was delivered, the tool call was blocked, and the process died before the
continuation executed. Resuming twice duplicates side effects; resuming zero
times loses the run.

**Implemented mechanism:**

1. `continuations` is keyed `UNIQUE(run_id, generation)`. `enqueue_continuation`
   is `INSERT … ON CONFLICT DO NOTHING`, so a repeated enqueue — same process,
   another process, or a restart — never creates a second resumption.
2. `claim_continuation` is a conditional `queued → claimed` UPDATE plus a
   compare-and-swap grant of the worker lease. A second claimer gets `None`.
3. `recover_pending` returns work this process now owns (bounded by `limit`).
4. `commit_continuation` is conditional on `queued|claimed → committed`; the
   second call returns `False` and changes nothing.
5. A crash after `claim` but before `commit` re-queues the **same generation**
   (`requeue_continuation`), never a new one: a new generation would be a second
   logical continuation, i.e. exactly the double execution being prevented.
6. The whole read-decide-write sequence runs under a **per-run lock**
   (`RunStore.run_lock`) plus the compare-and-swap, so two supervisors racing on
   one run produce one claim, one continuation, `recovery_count == 1`.

Semantics of the counters: generations count *logical continuations*;
`recovery_count` counts *recovery decisions* for the run.

**Status:** implemented + tested at store level
(`test_crash_between_redirect_and_continue_produces_exactly_one_resume` closes
the handle, reopens the store, and asserts exactly one continuation).
Multi-process recovery is covered by a two-thread race test;
`live verified` across a real `kill -9` is **open** (see §9).

## 6. Waits and wake sources

A wait is only a wait with a wake source. The wake source is fixed by the
state owner, never by the caller:

| State / situation | Wake source |
|---|---|
| any explicit wait | caller-declared, must be non-empty |
| `USER_PAUSED` | `user:resume` |
| `SAFETY_BLOCKED` | `safety:clearance` |
| `APPROVAL_PENDING` | `approval:decision` |
| `BUDGET_STOPPED` | `budget:top_up` |
| `UNKNOWN_SIDE_EFFECT_STATE` | `operator:side_effect_verified` |

`wake(run_id, source=…)` lifts a hold/wait **only** when the source matches what
that state requires. `hold()` rejects any state outside the policy-owned set, so
no caller can invent a weaker wake source for a safety hold.

## 7. Orphan recovery (watchdog)

`RunWatchdog` wraps `sweep()`, modelled on `pause.VoicePause._ensure_watchdog`:
own daemon thread, bounded interval, exceptions counted in `.errors` and reported
— never swallowed into silence, never allowed to kill the loop.

`sweep(store, owner=…)` per pass: **check** (`check_invariants`) → **repair**
(`repair_run` for each violation) → **claim** (`recover_pending`), returning a
receipt (`violations_before`, `violations_after`, `repairs`, `claimed`, `ok`).

`repair_run` decides exactly one action and always reports why:

| Action | Condition |
|---|---|
| `TERMINAL_UNTOUCHED` | terminal state — never rewritten |
| `POLICY_OWNED_UNTOUCHED` | `USER_PAUSED` / `SAFETY_BLOCKED` / `APPROVAL_PENDING` / `BUDGET_STOPPED` — **recovery never overwrites these**, not even when they violate the invariant |
| `HEALTHY` | invariant holds |
| `WAITING_ON_WAKE_SOURCE` | holds only via an explicit wait |
| `REQUEUED` | orphan with no side effect in flight (or repeatable) → new/kept generation, run → `QUEUED` |
| `PARKED_UNKNOWN_SIDE_EFFECT` | orphan with an unproven, unverifiable pending action → `UNKNOWN_SIDE_EFFECT_STATE`, operator wake source, **no automatic retry** |
| `SUPERSEDED_BY_OTHER` | a concurrent recovery/worker got there first (CAS refused) |
| `UNKNOWN_RUN` | no such run |

**Implemented:** the live finding — 2 expired host `session_turn_leases` whose
holder PIDs are long dead, previously a silent state nobody treated as an error —
is now detected (`EXPIRED_LEASE` + `NO_QUEUE_NO_WAIT`) and repaired.
`adopt_host_leases` turns *orphaned* host turn leases into run records
(idempotent, keyed by session); a healthy host turn is never doubled. Adopted
work is named `host-turn:<session>` — an action GV cannot prove idempotent — so
the supervisor parks it for the operator instead of silently re-running a turn.

## 8. Side-effect recovery

Recovery may repeat an action **only** when that is provably safe
(`side_effect_disposition`, `run_actions` registry):

| Disposition | Condition | Consequence |
|---|---|---|
| `IDLE` | no action recorded at all | nothing was in flight → requeue |
| `REPEAT` | action registered `idempotent == true` | requeue, repeat |
| `RESOLVED` | result existence verified (recorded via `verify_result`, or `result_path` exists on disk) | requeue; the resolved action is not repeated |
| `UNKNOWN` | anything else | **no retry** → `UNKNOWN_SIDE_EFFECT_STATE`, operator wake source |

`UNKNOWN` is the default and the safe direction: an unregistered or
non-idempotent action is never retried automatically. Parking supersedes any open
continuation of the run first, so a claim left behind by the dead owner cannot be
executed behind the operator's back.

## 9. Implemented vs. open

**Implemented + tested**
* Invariant check (`check_run` / `check_invariants`) with the three disjuncts.
* Persistent 16-field run record, additive migration, tolerant legacy reader.
* Lease grant / heartbeat / expiry / orphan detection; stale heartbeat as warning.
* Continuation queue: unique `(run_id, generation)`, CAS claim, CAS commit,
  same-generation requeue, generation supersede, exactly-once at store level.
* Side-effect rule with the four dispositions and `UNKNOWN_SIDE_EFFECT_STATE`.
* Policy holds (`USER_PAUSED`, `SAFETY_BLOCKED`, `APPROVAL_PENDING`,
  `BUDGET_STOPPED`) that recovery never overwrites — one test each.
* Watchdog (`sweep`, `RunWatchdog.pass_once`, thread lifecycle, error reporting).
* Host turn-lease reading + idempotent adoption of orphans only.
* `gv_crash_recovery.py --check` finds the running instance: the marker matcher is
  path-aware. Both real spellings match — `…/.hermes/godsv/` (the live instance) and
  `…/.hermes/godsv/desktop-userdata` — and a second, independent marker source reads
  `/proc/<PID>/environ`. Regression test replays the live case over a mocked process
  list (no kill, no process start).

**Wired**
* `run_supervisor` is a self-contained library over the existing runtime store;
  `tools/gv_crash_recovery.py` uses the shared marker helpers in `godsv.desktop`.

**Open (named, not done)**
1. **Plugin/Turn wiring.** No production caller starts `RunWatchdog` yet: the
   plugin hook layer is owned by the Block 4+5 workstream. Until that lands, the
   supervisor must be driven explicitly (`sweep()` per turn, or a future
   `godsvoice runs` command).
2. **Real `kill -9` run.** Everything here is proven with an isolated `HERMES_HOME`
   and a store-level crash; the destructive live crash run stays with the central
   control plane, as agreed.
3. **Cross-process recovery.** Exactly-once is guaranteed by SQLite uniqueness +
   per-run locking *within* a process and by the unique constraint *across*
   processes; a real multi-process recovery run is not yet exercised.
4. **Completion-state coupling.** The run state and the completion contract state
   (`completion.py`) are not yet mirrored onto one another; `goal_revision` is
   carried but not yet enforced against contract revisions.
5. **Retention.** `runs` / `continuations` / `run_audit` have no compaction or
   archiving policy yet (same open item as the event store, MEM-31).
6. **Event emission.** The watchdog optionally emits `run_sweep` through an
   injected emitter; no consumer subscribes yet.
