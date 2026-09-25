# Chat-Integration — Architecture & Checkpoint Plan

Goal: **Skaldir Agent becomes part of the normal Hermes session experience** — not a panel you
occasionally open. The chat itself becomes the live cockpit (voice interventions, verification,
goal state, tool activity inline); configuration moves to Skaldir Agent Settings; secrets stay in
Skaldir Agent Treasury.

Branch `godsv-chat-ui` (baseline tag `chat-ui-baseline`). Every checkpoint: build → launch real
app → inspect → screenshots → commit → tag.

## The extension surface (no core forks)

The desktop app already exposes every seam we need through `@hermes/plugin-sdk` +
`src/contrib/*`. Our plugin (`integrations/hermes/godsv_plugin/desktop/plugin.js`) registers
into the SAME registry schema as core UI:

| Need | Seam | Notes |
| --- | --- | --- |
| `@goal` mention | `COMPOSER_AREAS.atCompletions` | plugin-provided rows merged into the `@` popover (hermes-bots does the same) |
| `@goal` → pact | `COMPOSER_AREAS.middleware` | ordered async chain around submit; may rewrite/pass/cancel the draft |
| Session status strip (● WORKING · goal · 7/10) | `COMPOSER_AREAS.top` | banner strip above the composer — compact, clickable |
| Inline voice events / verification | `COMPOSER_AREAS.top` / `underside` (v1), live WS feed (v2) | keep it quiet — collapsed rows, `[Inspect]` opens the rail |
| Inspect drawer | `PANES_AREA` (+ `host` open-pane) | right-side pane, deep evidence: reason, evidence, files, metadata |
| Session list voice state | `SessionStatusDot` (SDK export) | subtle dot states per session row |
| Settings / Treasury top-level | `ROUTES_AREA` + `SIDEBAR_NAV_AREA` | existing panel views become "Skaldir Agent Settings" + "Skaldir Agent Treasury" |
| Model-graded directives in transcripts | `TRANSCRIPT_DIRECTIVE_AREA` (`::name{...}`) | only for model-addressed leaves; NOT for live voice events |

Plugin loading: **runtime plugins load live from disk** —
`<hermes home>/plugins/<name>/desktop/plugin.js` (this is exactly where the deployed godsv
plugin lives; rewriting it hot-reloads: "agent rewrites a plugin file → clean reload").
No 100 s rebuild needed for plugin-side iteration; the `desktop build-app` path bakes the
same file into the packaged app for release.

Backend door: `ctx.rest('/…')` → `/api/plugins/godsv/…` (the existing FastAPI panel API);
`ctx.socket('/…')` → live WebSocket twin for event streaming. Everything the UI shows must
come from these real stores — no mock state (§26).

## Real state sources

- `GET /completion` — state machine, goal, requirements ✓/✗, gates, timeline, metrics
- `GET /summary` — kinds, recent events, pauses, sessions
- `POST /completion/goal|verify|override` — supervisor actions
- NEW `POST /completion/criteria` — append acceptance criteria to the active goal (for `@goal`)
- NEW `WS /events` — tail of the event store (voice.*, tool.*, completion.*) for live UI

## Checkpoint plan

| Tag | Scope |
| --- | --- |
| `chat-ui-baseline` | done — pre-integration state (cockpit shell + verification views) |
| `chat-ui-v1-mentions` | @goal completions + middleware → pact; criteria API; status strip (composer.top) |
| `chat-ui-v2-events` | live WS event feed, quiet grouped voice/completion rows, `[Inspect]` |
| `chat-ui-v3-inspect` | PANES_AREA inspect drawer with evidence detail |
| `chat-ui-v4-settings` | "Skaldir Agent Settings" + "Skaldir Agent Treasury" as top-level destinations; models/credentials/product boundary |
| `chat-ui-v5-polish` | composer geometry pass (rounded-rect, calmer), responsive sizes, empty/loading/error states |
| `chat-ui-final-candidate` | E2E demo session (§29), screenshots, docs |

## Product boundary (final mental model)

```
CHAT      = live operational cockpit (goal, voice, verification, tools — inline, quiet)
INSPECT   = deep evidence for one event (right pane)
SETTINGS  = configuration + advanced management (models, policies, MCP, computer use)
TREASURY  = credentials + secret:// handles + grants + audit
```

## Design language for the chat surfaces

Codex-style calm, reinterpreted in black + GV gold: rounded surfaces, restrained hierarchy,
compact grouped tool activity, single-gold accents for voice/verification moments only, no
dashboard-card walls in the transcript. Tokens over literals (`--ui-*`, `--ui-stroke-tertiary`
in-transcript), `WIDGET_SHELL_CLASS` for inline panels, `Button`/`Badge`/`StatusDot` primitives
from the SDK — same rules as the rest of the app (DESIGN.md).
