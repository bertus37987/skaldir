# Skaldir Agent — Implementierungsdokumentation (Stand 13.09.2026)

> **Verifikationsbasis:** Alle Aussagen und Zahlen in diesem Dokument wurden direkt aus dem
> Repository, den Tests, den Laufzeit-Datenbanken oder Live-Läufen erhoben — nicht aus dem
> Gedächtnis. Prüfbare Quellen sind je Kapitel angegeben; die Kommandos unter §6 kann jede:r
> selbst ausführen. Branch `godsv-supervisor-hardening` (60 Commits seit Baseline `85ee575`),
> 61 Testdateien, volle Suite grün, `ruff check src tests tools integrations` clean.

---

**Run-Lifecycle (live bewiesen 13.09.):** Unbelegter Abschluss gegen offenen Contract → `pre_verify`-Stop-Gate ruft den Voice-Guard (`guard_pre_verify`) → `completion_rejected` + Redirect-Nudge → derselbe Run setzt automatisch fort (kein Nutzer-Continue). Beweise: PROJECT-STATE §4 (Sessions 20260913_132509_08f201 / 20260913_132914_48f0e7).

## 1. Was Skaldir Agent ist

Skaldir Agent (``godsv``) ist ein **Long-Horizon-Supervisor** über dem Main Agent. Der Main Agent
arbeitet; Skaldir Agent schützt unabhängig: user intent, goal integrity, context continuity,
evidence integrity, trajectory quality, architecture quality, tool reliability, completion
correctness.

**Produktgrenze:** CHAT = Live-Betrieb · Skaldir Agent Settings = Konfiguration ·
Skaldir Agent Treasury = Secrets & Grants. Ausgeliefert als native Fedora-App (Hermes-Desktop +
eingebackenes, umbenanntes Panel-Plugin) plus CLI ``godsvoice``.

**Kernprinzip (bindend, seit der Semantik-Runde §32–§36):**
> Heuristiken erkennen verdächtige Muster. Die Voice bestimmt, was sie bedeuten.
> Starke Intervention verlangt Evidenz, dass die Trajektorie wirklich schlecht ist.

---

## 2. Architektur — die Schichten

| Schicht | Ort | Rolle |
|---|---|---|
| Kern | `src/godsv/` — **43 Dateien** (41 Funktionsmodule + `__init__`/`__main__`) | Monitor, Pause, Voice, Completion, Memory, Tools, Treasury, CLI |
| Hermes-Integration | `integrations/hermes/godsv_plugin/` | Plugin-Hooks, Panel-API (`dashboard/plugin_api.py`), Renderer-UI (`desktop/plugin.js`) |
| CLI | `godsvoice …` (`src/godsv/cli.py`) | Betrieb, Checks, Metrics, Tools-Hub, Desktop-Build |
| Desktop-App | `~/.hermes/godsv/app/` | Electron (Hermes-Basis, eigenes userData, eingebackenes Panel) |

**Kernmodule (Auswahl, verifiziert per `ls src/godsv/*.py`):**
`monitor` (Detektoren), `pause` (Lease+Investigation), `voice` (Interventions-Gate, Hints),
`completion` (Abschluss-Supervisor), `incidents` (Failure-Memory), `research` (Forschungs-DB),
`pact` (Vertrag), `ledger` (Working Ledger), `capability`/`capability_mining` (Testing-Tools,
Kandidaten), `toolbuilder` (Tool-Pipeline), `toolhub` (Registry+Manifest), `treasury`
(Secrets), `secretscan` (Scrubber), `metrics` (Qualitätsmessung), `modelroles` (Rollen),
`inject`/`spotlight`/`guard` (Prompt-Sicherheit), `gates` (Sandbox), `computer_use`, `nightshift`,
`synthesis`, `retrieval`/`embedding` (Suche), `skills`, `notes`, `promptrun`/`prompts` (Registry),
`context`, `events`, `llm`, `toolscan`/`toolusage`, `mcpserver`, `desktop`/`appbuild`, `evalharness`, `demo`.

---

## 3. Die Fähigkeiten der Voice (Kernstück)

