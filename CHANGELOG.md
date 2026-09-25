# Changelog — God's Voice

Alle nennenswerten Änderungen. Format orientiert an Keep a Changelog;
Versionen folgen den Meilensteinen aus `docs/CORE-PLAN.md`.

## [1.6.0] — 2026-09-14 — Live-Control-Plane (Blöcke 1–8, §40 Stufen 1–5)

Architekturgrenze gewahrt: Hermes bleibt Execution Substrate, God's Voice die Control Plane.
Keine zweite Runtime, Registry oder MCP-Runtime. Alle Blöcke einzeln rot-first belegt.

### Sicherheit und Zustandsintegrität (Block 1)
- `gate_security` fail-closed **mit Fallunterscheidung**: Scanner-Crash/fehlendes Modul ⇒ BLOCKER-Finding,
  sauberer Scan ohne Treffer ⇒ kein Finding. Ein Secret erzeugt nur noch **ein** Finding (Dedupe in `scan()`).
- Panel-Trust-Boundary: `origin_allowed("")` ⇒ False, `_guard_origin` fail-closed, Mutationen ohne
  Origin/Token abgelehnt (Desktop-Pfad bleibt erlaubt). `_audit`-Fail-open geschlossen (WARNING statt `pass`).
- Contract-Store schreibt atomar (Temp + fsync + `os.replace`) und serialisiert.
- Generierte Tools laufen über `gates._run` in der bwrap-Sandbox, fail-closed ohne Sandbox.

### Run-Lifecycle (Block 2)
- `src/godsv/run_supervisor.py`: Invariante `NON_TERMINAL_RUN => ACTIVE_WORKER_LEASE OR QUEUED_CONTINUATION
  OR EXPLICIT_WAIT_WITH_WAKE_SOURCE`; Run-Record mit 16 Pflichtfeldern; Exactly-once-Continuation über
  `UNIQUE(run_id, generation)`; Side-Effect-Recovery nur bei Idempotenz oder verifizierter Result-Existenz.
- Marker-Erkennung der laufenden Instanz über echte userData-Pfade und `/proc/PID/environ`.

### Verification Closure (Block 3)
- Echte Runner für `run`, `ui_e2e`, `api`, `build`, `persistence_restart`: Exit 0 **und** explizite
  `PASS`-Zeile, sonst fail-closed; `persistence_restart` verlangt eine beobachtete SHA-256-Änderung.
- `gate_tests`-Deadlock aufgelöst; Evidenz an Session + Goal-Revision + Check + Workspace gebunden
  (eine leere Workspace passiert den Zaun nicht mehr).

### Live-Control und Kalibrierung (Block 4+5)
- Untrusted-Boundary: `prompt_injection_signal` und `untrusted_instruction_attempt` sind Signale,
  **keine** Verdikte; nur eine adressierte, ausführbare Anweisung wird neutralisiert.
- Wrong-Tool-Call-Interception vor der Ausführung (ALLOW / ASK_APPROVAL / PAUSE_AND_INVESTIGATE / BLOCK),
  Security-Pfade fail-closed; Muster über normalisierte Wirkungssignaturen statt String-Gleichheit.
- Golden Set §38: 39 Tests, alle 15 Pflichtfälle (11 testbar, 4 als `OPEN_LIVE_ONLY` ausgewiesen).

### Capability- und Memory-Learning (Block 6+7)
- Capability: kausale Generalisierung statt Literale (`inspect_blender_project(project_path)`),
  Search-before-build, Holdout-Validierung, Health-Lifecycle mit acht Zuständen, Live-Insertion.
- Memory: Incident-Schema (11 Felder) rückwärtskompatibel; Retrieval-Regel `retrieve → Anwendbarkeit →
  use/reject` (ein schwacher Match löst **nie** eine starke Intervention aus); Staleness/Provenance.
- Der Live-Monitor wird jetzt mit `intervention_signatures()` gespeist statt mit dem zu breiten `signatures()`.

### Benchmark und E2E (§40)
- `tools/gv_benchmark.py` nach `BENCHMARK-RUN-SCHEMA.json`: sieben Konfigurationen, Konstanten-Prüfung
  fail-loud, Metrik-Aggregation ohne Schätzung (nicht Messbares ⇒ `null` + Begründung).
- Zwei **real** gefahrene Konfigurationen dokumentiert (K1 semantic-only, K2 volle GV-Kette); der
  Konstanten-Vergleich scheitert an fehlenden Schema-Referenzwerten und wird als solches gemeldet.
- §40 Stufen 1–5 real belegt (4× BEWIESEN, 1× TEILWEISE).

## [1.5.0] — 2026-09-13 — Semantische Voice + Harness-Härtung (Audit §1–§36)

### Repariert
- **False-Loop-Kollaps:** Ohne stderr fiel der Fehlertext auf `"exit code 1 (no output)"`
  zurück — alle Exit-1-Fehler teilten einen Fingerprint, der als Known-Incident geseedet war;
  8 unzusammenhängende Fehler über ~50 Steps wurden als „bekannter Fehler wiederholt" gewertet
  und pausierten den Main Agent. Fix: `error_without_details` (coarse — nie Match, kein
  Trigger), `error_recurrence_after_progress` (kein Trigger), `error_cluster` (Beobachtung);
  `known_incident_repeat` nur bei gleichem Fingerprint ohne zwischenzeitlichen Fortschritt.
  Regression: `tests/test_false_loop_regression.py` (8 Tests).
