# Chat UI v6 — "The voice is always there" (request synthesis & plan)

Willy's ask, translated. Then the design decisions and the implementation plan. Each item gets
run through the standing rule: **already built? → visible in the UI? → yes: skip; no: build it
cleanly.**

## What he means (researched)

**"Komplettes live working UI im normalen Chat, immer wenn man New Session tippt"**
In every session — including blank new ones — the chat itself must show: the Voice (the watcher
IS the voice), the goal, tool creation, interruptions, and the streaming work. This matches the
OpenClaw-UIs people build ("responses, reasoning blocks, tool-use, file edits stream in chat as
they happen") and Codex Desktop, where the composer carries a **permission-mode control** and the
session chrome stays minimal. Skaldir Agent should feel like an intelligence layer *inside* the
session, not a place you go.

**"Man sollte das Goal und eine estimated time sehen, das Goal editieren können, erhält the voice
und der main agent"**
- The goal is a first-class citizen in the chat (visible, editable inline).
- There must be a time signal. We do NOT invent fake ETAs. Honest ladder:
  1. **elapsed since goal start** (always available — we add `started` to the goal record);
  2. **estimated remaining** only when the history has ≥3 comparable verifications → average
     time-to-verify × remaining requirement fraction, labeled `~est`; otherwise `estimating…`.
- Voice and Main Agent must be visually distinct: the Agent speaks as the normal assistant;
  the Voice speaks as its own labeled layer (gold `VOICE` chip, paused/redirecting states).

**"Chat UI etwas anders — kleinere, zentriertere, kürzere, weiter vom Boden abgerundete Chat-Bar
mit Approval-Modes"**
- Composer: centered column, capped width, floating (more distance from the bottom edge), softer
  radius. Values: max-width ≈ 46rem (vs. current full-bleed), bottom offset ≈ 2.5–3rem, radius
  up one step. Implemented as token overrides (styles.css) where possible.
- **Approval modes are real in Hermes**: `/approvals [manual|smart|off]`
  (`hermes_cli/approval_mode.py` → `approvals.mode` in config.yaml, currently `off`).
  The composer gets a small mode pill (Manual · Smart · Off) backed by a Skaldir Agent API
  (`GET/POST /approvals`) so the chat can show and switch the *actual* engine mode — no fake
  toggle. Reads/dispatches go through the panel API (origin-guarded, tested).

**"Auch im richtigen Hermes sichtbar — UI und Extension"**
- The plugin (chat strip, feed, mentions, drawer, panel) loads in **any** Hermes desktop that
  has the godsv plugin — the disk door `~/.hermes/plugins/godsv/desktop/plugin.js` is wired, so
  the normal Hermes gets everything on its next start (verified in this round).
- Composer geometry + approval pill ship as plugin contributions where the host allows it
  (`composer.top/bottom/leading/actions`), with token-level CSS as the fallback path inside the
  GV app build. Nothing in this round requires forking the app core.

**"Der Watcher ist die Voice"**
Terminology fix across surfaces: watcher = the voice process. UI copy uses "Voice" for the
watcher, its pauses, research, redirects and verdicts. Panel/CLI strings already say Voice.

## Gap board (built → visible → action)

| Capability | Built | Visible today | Action |
| --- | --- | --- | --- |
| Voice interventions inline | yes | feed rows (`VOICE`) | keep |
| Goal in chat | yes (strip, if armed) | only when a goal exists | **always render**: no-goal state with one-line `@goal` hint |
| Goal edit | criteria append (`@goal`) | not editable inline | **inline editor** on the strip + `POST /goal` |
| Elapsed / estimate | timestamps exist | not shown | **`started` on the goal record; strip shows `elapsed 12m`; `~est` once history allows** |
| Approval modes | `/approvals` + config | only as a slash command | **mode pill in the composer via `GET/POST /approvals`** |
| Tool creation inline | capability requests + builder | feed row `TOOLS` only | later: created/verified rows once builder emits them (already in `CHAT_KINDS` path) |
| Composer geometry | token system (`--composer-*`) | default full-width bar | **centered, capped width, floating, more radius** |
| Streaming visibility | — | plain streaming today | later: compact grouped activity line under the strip (§7 of the earlier brief) |

## Verification in the normal Hermes (the no-kill rule)

The normal desktop app (`~/.hermes/hermes-agent/apps/desktop/release/linux-unpacked/Hermes`)
IS the live session UI — **never kill/restart it from an agent turn** (observed: the turn
aborts, once as a full stack respawn). Read-only load-proof instead:

- `~/.hermes/desktop-plugins/godsv/plugin.js` is a **symlink into this repo** — the normal
  app reads the live file at its next start (its rescan watch picks up agent writes).
- `~/.hermes/logs/agent.log` logs `Mounted plugin API routes: /api/plugins/godsv/` plus
  `plugin_capabilities` checks on every app start — verified 2026-09-12: app up 19:57:43,
  routes mounted 19:57:50, with the v4+v5 plugin code.
- The composer geometry/chat UI itself is proven live in the **GV instance** (same Electron
  build, same plugin file, own userData + debug port). A DOM-level look inside the normal
  app requires a user-initiated restart with `--remote-debugging-port` — the user's move.

## Implementation order

1. **`chat-ui-v4-voice-visible`** (this round): always-on voice line + no-goal hint; `POST /goal`
   (edit goal text); `started` timestamp + `elapsed` in the strip; backend `GET/POST /approvals`
   + composer mode pill; tests + real-app screenshots.

**Observed during live testing (approvals):** a `POST /approvals` write lands in
`~/.hermes/config.yaml` immediately (verified), but some other engine process reverted the key to
`off` within ~10s in one test run. The UI is built to stay honest under this: the pill shows the
requested mode right after a successful POST, then converges to whatever the next server read
says (≤5s interval). UI state and runtime state always agree — the pill is a *view* of the real
key, never a local flag.
2. **`chat-ui-v5-geometry`**: centered/capped/floating composer (tokens), responsive pass,
   polish items from v3 (shorter drawer titles, denser empty drawers).
3. **`chat-ui-v6-estimate`**: `~est` completion estimate from the completion history (only with
   enough data), grouped tool-activity streaming line.
4. **`chat-ui-final-candidate`**: §29 demo run end-to-end + docs refresh.

Rollback: every stage is its own tag on `godsv-chat-ui`; nothing is force-pushed.
