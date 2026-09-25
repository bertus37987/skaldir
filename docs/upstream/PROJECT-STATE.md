# Skaldir Agent — Projekt-Gesamtstand (Harness)

**Stand: 14.09.2026 (Live-Control-Plane-Runde)** · Repo: `bertus37987/gods-voice` (privat, Default-Branch `main`;
Arbeitsbranch `godsv-supervisor-hardening`) · Version `1.4.0` · letzte Tags bis `v1.3.6`
(1.4-Härtung + UI-Runden sind auf dem Branch getaggt: `hardening-e1-evidence`,
`hardening-e2-contract`, `hardening-e3-gates`).
**238 Commits, 30 Tags, 51 Kernmodule, 88 Testdateien · volle Suite 1183 passed (RC=0, 14.09.2026, Branch `godsv-supervisor-hardening`).**

Dieses Dokument ist die zentrale Stand-Übersicht: Was das System ist, wie es gebaut ist,
was verifiziert ist — und **was (noch) nicht stimmt**. Alle Namen/Dateien sind gegen den
Code geprüft, nicht aus dem Gedächtnis.

---

**Anspruch-Abgleich:** Der vollständige Audit gegen den echten Laufzeit-Pfad (Subsystem-Matrix,
9 Failure-Klassen, priorisierte Fix-Liste P0-P2) liegt in `docs/SUPERVISOR-AUDIT.md` —
Stand 13.09.2026: Kernketten (Goal-State, Watcher→Pause→Redirect→Resume, Gates,
Incident-Reuse, Treasury) VERIFIED+WORKING; P0-Lücken: Ledger tot, Testing-Tools nicht am
Gate registriert, Research-DB ohne Reuse-Index/Schema-Felder; Tool Hub MISSING (Design).

**Lücken-Register-Abarbeitung (13.09., Plan `.hermes/plans/2026-09-13_161102-*`):** T1-T12 umgesetzt,
je rot-first getestet: `pause_no_action`+`treasury_selftest` im Event-Register · DoD-Status-Lifecycle
(`record_dod_outcomes`, `verified`/`failed` werden jetzt geschrieben) · `project_id`-Plumbing
(CLI `--project` bei request/build + Plugin-Kontext) · `run_model_test`-Restore + `ctrl+comma`-Fix ·
Testing-Tool-Redirect-Text · MCP-Konflikt-Warnung · `uninstall --remove-plugin` (opt-in, Altverhalten
bleibt Spezifikation) · Testing-Tool-Requests mit `--validation-case` · Scope-Promotion-Eintritt
(`tools promote-global` + Panel-Route) · `workflows list`. Repariert: stdio-E2E (Build+Serve teilen
jetzt den Projektkontext). Offen dokumentiert: `tools draft` (braucht CLI-LLM-Client), Ledger-Live-Pfad,
Embeddings, MCP-Auto-Registrierung. Re-Mapping (Phase 3 des Plans) folgt.

**Live-Control-Plane-Runde (14.09.2026):** Die acht Arbeitsblöcke aus `docs/IMPLEMENTATION-PLAN-live-control-plane.md`
sind umgesetzt und einzeln rot-first belegt. Neu sind elf Dokumente: `SECURITY-BOUNDARIES.md` (Block 1) ·
`RUN-LIFECYCLE.md` (2) · `VERIFICATION-CLOSURE.md` (3) · `VOICE-CALIBRATION.md` (4+5) ·
`CAPABILITY-LEARNING-E2E.md` (6) · `MEMORY-LEARNING.md` (7) · `BENCHMARK-RUN-SCHEMA.json` +
`BENCHMARK-MATRIX.md` (8) · `E2E-LIVE-PROOF-40.md` (§40 Stufen 1–5) · `IMPLEMENTATION-MATRIX.md`
(Status je Feature) · `VERIFIED-FINDINGS-live-2026-09-13.md` (eigene Code-Verifikation).
Nachweis je Block: `IMPLEMENTATION-MATRIX.md` §6a–§6f.
**Offen:** §40 Stufen 6–9 · Schema-Platzhalter in `BENCHMARK-RUN-SCHEMA.json` (der Harness meldet sie als
`schema_reference_missing`, statt zu schweigen) · Dedupe-/Retention-**Durchsetzung** (nur Prüfung implementiert).

## 1. Was Skaldir Agent ist