- **Gradual Goal Drift:** Drei Scope-Verletzungen über *verschiedene* Pfade erreichten die
  Schwelle nie (Zählung pro Pfad-Fingerprint) — der realistische Drift-Fall eskalierte nicht.
  Fix: `scope_drift` zählt die Klasse über Pfade hinweg. Regression:
  `tests/test_goal_drift_gate.py` (3 Tests, vor dem Fix rot).

### Neu / Erweitert
- **Investigator-Ausgang `NO_INTERVENTION_NEEDED`:** Pause endet ohne Redirect (Event
  `pause_no_action`); Prompt `voice_redirect.v2` mit Guardrail (nie zum Umgehen harter Befunde).
- **Semantik-Prompts v2** (`voice_triage.v2`, `same_problem_judge.v2`): Detektoren liefern
  Evidenz, keine Wahrheit — Konfidenz HIGH ≥0.7 steuert, MED nur Hinweis, LOW nicht.
- **Voice-Qualitätsmetriken** (§34, `voice_quality` aus der durablen Pause-Historie):
  recovery_success_rate, recurrence_after_intervention, no_action_rate,
  steps_before_first_intervention — live: 12 Interventionen, 0.70 recovery, 3 Recurrences.
- **Testing-Tool-Metriken (§26, `testing_tool_stats`):** runs/passes/fails/`bugs_detected`
  je Testing-Tool aus dem Usage-Ledger + Validierungs-Status je Manifest; false passes/fails
  strukturell per Validation ausgeschlossen. In `godsvoice metrics` sichtbar (`testing_tools`);
  live: 4 Tools, 11 Runs, 2 finds. Regression: `tests/test_testing_tool_stats.py` (3 Tests).
- **Strategy-Novelty (§5/§6):** neues Signal `strategy_repeat` — derselbe Fehler kehrt nach
  derselben Tool-Strategie zurück (Name+Args-Signatur, ≤2 Steps Abstand) → Warnung „neue
  Strategie prüfen statt Blind-Retry"; ein ANDERER Ansatz nach demselben Fehler bleibt ruhig
  (normaler Debug-Verlauf). Pause-Trigger ab 2. Regression: `tests/test_strategy_novelty.py`
  (4 Tests: Wiederholung erkannt, Strategiewechsel nicht geflaggt, Fehler ohne Tool-Call
  sicher, Trigger pausiert).
- **Eval-Harness erweitert** (`tools/gv_eval_longhorizon.py`): Semantik-Szenarien
  (False-Loop/Fremd-Incident/Burst/Eskalation/Contradiction), Compression-Recovery
  (frischer Prozess rekonstruiert den Vertragsblock), Goal-Drift (gleiche und variierende
  Pfade eskalieren, In-Scope bleibt ruhig).
- **Live-Quittung Claim→Review** (Cockpit-Session): `claim ["done"]` →
  `guard_check {kind: claim_pending_review, open_items: 2}`; ohne Contract bleibt es beim
  korrekten Ausbleiben. F15 (Session-Hydration-Stall) erklärt; Backend-WS als robuster
  Live-Kanal dokumentiert (Skill `gods-voice` → `references/live-app-driving.md`).

### Dokumentation
- **`docs/IMPLEMENTATION.md` (neu):** komplette, grep-verifizierte
  Implementierungsdokumentation — Architektur, Voice-Fähigkeiten (16 Signal-Familien,
  Schwellen, Kosten-Gate, Pause-Protokoll, Completion-Supervisor, Lernen) und Tools
  (Testing-Tools, Pipeline, Tool Hub, Treasury, Panel-API).

## [1.4.0] — 2026-09-12 — Strict Completion Supervisor + englische UI/CLI

### Neu
- **Strict Completion Supervisor** (`src/godsv/completion.py`): Der Main Agent
  kann nur noch **Completion anfragen** — entschieden wird über echte Fakten.
  - State Machine: `WORKING → COMPLETION_REQUESTED → VERIFYING → VERIFIED_COMPLETE
    | INCOMPLETE (→ REDIRECT → WORKING)`; Endzustände außerdem `BLOCKED_USER`,
    `BLOCKED_EXTERNAL`, `FAILED_UNRECOVERABLE`, `USER_CANCELLED`,
    `SHIPPED_WITH_USER_OVERRIDE`. **`INCOMPLETE` ist kein Endzustand.**
  - **Gates über echte Fakten:** evidence (nur ausgeführte Checks, frisch nach dem
    letzten Edit), tests (Code geändert ohne Testlauf → BLOCKER), build,
    integration (neues Modul ohne Import → BLOCKER), fake-completion
    (TODO/placeholder/skip im Diff), security (Secret im Diff), cleanup,
    definition-of-done (blocking braucht Nachweis; Nice-to-haves → MINOR).
  - **Loop-Tracking:** gleicher Blocker-Fingerprint erneut → `escalate`
    (Strategiewechsel statt Blind-Retry); Timeline + Metrics persistiert.
- **Mandatory Completion Loop im Plugin**: `pre_verify` verifiziert bei **jedem**
  Versuch (kein Einmal-Nudge); Rejection → `action=continue` mit präzisem Redirect,
  bis verifiziert oder ehrlich blockiert. Shadow beobachtet nur; Hooks für nie
  gestartete Sessions sind No-ops.
- **Panel: Tab „Completion"** (jetzt 11 Tabs): Goal + Requirements ✓/✗,
  Produkt-Status-Grid, Verify now / Begin goal / User-Override, Timeline, Metrics.
  API: `/completion`, `/completion/verify`, `/completion/goal`, `/completion/override`.
