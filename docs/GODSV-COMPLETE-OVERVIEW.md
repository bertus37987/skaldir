# Skaldir Agent — Complete Feature & Implementation Overview

Everything Skaldir Agent ships: what it does, where it lives, where you can see it.
Status legend: **live** (implemented, wired to real state, tested) · **partial** (implemented,
limited surface) · **open** (planned/designed, not implemented).

Repo: `~/projects/gods-voice` (private `bertus37987/gods-voice`) · Python 3.12 · 44 modules,
~12.3k lines in `src/godsv/` + the desktop plugin · tests: `tests/` (38 files) ·
Panel-API: `integrations/hermes/godsv_plugin/dashboard/plugin_api.py` ·
Desktop plugin: `integrations/hermes/godsv_plugin/desktop/plugin.js` ·
Hermes-Plugin (engine side): `integrations/hermes/godsv_plugin/__init__.py` · CLI: `godsvoice`.

---

## 1. Surfaces

| Surface | What it is | Entry |
| --- | --- | --- |
| **Native app** | A dedicated Hermes-desktop build (own userData, WM class, icon, embedded plugin). Chat + the Skaldir Agent panel live in the same window | `godsvoice desktop start`, binary `~/.hermes/godsv/app/linux-unpacked/gods-voice` |
| **Skaldir Agent panel** | Full control surface, sidebar route `/gods-voice` — 10 views in 4 groups | sidebar → Skaldir Agent |
| **Chat integration** | Live goal strip, event feed, `@goal`/`@tool` mentions, inspect drawer — inside every normal session | New session / any session |
| **CLI** | `godsvoice …` — same feature set headless | `godsvoice --help` |
| **MCP server** | Skaldir Agent exposes its tool-building + capability surface to other agents | `python -m godsv.mcpserver` |

Panel views (after the UI overhaul):
`CONTROL: Cockpit · Verification · Activity` — `INTELLIGENCE: Memory · Skills · Tools` —
`OPERATIONS: Models · Computer Use · Treasury` — `SYSTEM: System`.

---

## 2. The supervisor core

| Feature | What it does | Implementation | UI |
| --- | --- | --- | --- |
| **Smart Voice (watcher)** | Watches live sessions for `loop`, `error_burst`, `known_incident_repeat`, `hallucinated_api`, `diff_churn`, `stall`; pauses the agent, researches the blocked step, delivers one bounded redirect, resumes | `voice.py`, `monitor.py`, `pause.py`, `research.py`, `context.py`, `inject.py` (leases, heartbeat, watchdog) | Panel "Activity", chat feed (`VOICE` rows), cockpit "Voice activity" |
| **Pause → Research → Redirect → Resume** | The intervention pipeline end-to-end; redirect delivered exactly once; leases expire safely | `pause.py`, `inject.py`, plugin hooks (`pre_tool_call`…) | Activity view + chat feed + timeline |
| **Strict Completion Supervisor** | `WORKING → COMPLETION_REQUESTED → VERIFYING → VERIFIED_COMPLETE / INCOMPLETE → REDIRECT → WORKING`; `INCOMPLETE` is never terminal; only the gate runner may set `VERIFIED_COMPLETE`; 8 gates over real repo facts (evidence, tests, build, integration, fake-completion, security, cleanup, definition-of-done); fingerprint escalation; timeline, metrics | `completion.py`, `gates.py`, plugin `guard_pre_verify` | Panel "Verification", cockpit hero, chat strip (`GATE` rows), CLI `godsvoice completion` |
| **Goal & Pact** | Persistent goal contract (goal, acceptance criteria, constraints, DoD) outside the LLM context; survives compaction | `pact.py`, completion store (`requirements`) | Cockpit, Verification, chat strip |
| **Guard / claim checks** | Catches unbacked completion claims, contradiction between claim and later errors, appends honesty footers, one-shot legacy nudge | `guard.py`, plugin hooks | Panel "Activity", chat feed (`guard_nudge`) |
| **Incidents & memory** | Incident ledger feeding the voice (`known_incident_repeat`); notes store | `incidents.py`, `notes.py`, `memory` | Panel "Memory" |
| **Evals** | Episodes with expected signals, measured vs. estimated token accounting | `evalharness.py`, `demo.py` | Panel "Verification" (episodes + token metrics) |

## 3. Configuration & models

| Feature | What it does | Implementation | UI |
| --- | --- | --- | --- |
| **Model roles** | Per-role model assignment (watcher, investigator, judge, main agent) reusing the Hermes provider registry — no second provider stack; capability-gated overrides | `modelroles.py`, `llm.py` | Panel "Models" |
| **Registry origin** | Shows which provider/models come from the live Hermes registry (`~/.hermes/provider_models_cache.json`) | `llm.py` | Panel "Models" |
| **Model self-test** | Runs a real ping against a selected role/model | `llm.py`, panel `/models/test` | Panel "Models" |

## 4. Capabilities (tools, skills, mining)

| Feature | What it does | Implementation | UI |
| --- | --- | --- | --- |
| **Tool registry** | Installed + generated tools with usage ledger (calls, duration, errors — redacted) | `tools.py`, `toolusage.py` | Panel "Tools", CLI `godsvoice tools` |
| **Tool builder** | Proposes → builds → validates new tools; generated tools pass the same gates; negative tests required | `toolbuilder.py`, `toolscan.py`, `gates.py` | Panel "Tools" (build queue, detail) |
| **Capability mining** | Recurring requests/commands → tool candidates → promote into the builder pipeline (propose-first) | `capability_mining.py` | Panel "Tools", CLI `tools mine\|candidates\|promote` |
| **Capability requests** | The agent requests a missing capability; the request appears in the panel and can be fulfilled into a tool | `capability.py`, plugin hooks | Panel "Tools", MCP |
| **Skills** | Skill registry + promotion; consumed-skill surfacing | `skills.py`, `retrieval.py` | Panel "Skills" |
| **MCP server** | Exposes the tool-building surface to other agents (stdio + HTTP) | `mcpserver.py` | CLI / external agents |

