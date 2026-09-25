# UI-FEATURE-MAP — Ist-Zustand (Feature → Runtime → UI → UX)

Erstellt 17.09.2026 gegen HEAD `ac0c22e` + dirty workspace. **Alle Zeilen sind aus dem Code
gemessen**, nicht aus Dokumenten übernommen; wo etwas nicht geprüft ist, steht das da.
Zweck: §2 des UI/UX-Refresh-Auftrags — vor jeder UI-Arbeit klären, welche Fähigkeit schon
einen UI-Ausdruck hat, welcher fehlt und welcher nur Behauptung ist.

## 0. Gemessene Oberflächen-Inventare

| Ebene | Umfang | Quelle |
|---|---|---|
| Panel-Plugin (Chat-Oberfläche) | 3945 Zeilen, **3 eigene Komponenten**: `GvOrb`, `GvLiveBlock`, `GvEventDetail`; 10 `gv*`-Funktionen (`gvOrbState`, `gvOrbBuildStage`, `gvFeedRows`, `gvFeedTone`, `gvFeedSource`, `gvStateTone`, …) | `integrations/hermes/godsv_plugin/desktop/plugin.js` |
| Registrierte SDK-Flächen | `ROUTES_AREA`, `SIDEBAR_NAV_AREA` (4 Ziele: verification/tools/settings/treasury), `PALETTE_AREA`, `CHAT_EMPTY_AREA`, `COMPOSER.*` (`atCompletions`, `goal-middleware`, `status-strip`, `approvals`, `contextual-actions`) | ebd., `ctx.register`-Blöcke |
| Backend | **31 Routen** unter `/api/plugins/godsv/` (`/summary`, `/events`, `/completion*`, `/tools*`, `/treasury*`, `/models*`, `/cu`, `/cu/demo`, `/memory`, `/skills`, `/evals`, `/goal`, `/approvals`, `/health`) | `dashboard/plugin_api.py` (1966 Z.) |
| Runtime-Ereignisse | **88 Arten** in `KINDS`, davon **38 in `CHAT_KINDS`** — d. h. 50 Arten werden protokolliert, aber nie in den Chat projiziert | `src/godsv/events.py` |
| Completion-Zustände | `WORKING`, `VERIFYING`, `INCOMPLETE` (nicht terminal) + terminal `VERIFIED_COMPLETE`, `BLOCKED_USER`, `BLOCKED_EXTERNAL`, `FAILED_UNRECOVERABLE`, `SHIPPED_WITH_USER_OVERRIDE`, `USER_CANCELLED` | `src/godsv/completion.py` |

**Wichtige Arbeitsteilung:** das Panel-Plugin besitzt nur die Chat-Beiträge (Strip, Live-Block,
Feed, Composer-Aktionen). Die **Tool-Call-Darstellung im Transkript liegt im App-Core**
(`apps/desktop/src/components/assistant-ui/thread/message-parts.tsx` u. a.) und wird ausschließlich
über `app-overlay/patch_app.py` (36 Patches) verändert — **nie** direkt im Mirror
(Hardlink-Kopplung, AF-12).

## 1. Cross-Cutting: die Primitives aus §3 sind größtenteils NICHT vorhanden

| Primitive | Ist-Zustand (gemessen) | Klasse |
|---|---|---|
| **Thinking Orb** | vorhanden und verbindlich: `GvOrb` + `gvOrbState`/`gvOrbBuildStage`, 6 Verwendungen, `docs/UI.md` §Thinking-Orbs pinnt die Abbildung; Smoke prüft „kein Orb bei Endzustand/unbekanntem Zustand" | ✓ |
| **Action Strand** | **fehlt** (`strand` = 0 Treffer in Plugin und App-Core) — mehrstufige Abläufe erscheinen als Einzelzeilen, nicht als zusammenhängender Strang | A |
| **Node** (✓ / ! / ◇ / ◆) | **kein Node-Baustein**; es gibt `gvStateTone` + `GV_FEED_TEXTS` (Ton + Satz pro Ereignis) — Zustände werden als Text/Ton gezeigt, nicht als einheitliches Symbol | B/C |
| **Peek** (Screenshot/Bild/Diff/Artifact inline) | **fehlt** (`peek|preview` = 0 in Plugin und Core-Komponenten); CU-Beobachtungen, Recherche-Quellen, Diffs landen nie als Vorschau im Chat | A |
| **Fold** (`Details ›`) | **teilweise**: `GvEventDetail` + Inspect-Drawer zeigt Metadaten/Ereignisse/Evidenz; keine generische Fold-Zeile an jedem Tool-/Ergebnis-Element | C |