Die Voice ist **deterministisch im Wahrnehmen, semantisch im Beurteilen, protokollarisch im
Intervenieren, unabhängig im Abschließen**. Vier Fähigkeitsstufen:

### 3.1 Wahrnehmen — 16 deterministische Signal-Familien

Der Monitor (`src/godsv/monitor.py`) emittiert **Signale, keine Verdikte**. Verifizierte
Signal-Arten (grep über `monitor.py` + Plugin):

| Familie | Bedeutung | Eskalationsverhalten |
|---|---|---|
| `error_without_details` | Fehler ohne verwertbaren Text (nur Exit-Code) | **nie** Repeat/Incident-Match, **kein** Pause-Trigger |
| `error_recurrence_after_progress` | gleiche Signatur, aber nach echtem Fortschritt | Signal, **kein** Pause-Trigger |
| `error_cluster` | ≥3 verschiedene Signaturen im Fenster | Beobachtungs-Signal (kein starker Eingriff) |
| `error_burst` | **identische** volle Signatur wiederholt | Pause-Trigger ab Schwelle 2 |
| `loop` / `stall` / `progress_stall` | Wiederholung / Stillstand / Aktivität ohne Fortschritt | je nach Schwelle |
| `known_incident_repeat` | **gleicher** Fingerprint wie bekanntes Incident, ohne Fortschritt | Schwelle 1 |
| `hallucinated_api` | erfundene API/Datei | Schwelle 2 |
| `scope_drift` / `context_drift` | Ziel-/Kontext-Drift (verstreute Edits, fremde Pfade) | Schwelle 3 — **klassenweit über Pfade** (gradual drift, §33-Fix); Persistenz über Turn-Grenzen (`*.drift.json`) |
| `diff_churn` / `dependency_change` / `revert` | Diff-Wildwuchs / neue Abhängigkeiten / Rückbau | Beobachtung |
| `strategy_repeat` | derselbe Fehler **nach derselben Tool-Strategie** (Name+Args, ≤2 Steps Abstand) — Blind-Retry-Evidenz; anderer Ansatz bleibt ruhig | Pause-Trigger ab Schwelle 2 |
| `skill` / `capability_gap` | wiederverwendbare Prozedur / gemeldete fehlende Fähigkeit | `capability_gap` startet den Tool-Build (Schwelle 1) |

**Der historische Bug (13.09., live bewiesen):** Ohne stderr fiel der Fehlertext auf
`"exit code 1 (no output)"` zurück → **alle** Exit-1-Fehler teilten einen Fingerprint
(`67e08319607a`), der zudem als Known-Incident geseedet war → 8 unzusammenhängende Fehler
über ~50 Steps kollabierten zu „bekannter Fehler wiederholt sich" und pausierten den Agenten.
**Fix:** Die drei Semantik-Familien oben — coarse Fehler matchen nie, Recurrence nach
Fortschritt eskaliert nie, Cluster bleibt Beobachtung; `known_incident_repeat` nur bei gleichem
Fingerprint **ohne** zwischenzeitlichen Fortschritt. Regression: **8 Tests**
(`tests/test_false_loop_regression.py`).

### 3.2 Beurteilen — Schwellen + Kosten-Gate + Semantik-Prompts

- **Trigger-Schwellen** (`pause.py: TRIGGER_RULES`): `loop 2 · error_burst 2 · scope_drift 3 ·
  known_incident_repeat 1 · hallucinated_api 2 · capability_gap 1` — plus Strictness-Delta.
- **Kosten-Gate** (`voice.assess_intervention`, verifiziert): `intervene = continuation_tokens >
  EST_PAUSE_COST`, mit `tokens_per_step × severity_factor(0.6/1.0/2.2) × occurrences(≤6) ×
  strictness_factor`. **Gemessen** werden occurrences/severity/Fenster, **geschätzt** nur die
  Tokens — strikt getrennt ausgewiesen.
- **Strictness** (`relaxed/standard/strict`): Schwellen ±1 (Floor 1) **und** Kostenfaktor
  (0.6/1.0/1.7); in Settings umstellbar, bei jeder Entscheidung frisch gelesen.
