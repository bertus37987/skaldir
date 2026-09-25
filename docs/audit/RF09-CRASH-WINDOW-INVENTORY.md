# RF-09 Crash-Window Inventory (engine `~/.hermes/hermes-agent`, HEAD 949fd7b)

Criterion RF-09 (design 09 §4): "A crash before/after the checkpoint, before/after an
external effect and during a fallback leads neither to lost user corrections nor to
double side effects nor to two simultaneous owners of the same segment."

This file is the worker-owned inventory (NOT the RF-STATUS doc — the integrator owns that).
All line numbers refer to the pristine engine tree as of 949fd7b-adjacent installs.

---

## W1 — external action dispatched, crash BEFORE its result is recorded

Location:
* `agent/tool_executor.py` — sequential path: parse → `tool_start_time` →
  `_resolve_sequential_dispatch` (real execution happens inside `managed`) →
  `_publish_sequential_result` → `_commit_tool_result` → `messages.append(...)`
  → `_flush_session_db_after_tool_progress` (tool_executor.py:1707-1719, flush at :189).
* Concurrent path: `batch.run()` (line ~1459) followed by `_append_batch_results`
  (line ~1462) — same result-after-effect shape, batched.

Why UNSAFE (pristine):
* The side effect (HTTP request, terminal command, file write) has already left the
  process before `_flush_session_db_after_tool_progress` survives it to the session DB.
* A kill -9 inside the window loses BOTH the row and any evidence that the effect
  was in flight — the restarted run re-drives the call from the replayed assistant
  row and silently re-sends the effect.
* The engine's replay sanitizer (`agent/replay_cleanup.py`) correctly keeps
  side-effecting calls as ``unknown`` when it SEES an interrupted shape, but an
  abrupt SIGKILL mid-dispatch leaves the call without any tool result row at all,
  so the sanitizer's `_orphan_recovery` path never fires for it.

Fix (host-overlay RF-09):
* `_rf09_mark_pending_external` (tool_executor pre-dispatch) writes a durable
  `<hermes home>/tool_events/rf09_pending_external.json` marker BEFORE the dispatch
  and `_rf09_clear_pending_external` clears it only after the result persisted.
* On a restarted turn (`agent._rf09_restarted_turn = True`), a sequential call in
  the pending fence list is converted into an ``unknown`` `effect_disposition`
  result (`RF09_UNKNOWN_NOTICE`) instead of a re-dispatch.
* `finalize_turn` surfaces unanswered side-effecting calls
  (`_rf09_classify_unanswered_external`, using the engine's own
  `agent.tool_result_classification.tool_may_have_side_effect`) as
  `rf09_side_effect_reconciliation` with `side_effect_state = "unknown"` — the
  model decides; never a silent re-send.

Residual risk (honest): the concurrent path (`execute_tool_calls_concurrent`) is
NOT fenced in this round — the fence is installed on the sequential dispatcher,
which covers terminal/deploy/file tools; parallel batches have the same window
but no `pending_external` marker yet. The marker is also keyed per hermes home
(one live pending call), not per call id — a second parallel external call inside
the window would overwrite the marker row. Sequential-only fencing is the
smallest change that covers the criterion's own example shapes.

## W2 — after the segment cursor is written but before the RUN resumed (duplicate-resume)