## 2. Zustandsmodell (§4) ist im UI nicht implementiert

Der Code kennt **kein** Vokabular `queued | running | waiting | approval_required | paused |
succeeded | failed | unknown | cancelled` (alle acht als String-Literal: **0 Treffer** im Plugin).
Stattdessen existieren nur `succeeded` (2), `failed` (8), `running` (1). Die Runtime hat dagegen
neun echte Completion-Zustände (s. o.) plus 88 Ereignis-Arten. **Folge: die UI projiziert
Zustände heute über Ad-hoc-Sätze/Ton statt über ein gemeinsames, prüfbares Zustands-Vokabular —
und `unknown` hat gar keinen Ausdruck, obwohl die Regel „unknown nie als Erfolg" gilt.**

## 3. Feature-Tabelle (die sieben §2-Fragen je Fähigkeit)

Legende Klasse: **A** = Runtime kann es, UI fehlt · **B** = UI da, echte Funktion fehlt ·
**C** = beides da, UX schlecht.

### 3.1 Goal-Vertrag (Criteria, Revision, Verdict)
- Start: `@goal …` im Composer (Mention-Autocomplete) oder Kriterien-Editor im Strip.
- Runtime: Plugin-Middleware → `POST /completion/criteria` → `completion.py` (`add_criteria`/`update_criterion`, Revisionszaun, `request_id`-Replay).
- Zustände: `WORKING` → `VERIFYING` → `INCOMPLETE` → terminal; Orb je Phase.
- Währenddessen: `GvLiveBlock` (Orb + Kriterien + Voice-Aktivität), Strip (`SessionStatusStrip`).
- Danach: Verdict-Satz + Inspect-Drawer (`GvEventDetail`).
- Fehler: `CriterionConflict` → Editor behält Entwurf und schickt neue `request_id` (verifiziert).
- Eingriff: Kriterien-Editor, `@goal`, Panel-Override (nie `VERIFIED`).
- **Verdikt: C** — funktioniert, aber §11 verlangt ein *Abschlussprüfungs*-Gefühl statt einer Liste; heute ist der Verdict ein Satz im Live-Block. Kleinster Schritt: Verdict als Strand mit Node-Zeilen (`◇ 2 Kriterien benötigen aktuelle Nachweise` → `✓ vollständig verifiziert`).

### 3.2 Voice-Interventionen
- Start: automatisch (Watcher/Signale), kein Nutzerstart.
- Runtime: `monitor.py` → `pause.py` → Ereignisse `pause_requested|delivered|released|expired|stale|no_action`, `research_started|done|failed` (**alle in `CHAT_KINDS`** außer `pause_no_action`/`pause_stale`).
- UI: Feed-Zeilen mit Ursachentext + Ton (`GV_FEED_TEXTS`, `gvFeedTone`).
- Fehler: **unsichtbar** — `pause_stale`/`pause_no_action` erreichen den Chat nicht; ein stumm gescheiterter Eingriff sieht aus wie „nichts passiert".
- **Verdikt: A (teilweise)** — während/danach gut, Fehlerfall fehlt. Kleinster Schritt: `pause_stale` in die Feed-Text-Tabelle aufnehmen (keine neue Komponente).

