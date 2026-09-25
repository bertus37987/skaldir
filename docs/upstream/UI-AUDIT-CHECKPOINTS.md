# UI-Audit — Umsetzung in visuellen Checkpoints

Grundlage: unabhängiger UI-Audit vom 12.09.2026 (Screenshots `chat-v5-geometry.png`,
`chat-v4-voice-visible.png`, `v2-cockpit.png` + Desktop-Plugin-Code). Kein pixelgenauer
Auftrag; die Empfehlungen sind Gestaltungsvorschläge, die Richtung ist verbindlich.

**Referenzgrenze:** Das vom Nutzer gemeinte „OpenClaw 2"-Frontend ist noch offen (der
Audit sagt das ausdrücklich) — kein pixelgenauer Nachbau. Referenzen für Arbeits-
organisation: Codex-App (Projekt-/Task-Wechsel, Multi-Agent-Command-Center),
OpenClaw (Companion-App + WebChat + Dashboard-Settings-Split).

## Screenshot-Serie des laufenden Builds (Audit-Schritt 1)

- `ui-artifacts/audit/00-current.png`, `05-cockpit.png`, `14-panel-cockpit.png` — Cockpit
- `01-chat.png`, `06..08`, `10/11` — leerer Chat (vor / nach Checkpoint A)
- `02-verification.png`, `12-panel-settings.png`, `13-settings-subtabs.png` — Panel
- `03-treasury.png` — Treasury (leer, Vault locked)

Hinweis: `04-system.png` entspricht byte-identisch 03 — der „System"-Klick griff bei der
Serie nicht; über die Settings-Sub-Tabs ist die Ansicht erreichbar. (Ehrlich statt
weggelassen.)

## Checkpoint A — Chat-Zustand (umgesetzt)