- **Semantik-Prompts v2** (v1 bleibt Historie, `prompts/`): `voice_triage.v2` und
  `same_problem_judge.v2` lehren explizit: *a failure is not automatically a loop; temporal
  proximity is not semantic similarity; error count is not root-cause classification; a
  detector provides evidence, not truth; prefer productive recovery; strong intervention
  requires evidence.* Konfidenz: **HIGH ≥0.7 steuert, MED nur Hinweis, LOW steuert nicht.**
- **Failure-Klassifikation** (`monitor.TOOL_ERROR_CLASSES`, 11 Fehlerklassen — u. a. `auth`,
  `rate_limit`, `timeout`, `permissions`, `provider_outage`, `schema_mismatch`,
  `mcp_unavailable`, `ui_failure`, `api_changed`, `invalid_args`) — mit deutschen
  Recovery-Hinweisen; gewöhnliche Bugs werden nicht fehlklassifiziert (Tests grün).

### 3.3 Eingreifen — das Pause-Protokoll (crash-sicher, idempotent)

`src/godsv/pause.py`: ein **Lease-Protokoll** mit Store (`pauses.db`, SQLite), Statuskette
(requested → researching → ready → delivered/stale/abandoned/expired), **Watchdog + Heartbeat +
Crash-Recovery**: Eine sterbende Voice kann den Main Agent nie dauerhaft stranden.

- **Investigator** (`research.py: ResearchRunner`): wird nur bei berechtigtem Trigger gestartet,
  bekommt frischen Kontext und recherchiert **erst die eigene Forschungs-DB** (P0-3:
  `find_prior_research` mit Term-Overlap, Alter, Staleness) — nur echte Lücken werden neu
  erforscht, Ergebnisse wandern mit Provenienz (`created_at`, `stale_after`,
  `confidence: unverified`) zurück ins Archiv.
- **`NO_INTERVENTION_NEEDED`** (neu, §10): Der Investigator darf schließen, dass normaler,
  erholbarer Verlauf vorliegt — dann endet die Pause **ohne Redirect** (Event
  `pause_no_action`), der Agent arbeitet ungestört weiter. Default bleibt „Intervention nötig";
  nichts wird ohne Evidenz behauptet. Prompt `voice_redirect.v2` lehrt die Option **mit
  Guardrail** (nie zum Umgehen harter Befunde; bei Unsicherheit Redirect).