### 3.3 Jev / System-One
- Runtime: 11 Ereignis-Arten (`system_one.requested|evaluation|decision|escalated|blocked|stale|discarded_stale|error|failed|completed|shadow`) — **keine davon in `CHAT_KINDS`**.
- UI: keine (kein Panel-Tab, keine Chat-Projektion).
- Spec §10 verlangt ausdrücklich *minimale* Sichtbarkeit: nur die resultierende Aktion (`◆ Genauere Verifikation erforderlich`).
- **Verdikt: A** — bewusst unsichtbar, aber auch die *eine* geforderte Zeile fehlt. Kleinster Schritt: `system_one_decision` + `system_one_blocked` als stille Node-Zeile in den Feed (nur diese zwei Arten).

### 3.4 Generisches Tool-UI (§6)
- Runtime: `tool_call`/`tool_result`/`test_run`/`tool_error_class` werden protokolliert.
- Chat: **nicht in `CHAT_KINDS`** — nur `tool_call_intercepted` erreicht den Feed. Die Tool-Zeilen im Transkript stammen aus dem App-Core und sind nicht Skaldir-gefärbt (kein Orb, keine Gruppierung).
- Panel: Tools-Tab gruppiert Builds mit Stage-Chips (review16 bestätigt).
- **Verdikt: A/C** — der geforderte *generische Renderer mit Orb* fehlt für den Chat. Kleinster Schritt: ein Plugin-Beitrag, der pro laufendem Tool-Fenster eine Zeile mit Orb + Toolname aus bereits vorhandenen Events ableitet (Daten sind da: `post_tool_call`/`pre_tool_call`-Hooks), ohne jedes Tool zu spezialisieren.

### 3.5 Computer Use (§9)
- Runtime: 6 Arten (`cu_attempt|action|verified|blocked|failed|aborted`), nur `cu_blocked`/`cu_failed` in `CHAT_KINDS`; Runner: dry-run, sandbox, Container+GUI (`docker/cu-x11.Containerfile`, `cu check` = verfügbar, E2E-Probe 18/18).
- UI: Panel → Settings → „Computer Use" (Status + Dry-Run-Demo), **kein Chat-Einstieg**, keine Beobachtungs-Vorschau.
- **Verdikt: A + P2 (review16)** — Erfolg darf erst nach Zustandsprüfung erscheinen; heute fehlt der ganze visuelle Pfad. Kleinster Schritt: Peek-Vorschau (Screenshot aus `cu_attempt`-Payload) + `cu_verified` als Erfolgs-Node (derzeit nicht im Feed).

### 3.6 Capability-Aufbau (Tool-Builder)
- 9 `tool_build_*`-Arten **sind im Chat**; Panel Tools-Tab zeigt Queue + Stufen; Orb-Verben je Build-Stufe gemappt (`gvOrbBuildStage`).
- **Verdikt: ✓/C** — sichtbar und gestreamt; UX-Frage bleibt die Gruppierung (§8: ein Werkzeug-Aufbau als *ein* Strang statt neun Zeilen).

### 3.7 Treasury / Freigaben
- Routen `/treasury*`, `/approvals` real; Events `spend_requested` (Chat ✓), `spend_approved|completed|denied`, `treasury_*` (Chat nur teils).
- UI: Panel Treasure-Tab (Grants/Credentials, Handle-only), Approvals-Pill im Composer (echter Backend-Modus).
- **Verdikt: B/C** — Freigabe-UI existiert und ist echt; `spend_approved|completed` fehlen im Feed, d. h. der Ausgang einer Freigabe ist im Chat nicht sichtbar.

### 3.8 Recherche / Memory / Skills
- Recherche: `research_*` im Chat ✓, aber **kein Peek der Quellen** (Payload → Textzeile).
- Memory/Skills: Panel-Settings-Tabs, kein Chat-Ausdruck (spec-konform).
- **Verdikt: C** (Recherche-Quellen als Peek fehlt).