| Audit-Befund | Umsetzung | Nachweis |
|---|---|---|
| Großes Serif-Wordmark + farbiges Logo im Leerzustand | **App-Overlay** (`app-overlay/patch_app.py`): Logo 96px → 36px, Riesen-Wordmark ersetzt durch kleine Sans-Zeile mit Tracking; eine langatmige Intro-Copy gekürzt | `ui-artifacts/audit/10-chat-quiet-1920.png` |
| Feature-Grid verstopft den Leerzustand | Plugin-`chat.empty`: nur noch kleine Wortmarke + kurze Zeile; Grid entfernt (Smoke-Check angepasst: „chat-empty is a quiet start") | `06-chat-new-1920.png` |
| Viele kleine Symbole im Composer | Seltene AV-Controls (Voice dictation / Read replies aloud / Wake word) per **element-scoped** Chrome-Style ausgeblendet — eindeutige `aria-label`, nichts anderes betroffen (+ Smoke-Check) | `07/08` |
| Goal/INCOMPLETE dominieren den Composer-Block | Statuszeile kompakter (eine ruhige Zeile, `text-[11px]`, kleinere Flächen) | `06..08` |

## Checkpoint B — Panel & Shell (umgesetzt)

1. **Zweite Navigation gebündelt ✓** — CONTROL (Cockpit, Verification, Activity) +
   MANAGE (Settings, Treasury); die sechs Admin-Views leben als Sub-Tabs in **einer**
   Settings-Ansicht (Models, Tools, Computer Use, Memory, Skills, System).
   Cockpit-Deep-Links routen automatisch in den passenden Settings-Sub-Tab.
   Nachweis: `ui-artifacts/audit/13-settings-subtabs.png`, `14-panel-cockpit.png`;
   Smoke: „nav renders groups/views", „settings bundles the admin views" (110 Checks).
2. **Inspect-Drawer:** Standardbreite 20rem → **23rem (~368px)**, der Host-Pane ist
   resizable; der Drawer öffnet nur auf Klick (geschlossen initial) ✓.
3. **Cockpit-Zeiträume getrennt:** „Latest evidence (previous task)" / „Voice activity
   (history)" bei leerem Goal; der Zustands-Text unterscheidet jetzt ausdrücklich
   „voriges Ziel verifiziert" vs. „voriger Task INCOMPLETE — die Befunde sind Historie".
   Leere Zustände handlungsorientiert („none yet — no check has produced evidence").
   (Diese Textschärfung ist im nächsten App-Build sichtbar; die Struktur-Screenshots
   12–14 zeigen bereits die gebündelte Navigation.)
4. Typo/Farben: offen — Feinschliff zusammen mit Checkpoint C.

## Checkpoint C — Interaktion & Farben (umgesetzt)

5. **Mention-Picker:** Das Plugin priorisiert Präfixe (nur `@goal`/`@tool` erscheinen bei
   passendem Query, mit `meta`-Label und Icon) und wird vom Host **über** den Datei-Rows
   gemergt (`COMPOSER.atCompletions`-Kontrakt). Die Datei-Liste selbst gehört dem
   Host-Popover — Begrenzung aufs Projekt/Gruppen ist außerhalb der Plugin-Reichweite
   und bleibt als Host-Grenze dokumentiert.
6. **Event-Zeilen mit Uhrzeit ✓** — jede Feed-Zeile führt jetzt HH:MM (Tooltip: voller
   Zeitstempel); Live-Zustand (Strip) vs. Historie (Zeilen der letzten 45 Min) bleibt
   optisch getrennt.
7. **Fehlerursachen als Text ✓** — Blocker/Redirects nennen Ursache und nächsten Schritt
   („STOP repeating the same fix…", DoD-Text) statt nur Farbbadges.

**Farben/Typo (B4) ✓** — GV-Theme (App-Overlay) auf neutrale Flächenstaffelung umgestellt
(`#111214 / #181a1d / #22252a / …`), Gold bleibt gezielt für Status/Voice/Selektion
(`#f5be36` beibehalten — Nutzer-Identität). Nachweis: `17-panel-c-1920.png`
(„Flächen deutlich neutraler, Gold nur noch als Akzent, Karten klar getrennt").

## Nachbesserungen nach Checkpoint C (Live-Befunde aus der laufenden App)

- **`@goal` im Chat setzt den Contract dieser Session** (vorher fehlte der Kanal
  komplett — nur Panel/Strip-Edit konnten Ziele setzen). Der `pre_llm_call`-Hook
  erhält Session-ID + User-Nachricht vom Host: Er beginnt bei `@goal <text>` den
  Per-Session-Contract (`goal_set_from_chat`-Event, im Feed mit Uhrzeit) und gibt
  dem **Main Agent** jeden Turn den Vertragsblock mit (state · goal · DoD-Fortschritt
  · offene Blocker · `BLOCKED_USER`-Konvention) — als Kontext an der User-Nachricht,
  nie am Systemprompt.
- **Escape schließt den Inspect-Drawer** (das Host-`openWorkspace`-Tile hatte keinen
  Tastatur-Weg; der vom Host zurückgegebene Closer hängt jetzt an einem
  Escape-Handler im Drawer-Inhalt).
- **P1-Regel geschlossen (hohles Grün):** `GateRunner` konnte einen Contract **ohne
  Blocking-Kriterien und ohne bestandenen Check** als `VERIFIED_COMPLETE` zertifizieren
  (der Panel-„Verify now"-Klick auf einer leeren Sitzung). Jetzt: Blocker
  „nothing was verified — … define done and run a check". Gegenprobe: Ein bestandener
  Runner-Check zertifiziert weiterhin evidenzbasierte Claims (kein falsch-negativ).
- **E2E läuft idempotent** (`tools/gv_ui_e2e.mjs`): 40 Checks gegen die laufende App,
  inkl. Tastatur-Flows (Escape → Drawer zu, Fokus zurück) und Selbstheilung des
  Verify-Checks (legt bei Bedarf per `@goal` einen frischen Contract an). Stand:
  **alle Checks grün** (nach dem Harness-Kurs).


## Abnahme (aus dem Audit übernommen)

- Inspect zu: Unterhaltung ist der klare Hauptinhalt; Inspect offen: Eingabe + Gespräch
  bleiben komfortabel.
- „Was mache ich? Was macht die Voice? Muss ich handeln?" ist ohne JSON/Statuscodes klar.
- Screenshot-Matrix 1280×800 / 1440×900 / 1920×1080 + OS-Skalierung; lange Goals/Modelle/
  mehrzeilige Eingaben überdecken nichts.
- Tastatur: Mention-Auswahl, Escape, Send, Drawer schließen, Fokus zurück zum Composer.
- UI-Abnahme braucht visuelle Inspektion — grüne „Element vorhanden"-Tests genügen nicht.