- **CLI**: `godsvoice completion status|history|begin|override`.
- **Capability-Mining** (`src/godsv/capability_mining.py`): wiederkehrende
  Requests + Kommandos → Kandidaten (`skill | cli | mcp | testing_tool`) mit
  occurrences/sessions/workaround/secrets; `godsvoice tools mine|candidates|promote`
  (Promotion in die bestehende Gates-Pipeline). **Testing-Tool-Negativtest:**
  `validate_testing_tool` — ein Tester, der am kaputten Ziel „PASS" meldet, ist
  `invalid` und darf nie als Completion-Evidenz gelten.
- **Englische UI/CLI**: Panel (~200 Strings), CLI (86 Stellen), Panel-API,
  Guard-/Capability-/Treasury-Meldungen; Tests/Smoke/UI-E2E nachgezogen.

### Beweis
- `tests/test_completion.py` (22) + `tests/test_completion_loop_e2e.py` (13):
  Mandatory Full-Loop (Voice lehnt 2× ab → Agent arbeitet automatisch weiter →
  3. Request vollständig belegt → VERIFIED_COMPLETE), §41-Matrix (disconnected UI,
  fake data, tests-not-executed, persistence, runtime, regression, actual
  completion), Crash-Safety (Absturz während Verifikation → nie „complete"),
  §44-Invarianten.
- Plugin-Integration: 6 neue Tests (Rejection auch bei attempt>0, Verifikation nur
  mit echter Evidenz, Eskalation, BLOCKED_USER, ehrlicher Cap) + Guard-Tests auf
  den neuen Vertrag umgestellt; Ghost-Session-Robustheit (`started`-Flag).
- Mining: 10 Tests inkl. Negativtest (lügender Tester ⇒ `invalid`) und CLI-E2E
  (`tools mine → candidates → promote`).
- Node-Smoke: 69 Checks inkl. Completion-Tab; reguläre Suite komplett grün.

## [1.3.6] — 2026-09-12 — „Test credential safely" (§16) + Sandbox-Fix

### Neu
- **`Broker.selftest(handle)`**: sicherer Credential-Selftest ohne jeden
  Netzwerk-Call — Handle existiert, Vault entschlüsselt, das Secret erreicht
  **nur den Env des isolierten Subprozesses** (bwrap), Ausgabe gescrubbt,
  audit-geloggt (`treasury_selftest`). Kein Grant nötig (Vault-Eigentümer-Test),
  niemals Klartext in der Antwort.
- **Treasury-Tab**: pro Credential ein **„testen"**-Button mit Ergebniszeile
  (✓ Injection ok / ✗ Grund). Endpoint `POST /treasury/credentials/test`
  (Origin-Guard, Handle-Validierung).

### Bugfix
- **`gates._run`**: relative `workdir`-Pfade brachen die **bwrap-Sandbox**
  (`bwrap: execvp …: No such file or directory`, weil relative `--bind`-Ziele
  nicht funktionieren). Workdir wird jetzt absolut aufgelöst — betrifft alle
  sandboxed Runs (Gates, Broker, Selftest).

### Beweis
- `tests/test_panel_actions.py` (+3): Selftest beweist Injection (`injection-ok`)
  **und** dass das Klartext-Secret in keiner Antwort auftaucht; unbekannter
  Handle → deny; ungültiger Handle → 400.
- Node-Smoke (+1 Check, 62 gesamt). **Echte Electron-App: 24/24** — Klick auf
  „testen" im UI (wenn ein Credential existiert), DOM-Secret-Scan 0.

## [1.3.5] — 2026-09-12 — Usage-Ledger: Nutzungsstatistik & Fehlerhistorie (§15)

### Neu
- **`src/godsv/toolusage.py`**: append-only Usage-Ledger (`tool_usage.jsonl`) —
  jeder Tool-Aufruf wird gezählt (ok/Fehler, Dauer), **Fehlertexte laufen durch
  den Secret-Scanner-Redactor** (§11), niemals Payloads.
- **MCP-Wrapper** (`mcpserver._wrapper_for`) und der **generierte CLI-Wrapper**
  (Builder-Template) schreiben ins Ledger — beide realen Ausführungspfade.
- **Tools-Tab**: Spalte „Aufrufe" in der Registry; **Tool-Detail** bekommt die
  Sektion **„Nutzung & Fehler"** (Aufrufe, Erfolge/Fehler, Erfolgsquote,
  Ø Dauer, zuletzt, letzte Fehler) — §15.
- `tool_detail_payload` + `tools_payload` liefern die Statistik mit.

### Beweis
- `tests/test_mcp_server.py`: MCP-Roundtrip schreibt Ledger-Eintrag (ok + Dauer).
- `tests/test_panel_actions.py` (+2): Redaktion (`sk-…` erscheint nicht im
  Fehlertext), Zählung/Durchschnitt, Detail- und Listen-Integration.
- **Live im echten Home**: `money_convert_v2` gebaut → 1× ok (`{"converted":
  270.0}`) + 1× Fehler (`ValueError: could not convert string to float`) →
  Ledger enthält beide Einträge mit redigiertem Fehlertext.
- Node-Smoke: +2 Checks (61 gesamt); **echte Electron-App: 23/23** — inkl.
  Klick aufs Tool-Detail mit sichtbarem Usage-Ledger.

## [1.3.4] — 2026-09-12 — Panel: Computer-Use- und Evals-Tab; §20-Token-Metriken

### Neu
- **Computer Use**-Tab: Live-Runner-Probe (dry-run / bwrap / podman), feste
  Policy (Kanal-Reihenfolge API > DOM > Vision, Grenzen, Isolation),
  **„Demo ausführen"** (echter CU-Executor + DryRunRunner, persistiert in
  `cu_runs.jsonl`) und die letzten Läufe.
