# Research-Inputs für Experience v6 (12.09.2026)

Drei unabhängige Recherchen (OpenClaw v2 / Codex Desktop · Provider-/Key-UX ·
Chat-Performance) mit Quellen fassen zusammen, was „integriert und modern" konkret
bedeutet. Jede Zeile ist als umsetzbare Richtung formuliert.

## A. Layout, Navigation, Status (OpenClaw v2 · Codex Desktop)

- **Chat-first, Drei-Zonen:** Session-/Projektliste links, Gespräch in der Mitte,
  kontextsensitives Pane rechts (Diff/Aktivität); das rechte Pane ist zu- und
  aufklappbar. Codex-Reviews: „erste GUI für agentisches Coden, die sich wie ein
  Fortschritt anfühlt"; OpenClaw 2.0 hat die Dashboard-Startseite **abgeschafft**
  und die UI bewusst an ChatGPT/Claude/Gemini angelehnt.
- **Dockbares Tab-Panel pro Session** (OpenClaw: Terminal/Browser/Files/Tasks/Review
  als Tabs, Position rechts/unten, per-Session gemerktes Layout). → Unser
  Inspect-Drawer ist der erste Baustein; Tab-Bündelung ist die Ausbaustufe.
- **System-Eingriffe gehören in den Thread:** Approval-/Berechtigungsfragen als
  interaktive Karten **in der auslösenden Konversation** (Codex „Ask for approval";
  OpenClaw mit rollierender Historie) — nie als beziehungsloses Modal. → Deckt sich
  mit unserer Live-Block-/Inspect-Richtung.
- **Ruhige, konsistente Status-Signale:** Unread-Dot, Activity-Ring (spinnt bei
  Arbeit), Attention-Icon mit TTL; OpenClaws „Session Rail" fasst den Run-Status als
  Pill mit Live-Digest zusammen und expanded **einmal** automatisch, wenn ein Run
  festhängt oder Input braucht (400-px-Spalte rechts auf breiten Panes).