Location:
* RF-08 writes an idempotent JSON cursor per (session, segment_index) under
  `<hermes home>/segment_continuations/` (`_rf08_record_segment_continuation`,
  turn_finalizer.py after RF-08's overlay; result attach at `finalize_turn`).
* Nothing in the pristine flow fences WHO acts on the pending continuation: two
  restarts (a scheduler retry + a gateway restart, e.g.) can both read the same
  cursor and both re-drive the segment — two simultaneous owners.

Why UNSAFE (pristine): the cursor documents WHAT to resume with but the RESUME
side has no claim. Even with the engine's own lease tooling
(`godsv/run_supervisor.py:739 claim_continuation` — conditional
`QUEUED -> CLAIMED` UPDATE as the exactly-once gate), that gate is out of scope
for the engine's in-process replay path; nothing imports it there.

Fix (RF-09):
* `_rf09_claim_resume` (turn_finalizer module scope): the resume-claim row lives
  in the SessionDB `state_meta` key `rf09:resume:<session>/<identity>` — the
  same store RF-08's goal identity reads, real cross-restart durability, no new
  lease system (adapted from run_supervisor's generation-claim pattern: a claim
  wins ONLY with a newer `owner_epoch`; same-or-older is a `CURSOR-MISMATCH`
  reject).
* The claim runs in `finalize_turn` AND at iteration start
  (`agent/turn_iteration_prep.py` — claim-before-resume, before any provider call).
* `_rf09_release_resume` never deletes the row; a late duplicate still loses to
  the recorded epoch.

## W3 — during RF-04/05 fallback swap (client swapped but messages not consistent)

Location: `agent/chat_completion_helpers.py:1865 try_activate_fallback` swaps
model provider in place; RF-05 (already landed) gates each candidate BEFORE the
swap (`_rf05_fallback_fitness_ok`), and RF-02's request-wide deadline bounds the
whole retry loop.

Why SAFE ENOUGH (already bounded): a crash DURING the swap is atomic at the
Python level (the swap itself is two attribute assignments; no intermediate
durable state). The messages the next attempt re-assembles are rebuilt from the
live session rows (`prepare_iteration` → `assemble_api_request`), so the swap is
idempotent across restarts. No RF-09 fence required here beyond the inventory;
adding a second swap lease would duplicate RF-05's own gate.

## W4 — between RF-06 compression and its reintegration

Location: `agent/context_compressor.py:4878` (`self._rf06_preserve =
self._rf06_collect_preserve(messages)` — snapshot BEFORE anything is dropped)
through `:4949` (`_rf06_reintegrate(self, compressed, ...)`).

Why SAFE (pristine): the preserve list is captured BEFORE the destructive prune,
and `_rf06_reintegrate` is deterministic — a crash between them is healed by the
next compression pass recomputing both from the live transcript (the user
correction is still in the messages until a PASSING summary replaced them). The
sub-window where the summary call already landed but the rows are still lost is
covered by RF-06's own verify probe; no second fence invented here.

---

## Fixes installed by RF-09 (summary)

| File | Edit |
|---|---|
| `agent/turn_finalizer.py` | `_rf09_*` helper block, per-turn stash reset, claims + reconciliation in `finalize_turn`, result fields `rf09_resume` / `rf09_side_effect_reconciliation` |
| `agent/tool_executor.py` | module fence helpers (`_rf09_mark_pending_external` / `_rf09_clear_pending_external` / `_rf09_seq_restart_fenced`), pre-dispatch marker, post-publish clear, restart fence before sequential dispatch |
| `agent/conversation_loop.py` | `result["rf09_resume"]` also stamped on the raw loop exit |
| `agent/turn_iteration_prep.py` | W2 claim-before-resume at iteration start |

Anchored on the installed engine; `--check` mode; `.gv-backup` on every touched
file; loud abort on anchor drift. Probe: `tools/host_rf09_crash_probe.py`
(RC=1 pristine → RC=0 patched, with a REAL `SIGKILL` helper kill in the W1
scenario). Pins: `tests/test_rf09_crash_windows.py` (anchors, idempotency,
drift-breaks-loud, red non-vacuity, composition with RF-08 in both orders, and
the green full-copy probe round trip with the real SIGKILL path).

## Honest limits

* The concurrent-batch executor path shares the W1 shape but is not marked;
  RF-09 fences the sequential dispatcher only.
* The REAL live-crash E2E against a real provider (kill a running turn mid-HTTP,
  restart against the same session) stays NOT_RUN — it needs the RF-01 budget
  release; the probe's crash injection is real at the PROCESS level (SIGKILL of
  a helper child) but does not drive a real provider call.
* The `owner_epoch` comparator uses wall-clock ns; two same-process claims in
  the same nanosecond tick resolve arbitrarily (threading discipline assumes
  one scheduler). Cross-PROCESS duplicates (the criterion's case) are correct
  because each restart's clock is later.
