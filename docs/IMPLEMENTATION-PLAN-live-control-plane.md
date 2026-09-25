# Skaldir Agent — Implementation Plan: Persistente Live-Control-Plane

**Quelle:** Master-Auftrag „Feature Implementation & Reliability Redirect" (13.09.2026) ·
**Ground Truth:** `docs/COMPLETE_AUDIT-2026-09-13.md` (250 Anforderungen, 5 Domänen-Audits)
**Regel:** Nicht neu bauen, was existiert — erweitern, verdrahten, live beweisen.
Trennung: `exists` → `wired` → `tested` → `live verified`. Fertig = mindestens `wired + tested`,
bei zentralen Zusagen `live verified`.

## Architekturgrenze
Hermes = Execution Substrate (Agent, Runtime, Tools, Terminal, Browser, MCP, Hooks, Sessions, Delegation, CU-Unterbau).
Skaldir Agent = darüber: Goal Ownership · Trajectory Supervision · Policy Enforcement · Live Intervention ·
Independent Investigation · Completion Verification · Run Lifecycle/Recovery · Incident-/Research-Memory ·
Capability Learning/Verification/Routing · Tool Health · Security/Trust Boundaries.
**Keine** zweite Agent-/Tool-/MCP-Runtime.

## Arbeitsblöcke (Reihenfolge = Abhängigkeit + Audit-Schwere)

### Block 1 — Security / State Integrity  ← START
- `gate_security` fail-**closed** (Scanner-Exception → [] + Secret-Diff passiert still; Fehlerzweig ungetestet)
- Panel-Trust-Boundary: plugin-seitige Auth/Origin — `HERMES_DASHBOARD_SESSION_TOKEN` fehlt in `plugin_api.py`,
  `origin_allowed("")==True`, `_guard_origin` fail-open; `_audit` fail-open
- Contract-Store: atomare/locked Writes (JSON `write_text` → Korruption ⇒ stiller leerer WORKING-Zustand)
- Generated-Tool-Runtime-Isolation (aktuell ungesandboxt in-process via `mcpserver.py`; `allowed_domains` deklarativ)
- Deliverable: `docs/SECURITY-BOUNDARIES.md`

### Block 2 — Persistent Run Supervisor (P0)  ← START
- Invariante: `NON_TERMINAL_RUN => ACTIVE_WORKER_LEASE OR QUEUED_CONTINUATION OR EXPLICIT_WAIT_WITH_WAKE_SOURCE`
- Run-Record persistieren (run_id, session_id, goal_revision, state, next_owner, pending_action,
  continuation_generation, lease_owner/expires_at, heartbeat_at, wait_reason, wake_source, retry/recovery_count,
  last_progress_at, last_committed_event)
- Orphan-Watchdog (2 abgelaufene Leases live gefunden) · Continuation-Queue · Wait/Wake
- Crash zwischen Redirect und Continue: exactly-once Continuation, keine doppelten Side Effects
  (Side Effects nur bei `idempotent == true` oder verifizierter Result-Existenz; sonst `UNKNOWN_SIDE_EFFECT_STATE`)
- `gv_crash_recovery.py`: laufende Instanz zuverlässig erkennen (Marker-Mismatch `…/godsv/` vs. `…/godsv/desktop-userdata`)
- Deliverable: `docs/RUN-LIFECYCLE.md`

### Block 3 — Verification Closure  (nach Block 1: gemeinsame `gates.py`)
- Runner für `ui_e2e` / `api` / `build` / `persistence_restart` / `run` (sonst unzertifizierbare DoDs)
- `gate_tests`-Deadlock auflösen
- Evidenz-Bindung: Workspace-Bypass schließen (leere workspace passiert den Zaun), `RunEvidence` + Session
- Terminalzustand erzwingen: `transition("WORKING")` darf `VERIFIED_COMPLETE` nicht ohne neues Goal entfrieren