- **Evals**-Tab: Episoden-Katalog der Harness, **„Eval ausführen"** (führt die
  komplette Suite real aus, persistiert in `eval_runs.jsonl`, PASS/FAIL je
  Episode), Token-Metriken (§20): **MEASURED** (Zählungen aus dem Event-Store)
  strikt getrennt von **ESTIMATED** (Tokens ≈ Zeichen/4, gelabelt).
- Backend: `GET /cu`, `POST /cu/demo`, `GET /evals`, `POST /evals/run`
  (Origin-Guard wie alle mutierenden Endpunkte).
- Navigation damit komplett: Live, Models, Pausen, Memory, Skills, Tools,
  Treasury, Computer Use, Evals, System (Spec §22).

### Beweis
- `tests/test_panel_actions.py` (+4, gesamt 15): echte CU-Demo über den
  Executor + Persistenz; Eval-Suite real ausgeführt (passed == total);
  ehrliche Null-Metriken auf leerer Runtime.
- Node-Smoke (+9 Checks, 59 gesamt) für beide Tabs.
- Echte Electron-App: **21/21 Checks** — inkl. Klick auf „Demo ausführen" und
  „Eval ausführen" im realen UI; DOM-Secret-Scan 0.

## [1.3.3] — 2026-09-12 — Panel: Capability-Request-Karte im Tools-Tab (Spec §17)

### Neu
- Der Tools-Tab führt jetzt mit **„Capability Requests"**: je Request eine
  Karte mit `CAPABILITY REQUEST`-Kopf, `requested by: main-agent`, Kind
  (mcp/cli), Ziel-Tool, Grund, Secret-**Handles** + erlaubte Domains, Status
  (… läuft / ✓ ready / ✗ fehlgeschlagen), verknüpftem Build mit den echten
  Pipeline-Stufen und klickbarem Ergebnis-Tool (→ Tool-Detail). Kein
  Build-Transkript, keine Secret-Werte.
- Backend: `tools_payload()` liefert `requests` (jüngster Stand je
  request_id; Build-Verknüpfung über den Tool-Namen; nur Handles).

### Beweis
- `tests/test_panel_actions.py` (+1): fulfilte Request mit Build-Stufen,
  offene Request, Handles-only-Assertion.
- Node-Smoke (+1 Check, 50 gesamt): „tools tab renders capability requests".
- Echte Electron-App (`tools/gv_ui_e2e.mjs`, 18 Checks, alle grün):
  Karte rendert mit echtem Request (`cap-5dcadb9776` → `money_convert_panel`),
  „requested by: main-agent" + Handle sichtbar, kein Klartext-Secret im DOM.

## [1.3.2] — 2026-09-12 — Capability-Gap-Flow: Pause → Tool-Build → Redirect → Resume

### Neu
- **Ein durchgehender Fluss (Spec §6/§17/§18/§19):** Meldet der Main-Agent eine
  fehlende Capability (er läuft `godsvoice tools request …` mitten in der
  Session), erkennt das Plugin den Request am Tool-Call, erzeugt ein
  `capability_gap`-Signal, und die **bestehende** Pause-Pipeline führt den
  **ToolBuilder** als Researcher aus: PAUSE → Build (Gates) → Redirect
  (`tool_created`: Name, how_to_use, Constraints — nie das Build-Transkript)
  → RESUME. Kein zweites System, keine Parallelarchitektur.
- `src/godsv/capability.py`: `CapabilityRequest` + append-only Store
  (`capability_requests.jsonl`), `ToolBuildRunner` (research_runner-kompatibel),
  `CapabilityAwareRunner` (Dispatch: capability_gap → Build, sonst Research).
- CLI: `godsvoice tools request …` (record) und `godsvoice tools fulfill [id]`
  (führt die Pipeline sofort aus — auch ohne laufende Session).
- Ein fehlgeschlagener Build ist trotzdem ein Redirect wert: der Agent erhält
  Grund + nächsten Schritt statt eines stillen Pause-Endes.
- Panel: `capability_requested`-Events erscheinen im Event-Stream.

### Beweis
- `tests/test_capability_flow.py` (9): Signal → echte `VoicePause` → Build im
  Research-Thread → `pause_delivered` mit Tool-Namen im Redirect; Request
  `fulfilled`; Tool `approved`; Fehlschlag- und Dispatch-Pfade.
- Live-CLI: `tools request` → `tools fulfill` → alle Build-Stages grün →
  Redirect-Text → Wrapper-Aufruf `{"converted": 270.0}` → `tools builds` zeigt
  `ready`.
- MCP über echten Prozess (`tests/test_mcp_stdio_e2e.py`): separater
  OS-Prozess (`python -m godsv mcp serve`), JSON-RPC auf stdin/stdout —
  `initialize` → `tools/list` (approved sichtbar, drafts gefiltert) →
  `tools/call` → `{"doubled": 10}` → Prozess-Neustart: Tool weiter registriert.

## [1.3.1] — 2026-09-12 — Voice-Context-Budget, Rollen-Profile, Model-Routing-Beweis

### Neu
- **Bounded & frischer Voice-Context (Spec §2):** Der Investigator baut seinen
  Kontext **immer neu** aus den Roh-Events der Stores — keine wachsende
  Voice-Conversation, keine „Summary of Summary"-Kette. Neue Tests beweisen:
  wiederholte Builds sind byte-identisch, ein „Neustart" (frischer Builder)
  rekonstruiert denselben Kontext, neue Events erweitern nur das Fenster.