### 3.9 Docker / Deployments / Workflows / Subagents
- Docker: Runtime + CU-Container real, **keine `/docker`-Route, kein Chat-Ausdruck**; Container-Lebenszyklus ist über Tools/CU erreichbar.
- Deployments: `VD-14`-Audit sagt „Deployment-Tools fehlen (leere Registry)" → **blockiert**, keine erfundene UI.
- Workflows: `src/godsv/workflows.py` (`open_app_snapshot`) hat **keinen CLI-/MCP-/Panel-Aufrufer** (FEATURE-MAP bestätigt).
- Subagents: **kein Gruppierungs-Ausdruck** im Chat (review16: kein `parallel|subagent`-Fund), nur `run_sweep` als Ereignis.
- **Verdikt: A** — jeweils Runtime vorhanden, UI fehlt; für Deployments ist der Blocker benannt statt überbrückt.

## 4. Dokument-Drift (gefunden, nicht behauptet)

| Dokument | Behauptung | Realität |
|---|---|---|
| `docs/UI.md` („Native Features → UI", Zeilen 18-37) | ordnet Features Tabs zu: *Live*, *Pausen*, *Memory*, *Skills*, *Completion*, *System*, *Models* | die NAV registriert nur `verification/tools/settings/treasury`; Models/Memory/Skills/System sind **Settings-Untertabs**, „Live/Pausen/Completion" existieren als Tabs nicht mehr |
| `docs/UI.md` Zeile 47-96 (Thinking-Orbs) | verbindlicher Indikator | **korrekt** und im Code belegt (`GvOrb`, Smoke-Pins) |
| `outputs/review16/ux/UX_REVIEW.md` P1 („microActions hat null Provider") | Composer-Aktionsleiste strukturell leer | **überholt**: `contextual-actions` ist im Repo registriert (Plan öffnen / Werkzeug suchen / Docker-Image …), P1 damit erledigt |
| `outputs/review16/ux/UX_REVIEW.md` P2 (ausgeliefertes Plugin hinkt) | Deploy divergiert | **bestätigt heute**: ausgelieferter App-Baum enthält weiterhin `cockpit`/`activity` in der NAV (Repo: 4 Ziele, Deploy: 6), md5 unterschiedlich |

## 5. Reihenfolge der Arbeit (aus diesem Inventar abgeleitet)

1. **Zustands-Vokabular (§4)** — die gemeinsame Basis: ein Mapping Runtime-Zustand → UI-Zustand
   (`queued|running|waiting|approval_required|paused|succeeded|failed|unknown|cancelled`), im
   Plugin implementiert und im Smoke gepinnt, inkl. „`unknown` ist nie Erfolg". Erst danach sind
   Node/Strand/Morphing überhaupt konsistent.
2. **Feed-Vollständigkeit** — die kleinen Lücken mit vorhandenen Daten: `pause_stale`,
   `system_one_decision|blocked`, `cu_verified`, `spend_approved|completed` in `CHAT_KINDS` +
   Feed-Texte. Keine neue Komponente nötig.
3. **Node + Fold (§3)** — Symbol- und Detail-Primitive, auf denen Feed/Strand aufsetzen.
4. **Strand (§3/§8/§11)** — Gruppierung zusammengehöriger Schritte (Build-Aufbau, Completion-Prüfung, parallele Arbeit).
5. **Peek (§3/§9)** — inline Vorschau für CU-Beobachtung, Recherche-Quellen, Artefakte.
6. **Generischer Tool-Renderer (§6/§13)** — ein Renderer für alle Tools, Result Morphing an derselben Position.
7. **Deploy-Gleichstand** — Repo-Plugin in den App-Baum bringen (Ownership: `app-overlay`), sonst erreicht keine der Änderungen die gebaute App.
8. **Produkt-Audit (§17/§18)** auf der gebauten App inkl. Playwright + Axe, Responsive 1280×800/900×650, Light/Dark, Performance, Electron-Grenzen.

## 6. Offen / nicht verifiziert

- Live-Verhalten im laufenden App-Fenster (bisher nur Code + Node-Smoke; das App-Fenster war in
  dieser Sitzung nicht gestartet).
- App-Core-Transkript-Darstellung im Detail (nur Inventar der Dateien, kein Rendering geprüft).
- Playwright/Axe sind **nicht** installiert (§19-Werkzeuge fehlen) — Installation ist app-seitig möglich (`uv pip install playwright`, `playwright install chromium`).