## 5. Security & money boundaries

| Feature | What it does | Implementation | UI |
| --- | --- | --- | --- |
| **Treasury (staged money)** | Allowance/broker for test credentials; `secret://` handles only — the agent never sees keys; tool-bound grants, fail-closed; allowed domains; audit | `treasury.py` | Panel "Treasury" |
| **Secret scan** | Scans text/payloads for secret material (used by stores, payloads, diffs) | `secretscan.py` | gates + payload tests |
| **Spend ledger** | Journal of test-money spend intents (request/approve/deny/complete) — "just fake money", no real connections | `treasury.py`, `ledger.py` | Panel "Treasury" |
| **Spotlighting** | Untrusted content (web, files) is flagged so the model treats it as data, not instructions | `spotlight.py` | internal |
| **Origin-guarded mutations** | Every mutating panel endpoint checks origin, validates, and writes an audit event | `plugin_api.py` | internal |

## 6. Computer Use

| Feature | What it does | Implementation | UI |
| --- | --- | --- | --- |
| **CU policy & dry-run** | Policy-guided desktop actions; dry-run first; API > DOM preference; stop conditions; audit | `computer_use.py` | Panel "Computer Use" |
| **CU runner** | Executes attempts through the policy chain, records `cu_attempt/action/verified/failed/blocked` events | `computer_use.py`, command dispatch | Panel "Computer Use", chat feed (failures/blockers) |
| **CU vision** | Screenshot-based verification as the final escalation stage | — | **open** (policy/dry-run/API>DOM live; vision pending) |

## 7. Chat integration (inside every session)

| Feature | What it does | Implementation | Surface |
| --- | --- | --- | --- |
| **Session strip** | Compact goal state above the composer: state dot, `WORKING/INCOMPLETE/VERIFIED_COMPLETE/NO GOAL`, goal, `n/m` + blockers; click → cockpit. Renders while a goal is armed | `plugin.js` (`composer.top`) | Chat (all sessions) |
| **Live event feed** | Newest ≤3 important events (voice interventions, gate verdicts, guard stops, capability, vault/spend, CU) with source label, tone dot, relative age; backend filter `CHAT_KINDS` served by `GET /events` | `plugin.js`, `plugin_api.py` | Chat |
| **`@goal` mentions** | Rows in the `@` popover; a submit middleware turns `@goal …` lines into **blocking pact criteria** (`POST /completion/criteria`) | `plugin.js` (`composer.atCompletions`, `composer.middleware`) | Chat composer |
| **`@tool` mention** | Pin a capability for the task (inserts `@tool:`) | `plugin.js` | Chat composer |
| **Inspect drawer** | Click a feed row → evidence pane docked right: source, what happened, exact time/session/step/seq, verdict detail (status, blocking/major findings, evidence), payload pairs, cockpit link | `plugin.js` (`host.openWorkspace`) | Chat |
| **Chat empty face** | Branded empty-session state | `plugin.js` (`chat.empty`) | Chat |

## 8. Runtime & plumbing

| Feature | What it does | Implementation | UI |
| --- | --- | --- | --- |
| **Event store** | Append-only SQLite/JSONL event log — single source of truth for panel, CLI, voice, chat feed; `KINDS` registry; secret screening on append; `CHAT_KINDS` for the chat | `events.py` | all surfaces |
| **Panel API** | Read-mostly aggregation (`/summary`, `/completion`, `/events`, `/models`, `/tools`, `/memory`, `/skills`, `/treasury`, `/cu`, `/evals`) + guarded mutations (`/completion/*`, `/goal`, `/tools/*`) | `plugin_api.py` | panel |
| **Night shift** | Bounded background improvement runs | `nightshift.py` | Panel "System" |
| **Metrics** | Usage/quality counters (completion requests/rejections, redirects, guard checks, token accounting) | `metrics.py` | Panel (Verification, System) |
| **App build** | Rebuilds the native app from the Hermes source mirror + overlay (theme, brand, plugin, instance identity) | `desktop.py`, `appbuild.py`, `app-overlay/patch_app.py` | `godsvoice desktop build-app` |
| **Doctor** | Environment self-check | `cli.py` | `godsvoice doctor` |

## 9. Verified evidence (what proves what)

| Claim | Proof |
| --- | --- |
| Supervisor state machine | `tests/test_completion.py` (incl. `add_criteria`), `tests/test_completion_loop_e2e.py` (two rejections → auto-resume → `VERIFIED_COMPLETE`; crash safety; self-certification regex guard) |
| Chat feed & drawer | `tools/check_desktop_plugin.mjs` (91 checks incl. feed + drawer), real-app `tools/gv_ui_e2e.mjs` (all checks passed) |
| Panel API | `tests/test_panel_api.py` (feed filter/cursor, treasury, summary) |
| Secrets stay sealed | payload tests + DOM scan in the app E2E + redaction in usage ledger |
| Rebuild reproducibility | `godsvoice desktop build-app` (mirror + overlay + pack, exit-asserted) |

## 10. Open / next (honest)

- **Chat UI v6** (this round): voice always visible (even without a goal), editable goal, estimated
  completion signal, approval-mode control in the composer, smaller centered composer geometry.
- CU vision (screenshot verification stage), GNOME icon sign-off, live provider smoke.
- Settings/Treasury split as top-level destinations (v4 of the chat plan).