- **Rollen-Profile (Spec §3):** `ContextBuilder.build(role=…)` kennt jetzt
  **watcher** (≤ 2500 Zeichen, 10 Steps Fenster) und **investigator**
  (≤ 4000 Zeichen, 24 Steps) — der Watcher bleibt klein und billig, die
  Untersuchung bekommt ein frisches, größeres Fenster. Default unverändert.
- **Instrumentierung (Spec §2/§20):** `DecisionContext.stats()` trennt
  **MEASURED** (Zeichen, Sektionen, Truncations) von **ESTIMATED**
  (Tokens ≈ chars/4, explizit gelabelt). Der Research-Run schreibt ein
  `voice_context_built`-Event (Rolle, Budget, Verbrauch, Truncations,
  `tokens_estimated`, `measure: ESTIMATED chars/4`) — sichtbar im Panel-Stream.
- **Investigator-Ausgabeformat (Spec §3):** `voice_redirect.v1.md` verlangt
  jetzt das volle Schema `problem / evidence / why_current_direction_is_bad /
  direction / avoid / confidence` (confidence wird von `synthesis.py` bereits
  geparst, jetzt auch angefordert).
- **Model-Routing-Beweis (Spec §5/§24):** `tests/test_model_routing.py` zeigt
  ohne echten Provider-Call, dass Rollen aus `godsv/models.yaml` in den
  Side-LLM-Kwargs ankommen — **zwei unterschiedliche Rollen** routen auf
  unterschiedliche Provider/Modelle, das Tier-Mapping (small→watcher,
  mid→investigator) greift, ohne Rollen wird kein Override angefordert, und
  ein geschlossenes Capability-Gate führt **fail-open** zum Retry ohne
  Override. Änderungen an der Rollen-Datei wirken auf den nächsten Call.

### Fixes
- `voice_context_built` als Event-Kind registriert (unbekannte Kinds wirft der
  Store ab — der Test deckte das auf); die Kontext-Instrumentierung ist
  bewusst best-effort und kann einen Research-Run nie abbrechen.

## [1.3.0] — 2026-09-12 — Model Selection, autonomer Tool-Builder, Smart Voice

