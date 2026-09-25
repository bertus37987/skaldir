# Experience v6 — Upgrade-Plan (Willys Auftrag vom 12.09.2026)

> „Upgrade zum v6 Stand — erstmal alles auf die aktuelle Version upgraden. Mich nervt
> diese Plugin-Tab Skaldir Agent wirklich — das soll so tief in die App integriert
> werden: nicht mehr ‚Skaldir Agent ist ein Plugin von Hermes', sondern **Skaldir Agent
> ist ein Update von Hermes** — eine neuere, modernere Desktop-Experience. [Mit
> Research-Ergebnissen.] Neuer Hintergrund: das Logo aus dem CLI-Tool. Wenn die Voice
> etwas macht: als permanentes, schließbares Pop-up **im Chat**, nicht in der Chat-Bar.
> Performance: bei langen Chats entsteht Input-Delay — beheben (nicht immer die ganze
> Konversation rendern)." — dazu das Settings-Overhaul (eigener Abschnitt unten).

## Stränge und Status

| # | Strang | Ist | Ziel | Status |
|---|---|---|---|---|
| 1 | **App-Integration** (kein Plugin-Tab) | In der GV-App: 1 Sidebar-Eintrag „Skaldir Agent" + zweite Nav-Ebene in der Page; im normalen Hermes derselbe Einzeleintrag | GV-App: die Bereiche sind **native Sidebar-Ziele** (Cockpit/Verification/Activity/Tools/Settings/Treasury, je eigene Route, keine zweite Nav-Ebene, kein „Plugin"-Eintrag); normaler Hermes unverändert ruhig | **umgesetzt** (UA-Gate `God'sVoice/`, Integrations-Modus; Tools am 12.09. als sechster Bereich ergänzt) |
| 2 | **Upgrade / v6-Stand** | Version 1.4.0 im Repo; V6-Plan (`CHAT-UI-V6-PLAN.md`) teils offen (`~est`, Aktivitätszeile, final candidate) | Alles auf aktuellen Stand: Hermes-Basis-Mirror aktualisieren + v6-Reste schließen + Version heben | offen |
| 3 | **Research** (OpenClaw v2 / Codex / Settings-UX / Chat-Performance) | — | Konkrete, quellenbelegte Design-Findings, in die Umsetzung eingearbeitet | **läuft** (3 Subagenten) |
| 4 | **Hintergrund mit CLI-Logo** | ANSI-Blockkunst (`~/gods-voice-cli-logo-art.ansi`, 24-bit Gold) nur im CLI | Logo als dezenter App-Hintergrund (aus der ANSI-Art gerendert, z. B. SVG) | offen |
| 5 | **Voice-Aktionen als Chat-Pop-up** | Voice-Ereignisse als kompakte Zeilen im Strip über dem Composer + im Feed | Permanentes, **schließbares Pop-up im Chat** (nicht in der Composer-Bar), solange die Aktion relevant ist | offen |
| 6 | **Performance lange Chats** | Vollständiges Transkript re-rendert; Input-Delay | Composer-Eingabe entkoppelt vom Transkript-Rendering (Virtualisierung/Memo), messbar belegt | offen |
| 7 | **Settings-Overhaul** | Panel-Tabs mit Teilabdeckung | **Skaldir Agent Settings = einzige Konfigurationsfläche** (Modelle/Rollen, Provider + Custom-Endpoints, Voice-Strenge, Tools/MCP/CU, Memory/Mining, Advanced); Keys NUR in der Treasury (Handles `secret://…`), Never-Reveal | offen |

## Settings-Overhaul — Zielbild (verbindlich)

- **Ein Ort:** Skaldir Agent Settings konfiguriert das System:
  - Main Agent / Watcher / Investigator / Judge / Fallback-Modelle
  - Provider und custom OpenAI-kompatible Endpoints
  - Voice-Strenge und Verifikationsverhalten
  - Tools / MCP / Computer Use
  - Memory, Skill Mining, Tool Mining, Testing-Tool Mining
  - Advanced Runtime
- **Keys:** hinzufügen, ersetzen, testen, entfernen **aus der UI** — die Keys landen
  **ausschließlich** in der Skaldir Agent Treasury; Settings hält nur Handles
  (`secret://openrouter-main`). **Kein zweiter Secret-Store, nie Klartext anzeigen.**
- **Treasury bleibt separat:** Credentials, Secret-Handles, Tool-Grants, erlaubte
  Domains, Revoke/Replace/Delete, Credential-Tests, Audit.
- **Produktgrenze:** `Chat = live work · Skaldir Agent Settings = configuration ·
  Skaldir Agent Treasury = secrets and grants`.
- **Alle Controls ändern echten Runtime-State** (kein Fake-Toggle; Pill/Views sind
  Sichten auf die echte Konfiguration).

## Beweis-Matrix (je Etappe)

- Repo: Unit-Tests je Modul; volle Suite; ruff; `node tools/check_desktop_plugin.mjs`.
- Echte App: `node tools/gv_ui_e2e.mjs 9333` (erweitert je Etappe) + Screenshots in
  `ui-artifacts/` + Vision-Abnahme.
- Normaler Hermes: read-only Load-Proof (Symlink + `agent.log`); visuell nur auf
  Nutzer-Neustart.
