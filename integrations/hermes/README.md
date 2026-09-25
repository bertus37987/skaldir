# Hermes integration — Skaldir Agent on the Hermes host

**Status: plugin adapter implemented; verified live (2026-09-11)** — the shadow smoke test,
whisper delivery and the thread-safety fix are backed by real sessions (see `docs/STATUS.md` §V0
and the audit log in `docs/SECURITY.md`). The default mode stays `shadow`.
Base checkout: `~/.hermes/hermes-agent` · pinned to commit `c8aa5608c2` (Sep 2026) ·
upstream fork for PRs: `bertus37987/hermes-agent`.

## Plugin contract (verified against `plugins/AGENTS.md`)

- **Ordinary plugin:** a directory with `plugin.yaml` + `__init__.py` and a `register(ctx)`
  function. Install locations: `~/.hermes/plugins/<name>/`, `./.hermes/plugins/`,
  pip entry points. **Plugins never touch the core.**
- **Hooks:** `pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`,
  `on_session_start`, `on_session_end` → registered via `ctx.register_hook(name, fn)`.
- **CLI:** `ctx.register_cli_command(...)` (shows up as a `hermes …` subcommand).

## Mapping Skaldir Agent ⟶ Hermes

| Skaldir Agent | Hermes attachment point |
|---|---|
| Event store | `on_session_start`: open the store under `$HERMES_HOME/godsv/events.db`; `post_tool_call`/`post_llm_call`: append `tool_call`/`tool_result`/`thought` events (payloads pass through the secret gate) |
| Step monitor + Voice | after every append: `monitor.ingest()` → `voice.consider()` |
| Hint delivery | write hints to `$HERMES_HOME/godsv/inbox.jsonl`; delivery into the running session is the **critical open point** (see below) |
| Pact/ledger | load on `on_session_start`; closing review on `on_session_end` (night-shift stub) |
| CLI | optional `ctx.register_cli_command` → `hermes godsv status|pact|ledger` |
| Shadow mode | default until validated: hints are logged but not delivered |

## Delivery mechanism (verified against `website/docs/user-guide/features/hooks.md`)

Hooks receive keyword arguments (`**kwargs` must always be accepted); observer returns are
ignored, transform hooks accept the first string as a replacement. Hot-path hooks
(`post_tool_call`, `pre_llm_call`, `pre_tool_call`) are timeout-bounded (default 30 s;
observers fail open) — our monitor runs in microseconds; callback errors are logged and
skipped.

**Verified hook payloads (excerpt):**

| Hook | Type | Relevant fields |
|---|---|---|
| `post_tool_call` | Observer | `tool_name`, `args`, `result`, `task_id`, `session_id`, `tool_call_id`, `turn_id`, `duration_ms`, `status`, `error_type`, `error_message` |
| `pre_llm_call` | Directive | `session_id`, `turn_id`, `user_message`, `conversation_history`, `is_first_turn`, `model`, `platform` — returns (a string or `{"context": ...}`) are injected into the user message; fires **once per turn, before the loop** |
| `post_llm_call` | Observer | `session_id`, `turn_id`, `assistant_response`, `conversation_history`, `model` |
| `transform_tool_result` | Transform | like `post_tool_call`; the first string return replaces the tool result **before the conversation append** |
| `on_session_start` / `on_session_end` | Observer | session lifecycle |
| `pre_verify` | Directive | verify gate for edited files (`changed_paths`, `attempt`) |

**Delivery design (mid-loop, cache-safe) — implemented & verified live:**

- **Primary channel:** `transform_tool_result` — the `[voice …]` block is appended to the tool
  result **additively** (original result + hint). No role/history rebuild, cache-safe. Observed
  order in v0.21.1: transform fires **before** `post_tool_call` of the same tool use → a hint
  from call N rides on the result of call N+1. Delivery is audited as a `hint_delivered` event
  in the store.
- **Turn start (planned):** `pre_llm_call` with `{"context": ...}` for turn-level summaries
  (ledger excerpt, open questions).
- **Durable (planned):** "cache-safe system prompt section" for `main_agent_steering`.
- **The default stays `shadow`** (hints only in the inbox log); `whisper` is opt-in via
  `GODSV_MODE=whisper` and validated live.
- **Evidence from memory:** hints carry evidence from the incident/note store
  (`find_evidence`: exact signature → FTS5 → similarity; notes spotlighted as untrusted).
  Verified live on 11.09. (exact-match evidence in the hint, session `…165731_1fe245`).
- **LLM synthesis (optional, `GODSV_LLM=1`):** the template hint is refined by
  `voice_synthesis` through `ctx.llm` — on a daemon thread, so the hook never waits for the
  model (measured live: host call ~25 s despite the `timeout` kwarg → synchronously it would
  have stalled the loop). Finished before the next delivery point → upgrade; otherwise the
  template goes out (nothing is lost). Verified live on 11.09. (session `…172239_4b76bd`:
  `held-for-enrichment` → delivery ~1 s later with evidence).
