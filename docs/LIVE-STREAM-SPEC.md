# Skaldir Agent — Live-Operations-Streaming in die Hermes-Session (verbindliche Spec)

> Kernregel: **„If something is happening right now to the current task, it should be
> able to appear live inside the conversation."** Konfiguration, Historie und
> Tiefen-Diagnostik bleiben außerhalb des Chats.

## Was live in die Konversation gestreamt wird (kompakt, echtzeitfähig)

- aktuelles Goal / Goal-Fortschritt · `@goal`-Updates · aktueller Task/Fortschritt des Main Agents
- Skaldir Agent-State: `WATCHING / INVESTIGATING / REDIRECTING / VERIFYING`
- Goal-Drift / Scope-Creep / unsupported-claim Warnungen
- Pause → Research → Redirect → Resume
- wichtige Tool-Calls · Tool-Erstellung / Capability Mining · Testing-Tools
- Computer-Use-Meilensteine · relevante Memory/Incident-Treffer
- Verifikations-Fortschritt · Completion-Rejection · automatische Fortsetzung
- final `VERIFIED_COMPLETE`

**Kein Chain-of-Thought.** Ereignisse kompakt: **gruppierte, sich aktualisierende
Blöcke** statt vieler Nachrichten — dasselbe Event wird bevorzugt ge-**updated**:

```text
SKALDIR AGENT
⚠ Goal drift detected
Main Agent paused · Investigating…

→ Evidence found
→ Redirect delivered
✓ Agent resumed
```

Tool-Aktivität kompakt (`⌁ Read 6 files` / `⌁ Ran 3 tests` / `⌁ Browser checked API docs`);
Details hinter **Inspect**.

## Completion-UI (direkt im Chat)

```text
SKALDIR AGENT · VERIFYING
Goal          8/10
Build         ✓
Tests         ✓
Runtime       ✓
UI            ●
Persistence   ●
Regression    ✓
```

```text
✕ COMPLETION REJECTED
2 blockers remain
Main Agent resumed automatically.
```

Erst nach unabhängiger Verifikation: `✓ VERIFIED COMPLETE — 10/10 requirements verified`
— **dann** darf der Task enden.

## Tool-Creation-UI · Computer-Use-UI

```text
Capability detected → Building Testing Tool → Tests → Negative test
→ Voice verification → ✓ VERIFIED TOOL        (danach real verfügbar)
```

```text
Computer Use: Opened Settings → changed model → restarted app → ✓ state verified after restart
```

Screenshots/DOM/AT-SPI/Aktionen → `Inspect`.

## Inspect-Drawer (rechts)

Für jedes wichtige Live-Event: evidence · tests · files · tool I/O · screenshots ·
Voice-Findings · confidence · Event-Metadaten. **Der Chat bleibt sauber.**

## Persistente Session-Statuszeile

```text
● WORKING · Model Routing · 7/10        ◉ VERIFYING · Model Routing · 9/10
```

Klick → Goal-Details.

## Außerhalb des Chats

- **Skaldir Agent Settings** — Modelle/Provider · Voice-Verhalten · Completion-Policies ·
  Tools/MCP · Computer Use · Memory/Skill/Tool-Mining · Advanced Runtime.
- **Skaldir Agent Treasury** — API-Keys · Secret-Handles · Grants · erlaubte Domains ·
  Revoke/Replace/Delete · Credential-Audit.
- **Inspect / History** — tiefe Event-Details, vollständige Logs, historische Runs, Debugging-Interna.

## Grundprinzip

```text
CHAT      = live operational state
INSPECT   = deep evidence for a specific event
SETTINGS  = configuration
TREASURY  = secrets and grants
```

Der Chat bleibt ein normaler, hochwertiger Hermes-Chat — Skaldir Agent ist darin
**natürlich eingebettet als lebendige Supervisor-Schicht**. Es darf sich **nicht**
wie ein separates Extension-Panel anfühlen, sondern wie:

> „Hermes is working, while Skaldir Agent is visibly supervising, correcting, testing
> and verifying the work in real time."