- **Novel-Strategy-Klausel** (P1-5): Ab der **dritten** Pause derselben Trigger-Klasse (durable
  aus dem PauseStore gezählt) verlangt der Redirect: *„REPEATED INTERVENTION (#N): the next
  attempt must be a NOVEL strategy — state first why it differs"* — live im Harness-Lauf
  (`novel_escalations: 3`).
- **Claim-Contradiction**: Erfolgs-Behauptung gefolgt von einem Fehler ≤12 Steps (Fingerprint
  im bekannten Korpus) → Contradiction-Pause mit der Richtung „Behauptung zurücknehmen, mit
  echtem Beleg neu prüfen" (live auf dieser Session bewiesen).

### 3.4 Abschließen — der Completion-Supervisor

`src/godsv/completion.py`: Der Main Agent kann nur **`REQUEST_COMPLETION`**; die Voice
entscheidet. **11 Zustände** (verifiziert): `WORKING · COMPLETION_REQUESTED · VERIFYING ·
INCOMPLETE · REDIRECT · VERIFIED_COMPLETE · BLOCKED_USER · BLOCKED_EXTERNAL ·
FAILED_UNRECOVERABLE · USER_CANCELLED · SHIPPED_WITH_USER_OVERRIDE`.

- DoD-Items mit `blocking`/`verification`; `execute_check` läuft in der **Sandbox**
  (`gates.py`, fail-closed, read-only Extra-Binds); CODE_COMPLETE ≠ PRODUCT_COMPLETE.
- `INCOMPLETE` liefert **exakte Blocker** → Redirect → derselbe Main Agent arbeitet automatisch
  weiter (kein manuelles „continue").
- **Claims sind keine Evidenz** (F10): `record_claims` (post_llm_call) erkennt Erfolgs-Claims;
  bei offenem Contract mit unverifizierten Requirements wird ein Review gequeued und im
  nächsten Turn **genau einmal** als `voice·claim-check` in den Vertragsblock injiziert.
  **Live-Quittung** (events.db, Cockpit-Session): `seq 4339 claim ["done"]` →
  `seq 4340 guard_check {kind: claim_pending_review, open_items: 2}` — ohne Contract: 0 Events.
- **Session-scoped Vertrag**: `CompletionStore(runtime, session_id)` → `completion/<session>.json`;
  Trajectory-State (Entscheidungen/Annahmen/Non-Goals) liegt **außerhalb** des LLM-Kontexts;
  Drift-Zähler persistieren in `<contract>.drift.json` (überleben Turn-Grenzen, F13).
- **Compression-Recovery** (live): Ein frischer Prozess ohne Chat-Verlauf rekonstruiert den
  vollständigen Vertragsblock aus durable State —
  `state=INCOMPLETE · goal=… · dod: 0/2 blocking requirements verified · blocker: …`.

### 3.5 Lernen — Incident-Memory, Research-DB, Skills, Qualitätsmessung

- **Incident Memory** (`incidents.py`): Upsert/Sichtungen/Promotion, FTS-Suche + Embeddings;
  geseedete Fingerprints fließen als `known_error_fingerprints` in den Monitor — **semantisch
  geschützt** (coarse Fehler matchen nie; siehe 3.1). Prinzip: *Jede gelöste Fehlerklasse kostet
  uns nur einmal — aber verschiedene Klassen werden nicht kollabiert.*
- **Research-DB** (`research.py`): wiederverwendbares, evidenzbelegtes Wissen mit Frage,
  Finding, Quellen, Konfidenz, Freshness/Staleness, Provenienz; Prior-Findings erscheinen in
  späteren Evidenz-Anzeigen („age 11.2h, unverified" — live gesehen).
- **Skills/Mining** (`capability_mining.py`): wiederkehrende Arbeit → Skill-Kandidat →
  Promotion über die **bestehende** gated Builder-Pipeline (kein zweites System).
- **Qualitätsmessung** (`metrics.py: voice_quality`, §34): aus der durablen Pause-Historie —
  `delivered/no_action/abandoned/expired`, **`recovery_success`** (Redirect hielt),
  **`recurrence_after_intervention`** (kam wieder), Raten, `steps_before_first_intervention`
  je Session. **Live auf der echten Historie:** 12 Interventionen · 10 delivered ·
  recovery_success_rate **0.70** · 3 Recurrences. Zusätzlich: **echte** Host-Token (`*_measured`
  aus `state.db`, strikt getrennt von `*_est`, None = nie geraten).

### 3.6 Die Stimme im Chat (Cockpit)

Das Plugin (`integrations/hermes/godsv_plugin/__init__.py`) hängt sich an die **feuernden**
Host-Hooks (verifiziert): `on_session_start`, `on_session_end`, `pre_llm_call`,
`post_llm_call`, `transform_llm_output`, `pre_verify`, `observe`. Wichtige Erkenntnis (korrigiert 13.09.):
Der `pre_verify`-Stop-Gate ist im Desktop ERREICHBAR und live im Einsatz — der Host
befüllt `agent._turn_file_mutation_paths` über `_commit_tool_result` →
`_record_file_mutation_result` für landed `write_file`/`patch`-Results; der Voice-Guard
(`guard_pre_verify`) feuerte live (Reject → Redirect → automatische Fortsetzung ohne
Nutzer-Continue, Belege in PROJECT-STATE §4). Die Chat-Mechanik nutzt zusätzlich die
`pre/post_llm_call`-Kanäle. Der **Live-Block** im Chat kommt über `transcript.directives`
(`::godsv-live{stage=…}`); **der Record gewinnt immer** über das stage-Attribut (Owner-Kriterium:
ein fertiges Goal zeigt niemals „WORKING").

---

## 4. Die Tools (Fähigkeitsinventar)

### 4.1 Testing-Tools — wiederverwendbarer Beweis

- Generierte Testing-Tools (`~/.hermes/godsv/generated_tools/<name>/`) werden beim Session-Start
  registriert (`capability.register_testing_tools` — live im Trace: `testing tools registered:
  check_readme`) und laufen über den **Gate-Runner** (`execute_check`) — genau die Naht, die der
  Completion-Gate nutzt.
- **Pflichtnachweis je Tool** (P0-2, live): WORKING-Target → **PASS**, BROKEN-Target → **FAIL**
  (check_readme real auf echten Verzeichnissen verifiziert). Ein Testing-Tool ohne
  `validation: validated` wird vom Hub-Manifest **verweigert**.
- **Metriken** (`capability.testing_tool_stats`, §26): runs/passes/fails je Tool aus dem
  append-only Usage-Ledger — ein fail ist ein Gate-Lauf, der das Target als kaputt
  zurückwies (**bugs detected**); der Validierungs-Status je Manifest (BROKEN→FAIL +
  WORKING→PASS bewiesen = `validated`) schließt **false passes/false fails strukturell**
  aus. Totals + Je-Tool-Zeilen erscheinen live in `godsvoice metrics` (`testing_tools`);
  live gesehen: 4 Tools, 11 Runs, 2 finds.

### 4.2 Generated-Tool-Pipeline

Capability-Request (z. B. `capability_gap`-Signal) → `toolbuilder` (Entwurf → Tests → Security →
realistischer Smoke) → Voice-Verifikation → Registry. **Kein Registrieren nur weil Code
existiert** — die bestehende Pipeline wird wiederverwendet, nie dupliziert.

### 4.3 Local Tool Registry + Tool Hub (`toolhub.py`, `docs/TOOL-HUB.md`)

- `godsvoice tools-hub [--search|--show|--manifest]`: lokales read-only Registry über
  generierte Tools, Skills, MCP-Server, Testing-Tools — mit Permissions, `secret://`-Handles,
  Provenienz, Verifikationsstatus. **Live:** 105 Einträge (4 generated, 101 Skills, 0 MCP),
  deterministisches Ranking (`check_readme` #1).
- **Manifest mit Verweigerungsregeln** (verifiziert in Tests): nicht-`ready` → Nein;
  Testing-Tool ohne `validated` → Nein; Klartext-Secret → Nein; enthält immer
  `publish: {automatic: false, requires: "explicit owner approval"}` — **nie Auto-Publish**.
  Nurreal ausgeführte/validierte Tools dürfen „✓ SKALDIR AGENT VERIFIED" tragen.

### 4.4 Treasury — die einzige Secret-Quelle

`src/godsv/treasury.py`: **Fernet-verschlüsselt** (`_fernet_key`, restriktive Dateirechte via
`_restrict`), API: `add/revoke/list_handles/describe` + Grants; Tools/MCPs/Modelle erhalten nur
**Handles** wie `secret://provider-key`. Settings speichert ausschließlich solche Handles —
nie Klartext. `secretscan` (Scrubber) schützt Memory/Research/Logs/generierte Tools/Manifeste;
Docs, Memory und Event-Store sind frei von Secrets (Redaktions-Regel).

### 4.5 Tool-Nutzung, MCP, Skills-Suche

`toolusage`/`toolscan` (Nutzungsspuren, Health), `mcpserver` (MCP-Anbindung), `retrieval` +
`embedding` (semantische Suche über Incidents/Notes), `skills` (Skill-Index). Vor jedem Neubau
gilt die Reihenfolge: Skill → Tool → MCP → Testing-Tool suchen, dann erst bauen.

### 4.6 Panel-API (Bedienoberfläche)

28+ verifizierte Routen in `dashboard/plugin_api.py`, u. a.:
`/health /summary /events /goal /completion{,/goal,/criteria,/verify,/override,/trajectory}`
`/models{,/roles,/strictness,/test} · /tools · /skills · /memory · /approvals · /evals{,/run}`
`/treasury{,/credentials,/credentials/revoke,/credentials/test,/grants,/grants/revoke} · /cu{,/demo}`.
Alle Routen liefern echte Laufzeitdaten; mutierende Routen sind session-scoped (unscoped
Payload-Leak im Harness-Lauf behoben).

---

## 5. Die Hardening-Runde (Auftrag §32–§36) — Ergebnisse

**Fünf Semantik-Szenarien im Harness** (`tools/gv_eval_longhorizon.py`, echte Maschinerie:
Live-Incident-Signaturen → `Monitor.ingest` → `VoicePause.consider`):

| Szenario | Ergebnis |
|---|---|
| Nutzer-Original: AssertionError → unrelated SyntaxError | nur `stall`-Signal, **keine Pause** |
| Irrelevant-Incident (unbekannter Volltext vs. Live-Signaturen) | **kein Match, keine Pause** |
| Echter Loop, 2× identisch | `error_burst`-Signal, **noch keine Pause** (2 Fehlläufe ≠ Verdikt) |
| Echter Loop, 3× identisch | Schwelle erreicht → **echte Pause** mit Redirect |
| Claim-Contradiction | **echte Pause** (der Pfad, der live feuert) |

**Voller Harness-Lauf** (gegen die echte Serve-API): Goal → **2× Premature-Claim rejected**
(echte Blocker) → Voice-Chain 3 Runden (alle delivered, **novel-escalations: 3**) → Semantik-
Szenarien wie oben → **Compression-Recovery ok** → **Goal-Drift ok** (gleiche + variierende Pfade eskalieren,
In-Scope ruhig — Fix + 3 Regressionstests) → Testing-Tool **PASS** →
**VERIFIED_COMPLETE** (`pytest: ok`-Evidence), Reject-Latenz 1,7 s.

**Einordnung nach §35 (SKIP/EXTEND/…):** ausführlich in `docs/PROJECT-STATE.md` — u. a. wurden
Pact/Goal-State, Completion-Gate, Treasury, Pause-Lease+Watchdog, Research-DB, Tool Hub,
Testing-Tools bewusst **nicht angefasst** (already strong, live bewiesen); erweitert wurden
Detektoren, Incident-Matching, Prompts, Investigator.

---

## 6. Selbst prüfen (die echten Kommandos)

```bash
cd ~/projects/gods-voice
.venv/bin/python -m pytest -q                                  # volle Suite
.venv/bin/ruff check src tests tools integrations               # Lint-Gate
.venv/bin/python tools/gv_eval_longhorizon.py --semantic-only   # Semantik-Szenarien (ohne App)
.venv/bin/python tools/gv_eval_longhorizon.py                   # voller Lauf (App/Serve nötig)
.venv/bin/godsvoice metrics --events ~/.hermes/godsv/events.db \
  --memory ~/.hermes/godsv/memory.db                            # inkl. voice_quality
.venv/bin/godsvoice tools-hub --manifest                        # Hub-Manifest + Publish-Regeln
```

Live-Belege zum Nachschlagen: `events.db` (seq 4339/4340 — Claim→Review), Trace
`/tmp/godsv_plugin_trace.log` (Hooks), `pauses.db` (Qualität), `completion/*.json` (Verträge).

---

## 7. Offene Punkte (ehrlich, nichts davon ist „fertig geredet")

1. **F10-Deliver-Live-Beobachtung** — Queue + Bedingung live belegt, Deliver-Code exactly-once
   + Suite-Test; die Beobachtung der Injektion in einem kontrollierten Turn steht aus.
2. **F15-Umgebungsbefund** — Session-Hydration-Stall erklärts (Session im Backend nicht
   auflösbar); Handling in Skill `gods-voice` → `references/live-app-driving.md`.
3. **Interaktive Abnahme** (OS-Skalierung, Stift/Haptik) + **OpenClaw-2-Referenz** offen.

---

*Quellen dieser Dokumentation: `src/godsv/*.py` (grep-verifizierte Signaturen/Konstanten),
`tests/` (61 Dateien), Live-Läufe vom 13.09.2026, `docs/PROJECT-STATE.md`,
`docs/SUPERVISOR-AUDIT.md`, `docs/TOOL-HUB.md`, GitHub `bertus37987/gods-voice`
**(privat)** Branch `godsv-supervisor-hardening`.*