- **Triage gate (`GODSV_TRIAGE=1`, needs `GODSV_LLM=1`):** suspicious loop signals are checked
  by the LLM; an explicit "not stuck" (confidence ≥ 0.6) suppresses the hint. Fail-safe:
  without an explicit verdict the hint stays. Live on 11.09.: false alarm suppressed within
  3 s (session `…172854_29c5bf`; `hint_triage`/`hint_suppressed` events, inbox
  `"suppressed": true`).
- **Evidence judge:** fuzzy incident hits pass through `same_problem_judge` before delivery;
  exact hits and research notes never do; judge errors are fail-open
  (`evidence_judged` events).

**Release channels (11.09., implemented & verified live):**

- **Completion guard:** `pre_verify` (continue nudge until evidence exists; one-shot,
  `attempt`-gated, capped by `max_verify_nudges`) and `transform_llm_output`
  (additive `[voice·guard]` footer on the final text — reaches the agent even **without**
  another tool call). Evidence rule: only execution tools after the last change count
  (write tools never); a second LLM opinion is additive only, fail-open; in shadow mode it is
  observation only. Live: `pre_verify NUDGE` → the agent verifies afterwards → footer.
- **Pause/redirect:** `pre_tool_call` blocks **one** tool call when a redirect is ready
  (`{"action": "block", "message": <redirect>}`) — before execution, so no inconsistent state.
  Lease + heartbeat + watchdog + exactly-once delivery; research runs on a daemon thread (the
  hook never blocks indefinitely). Live: `pause_requested → research_done → pause_delivered`
  (27 s, session `…183209_ec14e0`; the transcript contains the redirect; exactly one block).
- **file_edit mapping:** `write_file`/`patch` → `file_edit` events (slop/scope/dependency now
  run live); tool results are normalized (`exit_code`/`stdout`/`error` out of the JSON string).

**Desktop (unified package, 12.09.):**

- **Launcher:** `godsvoice desktop install|status|start|uninstall` — user-local app-menu entry
  "Skaldir Agent" (absolute exec path, no shell), hicolor icons, deployment check before the
  start; `godsvoice doctor` as the health check.
- **Panel:** `desktop/plugin.js` (ESM, uncompiled; sidebar row, full page `/gods-voice`,
  ⌘K command) + `dashboard/plugin_api.py` → `/api/plugins/godsv/` (`/health`, `/summary`).
  **Read-only**: aggregates `events.db`/`pauses.db`/optional `treasury.db` (test money) — no
  vault access, no write paths, defensive against missing sources. Opt-in in Settings → Plugins
  (like the Python plugin).

**Operation & debugging:**

| Environment variable | Effect |
|---|---|
| `GODSV_MODE` | `shadow` (default) · `whisper` (delivery) · `off` (kill switch) |
| `GODSV_LLM=1` | enable Voice-v2 enrichment through `ctx.llm` (default off) |
| `GODSV_TRIAGE=1` | triage gate for loop signals (needs `GODSV_LLM=1`; default off) |
| `GODSV_GUARD` | completion guards: `pre_verify` nudge + final footer (`1` default; `0` off) |
| `GODSV_GUARD_FORCE=1` | channel test: forces guardian nudge/footer (smokes only) |
| `GODSV_PAUSE` | pause→research→redirect enabled (`1` default; `0` off) |
| `GODSV_PAUSE_WAIT` | max. seconds a tool call waits for the redirect (default 6, cap 20) |
| `GODSV_PAUSE_TTL` | lease TTL in seconds (default 90) |
| `GODSV_LLM_TIMEOUT` | timeout kwarg for the host call (default 8 s; advisory in v0.21.1) |
| `GODSV_LLM_GRACE` | seconds a held hint waits for the synthesis (shadow mode; default 12) |
| `GODSV_DEBUG_TRACE=1` | trace lines to `$GODSV_TRACE_FILE` (default `<tmpdir>/godsv_plugin_trace.log`) |
| `HERMES_HOME` | root for `godsv/events.db` + `godsv/memory.db` + `godsv/inbox.jsonl` |

- Threading: hooks run on Hermes worker threads; `EventStore`/`IncidentStore`/`NoteStore` are
  thread-safe (`check_same_thread=False` + RLock), `observe()` is serialized per session;
  synthesis threads patch their hint only under the observe lock — fixes after a silent event
  outage and a latency finding, see the audit log in `docs/SECURITY.md`.
- Health check: `hermes plugins doctor godsv` (runtime discovery + registration) and
  `hermes plugins show godsv` (status/listens).

**Next steps:**

1. Skill consumption ✅ implemented (`promoted` only, once per session); observe across more sessions.
2. A `hermes godsvoice …` subcommand (status/metrics/nightshift) via `ctx.register_cli_command`.
3. `pre_llm_call` for ledger/turn summaries.
4. A real embedding provider (API/local) instead of HashingEmbeddings.
5. Readjust the pause thresholds against the false-alarm numbers from real sessions.