Ein **Agent-Harness**: Ein Main Agent tut die Arbeit, eine unabhängige **Voice-Schicht**
(der Watcher) beobachtet, greift evidenzbasiert ein (Pause → Research → Redirect → Resume)
und entscheidet allein, wann eine Aufgabe **wirklich** fertig ist. Ausgeliefert als
**native Fedora-Desktop-App** (Hermes-Engine mit eingebackenem Panel + Branding — „ein
Update von Hermes", kein Plugin-Tab) **plus CLI** (`godsvoice`).

**Produktgrenze (verbindlich):**

```text
CHAT      = live operational state      (die Konversation ist das Cockpit)
INSPECT   = deep evidence for a specific event (rechter Drawer)
SETTINGS  = configuration               (Skaldir Agent Settings)
TREASURY  = secrets and grants          (Skaldir Agent Treasury)
```

## 2. Architektur in Schichten

| Schicht | Ort | Rolle |
|---|---|---|
| Engine | `~/.hermes/hermes-agent` (Hermes, unangetastet) | Sessions, Tools, Modelle, Streaming |
| Voice-Kern | `src/godsv/*.py` (42 Module, ~5,4k Zeilen in den 10 Kernmodulen) | Supervisor, Gates, Events, Pause/Research, Mining, Treasury, CU, Rollen |
| Panel-Backend | `integrations/hermes/godsv_plugin/dashboard/plugin_api.py` (1.694 Z.) | `*_payload()`-Funktionen + Routen der Plugin-API |
| Plugin | `integrations/hermes/godsv_plugin/__init__.py` (1.535 Z.) + `desktop/plugin.js` (2.554 Z.) | Hooks ↔ Hermes; Panel-UI + Chat-Chrome (Strip, Feed, Drawer, Mentions, Live-Block) |
| App-Build | `src/godsv/appbuild.py` + `app-overlay/patch_app.py` (369 Z.) | Hardlink-Mirror → exakt verankerte Overlay-Patches → `npm run pack` → `~/.hermes/godsv/app/` |
| CLI | `src/godsv/cli.py` (`godsvoice`) | gleiche Module, eigene Oberfläche |
| Live-Beweis-Tools | `tools/gv_ui_e2e.mjs` (337 Z., 40 Checks), `tools/gv_product_run.py` (237 Z.), `tools/gv_shot.mjs`, `tools/gv_eval.mjs` | fahren die echte App über CDP bzw. die echte Panel-API |

**Datenpfade (Runtime `~/.hermes/godsv/`):** `events.db` (EventStore, alle Session-Events),
`completion/<session>.json[l]` (Verträge + Verdicts je Session), `inbox.jsonl`,
`research/`, `capability_*.jsonl`, `cu_runs.jsonl`, `eval_runs.jsonl`.
Die GV-App teilt `HERMES_HOME` mit der Stock-App; nur userData/App-Identität sind eigen.
**Session-Identität (fix dieser Runde):** Jede Chat-Oberfläche (Strip, Feed, Live-Block,
Cockpit, Completion-Tab) löst ihre Daten gegen `host.state.focusedStoredSessionId` auf — die
durable Session-ID (= `session_key`, die die Hooks bekommen und die der Store als Dateinamen
nutzt; der Runtime-Atom ist im Desktop-Chat regelmäßig leer). Ohne fokussierte Session rendert
die Strip idle und fragt nichts ab. Die Strip trägt `data-godsv-sid` / `-runtime` / `-hasstate`
als CDP-/E2E-Marker. **Wichtig:** Renderer-`plugin.js` erreicht die GV-App NUR über
`godsvoice desktop build-app` (das Plugin wird ins App-Bundle gebacken; Datei-Edits +
Neustart/Reload genügen nicht).

## 3. Funktionsbereiche (mit Implementierungs-Beleg)

### 3.1 Completion-Supervisor (das Herz) — `src/godsv/completion.py` (1.318 Z.)
- **Vertrag je Session:** `CompletionStore(state_dir, session_id)` → `<runtime>/completion/<session>.json`;
  `begin_goal` (Revision 1, `task_base`), Revisionen invalidieren alle DoD-Items (`open`).
  Legacy-Record wird nur von der Session adoptiert, die er nennt (P1/A4).
- **P1 — Evidence-Strenge:** spezifische Anforderungen werden **nur** durch runner-ausgeführte
  Checks zertifiziert; `run_requirement_checks(dod, repo_root)` führt `pytest` (Projekt-venv
  bevorzugt) real aus; Tool-Event-Evidenz ist Kontext, nie Zertifikat.
- **Kein hohles Grün:** Ein Vertrag **ohne Blocking-Kriterien und ohne bestandenen Check**
  kann nicht `VERIFIED_COMPLETE` werden (Blocker „nothing was verified …");
  Gegenprobe: grüne Evidence zertifiziert weiterhin (2 Regressionstests).
- **Zustände:** `WORKING / VERIFYING / INCOMPLETE / VERIFIED_COMPLETE / BLOCKED_USER /
  BLOCKED_EXTERNAL / FAILED_UNRECOVERABLE / USER_CANCELLED / SHIPPED_WITH_USER_OVERRIDE`.
- **Redirect:** `VerificationResult.redirect_md()` — evidenzbasierter Korrekturtext an den
  Main Agent, inklusive `BLOCKED_USER:`-Konvention (nur Statuszeilen beenden einen Lauf)
  und dem Live-Block-Hinweis.
- **Testing-Mining-Pflichtgate:** „immer PASS"-Validierungen ⇒ Promotion verweigert;
  Registrierung ohne Fälle ⇒ `needs_validation` (`toolbuilder.py`, `capability_mining.py`).

### 3.2 Gates + Sandbox — `src/godsv/gates.py` (295 Z.)
- **Fail-closed:** kein `bwrap` ⇒ keine Ausführung; nur positive Verfügbarkeit wird gecacht.
- **Minimale Binds:** `/usr /etc /opt` read-only, `tmpfs` für `/home /root /tmp /var`,
  Interpreter-Symlink-Kette per BFS gebunden (venv → uv `cpython-3.11` → `…16`).
- Der Secret-Broker verweigert Injection ohne Sandbox (`treasury.py`, 924 Z.).

### 3.3 Events · Monitor · Pause/Research — `events.py` (300) · `monitor.py` (402) · `pause.py` (556)
- `CHAT_KINDS` + `KINDS` (append-Whitelist; unbekannte Kinds **werfen** — in Hooks gekapselt).
- Fehl-Signale → Incident-Memory; Pause-Lease mit Watchdog; Research-Template-Pfad;
  `pause_requested → research_* → pause_delivered → resume` live im Feed/Live-Block.
- Der echte Voice-Eingriff ist in dieser Session **real passiert** (Pause mit
  „contradiction"-Grund, blockierter Tool-Call, Redirect, Resume).

### 3.4 Chat-Integration (Plugin) — `plugin.js`
- **Status-Strip** über dem Composer: Zustand · Goal · `n/N` · elapsed/`~est` · Goal-Edit.
- **Live-Block im Verlauf** (`::godsv-live{stage="…"}` als Transcript-Direktive):
  kompakt, selbst-aktualisierend (4 s), Voice-Events als Zeilen, **Inspect**-Button.
  Emission: der Main Agent lernt die Zeile im Vertrags-Kontext („godsv·ui: …") und im
  Reject-Redirect; zusätzlich emittiert `transform_llm_output` sie (CLI/History-Pfad).
- **Inspect-Drawer** (`host.openWorkspace`, ~368 px, resizable, Escape schließt): Evidence,
  Tests, Tool-I/O, Findings — der Detail-Ort für jedes Event.
- **Feed** (letzte 45 min) + **Approval-Mode-Pill** (echte Engine-Modes) + `@goal`/`@tool`
  Mention-Rows; Composer 46 rem, 60 px Bodenabstand, 22 px Radius.
- **App-Integration (kein Plugin-Tab):** In der GV-App (UA-Gate) sind Cockpit · Verification ·
  Activity · Tools · Settings · Treasury **native Sidebar-Ziele** mit eigenen Routen; keine
  zweite Nav-Ebene. Im normalen Hermes bleibt die ruhige Ein-Eintrag-Integration.

### 3.5 Tool-System · CU · Treasury · Rollen · Eval
- `toolbuilder.py` (475) + `capability_mining.py` (382): 8 Build-Stufen als Chat-Events,
  Validierungsfälle, Gate-Lauf im Sandbox; `tools/check_desktop_plugin.mjs` (110 Checks) +
  `tests/test_mcp_*.py` für Wrapper/MCP.
- `computer_use.py` (538): Runners + Policy; Fehler als `cu_failed/cu_blocked` im Chat.
- `treasury.py` (924): Credentials als **Handles** (`secret://…`), Grants, Domains, Audit,
  Never-Reveal; Selbst-Test über die echte API.
- `modelroles.py` (233): Main/Watcher/Investigator/Judge/Fallback — Registry-basiert
  (Hermes-Provider wiederverwendet, keine eigene Provider-Infrastruktur).
- `evalharness.py` + `/evals`-Routen: Eval-Runs live aus der App.

### 3.6 Desktop-App + CLI
- Build: `godsvoice desktop build-app` — Hardlink-Mirror, **jeder Patch-Anker muss exakt
  einmal treffen** (Build bricht sonst laut ab), temp+`os.replace` (Hardlinks sicher).
- Eingebacken: Name/ID/Binary/Icon, Splash-Mark (36 px + Wortmarke), Theme `gods-voice`
  (Gold `#f5be36` auf neutralen Anthrazit-Stufen), neutrale Flächen ab Checkpoint C,
  Nous-Portal-Texte neutralisiert, `nous-girl` entfernt.
- CLI: `godsvoice` (alle Subsysteme: status, tools, treasury, evals, desktop, …),
  Brand-Banner (Pixel-Ohr + Wortmarke, 13.09.2026 vom Owner geliefert, ersetzt das alte
  ANSI-Logo `~/gods-voice-cli-logo-art.ansi` im Leerzustand; embedded base64 in `plugin.js`).

**Session-UI Stufe 3 (13.09.): Kriterien revisioniert editierbar.** Store+API: `update_criterion`/
`remove_criterion` mit Revisionszaun (`CriterionConflict` → HTTP 409), Idempotenz über
`request_id` (`applied_requests`), Terminal-Sperre; Panel: `criteria_op_payload` (add/update/remove,
`status` wird nie vom Client akzeptiert — eine UI kann Verifikation nicht vortäuschen). UI: der
Live-Block klappt die Kriterienliste auf (Statusglyphen), Inline-Editor mit Pending/Error/Retry,
409 → Reload+Retry unter frischer ID, VERIFIED_COMPLETE friert den Editor. Belege: 10 Store-/Panel-
Tests (rot-first), 8 neue Verhaltens-Checks (Render+Klick+REST-Body: `expected_revision` +
`request_id` + kein `status`), Build `BUILD_EXIT 0`, Strings im gebauten App-Artefakt
(`app.asar.unpacked/dist/assets/index-BAnhOroi.js`). Commits `59520d9`, `17cb8e6`.

**Capability-Scopes (§6/§7, Etappenstart 13.09.):** Fundament auf der BESTEHENDEN Registry
(`generated_tools/<name>/manifest.yaml`) statt eines zweiten Systems: `scope: project|global` +
`project_id` + `version`; neue Builder-Tools sind per Default Projekt-Tools (ohne belegbaren Kontext bleibt
die ID leer → nie still global). `project_id_for` liefert stabile IDs (remote > shared git dir für
Worktrees > Pfad, gestuft und markiert). `visible_tools` = eigene Projekt-Tools + globale; Legacy ohne
Scope nur in der expliziten „alles anzeigen“-Ansicht. `resolve_tool_names` löst Namenskonflikte über
(Projekt > global, dann Version) und meldet echte Ties als Konflikt statt still zu überschreiben; Scope
bleibt reine Sichtbarkeit, keine zusätzliche Berechtigung. 15 rot-first Tests (inkl. echtem
git-Worktree-Nachweis), Commits `4c52832` + `b003678`. Offen für die Etappe: Projekt-ID-Verdrahtung in
den Ausführungskontext, Promotion-Flow (Belege aus ≥2 Projektkontexten + Negativtests), Cockpit-Tool-Views
mit Scope/Health/Promotion-Beleg.
**Promotion-Flow + Projekt-ID-Plumbing (13.09., §7):** `promote_tool` macht project→global zu einem
belegten, versionierten Akt: nur mit validierten Negativtests, **>=2 unabhängigen fremden
Projektkontexten** und ausschließlich `secret://`-Handles; fehlt etwas, bleibt das Tool Projekttool mit
Kandidatenstatus (`promotion_report` listet Kandidaten + fehlende Belege) — nichts flippt still.
Erfolg schreibt eine NEUE globale Manifest-Version mit `promoted_from` (Projekt, Belege, Zeitpunkt,
Akteur); laufende Aufrufer behalten ihre Version. Die stabile Projekt-ID fließt jetzt vom
`CapabilityRequest` über den `ToolBuildRequest` in das Builder-Manifest. 8 neue rot-first Tests
(inkl. Klartext-Secret-Ablehnung + Provenienz-Nachweis), Commits `8d81ed7`. Offen: Cockpit-Tool-Views
(Scope/Health/Promotion-Beleg) und der Workspace-getrennt-vom-Projekt-Kontext am Ausführungsrand.
**Cockpit-Ansicht der Scopes (§10, 13.09.):** Der Tools-Tab zeigt jetzt pro installiertem Tool `Scope`
(Global ↑ nach Promotion / Projekt · Kandidat / Legacy), die Version sowie Herkunft und fehlende
Promotion-Belege als Tooltip — Scope ist damit im Cockpit bedienbar, nicht nur im Store. Live-Beweis in
der gebauten App: Scope+`v`-Spalten sichtbar, die drei Bestands-Tools (`money_convert*`, vor der
Scope-Schicht gebaut) erscheinen korrekt als **Legacy** — kein stiller Global-/Projekt-Zuschlag.
Smoke: 'tools tab shows scope badges + promotion candidate'.**MCP-Export scope-aware (§7/§8, 13.09.):** Der Werkzeug-Export an externe Agenten
(`godsvoice mcp serve`) filtert jetzt nach Scope: ohne Projektkontext nur globale Tools,
Projekt-Tools nur für ihr Projekt (`--project <pfad>`), Legacy (Bestand ohne Scope) **nie
automatisch** — nur die explizite `--all`-Sicht zeigt sie. Live belegt: kontextfrei 0 Tools
(alle drei Bestands-Tools sind Legacy), `--all` zeigt 3. Dies ist der strukturierte
Live-Insertion-Punkt: jeder Serverstart liest die Registry frisch, neue geprüfte Tools sind
ohne manuelle Aktivierung da; der laufende Hermes-Agent bekommt sie über den bestehenden
Redirect-Flow (capability_gap → Pause → Build → Redirect → Resume).**Autonome Workflows §9-A/B (13.09.):** `workflows.py` — App-Snapshot und Settings-Snapshot mit
§9-Ergebnisvertrag und persistierten Run-Records. Verifikation statt Prozessstart: Fenster muss
gefunden, Fokus/Settings-Zustand nachgewiesen sein, sonst PARTIAL; fehlende App oder Screenshot-
Backend liefern `environment`-Fehler (deklarierte Voraussetzung, **kein** Auto-Install, **kein**
Ersatz-Erfolg). Live belegt: Blender fehlt → präziser environment-Fehler; laufende GV-App → SUCCEEDED
mit 125-KB-PNG (Vision: echte App). Live-Funde eingearbeitet: `pgrep -f`-Selbsttreffer der eigenen
Shell werden gefiltert; Wayland/Electron-Apps sind per xprop unsichtbar → deklariertes CDP-Backend
für Debug-Port-Apps. Offen: Workflow C (Modelltest mit begrenztem Testagenten und Backend-Evidenz).**Workflow §9-D (13.09.):** `verify_setting_persistence` — Original erfassen → Testwert speichern →
kontrollierter App-Neustart (nur gesammelte PIDs, TERM→KILL, Bereitschaft via CDP) → persistierter Wert
und Runtime-Wert prüfen → **immer** wiederherstellen. Scheitert der Restore, bleibt der geänderte
Zustand als `side_effect_state` sichtbar (PARTIAL, nie stiller Erfolg). Live belegt gegen die echte
App mit dem echten Setting `strictness`: standard → strict, Neustart, `persisted: true` /
`runtime_value: 'strict'`, Restore auf standard verifiziert (Run `setting-e8ff6c7871`).

**F15-/Kaltstart-Nachprobe (13.09., gebaute App):** (a) schnelle Navigation (3 Session-Wechsel in <2s):
kein hängendes Swap-Overlay, Ziel-Session rendert <1s; (b) Kaltstart: die App stellt automatisch die
letzte Session wieder her, Composer + Live-Block + Verifizier-Karte in 0.9s; (c) elementgenaue Suche
nach „waking up": 7 Treffer, ALLE Fließtext (Goal-Prompt/Chat-Nachrichten), kein Overlay-Element;
(d) Vision bestätigt einen normalen, voll geladenen Screen. **Der F15-Hänger ist aktuell nicht
reproduzierbar** — die Overlay-Härtung (Owner-Token + Deadline) läuft im Build, und die historische
Stall-Beobachtung war ein `innerText`-Artefakt (Mess-Lehren). Negativ-Fall Kriterien-Editor live:
bei VERIFIED_COMPLETE sind Kriterien read-only sichtbar, keine Edit-/Remove-Controls (frozen).

**Live-E2E Kriterien-Editor + zwei Live-Fixes (13.09.):** In der gebauten App komplett durchgespielt:
Kriterienzeile expandiert (○/✓-Glyphen), ✎ öffnet den Editor, Text gespeichert → Store zeigt
`revision 2` + `applied_requests` (Idempotenz-IDs), UI refetch zeigt den neuen Text; zweiter Edit
(»Restore«) → revision 3. Dabei zwei Live-Funde behoben: (1) `completion_payload` lieferte `dod` und
`revision` nicht — der Live-Block blieb ohne Zähler/Kriterien/Edit-Buttons (Mock-Payloads verdeckten
es); Fix + Rot-First-Test (`5f8d6d5`). (2) Vier Contracts trugen die `repr()` eines dod-Dicts als
Kriterientext (`{'id': 'c1', …}`); Wurzel `Pact.from_dict` → `text=str(item["text"])` bei
verschachteltem Mapping; Fix `_criterion_text` + Rot-First-Tests (`77a6533`), die vier Store-Dateien
mit Backup unter `/tmp/gv_store_backups/` migriert. Volle Suite grün.

**Composer-Direktiven-Highlight (13.09.):** `@goal …`-Zeilen, `!decision:`/`!assumption:`/`!non-goal:`
und jedes `@mention` (Tool/Agent/Datei) erscheinen in der Chatbar gold (`#f5be36`) — Verifikation vor dem
Senden. Technik: der Core-Composer ist ein contentEditable (`data-slot=composer-rich-input`); ein
transparentes Overlay ÜBER dem Editor spiegelt den Text (pixelgenau, live gemessen `boxDelta [0,0,0,0]`;
`ResizeObserver` + Geometrie-/Font-Kopie pro Paint), färbt nur erkannte Runs; Editor-DOM, Caret und IME
bleiben unangetastet. Belege: 5 Smoke-Checks (Segment-Funktion), live in der gebauten App: computed
`rgb(245, 190, 54)` + Vision „crisp, no ghosting"; Commit `955329a` + Geometrie-Härtung.

**Live-Nachweis des neuen Brandings (13.09., ~15:00):** In der gebauten App (Bundle-Fingerabdruck
`dist/assets/index-BdhOjAei.js`) öffnete der „New session"-Klick den leeren Chat: DOM-Beweis
`img` mit **src-Länge 80522** (80500 Base64 = das neue Bild) + Empty-Text „What should get done?".
Mechanik: der Klick öffnet den Empty-State nur, **wenn vorher eine Session geladen ist** — die App
startet auf `#/` (Home). Für künftige Bild-Versionen: `img.src.length` ist der harte Fingerabdruck;
Vision-Beschreibungen von Screenshots erwiesen sich als unzuverlässig (widersprüchliche Texte) —
DOM-Fakten sind die Wahrheit.

## 4. Verifikationsstand (was wirklich bewiesen ist)

| Beweis | Artefakt |
|---|---|
| Volle Python-Suite grün | letzte Läufe `SUITE_EXIT=0`, ruff `All checks passed!` |
| Plugin-Smoke | `node tools/check_desktop_plugin.mjs` → 110 Checks OK |
| **Live-E2E in der echten App** | `node tools/gv_ui_e2e.mjs 9333` → **40 Checks, zuletzt „all checks passed"** (inkl. Esc/Drawer, Fokus, Verify-Flow, Selbstheilung, Idle-Waits) |
| **§6.2-Produktlauf** | `docs/PRODUCT-RUN-6.2.md`: Ziel → rote `pytest` → **INCOMPLETE** (0,3 s, echte Blocker) → Fix → **VERIFIED_COMPLETE** (0,4 s) → Restart-Persistenz → Session-Isolation |
| **Live-Block live** | Screenshot `ui-artifacts/audit/20-live-block-1920.png`; Vision bestätigt Block + sechs Sidebar-Bereiche |
| UI-Checkpoints A/B/C | `docs/UI-AUDIT-CHECKPOINTS.md` + Screenshots 06–17 |
| Regressions-Netzt | `tests/test_evidence_strictness.py` (13), `test_contract_sessions.py` (10), `test_gate_hardening.py` (14), `test_session_isolation.py` (6, neu), Live-Block-/Contract-Tests |
| **Session-Isolation live** | GV-App: Strip folgt der fokussierten Session — `sid` wechselte zwischen `20260912_225514_6d8dbf` ↔ `20260912_225838_261895` mit dem jeweils passenden Goal; frische Chats idle (kein Fremd-Contract mehr) |
| **Aktiver Modus live** | Trace `register() … mode=whisper` (vorher shadow); Cockpit zeigt „whisper mode · steering“ |
| **VERIFIED_COMPLETE live im Desktop** | Session `20260912_231434_ec74ff`: Kriterium „pytest suite is green“ trägt `verification='pytest'` (im Store geprüft) → **3× reale `pytest: ok`-Evidenz** im Scratch-Repo → `VERIFIED_COMPLETE`, 0 Blocker; Screenshot `ui-artifacts/audit/21-verified-complete-live.png` |
| **Voice-Intervention live auf dem Main Agent** | Beim Doku-Schreiben pausierte die Voice den laufenden Agenten (Grund „contradiction“), **blockierte den Tool-Call**, lieferte Richtung + Incident-Evidenz („Erfolgs-Behauptung zurücknehmen und mit echtem Beleg neu prüfen“); der Agent befolgte sie (Beleg im Store gelesen, dann fortgesetzt) — detect → pause → redirect → resume real |
| **INCOMPLETE live im Desktop (fail-closed)** | Session `20260912_230703_4b3683`: planlose Kriterien → echter Reject-Blob „no verification plan for … — derive a concrete check“; mit korrektem `repo_root` blieb exakt dieser Blocker (fail-closed by design) |

## 5. Bekannte Flaws & offene Punkte (ehrlich)

**F1 — Fremde Contracts leaken in jede Session (vom Nutzer beobachtet) — STRUKTURELL GEFIXT.**
Der Strip/Feed liest `/completion` **session-scoped**: jede Chat-Oberfläche löst Reads und
Writes gegen `host.state.focusedStoredSessionId` auf (stored-first; der Runtime-Atom ist
regelmäßig leer). **Fix 12.09., Commit `e46e882`** + `tests/test_session_isolation.py`
(2 parallele Sessions, Restart, frische Session idle, alte Test-Artefakte, Feed-Scoping).
**Live bewiesen:** Strip-Wechsel `20260912_225514_6d8dbf` ↔ `20260912_225838_261895` mit den
jeweils eigenen Goals; frische Chats rendern idle statt fremder Contracts. Der ungescopte
Aufruf bleibt als explizite Panel-Policy (`latest`) für Kontexte ohne fokussierten Chat.

**F2 — `transform_llm_output` erreicht die Desktop-Oberfläche nicht.**
Der Hook feuert im Serve und liefert den ersetzten Text — die Desktop-UI und der
DB-Content nutzen ihn aber nicht (auch der Completion-Guard-Footer wäre dort wirkungslos).
Live-Blöcke laufen deshalb über den Direktiven-Kanal (Modell-Emission) — funktioniert
(§4). CLI/History-Pfad nutzt den Hook weiterhin.

**F3 — Demo-Tasks sind zu kurz für echte Supervisor-Prüfung (Nutzer-Feedback).**
„reply with ok"-Turns testen nur den Kanal, nicht den Zyklus. Für belastbare Läufe:
mehrstufige Ziele (Datei anlegen → Test rot → Test grün → Build), s.
`tools/gv_product_run.py` als Vorlage; §6.2-Restläufe mit echtem Umfang.

**F4 — GV-App läuft im `shadow`-Modus.** Das Plugin beobachtet, steuert aber nicht
(`mode=shadow` beim Register). Für scharfe Läufe Modus auf `whisper` stellen (Config/Env
des GV-Starts) — vorher bewusst entscheiden.

**F5 — Test-/Session-Hygiene — GELÖST via Namespace.** `prodtest-*`-Sessions speichern unter
`<runtime>/completion/_tests/`; `sessions()`/`latest()`/Panel-Default überspringen sie;
expliziter Zugriff per ID bleibt möglich und wird als `test: true` markiert;
`tools/gv_product_run.py` nutzt den Namespace. Bestehende Artefakte liegen in `_archive/`.

**F10 — Agent-Claim → Auto-Reject/Auto-Continue im Desktop noch nicht reproduziert.**
Die Maschinerie feuert (Rejects via Panel-Verify, und in früheren Läufen nachweislich im
Turn-Pfad), aber in den heutigen Chat-Turns wurde der Claim-Zyklus über den
`pre_verify`-Hook nicht beobachtet (Verdacht: keine Claim-Formulierung im finalen Text bzw.
der finale Text kam nicht durch den Hook-Pfad). Nächster Schritt: expliziten Claim-Satz +
Hook-Trace fahren.

**F11 — Kriterien über die Panel-API haben keinen Verifikationsplan.** Fail-closed ist richtig
(Blocker „derive a concrete check“), der bequeme Nutzerweg ist das @goal-Format im Composer
(Middleware → Kriterien mit Plan). Ungetestet: feuert die Composer-Middleware auch bei echten
Nutzer-Sends zuverlässig (bei den CDP-Probes heute nicht).

**F12 — Server-`/completion/verify` ohne `repo_root` prüft im Prozess-CWD.** API-Nutzer müssen
`repo_root` explizit senden; sonst landen Checks im falschen Root. Empfehlung: Route sollte
den Session-`task_base`/file-edit-Hint als Default nutzen.

**F6–F9 (Feature-Rückstände aus dem V6-Plan):** Input-Delay-Fix bei langen Chats
(messen → Composer entkoppeln → memo → virtualisieren), Settings-Overhaul (Provider/Keys
via Treasury-Handles, Rollen-Modelle, Mining-Policies), CLI-Logo-Hintergrund (ANSI→SVG),
Upgrade/v6-Reste (`~est`, Aktivitätszeile, final candidate) + Versionshebung.
Details: `docs/EXPERIENCE-V6-PLAN.md`, `docs/RESEARCH-V6-INPUTS.md`, `docs/LIVE-STREAM-SPEC.md`.

## 6. Arbeitsregeln (Governance)

- **Kein Fake:** keine Buttons ohne Backend, keine hartcodierten Demo-Daten in Status-Views,
  keine „implementiert"-Behauptung ohne ausgeführten Beweis.
- **Secrets:** Panel zeigt nur Handles; Keys existieren nur in der Treasury; kein Reveal;
  Secret-Scanner-Gate + `[REDACTED]` in Doku/Memory/Events.
- **Normaler Hermes-Desktop wird nie gekillt/neu gestartet** (er ist die Session-UI der
  Agent-Sessions); die GV-Instanz (eigene userData + CDP-Port) ist frei.
- **Theme des normalen Hermes bleibt unangetastet**; Gold-Skin nur opt-in in der GV-App.
- Oberfläche Englisch, Kommunikation mit dem Eigentümer Deutsch.
- Kein Push/Publish/Cloud/Kauf ohne Freigabe (Repo-Commits/Tags sind freigegeben).

## 6b. Gap-Closure seit dem Audit (12./13.09.2026)

Nach `SUPERVISOR-AUDIT.md` wurden die P0-Lücken geschlossen und je live belegt:

| Paket | Commit | Live-Beweis |
|---|---|---|
| P0-1 Ledger wiederbelebt (decisions/assumptions/non_goals am Contract, `!decision:`-Intake im Chat) | `8799f86` | POST speichert, Payload liest zurück, invalid → HTTP 400 |
| P0-2 Testing-Tools am Completion-Gate (`register_testing_tools` im on_session_start; nur validierte Tools) + Sandbox-Fix (read-only Extra-Binds für Prüf-Targets) | `a9825a4` | Trace `testing tools registered: check_readme` in der App; WORKING-Target → PASS, BROKEN → FAIL (echte Verzeichnisse) |
| „Voice zeigt nie ‚working', wenn das Goal fertig ist" (Record schlägt Direktiven-`stage`) | `de68260` | Session `…ec74ff` rendert `✓ VERIFIED COMPLETE` statt `● WORKING`; Smoke-Pin |
| P0-3 Research-DB wiederverwendbar (`find_prior_research`, prior-Findings im Lauf, `created_at/stale_after/confidence`, `reused_from`) | `e386da7` | Suche findet `research-8877a68594` per Problemtext, Unähnliches → leer |
| P1-4 `context_drift` + `progress_stall` Signale (deterministisch, im Observe-Pfad) | `61ad699` | 6 Tests inkl. Pipeline; Live-Trigger-Tuning offen (F13) |
| P1-5 Novel-Strategy bei wiederholter Intervention (durable Pause-Historie, `REPEATED INTERVENTION (#N)`-Klausel, Emit-Flags `repeated`/`novel_strategy_required`) | `0861a02` | 3. Pause derselben Klasse eskaliert; frischer Controller sieht die Historie (2 Tests, volle Suite grün) |
| F13-Fix: Drift-Zähler persistieren (`<contract>.drift.json`) + Shell-Redirect-Regex für relative Pfade | `93e0785` | Turn-Boundary-Test (pop + on_session_start → Signal); 9/9 Drift-Tests |
| §11: Testing-Tool-Metriken real (`usage_stats` aus dem append-only Ledger: runs/passes/fails/last_error; ToolHub-Reliability); false-pass/fail strukturell durch Validation ausgeschlossen | `HEAD` | 3 Tests + Live-CLI (`tools-hub --show check_readme`) |
| P4-Rest: Audit-Ergebnis — Mining-Sichtbarkeit (Capability Requests/Queue/Installed/MCP) und Treasury-Credential-Flows waren bereits real in der UI; Delta geschlossen: Treasury-Brücken-Hinweis in der Models-Sektion | `HEAD` | Live: `hasBridge/hasStrictness/hasMainRole` alle true in der gebauten App |
| P4: Voice-Strictness (relaxed/standard/strict) — validiertes Runtime-Setting (`settings.yaml`), live gelesen; verschiebt Trigger-Schwellen UND den Kosten-Faktor in `assess_intervention` (strict pausiert beim 1. Loop-Signal, relaxed beim 3.) | `HEAD` | Live: `GET /models` → standard; POST strict → GET strict → Reset (echter Serve); Control sichtbar in der App; 4 Tests |
| P4: Main-Agent-Rolle in Settings sichtbar (Backend hatte sie; die UI rendert die fünfte Rolle + Smoke verlangt alle fünf) | `HEAD` | Live-DOM: „Main Agent" neben „Watcher / Triage" in der gebauten App (`hasMainRole:true`) |
| F10: Claim→Review-Zyklus auf den feuernden Kanälen (record_claims queued bei offenem Contract → `voice·claim-check` im nächsten Vertragsblock; Event `guard_check/claim_pending_review`) | `c1875ec` | ~~Root Cause: `pre_verify` unerreichbar~~ **korrigiert 13.09.: Host füllt `_turn_file_mutation_paths` bei landed Edits, Gate live bewiesen** (PROJECT-STATE §4 Auto-Continue); 3 Tests inkl. Exactly-once-Lieferung; Live-Trace in der App erbracht |
| P2 Tool Hub (DoD §12): lokales Registry (`toolhub.discover/search` über Tools/Testing-Tools/Skills/MCP) + Hub-Manifest-Builder mit Verweigerungsregeln; Public-Hub-Spezifikation (`docs/TOOL-HUB.md`) | `0f91063` | Live: 105 Einträge, check_readme rankt #1, `hub OK: godsv-verified, auto: False`; 6 Tests |
| P2 Long-Horizon-Eval (DoD §18): `tools/gv_eval_longhorizon.py` — echte Serve-API + Verifier (pytest-Evidenz) + Pause/Research-Store + Testing-Tool am Gate | `dc67ce1` | 2/2 premature abgelehnt (Runner-Evidenz), 3 Interventionen + 2 Novel-Eskalationen, Testing-Tool PASS, **VERIFIED_COMPLETE**; shadow: 0 Interventionen, Gate identisch — Tabelle in `docs/EVAL-LONGHORIZON.md` |
| Mutierende Completion-Routen liefern session-scoped Payload (vorher: fremder/letzter Zustand) | `f33f60c` | Eval las vorher Fremddaten; danach Response == gespeicherter Contract; 31/31 Plugin-Tests |
| P1-7 Reale Token-Messung: `measured_session_usage()` liest die echten Session-Zähler aus der Host-`state.db` (input/output/cache/reasoning + Kosten), strikt getrennt von `*_est`-Schätzungen; `godsvoice metrics --session-id X` | `2020e7c` | Live: Session `…ec74ff` → input 18055 / output 511 / cache_read 98496 / reasoning 177 / $0.0059 **gemessen** |

Zusätzliche Live-Zeugen aus den Läufen: eine echte Voice-Pause auf den Main Agent
(`known_incident_repeat` mit Incident-Evidenz — Incident Memory wirkte live), und die
Testing-Tool-Validierung negativ+positiv durch die Sandbox.

**Neue Flaws (13.09.):**
- **F13 (behoben `93e0785`):** Session-Objekte werden am Turn-Ende entsorgt
  (`on_session_end` → `_SESSIONS.pop`) — die P1-4-Zähler resetteten dadurch jeden
  Turn und die Schwelle war über Turns unerreichbar. Zähler persistieren jetzt in
  `<contract>.drift.json`; zusätzlich Regex-Fix: relative Shell-Redirect-Ziele
  (`> src/foo.md`) wurden nicht als Edits gespiegelt. Verbleibend: Schwellen-Tuning
  nach echten Long-Runs.
- **F14:** `progress_stall` zählt im echten Turn weiter (40 Tool-Events) — noch kein
  Live-Feuern gesehen; Schwellen/Definition von „Progress" nach Real-Läufen kalibrieren.

## 6c. False-Loop-Fix (13.09., Owner-Evidenz)

**Bug (live an den Events dieser Session bewiesen):** Ohne stderr im Tool-Result
war `error_text()` = „exit code 1" — **alle** unverschiedenen Exit-1-Fehler teilten
den Fingerprint `67e08319607a`, der zugleich als Known-Incident-FP geseedet war
(aus dem alten `false`-Exit-1-Incident). Acht unrelated Fehler über ~50 Steps
wurden so zu „bekannter Fehler wiederholt sich" kollabiert → falsche Pausen
(u. a. nach einem Patch-SyntaxError direkt nach einem unrelated Fehler).

**Fix (`monitor.py`):** Detektoren liefern Signale, keine Verdikte —
- coarse (nur Exit-Status) → `error_without_details`, nie Repeat/Incident-Match;
- gleiche Signatur NACH echtem Fortschritt → `error_recurrence_after_progress`
  (Signal, kein Pause-Trigger);
- `known_incident_repeat` nur bei gleichem FP **ohne** zwischenzeitlichen
  Fortschritt; `error_burst` bleibt HIGH;
- ≥3 verschiedene Signaturen → `error_cluster` (Symptom-Signal, kein Trigger).
**Prompts v2** (`voice_triage`, `same_problem_judge`, v1 als History): lehren die
Semantik („a failure is not automatically a loop; temporal proximity is not
semantic similarity; error count is not root-cause") + Konfidenz-Stufen
(HIGH≥0.7 steuert, MED nur Hinweis, LOW steuert nicht).
**Regression (§32):** `tests/test_false_loop_regression.py` — exakte Szene,
Blur mit geseedetem FP, echter Repeat (mit/ohne Incident), Signal-only ohne
Pause-Trigger. Volle Suite grün.

### Einordnung nach Auftrag §35/§36 (SKIP · EXTEND · CONNECT)
| Subsystem | Kategorie | Begründung |
|---|---|---|
| Pact/Goal-State, Completion-Gate (P1-Strenge), Treasury, Pause-Lease+Watchdog+Heartbeat | **SKIP (already strong)** | mehrfach live bewiesen (VERIFIED_COMPLETE/INCOMPLETE, Voice-Pause auf den Main Agent); unangetastet |
| Deterministic Detectors (12 Familien) | **EXTEND** | Signale statt Verdikte: coarse/recurrence/cluster-Semantik, kein Blur-Match (Kap. 6c) |
| Incident-Memory-Retrieval | **EXTEND** | Judge-Prompt v2 (Konfidenz-Stufen, Anti-Muster); coarse nie gematcht; sammelt weiter „recurrence after progress" |
| Voice-Prompts (Triage/Judge/Redirect) | **EXTEND** | v2-Lehren: „a failure is not automatically a loop", Priorität user intent, NO_INTERVENTION-Option |
| Investigator | **EXTEND** | §10-Ausgang `NO_INTERVENTION_NEEDED` real: Pause endet ohne Redirect (Event `pause_no_action`) |
| Research-DB, Tool Hub, Testing-Tools, Mining, Claims (F10) | **SKIP (already strong)** | P0-3/P2-8/§11-Metriken + F10 — Tests + Live-CLI/API |
| Testing-Tool-Metriken (§26, `testing_tool_stats`) | **DONE** | runs/passes/fails/`bugs_detected` je Tool + Validation je Manifest; false passes strukturell ausgeschlossen; live in `godsvoice metrics` (4 Tools, 11 Runs, 2 finds) |
| Strategy-Novelty (§5-Rest/§6, `strategy_repeat`) | **DONE** | gleicher Fehler nach gleicher Tool-Strategie wird als Blind-Retry geflaggt (anderer Ansatz ruhig); Pause-Trigger 2; 4 Regressionstests + Demo-/Eval-Zahlen konsistent |
| Voice-Qualitäts-Metriken (§34) | **DONE** | `voice_quality` aus der durable Pause-Historie: delivered/no_action, recovery_success_rate, recurrence_after_intervention, steps_before_first_intervention; live: 12 Interventionen, 0.7 recovery, 3 Recurrences |
| Weitere E2E-Szenarien (§33) | **DONE (Kern)** | `gv_eval_longhorizon.py`: die Nutzer-Szene → nur `stall`, keine Pause; irrelevant-Incident → kein Match; 2× identisch → `error_burst`-Signal OHNE Pause; 3× → echte Pause; Claim-Contradiction → echte Pause. Voller Lauf: Goal → 2× Premature-Reject → Voice-Chain 3 Runden (novel) → Testing-Tool PASS → VERIFIED_COMPLETE (`pytest: ok`), Latenz 1.3 s, 7 Steps |
| §33-Rest (Compression-Verlust) | **DONE** | Harness-Szenario `compression_recovery`: frischer Prozess (kein Chat) rekonstruiert Contract-Block aus durable State — `state=INCOMPLETE`, `dod 0/2`, Blocker; live im vollen Lauf (`compression_recovery_ok: true`) |
| §33-Rest (Goal-Drift-Langlauf) | **DONE** | Harness-Szenario `goal_drift` live: gleicher Fremd-Pfad 3× → Pause; **3 verschiedene Fremd-Pfade → Pause** (Fix: Klasse zählt über Pfade, vorher Lücke — 3 Regressionstests, vor dem Fix rot); In-Scope bleibt ruhig. Voller Lauf: `goal_drift_varied_paths_paused: true` |

### F10-Live-Quittung (13.09., Cockpit-Session)
Der Claim→Review-Zyklus wurde live auf der Arbeits-Session verifiziert (Contract per Serve-API auf die
Session gebucht, 2 bewusst unverifizierte Kriterien): `events.db` zeigt **seq 4339 `claim`
Kategorien `["done"]` aus einer echten Agent-Antwort** und **seq 4340 `guard_check` mit
`kind: "claim_pending_review", open_items: 2`** — die Review-Queue feuert genau unter der Bedingung
„offener Contract + unverifizierte Requirements" (vorher, ohne Contract: 0 Events — korrekt).
Der Deliver in den nächsten Vertragsblock ist im Code exactly-once (Z.781-786) und per Suite-Test
belegt; die Live-Beobachtung der Injektion steht als letzter Feinschliff aus (Auto-Continuation-Turns
laufen ohne sichtbaren pre_llm-Pfad).

### F15 — „Waking up"-Stall (13.09.) — Messfehler erkannt, Härtung bleibt, Rest offen
**Ehrlicher Stand nach der Live-Untersuchung:**
1. Der in dieser Runde „live reproduzierte" Hänger war ein **Messartefakt des Probes**: `ChatSwapOverlay`
   ist permanent gemountet (Chat-Index 732) und trägt den Text „Waking up <profil>…" auch bei
   `profile=null` — dann nur `opacity-0`. `innerText.includes('Waking up')` liest ihn trotzdem.
   Belege: Screenshot `ui-artifacts/audit/gv-app-truth-20260913.png` zeigt die App gesund (Session
   `20260913_111833_6eaf46` mit Verlauf, Composer, Live-Block; kein Overlay), plus Sichtbarkeits-Probe
   über `getComputedStyle`/`elementFromPoint`.
2. Die Resolver-Probe per Backend-WS (`session.activate` → `4001` für eine in `session.list` gelistete
   Session) ist **als Testartefakt einzuordnen**: Der Renderer öffnet dieselbe Session erfolgreich
   (Screenshot). Host-Referenz für echte stranded Sessions bleibt
   `tui_gateway/methods_session.py::_resume_adopt_stranded` (nur profile-scoped) — Relevanz für die GV-App
   unbestätigt.
3. **Härtung bleibt drin** (live als geladen und ausgeführt bewiesen — `[f15] armed`-Logs im
   CDP-Console-Stream): `sdk/index.ts` Owner-Token + Owner-geprüfte Deadline (räumt Overlay, surfaced
   stranded-Recovery); `store/profile.ts` age-basierter 90-s-Watchdog (wert-stabil, immun gegen
   Release-Kollisionen überlappender Switches) + `window.__f15Watch`; alles über
   `app-overlay/patch_app_f15b.py` (33 Patches), `[f15]`-Marks als Diagnose-Kanal.
4. **Offen:** die Ursprungsbeobachtung (11:30 — Sends versickern still, Chat wirkt eingefroren) mit dem
   KORREKTEN Messverfahren (Screenshot/Visibility statt `innerText`) reproduzieren; danach Fix oder
   Schließen als nicht reproduzierbar.
Werkzeuge: `tools/gv_cdp_console_stream.py` (Live-Console via CDP), `tools/gv_cdp_script_search.py`
(Welcher Build läuft im Renderer?), `tools/gv_dom_probe.mjs`.

### VERIFIED_COMPLETE live bewiesen (13.09., Szenario 7 komplett) + repo_root-Fix
Der supervidierte Zyklus lief vollständig durch: unbelegter/offener Abschluss → `pre_verify STRICT reject
status=INCOMPLETE` (Auto-Continue arbeitet weiter, kein Nutzer-Input) → danach **`VERIFIED_COMPLETE`**
(Panel-Verdict der Session `20260913_142836_612f40`: `blocking_failures: []`, `verified_count: 1/1`,
Kriterium `c1` via runner-evidence zertifiziert; Store-State terminal).
**Dritter live gefundener Produktfehler derselben Kette (vom Agenten selbst diagnostiziert):**
`_repo_root_hint` leitete das Projekt-Root aus den Edit-Pfaden ab — Probe-Skripte in `/tmp` machten daraus
`/tmp`, der Guard führte registrierte Testing-Tools gegen das falsche Repo aus → runner-Evidence
`check_readme failed` → Kriterium unzertifizierbar. Fix (`8ea3e6e`): Der Contract trägt `task_base`
(Panel-`repo_root` wird als `task_base` gespeichert); `_repo_root_hint` und der Panel-Verify-Pfad bevorzugen
`payload.repo_root → task_base → cwd`. Belege: rot-first-Tests (`TestRepoRootDeclared`,
`test_repo_root_hint_prefers_contract_task_base`); live vorher `check_readme /tmp → FAIL`, nachher
`check_readme /home/willy/projects/gods-voice → PASS, source=runner`.

### Zwei live gefundene Produktfehler + Fixes (13.09., supervidierter Lauf)
Der supervidierte Lauf selbst hat zwei Defekte aufgedeckt und dokumentiert (Skill-Pitfalls); beide sind gefixt:
1. **`/dev/*`-Redirects wurden als `file_edit` gespiegelt** (`grep x 2>/dev/null` → datei-Edit auf `/dev/null`):
   Drift-Rauschen UND der Evidence-Freshness-Checkpoint sprang über jeden ausgeführten Check →
   `gate_dod` blockierte mit „needs a runner-executed check". Fix (`dbaa8ae`): Plugin filtert `/dev/*`-Ziele,
   `build_evidence_index` ignoriert `/dev/*`-Edits (deckt gespeicherte Alt-Events). Rot-First-Tests in
   `test_drift_signals.py` / `test_guard.py`; live: der nächste supervidierte Turn nutzte mehrfach
   `2>/dev/null` → **null** Pseudo-Edits.
2. **Kriterien, die ein registriertes Testing-Tool benennen, waren nicht verdrahtet:** `plan_verification`
   leitete nur aus tests/ui/api/persistence-Wortlaut ab; ein Kriterium mit Toolnamen im Text fiel auf
   `run` zurück und war damit nie zertifizierbar (Deadlock — der Agent dokumentierte den Pitfall selbst).
   Fix: `plan_verification(text, tool_names=…)` verdrahtet den registrierten Toolnamen (Wortgrenzen);
   `add_criteria` reicht die Registry (`register_testing_tools(runtime)`) durch. Tests:
   `TestPlanVerificationToolWiring` + `test_dod_registered_tool_certified_only_by_runner`
   (Runner-Evidenz zertifiziert, Tool-Event-Evidenz nicht — P1).

### §4 Auto-Continue live bewiesen (13.09., Szenario 7) — inkl. Korrektur eines Altbefunds
**Beweis (zwei unabhängige Sessions, echte GV-App, `GODSV_MODE=whisper`):**
1. `20260913_132509_08f201`: Turn schreibt Datei (`write_file`, `bytes_written`, landed) und endet mit
   unbelegtem Claim „Alles vollstaendig fertig und verifiziert." → Antwort wird `interim: true` markiert,
   `completion_rejected` (attempt=0) recorded, Trace **`pre_verify STRICT reject status=INCOMPLETE attempt=0`**
   → Host appendet Redirect-Nudge → **derselbe Run arbeitet OHNE Nutzer-Continue weiter** und liefert einen
   echten Verifikationslauf nach (`printf 'v1' | cmp - …` → `CMP_OK`, exit 0).
2. `20260913_132914_48f0e7`: gleiche Kette erneut; Datei wurde auf `v2` gebracht, cmp-Beleg (exit 0) folgte
   automatisch; Contract `INCOMPLETE` (`dod: c1/c2 open`), Agent untersucht weiter die Zertifizierung.
**Korrektur eines Altbefunds:** Der F10-Eintrag „`_turn_file_mutation_paths` wird nie befüllt → pre_verify
strukturell unerreichbar" ist **überholt/war falsch**: `agent/tool_executor.py::_commit_tool_result` ruft
`_record_file_mutation_result` (turn_explainers.py) bei `not blocked` auf; `landed` = `write_file` mit
`bytes_written` bzw. `patch` mit `success:true`. Der Kanal ist live erreichbar — genau so liefen die beiden
Beweise oben. (`guard_pre_verify` upstream, `max_verify_nudges` Default 3, Strict-Cap 40.)
**Durabler Betriebs-Fakt:** WS-RPCs adressieren den kurzlebigen `session.create`-Handle; **Contracts gehören
auf die durable `stored_session_id`** (sonst sieht das Plugin keinen Contract → Fallback-Reject „no blocking
acceptance criteria"; live erlebt). Beides dokumentiert in Skill → `references/live-app-driving.md`.

## 7. Wie du selbst prüfen kannst

```bash
cd ~/projects/gods-voice
.venv/bin/python -m pytest -q                       # volle Suite
node tools/check_desktop_plugin.mjs                 # Panel-Smoke (110 Checks)
godsvoice desktop build-app                         # App neu bauen (~2 min)
node tools/gv_ui_e2e.mjs 9333                       # Live-E2E gegen die GV-App
python3 tools/gv_product_run.py run                 # §6.2-Zyklus auf der echten API
.venv/bin/python tools/gv_eval_longhorizon.py       # DoD §18 Long-Horizon-Eval (+ --shadow)
```
Screenshots aller Runden: `ui-artifacts/audit/` und `ui-artifacts/`.
Doku-Kanon: dieses Dokument + `LIVE-STREAM-SPEC.md` (Produktziel),
`EXPERIENCE-V6-PLAN.md` (Stränge) + `RESEARCH-V6-INPUTS.md` (Fremd-Muster),
`UI-AUDIT-CHECKPOINTS.md`, `PRODUCT-RUN-6.2.md`.

**Verifikations-Re-Mapping (13.09. abends):** 4 unabhängige Code-Verifikations-Agenten bestätigten die Fixes und fanden einen HOCH-Bug im eigenen Fix (Sticky-Verified: persistierter Status zertifizierte Folgeläufe ohne frische Evidenz) — behoben in `499b7a5` (Skip-Zweig entfernt; MINOR⇒offen; zusätzlich die zwei CLI-Teilfixe `tools build --validation-case` + `desktop uninstall --remove-plugin`). Statusbilanz in `docs/FEATURE-MAP.md` Kapitel 10.

### 13.09.2026 (abends) — Audit-Runde: belegte Produktlücken repariert + Live-Beweis

#### F15 GESCHLOSSEN: 404-Reconcile-Loop gestoppt + Desktop-Turn live bewiesen (13.09. ~18:30)
**Ursache (live bewiesen, T8):** `use-background-sync.ts::reconcileTileTranscripts` liest alle ~10 s jedes Session-Tile
neu (`GET /api/sessions/<id>/messages`). Ein stale Tile aus localStorage (`sessionTiles.v2` + `layoutTree.v2`, Session
im Backend gelöscht) traf 404 — der bare `catch {}` ohne Zähler/Backoff/Eviction erzeugte den Endlos-Loop
("Session not found", 126→224+, ~6/min). Der Loop war real; nur die Overlay-ANZEIGE war teils das permanent
gemountete ChatSwapOverlay (Mess-Lehre bleibt).
**Fixes:** (a) `ac8894c` F15c — absolute Overlay-Frist ab erstem Arming + age-basierter 90-s-Watchdog
(`app-overlay/patch_app*.py`); (b) `ef43b82` F15c-404 — 404 im Tile-Reconcile ⇒ `closeSessionTile` + Signatur-Pruning
statt stillem Retry (`use-background-sync.ts`; Import+`catch` als Overlay-Anker; 9/9 appbuild-Tests).
**Live-Beweis (frischer Build):** 404-Zähler 5→5 (45 s) und 3→3 nach App-Neustart (vorher endlos wachsend);
Screenshot: kein sichtbares Overlay, Empty-State sauber; **volle Desktop-Runde:** Session `20260913_183421_230b31`
— user „Reply with the single word: ack" → assistant „ack" (State-DB + DOM `ackLeafEls=1`).
**Offen:** `gv_ui_e2e.mjs` 11 FAILs beim Lauf ohne offene Session (Triage: viele Checks setzen gemounteten Chat voraus).
#### Echter Long-Horizon-Agentenlauf GRÜN (13.09. ~20:00)
Fixture `/tmp/gv_lh_run` (textstat, 5 rote Tests über 4 Dateien) + GV-Vertrag (blocking pytest) →
supervisierter CLI-Lauf über den Go-Plan (`deepseek-v4.1-flash`/`opencode-go`), `--max-turns 200
--run-budget 1800`: **5 m 27 s, 70 Messages, 66 Tool-Calls → Suite 11 passed → `VERIFIED_COMPLETE`
(rev 1, c1 verified)**. Der Agent lieferte einen Release-Commit (`863f90e`, 8 Dateien, +250/−17):
top_words implementiert, robuste Worttrennung, ZeroDivision-Fix, CHANGELOG angelegt, README
synchronisiert — plus eigene Verifikation (`tests/test_readme_truthful.py`, `tools/verify_readme.py`).
Beleg: `/tmp/gv_audit/lh-run1.log`. **Agenten-Selbstnachweis im Abschlussbericht:** Baseline 5 failed/2 passed →
alle README-Beispiele extrahiert+ausgeführt (12 Claims, 0 Mismatches, verankert als `tools/verify_readme.py` +
`tests/test_readme_truthful.py`) → **Red-Proof** (Wegwerf-Kopie mit Original-`src/` + gefixten Tests: dieselben
5 Tests rot ⇒ die Fixes machen grün, nicht die Tests) → keine TODO/FIXME/NotImplementedError mehr, keine
erfundene Release-Historie. Testdateien + pyproject per Diff unverändert.

#### Long-Horizon-Supervisor-Eval GRÜN (13.09. ~19:53)
`tools/gv_eval_longhorizon.py` (voll, gegen die laufende App, deterministisch): **EXIT 0**.
Metriken: `final_state VERIFIED_COMPLETE` (pytest-Evidenz, 9 Schritte) · `premature_rejected 2`,
`interventions 3` · `false_loop_intervened false`, `irrelevant_incident_intervened false` ·
`real_loop_burst_detected true`, `claim_contradiction_intervened true` · Goal-Drift (same-path + varied)
beide pausiert+delivered · `compression_recovery_ok true` · registered testing tool `check_readme` → PASS.
Semantik-Teil (`--semantic-only`): EXIT 0. **Zusätzlich gestartet:** echter Long-Horizon-Agentenlauf auf
einem 5-Fehler-Fixture (`/tmp/gv_lh_run`, `textstat`) über den Go-Plan (`deepseek-v4.1-flash`,
`--max-turns 200 --run-budget 1800`) — Ergebnis in der nächsten Runde.

#### UI-E2E-Triage (13.09. abends) — **39/39 GRÜN** (Meilenstein)
**Endstand Lauf 11 (~19:50): `desktop UI E2E: all checks passed`.** Der Weg von 11 FAILs: (1) CU-Gruppe —
E2E-Tool-Bug (Settings-Subtab nicht gemountet, Settings-Re-Klick); (2) Goal/Feed-Gruppe — synthetische
KeyboardEvents senden nie → Node-seitiger `Input.dispatchMouseEvent`-Handoff über `window.__gvSendAt`,
`visibleComposer()` (mehrere Tabs!), Element-Warteschleifen statt fester Sleeps; (3) v6-nav — kein
fehlendes Feature, sondern die App-Identität: Overlay-Patch `electron/main.ts:915` Default `'Hermes'` →
`"God's Voice"` (UA `God'sVoice/0.17.1` matcht das Plugin-INTEGRATED-Gate); (4) verify-button — Element-Wait
nach dem Goal-Self-Heal. Commits: `b14db1c`, `e6721c9` + Docs.

#### UI-E2E-Triage (historisch, 13.09. abends) — 11 → 2 FAILs (Goal/Feed-Gruppe geheilt)
- **CU-Gruppe (3) war ein E2E-Tool-Bug:** nach der Tool-Detail-Navigation sind die Settings-Subtabs nicht mehr
  gemountet → `clickTab('Computer Use')` fand nichts (Kaskade). Live-Probe: CU-Tab voll intakt (`Isolation` +
  `API > DOM > Vision` + Run-demo-Button). Fix im E2E (Settings vor CU re-assert) → 3 Checks grün (32 ok).
- **Goal/Feed-Gruppe (5: goal-edit, chat-feed, feed-row, drawer-close, focus-return):** brauchen eine Session mit
  aktivem Goal + godsv-Feed; auf `#/` (Home) strukturell nicht erfüllbar → Precondition, kein Produktfehler.
  E2E-Self-Heal (Goal anlegen) als Folgeschritt vorgemerkt. `@goal mention` flaky (Lauf 1 grün, 2+3 rot).
- **Goal/Feed-Gruppe GELÖST (13.09. ~19:10):** Ursache war doppelt — (a) synthetische KeyboardEvents senden nie;
  der Self-Heal sendet jetzt über einen echten `Input.dispatchMouseEvent`-Klick (Node-Seite, Handshake über
  `window.__gvSendAt`), (b) `querySelector('[contenteditable=true]')` traf bei mehreren Tabs den versteckten
  Composer → `visibleComposer()` überall; dazu Element-Warteschleifen statt fixer Sleeps. **Lauf 7: 37 ok,
  `goal edit opens inline` + komplette Feed-/Drawer-Gruppe grün.** Commit `b14db1c`.
- **`@goal mention` flaky (neu):** Der Goal-Self-Heal startet unmittelbar davor einen echten Turn — ein laufender
  Turn unterdrückt den Mention-Popover. Fix: echtes Turn-Ende abwarten (idle() ist dafür unzuverlässig) oder
  Check-Reihenfolge drehen — offen, klein.
- **`integrated app nav` — GELÖST (13.09. ~19:45):** Ursache war kein fehlendes Feature, sondern die App-Identität: Ohne `HERMES_DESKTOP_APP_NAME` liefert Electron den UA `Hermes/…`, das Plugin-Gate `/SkaldirAgent\//` (INTEGRATED-Zweig: 6 native Areas, Aside aus) matchte nie. Fix: Overlay-Patch `electron/main.ts:915` Default → `"God's Voice"` (gebaut + live verifiziert: UA `God'sVoice/0.17.1`, `integrated app nav` grün).
- **`integrated app nav` (1) — offener v6-Punkt (historisch):** Erwartung aus Commit `8dd4efb`: Areas als native App-Sidebar,
  "no second nav layer inside the page" — erreicht? Nein: die Panel-eigene Nav `aside.w-44` (CONTROL/MANAGE)
  wird weiter aus dem Plugin gerendert (`cb9a6a0` checkpoint v1). Offen für den Session-UI-Auftrag.

**T8-Vorfall (transparent):** eine Test-Session (`20260913_142836_612f40`) wurde bei der Backend-Gegenprobe per
PATCH/DELETE gelöscht (Reste in events.db/completion; keine Row-Recovery möglich). Lehre: Diagnose-Aufträge explizit
GET-only fassen.

Unabhängiger Audit (BERICHT.md) reproduziert und adressiert. Commits: 05e8de4 · ef7585e · f784ffe · 249a9cb · 5aae51e · 3c59c52

**P0 Stop-Lücke (beide Hälften):**
- Host: `_pre_verify_nudge` verlangte `_turn_file_mutation_paths` — jetzt wird der
  `pre_verify`-Hook bei jeder Textantwort gefragt (der Hook entscheidet; Pause/Budget
  bleiben Host-eigen). Verankert als Overlay `host-overlay/patch_agent.py` (idempotent,
  lauter Abbruch bei fehlendem Anker) + Host-Regressionstest.
- Plugin: `pre_verify` stieg still aus, wenn die Session nicht in `_SESSIONS` lag
  (on_session_start feuert bei `hermes chat --resume --oneshot` nie). Der Wrapper
  adoptiert die Session, wenn ein On-Disk-Vertrag existiert.
- **Live-Beweis (CLI):** Falscher erster Abschluss ohne Dateiänderung
  (`edited=0`, „Done, all tests pass.") → unabhängige Ablehnung → Agent repariert
  double() selbst (2 file_edits) → realer pytest → **VERIFIED_COMPLETE**
  (Store: c1 verified). Session 20260913_171809_a6cf0f.
- GV-Desktop-Instanz neu gestartet (lädt Host-Patch + Plugin via Symlink); Backend
  lauscht jetzt auf 39781 — der gv_serve-Identitäts-Fix (Probe statt Port-Reihenfolge)
  war dafür notwendig.

**Weitere Fixes:** strukturierter Testing-Tool-Ergebnisvertrag (parse_testing_result:
FAIL gewinnt, kein Verdikt ⇒ fail-closed) · Evidenz-Bindung an Revision+Workspace
(RunEvidence.revision/workspace; alte Evidenz zertifiziert keine neue Revision) ·
`register_testing_tools` löst das Modul über das Manifest auf (module != Verzeichnis) ·
`add_criteria` mit expected_revision- und request_id-Vertrag wie update/remove ·
Monitor beurteilt strategy_repeat und error_recurrence_after_progress gemeinsam
(kein widersprüchliches Paar nach echtem Fortschritt) · gv_serve verifiziert die
Backend-Endpunkt-Identität per API-Probe.

**Offen (ehrlich):** MCP-Live-Insertion ohne Serverneustart · Restart-Recovery zwischen
Redirect und Continue · Budget-/User-Pause-E2E · UI-Anforderungen (Fragen-Merge,
Neuwürfeln, 10-min-Klärung) · Desktop-Turn-E2E (Backend lädt Fix, Turn-Beweis steht
auf CLI).