- **Session-Statuszeile:** klein, konsistent, klickbar (unsere „● WORKING · … · n/m").
- **Start-/Performance-Budget:** OpenClaw 2.0: 575 ms Start (statt 1,6 s), 140→45
  JS-Requests; Nicht-Chat-Panes lazy mit Skeletons.
- **Notifications granular & ruhig:** Completion-/Approval-Notifications nur im
  Hintergrund; maximal **ein** dismissibler Event-Chip pro Besuch; Klick darauf löst
  eine echte Chat-Anfrage aus (kein Toast-Spam).
- **Voice first-class:** globaler Diktat-Hotkey (Codex Ctrl+M, Push-to-Talk),
  Wake-Words + Talk-Mode + Quick-Chat-Bar (⌥Space) bei OpenClaw; bei Code-Antworten
  nur ein Sprech-Hinweis statt Vorlesen.
- **Power-Pfade ab v1:** Command Palette, vollständiger Shortcut-Editor,
  Keyboard-First-Navigation, Density-Einstellung (Kritik „not for power users" ernst
  nehmen).
- **Fedora/Wayland:** Floating Windows, Fokus und globale Shortcuts haken unter
  nativem Wayland (Codex-Linux-Preview) — früh auf XWayland/Wayland testen.
  *(Passt zu unserer Wacom-Lehre: X11 nötig.)*

## B. Provider-/Key-UX (Raycast, Vercel, Zed, LM Studio, OpenRouter, LiteLLM, …)

- **Verify-vor-Save:** Key beim Hinzufügen real gegen den Provider testen
  („Test Key"); Feedback im Setup-Moment statt Laufzeitfehler später.
- **Model-Auto-Discovery** nach dem Connect (Katalog vom Endpoint laden) — Nutzer
  tippen keine Modell-IDs.
- **Provider-Picker + Deep-Link** zur Key-Console des Anbieters; eigene Felder je
  Provider aus dem Hauptflow heraushalten.
- **Secret-Handles statt Klartext** (1Password `op://…`, n8n, Continue
  `${{ secrets.… }}`, Infisical) — die Settings-Schicht hält nur stabile Referenzen.
  **Genau unser `secret://…`-Muster.**
- **Write-only-Secrets:** Key nur einmal bei Erstellung sichtbar (LM Studio:
  „will not be shown again") — kein Eye-Icon, kein Copy für Gespeichertes.
- **Lifecycle:** Deaktivieren-Toggle (ohne Löschen), Entfernen, Rotations-Deep-Link;
  **Staged Rotation** als First-Class-Flow (neu hinzufügen → priorisieren → alt
  deaktivieren).
- **Multi-Key mit Priorität + Fallback** (OpenRouter prioritized/fallback, LiteLLM
  `fallbacks`) und **expliziter, sichtbarer** Fallback auf System-Credentials.
- **Scoping:** Keys auf Modelle/Verwendungszwecke einschränken (Least Privilege);
  getrennte Credential-Klassen (Management vs. Inferenz).
- **Rollen-Modelle mit Default-Inheritance** (Zed: Feature-spezifische Modelle
  fallen auf den Default zurück; Continue `roles:`) — unser Main/Watcher/
  Investigator/Judge/Fallback-Muster.
- **Broker-Pattern:** Der Agent-Prozess sieht den Roh-Key **nie** — kurzlebige,
  identitätsgebundene Tokens + Audit. Architektur-Begründung für die Treasury als
  einzigen Key-Ort.
- **Desktop-Storage:** OS-Keychain bzw. Electron `safeStorage`; **prüfen** ob das
  Linux-Backend `basic_text` ist (sonst „Verschlüsselung", die keine ist).
- Anti-Pattern: Key ohne Test speichern; Keys in config.yaml; Klartext-Reveal;
  stiller Proxy ohne Dokumentation; Master-Key für alles; keine Usage-Sicht.

## C. Input-Lag bei langen Transkripten (React/Electron)

Empfohlene Reihenfolge: **erst messen**, dann die billigsten großen Hebel, dann
Struktur, dann Streaming-Feinschliff.

1. **Messen** (Profiler, Produktionsbuild, DevTools-Einfluss ausschließen).
2. **Composer-State isolieren** (State-Colocation; Input-State lebt nur im Composer)
   und **uncontrolled** wo möglich — direkt gegen den Input-Lag.
3. **Rows memoisieren** (React.memo + stabile Props/Keys).
4. **Virtualisierung** (react-virtuoso/TanStack Virtual) + **Paging** (nur die
   letzten N Nachrichten; ältere nachladen). Risiken: Selektion/Kopieren/Suche über
   unmountete Rows; Flicker bei Höhenmessung; Scroll-Anchor-Pflege.
5. **Streaming bündeln:** Tokens in ref puffern, per rAF/30–100 ms flushen
   (verhindert 30–100 Renders/s).
6. **CSS `content-visibility: auto` + `contain-intrinsic-size`** als billige
   Ergänzung (Browser skippt Offscreen-Rendering; React reconciled weiterhin).
7. **Selektive Subscriptions** (useSyncExternalStore/Zustand-Selektoren) statt
   Context-Broadcast; `useDeferredValue` für abhängige, teure UI-Teile (nie für das
   Input-Update selbst).
8. React Compiler (Build-Gate) als Option für automatisierte Memoisierung.

## Einordnung für Skaldir Agent

- Die Live-Block-Richtung der Spec entspricht exakt den Mustern der beiden
  Referenz-Apps (Eingriffe im Thread, ruhige Signale, Status-Pill).
- Settings/Treasury-Trennung mit Handles entspricht den stärksten Mustern aus B
  (Handle-Referenzen, Write-only, Verify-vor-Save, Rotations-Flow).
- Der Input-Lag ist primär ein **Rendering-/DOM-Problem** (nicht JS-Berechnung) —
  die Reihenfolge in C ist die Umsetzungsstrategie.