### Block 4 — Live Control Layer
- False-Direction-Intervention (Goal-Contract + Trajektorie + Evidence → konkreter Redirect, kein „stay focused")
- Halluzinations-Interception (API/Datei/Tool-Capability/Runtime-State/Success-Claim → Verify → Redirect/Block)
- Prompt-Injection-Boundary (`UNTRUSTED CONTENT != CONTROL AUTHORITY`; Signal ≠ Verdikt; Inhalt weiter nutzbar)
- Wrong-Tool-Call-Interception vor Ausführung (Goal-Bezug, Tool-Health/-Schema, Parameter, Workspace, Destruktivität, Trust-Quelle)
- Tool-/Command-Pattern-Analyse (Loops, Oszillation, Churn, retry_without_strategy_change, test_without_change, …)
- Deliverable: `docs/VOICE-CALIBRATION.md` (Golden Set §38, Precision/Recall, false/missed interventions, no-intervention-Rate)

### Block 5 — Voice Calibration
- `NO_INTERVENTION_NEEDED` als gültiges Investigator-Ergebnis
- Golden Set §38 (15 Pflichtfälle) als Regressionstests; `no_action = 0` bei 38 Pausen ist ein Warnsignal (Überinterventions-Risiko)

### Block 6 — Capability Learning
- Kausale semantische Generalisierung (nicht String-Equality) · Search-before-Build · Holdout-Validierung
  (`synthesis examples != validation examples`) · Health-Lifecycle · **Live-Insertion** (verified → Registry →
  nächste sichere Modellgrenze → HOT-Set → echte Aufrufbarkeit in derselben Session, kein Restart)

### Block 7 — Memory Learning
- Incident-/Research-Memory: staleness, provenance, cold/warm, `retrieve → applicability check → use/reject`,
  cross-session-Nachweis, dass Memory spätere Arbeit verbessert

### Block 8 — Benchmarks
- Vergleichsmatrix (Hermes baseline → +Completion → +Watcher → +Investigator → Full → +Memory → +Capabilities)
- Konstant halten (Modell/Version/Reasoning/Tools/Env/Context/Timeouts/Budget/Retries/Temperature/Seed), messen
  (task success, false completion, intervention precision/recall, steps-to-correction, tokens, cost, runtime, …)
- Deliverable: `docs/BENCHMARK-RUN-SCHEMA.json`

## Der vollständige E2E-Beweis (§40)
Ein realer Run, der die **ganze Kette** zeigt: falsche Richtung → Watcher → Investigator → Redirect → Resume →
Prompt-Injection wird nicht befolgt → halluzinierte API erkannt → destruktiver Tool-Call geblockt → Crash →
exactly-once-Resume → wiederkehrender Workflow → Capability generalisiert/validiert (pos/neg/holdout) →
Live-Insertion → **Aufruf in derselben Session** → zu früher Completion-Request → Judge lehnt ab → Auto-Continue →
Fix → Verification (tests/build/runtime/UI/persistence/security) → `VERIFIED_COMPLETE` → Wissen persistiert.

## Nicht-Ziele (§41)
Keine Agent Swarms · kein Public Tool Marketplace · keine zweite Agent-/Tool-/MCP-Runtime · keine großen
Dashboard-Erweiterungen · keine Benchmark-spezifischen Hacks · keine kosmetische UI-Arbeit vor Reliability.

## Deliverables
`IMPLEMENTATION-MATRIX.md` · `RUN-LIFECYCLE.md` · `VOICE-CALIBRATION.md` · `SECURITY-BOUNDARIES.md` ·
`CAPABILITY-LEARNING-E2E.md` · `BENCHMARK-RUN-SCHEMA.json`

## Definition of Done (Kern)
Nutzerziel wird **während** der Arbeit geschützt; falsche Richtungen live korrigiert; Halluzinationen vor
Weiterverwendung gestoppt; Untrusted Content erlangt keine Control Authority; riskante Tool-Calls vor
Ausführung gestoppt; gesundes Debugging wird nicht blockiert; nicht-terminale Runs schlafen nie ein;
Crash/Restart verliert keine Arbeit und dupliziert keine Side Effects; Completion nur unabhängig zertifiziert;
Memory verbessert spätere Arbeit nachweisbar; wiederkehrende erfolgreiche Arbeit wird zu verifizierten
Capabilities, die **ohne Restart in derselben Session** aufrufbar sind.

> **Do not optimize for feature count. Optimize for closed, observable, recoverable and learnable loops.**