### Neu
- **Model Selection (Desktop-UI + Backend):** God's Voice bekommt einen
  **Models-Tab**. Rollen (Watcher/Triage, Investigator/Research, Judge/Verification,
  Fallback) sind **getrennt** auf Provider+Modell einstellbar; das Main-Agent-Modell
  bleibt bei Hermes (read-only-Abzeige). Keine eigene Provider-Infrastruktur:
  Provider-/Auth-/Modelllisten kommen aus der **Hermes-Registry**
  (`provider_models_cache.json` + `config.yaml`, live 5 Provider / 113 Modelle),
  die Rollen liegen in `~/.hermes/godsv/models.yaml` (nur Namen — keine Secrets).
  Der Voice-Call reicht die Rolle über den **capability-gated** `ctx.llm`-Override
  durch (`plugins.entries.godsv.llm.allow_model_override`); bei geschlossenem Gate
  läuft der Call ohne Override (fail-open, getraced). **Test model**-Button: kleiner
  Connectivity-/Tool-Use-Smoke über Hermes' LLM-Runtime (`✓ auth · ✓ response ·
  ✓ tool-call · Latenz`) — Keys werden nie angezeigt oder zurückgegeben.
- **Autonomer Tool-Builder** (`godsvoice tools build`): Capability-Gap → Build →
  Gates → Register. Ehrliche Pipeline-Stufen (`requested → researching →
  generating → manifest → dependency_inspection → gates → register → ready`) mit
  JSONL-Build-Queue; MCP-vs-CLI-Entscheidung (`choose_kind`); **Manifest**
  (type, purpose, network.allowed_domains, required_secrets als HANDLES…);
  AST-**Dependency-Inspection** (undeklarierte Imports blocken); Ausführung über
  die bestehenden **Gates** (Secret-Scan, Import, Tests, Determinismus in
  bwrap-Sandbox); Registrierung als `approved` im bestehenden `ToolStore`;
  CLI-Wrapper für den Main-Agent; Redirect-Payload `tool_created` (capability,
  how_to_use, constraints — kein Build-Transcript). MCP-Tools erscheinen im
  bestehenden `godsv-tools`-MCP-Server.
- **Tools-Tab im Panel:** Installed (Registry mit Typ/Status/Herkunft/letzter
  Test/Secret-Handles/Risiko), Build Queue (echte Stufen), MCP-Server (aus
  Hermes' `mcp_servers`), **Tool-Detail-Drilldown** (Manifest, Grants,
  Build-Historie) mit Aktionen: **Test** (führt den Wrapper mit Sample-Input aus),
  **Enable/Disable**, **Gates erneut prüfen**, **Grant credential**.
- **Treasury-Tab wird interaktiv:** `+ Add credential` (Service/Name/Wert →
  Handle `secret://…`, Wert danach nie wieder lesbar), Credentials-Liste
  („genutzt von", entfernen), **Tool-Grants** erteilen/widerrufen — der Klick im
  Panel ist die menschliche Freigabe; Mutationen sind audit-geloggt (Event-Store),
  Origin-geprüft, serverseitig validiert. Kein „Reveal secret".
- **Tool-gebundene Grants:** Grants tragen jetzt `tool_id` (Migration idempotent);
  `run_with_secret(tool_id=…)` läuft **fail-closed** ohne passenden Grant;
  Revoke entzieht den Zugriff sofort. Der Klartext erreicht nie Prompt, Events,
  Logs oder UI-Payloads (tests: keine Klartext-Secrets in irgendeiner Payload).
- **Smart Voice — mehr Signale, Kostenmodell:** neue deterministische Signale
  `hallucinated_api` (erfundene API/Signatur: has-no-attribute/cannot-import/
  signature), `known_incident_repeat` (Fehler aus der Incident-Memory,
  sessionübergreifend) und `diff_churn` (Diff wächst ohne verifizierten
  Fortschritt; Reset nur bei erfolgreichem Lauf). **Interventions-Kostenmodell**
  (`assess_intervention`): Pausen nur, wenn *expected cost of continuing >
  expected cost of the intervention* — mit **MEASURED**- (occurrences, severity)
  und **ESTIMATED**-Anteil (Tokens/Step × Severity × occurrences, gedeckelt);
  jede Bewertung geht als `voice_intervention_assessment`-Event in den
  Event-Store. Triage-Prompt um 10 neue Kategorien erweitert (scope_creep,
  goal_drift, overengineering, unnecessary_dependency, unverified_assumption,
  hallucinated_api, ignored_evidence, constraint_forgetting, wrong_tool,
  diff_growth) mit Evidenz-Pflicht.
- **Hard-Resume-Tests** (`tests/test_pause_hard.py`): Voice-Prozess-Kill →
  Watchdog-Abandon → Main Agent frei (kein Deadlock); Research-Hänger →
  Lease-Ablauf; voller Runtime-Neustart → stale Pause recovered; 8 parallele
  Resume-Events → **genau ein** Resume.

### Fixes
- Panel: `SimpleTable` reicht JSX-Zellen jetzt durch (vorher `[object Object]`);
  Tool-Detail: Spread von `null` (Crash im Children-Array) behoben.
- Monitor: `diff_churn`-Fenster wird nur noch bei **verifiziertem** Fortschritt
  zurückgesetzt (vorher bei jedem Edit).

## [1.2.1] — 2026-09-12 — Icon-Match + Instanz-Erkennung gefixt

### Fixes
- **App-Icon in GNOME:** Die GV-App meldet als Fenster-Klasse den App-Namen
  („God's Voice", xprop-verifiziert) — die `.desktop` verlangte dagegen
  „GodsVoice" → **kein Icon-Match**. `StartupWMClass` folgt jetzt dem App-Namen
  (testgesichert in `test_desktop_entry_content`).
- **GV-Instanz-Erkennung:** `_running_app_pids` suchte nur die Stock-Binary
  (`linux-unpacked/Hermes`) — die GV-Binary (`gods-voice`) wurde nie gefunden
  (Status „Instanz läuft" falsch, zweiter Start spawnte statt zu fokussieren).
  Jetzt werden beide Binary-Namen gescannt (dedupliziert).
- **`gods_voice_app_binary`** prüfte `XDG_DATA_HOME` statt `~/.hermes/godsv` →
  Status meldete „nutzt Stock-App" trotz vorhandenem GV-Build.
- **Installer fasst das globale Hermes-Theme nie mehr an:** `desktop install`
  aktiviert die Skin nicht mehr automatisch (opt-in bleibt explizit:
  `hermes config set display.skin gods-voice`). Regressionstests decken beides
  (kein `config set` beim Install; Aktivierung nur mit `activate=True`).

## [1.2.0] — 2026-09-12 — Native App: God's Voice als eigener Desktop-Build

### Neu
- **Native Integration** (`godsvoice desktop build-app`): Die Desktop-App wird
  als **God's-Voice-App** gebaut — Panel + Chat-Branding sind als
  **gebündeltes Plugin** (`src/plugins/godsv/`) eingebacken, **ab Boot aktiv:
  kein Installieren, kein „Reload plugins", keine Settings-Schritte** mehr.
- **Eigenes Theme `gods-voice`** (Logo-Gold auf warmem Charcoal, light+dark)
  als App-Default des GV-Builds; daneben wählbar in Appearance. Das globale
  `hermes`-Theme bleibt unangetastet (siehe Geändert).
- **Splash & Marke:** Splash zeigt das **Ohr-Logo über „GOD'S VOICE"** statt
  „HERMES AGENT"; Brand-Badge, App-Icon, App-Name, `appId` (`dev.godsvoice.app`),
  Binary (`gods-voice`) und Fenster-/Dokument-/HUD-Titel sind God's Voice.
- **Nous-Portal-Wortlaut aus der UI entfernt** („Nous Portal Support",
  „Sign in to Nous Portal", „Check Portal status", Subscription-Hinweise →
  neutral benannt; Links bleiben funktional). `nous-girl`-Asset fliegt aus dem
  Build; `appId`/`productName` ohne nousresearch.
- **Update-sicheres Overlay:** Hardlink-Mirror des Hermes-Quellbaums
  (`rsync --link-dest`), exakt verankerte Patches (brechen **laut** ab, wenn
  Upstream eine Zeile verschiebt), atomare Writes, die Hardlinks brechen —
  per Test abgesichert, dass der Quellbaum nie korrumpiert werden kann.
  Details + komplette UI-Parts-Tabelle: `docs/APP-BUILD.md`.
- Launcher bevorzugt den GV-Build (`desktop.hermes_app_binary`), `desktop
  status` zeigt die App-Variante, `build-app --force` für den Neusync.

### Geändert
- **Globales `display.skin` wieder auf `default`** — die GV-Skin (gold) färbt
  nur noch, wenn man sie bewusst setzt; die GV-App bringt ihre Farben aus dem
  eingebackenen Theme mit und lässt die Stock-Oberflächen unberührt.

## [1.1.0] — 2026-09-12 — Markenidentität + UI für alle nativen Features

### Neu
- **God's-Voice-Skin (gold, eine YAML für alle Surfaces):** Logo-Gold auf warmem
  Anthrazit, `branding.agent_name = God's Voice`, eigenes Prompt-Symbol. Wird von
  `godsvoice desktop install` nach `<hermes-home>/skins/gods-voice.yaml` gelegt und
  über `hermes config set display.skin gods-voice` aktiviert (CLI, TUI und Desktop-GUI
  färben live um).
- **Panel-Ausbau — UI für alle nativen Features:** Tabs *Live*, *Pausen*, *Memory*
  (Incidents + Recherche-Notizen), *Skills* (Kandidaten + Lifecycle), *Treasury*
  (Spend-Requests + Grants + Allowance), *System* (Runtime + Grenzen). Neue read-only
  Endpunkte `/memory`, `/skills`, `/treasury` (defensiv gegen fehlende DBs, nie
  Vault-Material). Detailmatrix: `docs/UI.md`.
- **Chat-Branding:** Leere Sessions zeigen über `CHAT_EMPTY_AREA` das God's-Voice-
  Gesicht — Wordmark (färbt sich aus der Skin), Feature-Karten, Panel-Hinweis —
  statt der neutralen Leerfläche.
- **Panel-Deployment automatisiert:** `desktop install` verlinkt
  `<hermes-home>/plugins/godsv` auf das Repo (idempotent, überschreibt nie ein
  echtes Verzeichnis); `desktop status` zeigt den Skin-Zustand.
- **Label-Audit:** Alle Hermes-Labels auf „God's Voice" umgestellt, wo die Marke
  spricht; „Hermes" bleibt bewusst, wo es die Herkunft bezeichnet (Engine-Hinweise,
  Settings-Seite, Envs/Pfade) — Tabelle in `docs/UI.md`.

### Geändert
- `uninstall` entfernt die Skin und setzt `display.skin` auf `default` zurück.
- Panel-Kopf/Footer gebrandet („Hermes Engine" als Herkunftshinweis), Tab-Bar im
  Theme-Token-Stil.
- Load-Smoke auf 35 Checks erweitert (alle vier Contributions, jeder Panel-Tab mit
  Beispieldaten, Chat-Branding, Tab-Wechsel über echte Klick-Simulation).

## [1.0.4] — 2026-09-12 — Echte God's-Voice-App-Instanz (koexistent mit Hermes)

- **„God's Voice" ist eine eigene App-Instanz**, nicht mehr das geteilte Hermes-Fenster:
  eigener Electron-userData (`~/.hermes/godsv/desktop-userdata` → eigener Single-Instance-Lock,
  eigene Fenster/Verbindungen), eigener App-Name (`HERMES_DESKTOP_APP_NAME=God's Voice` →
  `app.setName`: Fenster-/Menü-/About-Name) und eigener Launcher-Eintrag
  (`StartupWMClass=GodsVoice`, eigenes Icon).
- **Konfliktfrei neben Hermes:** Deep-Links (`hermes://open/gods-voice`) werden mit der
  GV-userData ausgeliefert und landen ausschließlich in der GV-Instanz — ein normales
  Hermes-Fenster kann weder fokussiert noch umgeleitet werden. Beide Apps laufen parallel
  mit getrennten Locks/Fenstern und teilen Sessions/Runtime (gleiches `HERMES_HOME`,
  kein Doppel-Setup).
- **Launch-Pfad:** laufende GV-Instanz → fokussieren + ins Panel routen; Kaltstart →
  schneller Build-Stamp-Check (`hermes desktop --build-only`), dann Direktstart der Binary
  mit `--class=GodsVoice` + Panel-Deep-Link.
- Live verifiziert: GV-Instanz mit eigenem userData + App-Name-Env; Hermes-Instanz
  unberührt; zweiter Start = Fokus (nie eine zweite Instanz); `desktop status` zeigt
  „God's Voice-Instanz läuft".

## [1.0.3] — 2026-09-12 — Klick landet im Panel, CLI-Welcome

- **„God's Voice" öffnet kein neues Hermes-Fenster mehr:** Läuft die App bereits, wird sie
  fokussiert und über ihren eigenen Deep-Link (`hermes://open/gods-voice`) direkt auf das
  God's-Voice-Panel geroutet — über den Single-Instance-Pfad der App, also nie ein zweites
  Fenster/Tab. Cold start: sobald die App hochgefahren ist, folgt die Panel-Route
  automatisch (Deep-Link wird von der App gepuffert). Live verifiziert.
- **CLI-Welcome im Stil der Hermes/Claude-CLIs:** ANSI-Logo (Truecolor-Gradient), Titelzeile
  mit Version, Runtime + Verzeichnis, „Loslegen"/„Erkunden"-Befehlsblöcke; Farbe nur bei TTY
  (NO_COLOR respektiert). `--help` mit Bereichs- und Beispiel-Epilog.
- `__version__` kommt jetzt aus den installierten Paket-Metadaten (keine Drift mehr).

## [1.0.2] — 2026-09-12 — Launcher-Fix, App-Stil-Panel, Setup-Skript

- **Desktop-App startete nicht aus dem App-Menü:** Der GNOME-Session-PATH enthält
  `~/.local/bin` nicht → `hermes` wurde nicht gefunden. Fix: Fallback-Kandidaten
  (`~/.local/bin/hermes`, `~/bin/hermes`, Hermes-venv) und Launch-Log unter
  `~/.hermes/godsv/desktop-launch.log`. Live verifiziert (Start aus simuliertem
  GNOME-PATH → „Launching packaged Hermes Desktop").
- **Panel im App-Stil:** Theme-Tokens des offiziellen Plugin-Templates
  (`text-(--ui-text-tertiary)`, `hover:bg-(--chrome-action-hover) hover:text-foreground`)
  statt eigener Opacity-Klassen.
- **`install.sh`** — user-lokales Setup in einem Schritt: venv (uv, wiederverwendet),
  `uv pip install -e ".[mcp]"`, CLI-Symlink nach `~/.local/bin`, Desktop-Eintrag +
  Icons, `godsvoice doctor`.
- README-Quickstart ergänzt.

## [1.0.1] — 2026-09-12 — CI green + Panel-Smoke

- **CI repariert:** Lint-Fehler (Import-Sortierung) behoben; MCP-Tests brauchen das
  `mcp`-Extra → CI installiert `[dev,mcp]` (+ `importorskip` als Fallback). Erster
  grüner CI-Lauf seit v1.5.
- **Panel-Load-Smoke** (`tools/check_desktop_plugin.mjs`, läuft in CI mit node):
  lädt das echte `desktop/plugin.js` gegen Mock-Specifier und prüft Vertrag,
  alle drei Beiträge (Route/Sidebar/⌘K), den `ctx.rest`-Datenpfad und die
  Render-Zustände (loading/data/error) — 16 Checks.
- **Repo-weit lint-clean:** auch `tools/` (Research-Helfer) und `integrations/`.

## [1.0.0] — 2026-09-12 — Release

**Desktop-App (Fedora):** `godsvoice desktop install|status|start|uninstall` — nutzerlokaler
Launcher (`God's Voice` im App-Menü), hicolor-Icons aus `assets/icon.png`, Deployment-Check
vor dem Start (Hermes-CLI, Plugin, Eintrag, Runtime). UI-Flächen bleiben logo-frei;
Desktop-Icon + ANSI-Logo (CLI-Banner) sind getrennt. `godsvoice doctor` als Health-Check.

**Treasury-Re-Audit (Härtungen):** TOTP-Handles respektieren jetzt `grant:required`;
Auto-Approve (Stufe 1/2) nur noch mit **verifizierbarem Intent** (Betrag/Merchant/Währung
gepinnt); Betrags-Validierung (NaN/inf/≤0 → denied); Vault-/State-Dateien mit 0600 (Parent
0700); Cross-Prozess-Lock (`flock`) für Vault-Schreibzyklen; Scrub erweitert (URL-encoded,
getrimmte Varianten) + redigierter Event-Fallback statt Crash; Sandbox-Kind erbt nie
Parent-Env (Canary-Test). Audit-Bericht: `docs/SECURITY.md`.

**Completion-Guards + Pause→Research→Redirect→Resume:** Claim-/Evidenz-Prüfung gegen Pact
und Tool-Ergebnisse (Belege nur von Ausführungs-Tools, nach der letzten Änderung);
Kanal `pre_verify` (Nudge) + `transform_llm_output` (Caveat am Finaltext); Pausen-Leases
mit TTL/Heartbeat/Watchdog, exactly-once-Delivery, stale-Verwerfung, isolierte Recherche
mit Budgets und redigiertem Archiv; Live-E2E in echtem Hermes verifiziert.

**Monitor/Kontext/Memory/Skills:** Tool-Fehlerklassen mit Recovery, Stall-/Revert-/
Dependency-Erkennung; budgetierter Entscheidungskontext mit Quellen-Refs; Research→Notes,
Incident-Kandidaten, Skill-Konsumption (nur `promoted`); Interventions-Metriken.

**Eval:** 15 deterministische Episoden (u. a. False Completion, Widerspruch, Pause-Flow,
langer Task mit begrenztem Kontext) in CI.

**Adapter:** `file_edit`-Spiegelung (write_file/patch), Tool-Result-Normalisierung
(exit_code/stdout/error), Hook-Robustheit (kein Hook wirft je — Constraint-Tests).

## [0.3.0] — 2026-09-11 — V2.5/V3 Treasury

- Zone A: Fernet-Vault (scrypt-abgeleiteter Schlüssel, nie gespeichert), Handles
  `secret://service/name`, Broker-Injection nur in Sandbox-Kind-Env + Scrub, Grants,
  Rate-Limit, Revoke, TOTP (RFC 6238).
- Zone C: gestuftes Testgeld (Stufe 0/1/2), Spend-Intents, Approval-Flow, Allowance-Ansicht.
- Live-Belege: 0× Klartext-Secret auf Platte, Approval-Pflicht erzwungen, RFC-6238-Vektoren.

## [0.2.0] — 2026-09-11 — V1/V1.5/V2

- Voice v2: Triage-Gate, Evidence-Judge, Skill-Mining/-Lifecycle, Metriken, Synthese.
- Tool-Suiten: propose-first, bwrap-Gates, MCP-Server (fastmcp).
- Computer Use: Isolations-Pflicht (dry-run/bwrap/podman), API>DOM>Vision, Verifikation
  je Aktion; GUI-Zyklus im Container (Xvfb) verifiziert.

## [0.1.0] — 2026-09-11 — V0/V0.5

- Pact, Ledger, Event-Store mit Secret-Gate, Loop-/Error-/Slop-/Scope-Monitor,
  asynchrone Whisper-Delivery, Incident-/Research-Memory, Budgetlimits, Eval-Harness.
- Repo-Publish (privat), Issues, Hermes-Integration (Plugin, 10 Hooks).
