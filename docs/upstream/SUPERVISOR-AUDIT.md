# Skaldir Agent — Supervisor-Audit (gegen den echten Laufzeit-Pfad)

**Datum: 12./13.09.2026 · Methode: Code-Trace vom Runtime-Hook-Pfad aus (Plugin → Module),
Live-Evidenz aus den Stores der heutigen Läufe. Code-Existenz zählt NICHT als Beweis — jede
Zeile hier nennt den Pfad, der sie im Betrieb erreicht, plus das Beobachtete.**

Beweismittel dieser Runde (real gelaufen):
- Live-Pause auf dem Main Agent (detect → blockierter Tool-Call → Redirect → Resume), Reason
  `contradiction`, mit Incident-Evidenz im Redirect.
- Desktop-Läufe: `VERIFIED_COMPLETE` (`20260912_231434_ec74ff`, 3× reale `pytest: ok`) und
  `INCOMPLETE` fail-closed (`20260912_230703_4b3683`, „no verification plan … derive a check").
- Trace `mode=whisper`, Live-Block-Emission, `pre_tool_call BLOCK pause=…`, `transform_tool_result
  DELIVERED n hint(s)`, Research-Artefakt `research-8877a68594.json` (23:16, von dieser Pause).
- 46 Testdateien grün (Zielmodule), Node-Smoke 110+ Checks, App-E2E zuletzt 40/40.

---

## 1. Subsystem-Matrix (Klassifikation gegen Runtime)

| Subsystem | Klassifikation | Runtime-Beleg (Pfad → Beobachtung) | Offene Lücke |
|---|---|---|---|
| Goal-State außerhalb des Kontexts (`completion.py`, `CompletionStore`) | **VERIFIED + WORKING** | Hooks schreiben/lesen je Session; Verdicts + Revisionen + DoD persistiert; Session-Isolation live | Non-Goals/Constraints/Decisions liegen NICHT hier (→ Ledger, tot) |
| Pact (`pact.py`) | **VERIFIED + WORKING** (Shell) | `_load_pact` (mtime-cached) lädt `pact.yaml`; Scope-Check speist `scope_drift` | Pact wird im Chat-Pfad nur importiert, wenn kein Contract existiert — kein kontinuierlicher Abgleich „Goal vs. Pact" über Revisionen |
| Watcher/Monitor (`monitor.py`) | **VERIFIED + WORKING** | `monitor.ingest(event)` pro Event; Signale: `stall, loop, revert, error_burst, known_incident_repeat, hallucinated_api, diff_churn, dependency_change, slop_*, scope_drift, tool_error_*`; live: Pause heute | Context-Drift (verlorener Kontext/Summary-Verlust) hat keinen eigenen Detektor; „Goal-Progress vs. Aktivität" nicht als Signal |
| Pause/Investigator (`pause.py`, `research.py`) | **VERIFIED + WORKING** | `_maybe_pause` → `PauseStore`; Live-Pause mit Research-Artefakt 23:16; `judge_same_problem` prüft vor neuer Untersuchung | Research durchsucht die eigenen alten `research-*.json` nicht systematisch (s. §9) |
| Hint-Injektion (`inject.py`) | **VERIFIED + WORKING** | `transform_tool_result` hängt Hints append-only ans Tool-Result (`DELIVERED n hint(s)` im Trace) | Delivery nur bei Session-State; „hint lost"-Fälle nicht gemessen |
| Voice-Policy (`voice.py`) | **VERIFIED + WORKING** | `consider(signals, …)` + Kostenmodell (`EST_PAUSE_COST=3500`, `intervene = continuation > cost`); Modus-Gate shadow/whisper | 5-stufige Policy (OBSERVE→…→BLOCK) existiert implizit (Modes + Redirect + `pre_tool_call BLOCK`) — nicht als explizites, konfigurierbares Stufenmodell |
| Completion-Gates (`completion.py`, `gates.py`) | **VERIFIED + WORKING** | 8 Gates (evidence/tests/build/integration/fake-completion/security/cleanup/dod) laufen im Verify; live: fail-closed + VERIFIED mit echter pytest-Evidenz | Real-UI-/Restart-/Persistence-Checks nur wenn als Kriterium mit Plan formuliert; keine automatische Produkt-Zustands-Prüfung ohne Kriterium |
| Incident Memory (`incidents.py`) | **VERIFIED + WORKING** | Monitor startet mit `known_error_fingerprints=incidents.signatures()` → `known_incident_repeat`-Signal; `find_evidence(incidents, notes, …)` im Pause-Pfad | „Intervene earlier"-Metrik (Zeit bis Stopp bei Wiederholung) wird nicht getrackt; Memory wird nicht im Chat-Kontext sichtbar (bewusst) |
| Research-Persistenz (`research.py` → `research/`) | **IMPLEMENTED, INCOMPLETE** | `_archive` schreibt secret-scrubbed `research-*.json` je Untersuchung | Kein Such-/Reuse-Index über die Dateien; Artefakt-Schema GEPRÜFT: `research-8877a68594.json` hat nur `id/problem/trigger/base_step/context/
context_stats/llm/memory/snippets/session_id` — **keine** `confidence/sources/conclusion/
recommendation/stale_after`-Felder; kein „erst DB, dann recherchieren"-Flow |
| Ledger (`ledger.py`) | **DEAD / NOT CONNECTED** | Kein Import im Runtime-Pfad; nur ein Text-Hinweis in `voice.py` nennt „Ledger" | Komplett tot: Entscheidungen/Annahmen/Non-Goals haben KEINEN lebenden Store |
| Voice Context Builder (`context.py` DecisionContext) | **IMPLEMENTED, PARTIAL** | Budgets (`_enforce_budget`, `truncated_sections`), genutzt von `research.py` + `evalharness.py` | Nicht im Chat-Supervisor-Pfad verdrahtet; Watch/Investigate/Judge teilen sich keine explizite, begrenzte Context-Pipeline |
| Skills (`skills.py`) | **IMPLEMENTED, PARTIAL** | `SkillStore`, `relevant_skills`, `consumption_hint` importiert | Anbindung „Skill wird im Prompt angeboten/verbraucht" nicht live nachgewiesen |
| Capability-/Tool-Mining (`capability_mining.py`, `capability.py`, `toolbuilder.py`) | **IMPLEMENTED, PARTIAL** | Store + Runner im Runtime importiert; `validate_testing_tool` (BROKEN→FAIL, WORKING→PASS) vorhanden; `generated_tools/` real (money_convert×3) | **`register_check_runner` ruft NIEMAND auf** → custom-Check-Pfad des Gates ist tot; Testing-Tools sind nicht ans Completion-Gate angebunden; „erst existierendes Tool suchen" nicht im Runtime erzwungen |
| Testing Tools | **IMPLEMENTED, NOT CONNECTED** | Typ + Negativ/Positiv-Validierung existieren im Builder | Kein Gate-Anschluss, keine Metriken (bugs detected, false passes…), kein Aufruf im Verify |
| Tool Hub | **MISSING** | Keine Manifest-/Registry-Struktur außerhalb `generated_tools/` (lokal) | Kein Hub-Layout, keine Provenienz/Trust-Level, kein Publishing-Flow |
| Treasury (`treasury.py`) | **VERIFIED + WORKING** | Handles, Grants, deny-Pfade, Rate-Limit; Tests; Panel nur Handles | Hub-Tool-Grants (später) müssen hier andocken — Design nötig, keine 2. Stelle bauen |
| Brittleness-Klassifikation (`monitor.classify_tool_error`) | **VERIFIED + WORKING** | `tool_error_{auth|rate_limit|timeout|…}`-Signale + Backoff-Hints | „provider outage/schema mismatch/MCP unavailable/UI failure" nicht in der Klassenliste (prüfen, ergänzen) |
| Slop-Control (`scan_slop` im Monitor) | **VERIFIED + WORKING** | `slop_*`-Signale aus Diffs; Gate `cleanup` existiert zusätzlich | Regelsatz-Doku + Schweregrade abstimmen |
| Token/Context-Ökonomie (`metrics.py`, BudgetTracker, Voice-Kosten) | **IMPLEMENTED, PARTIAL** | BudgetTracker im Research; Token-Estimator für Intervention | Gemessene (nicht geschätzte) Token über Turns fehlen; Context-Größe je Voice-Aufruf nicht getrackt |
| Intervention-Policy | **VERIFIED + WORKING** (Kern) | Pause live; `pre_tool_call BLOCK` live; Redirect; Modus-Gate | Als explizite 5-Stufen-Policy (OBSERVE/WHISPER/REDIRECT/PAUSE/BLOCK) dokumentieren + konfigurierbar machen |
| Long-Horizon-Eval (`evalharness.py`) | **IMPLEMENTED, PARTIAL** | Episoden + `expect_signals`; heute ECHTER Long-Run (Multi-File, Fehler, Fix, Verify) | Dedizierte Szenarien (Drift/Fake-Completion/Loop/Slop/Brittleness/Reuse) fehlen als Suite + Vergleich „Voice an vs. shadow" |

## 2. Die neun Failure-Klassen — Mechanismus, Pfad, Evidenz, Gap

1. **Long-Horizon Drift** — Mechanismus: Session-scoped Contract + Revisionen + Blocker-Persistenz
   über Turns. Pfad: `pre_llm_call` → Contract-Block in jeder User-Nachricht; `verify()` bei Claims.
   Evidenz: Desktop-Läufe heute. **Gap:** kein periodischer „Goal vs. aktuelle Aktivität"-Abgleich
   für den Watcher (nur datei-/fehler-basierte Signale).
2. **Context Drift** — Mechanismus: **fehlt.** Kein Detektor für „Kontext verloren/widersprüchlich zur
   Historie" (der Contract-Block mildert es, erkennt es aber nicht). → Fix-Kandidat: Monitor-Signal
   `context_drift` (z. B. Agent widerspricht früheren, im Contract/Zusammenfassung festgehaltenen
   Entscheidungen; Annahmen tauchen neu auf).
3. **Task/Goal Drift** — Mechanismus: `scope_drift` (Pact-Scope), `diff_churn`,
   `dependency_change`. Pfad: `monitor.ingest` auf `file_edit`/Diff-Events. Evidenz: Signale
   implementiert + Hint-Texte. **Gap:** Scope nur wenn `pact.yaml` existiert; Chat-Contracts
   tragen keinen Scope (nur Goal+DoD).
4. **False Completion** — Mechanismus: P1-Evidence-Regel + 8 Gates + `extract_claims` +
   `no verification plan`-Blocker. Evidenz: heute beide Zustände live (INCOMPLETE fail-closed,
   VERIFIED nur mit realer pytest-Evidenz). **Gap:** Produktzustand (UI/Restart/Persistenz) nur
   bei explizitem Kriterium; keine Default-Produktchecks.
5. **Tool Hallucination** — Mechanismus: `run_evidence_from_events` (nur wirklich ausgeführte
   Checks; Tool-Aufruf ≠ Erfolg; fehlender Exit-Code = unknown), `hallucinated_api`-Signal,
   Claim→Evidence-Paare. Evidenz: Evidence-Liste im Verdict (3× `pytest: ok` mit Kommando).
   **Gap:** Claim-Register (claim → receipt → VERIFIED/CONTRADICTED/UNKNOWN) nicht als eigenes,
   sichtbares Artefakt — steckt implizit in Verdicts.
6. **Looping** — Mechanismus: `loop`-, `revert`-, `error_burst`-Signale (Fingerprints) +
   `known_incident_repeat`. Evidenz: Klassifikation + Hints („Drosseln/Backoff"). **Gap:**
   „Novel-Strategy-Pflicht" nicht erzwungen (kein Signal-Eskalations-Zwang „anderer Ansatz nötig"
   → Redirect, der die Wiederholung der Strategie verbietet).
7. **Token Burn** — Mechanismus: `intervene = continuation_cost > EST_PAUSE_COST`,
   BudgetTracker (Research), `metrics.py`. Evidenz: Policy im Code. **Gap:** Messung statt
   Schätzung fehlt (echte Token je Turn/Voice-Aufruf).
8. **Code Slop** — Mechanismus: `scan_slop(added)` → `slop_*`-Signale + Gate `cleanup`.
   Evidenz: Signale + Regel-IDs. **Gap:** „über „Tests grün" hinaus"-Diffbewertung als Pflicht
   vor VERIFIED (nur wenn Kriterium es fordert).
9. **Tool/API Brittleness** — Mechanismus: `classify_tool_error` (auth/rate_limit/timeout/…) →
   `tool_error_*`-Signale + Backoff-Hint. Evidenz: funktioniert (Trace heute: Tool-Fehler →
   classify). **Gap:** Klassen-Liste erweitern (outage/schema_mismatch/mcp_unavailable/ui_failure)
   und in der Intervention differenzieren (Infra ≠ Code-Bug → kein Code-Fix-Loop).

## 3. Priorisierte Fix-Liste (nächste Runden, ohne Swarm)

**P0 (Anschluss toter Pfade, kleine Eingriffe, große Wirkung):**
1. **Ledger wiederbeleben ODER ersetzen:** Entscheidungen/Annahmen/Non-Goals im Contract
   (CompletionStore) halten (`decisions[]`, `assumptions[]`, `non_goals[]`) — an `@goal`-Kanal +
   `set_goal` hängen; `context_drift`-Signal kann darauf prüfen. Kein neues System — Erweiterung
   des vorhandenen Session-Stores.
2. **Testing-Tools ans Gate:** `register_check_runner`-Aufruf beim Laden generierter
   Testing-Tools (CapabilityStore → Completion `execute_check`); Negativ/Positiv-Validierung als
   Pflicht vor Registrierung; Metrik-Zähler (detected/false-pass/false-fail) im Payload.
3. **Research-DB-Reuse:** Vor jedem `ResearchRunner.run`: Index über `research/*.json` +
   Notes/Incidents durchsuchen (find_evidence erweitern), Treffer mit Freshness prüfen; nur
   Lücken/stale neu recherchieren. Artefakt-Schema um `confidence/sources/contradictions/stale_after`
   erweitern.

**P1 (Supervisor-Mechanik vertiefen):**
4. `context_drift`-Signal + „Aktivität ohne Goal-Progress"-Heuristik (Schritte/Tools je DoD-Fortschritt).
5. Novel-Strategy-Eskalation: bei wiederholtem Fingerprint den Redirect-Text erzwingen („gleiche
   Strategie verboten — begründe neuen Ansatz"), Counting in Pause/Notes.
6. Brittleness-Klassen erweitern + Interventionstext „Infra vs. Code".
7. Token-Messung: reale Usage je Turn (aus Events) in `metrics.compute_metrics` + Report im Panel.

**P2 (Vision-Ausbau):**
8. Tool-Hub-Layout (Manifeste unter `tools_registry/`, Trust-Level, GitHub-Actions-Verifikation,
   Pages-Frontend) — Design-Doc + lokale Manifeste zuerst; **kein** Auto-Publish (Treasury/Grants
   bleiben autoritativ).
9. Long-Horizon-Eval-Suite: Szenarien aus §18 als `evalharness`-Episoden + Vergleich
   „Voice an vs. shadow", Track-Metriken (task success, missed failures, false completion,
   recovery, token overhead).

## 4. Was bereits exzellent funktioniert (nicht anfassen)

- Die P1-Evidence-Strenge + 8 Gates (heute live: fail-closed UND echtes VERIFIED).
- Die Watcher→Pause→Redirect→Resume-Kette (heute live auf dem Main Agent).
- Session-Isolation, Live-Block, aktiver Modus (heute live bewiesen).
- Incident-Reuse im Monitor (`known_error_fingerprints`) und im Pause-Pfad (`find_evidence`).
- Treasury als einzige Secret-Autorität (Handles, Grants, deny/audit).

*Dieses Audit ersetzt keine der Projekt-Dokus; `PROJECT-STATE.md` verweist hierauf ab jetzt als
Anspruch-Abgleich „Vision vs. Ist".*
