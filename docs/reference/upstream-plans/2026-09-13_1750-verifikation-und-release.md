# God's Voice — Verifikations- und Release-Plan

**Stand:** 13.09.2026, **v2** · Quellen: 5 Recherche-Agents (T1–T5) mit Artefakten unter
`/tmp/gv_verif_research/` (T1 Langzeit, T2 Tool-Lernen/CU, T3 Recovery/Semantik/Memory,
T4 lokales Inventar, T5 F15-Eingrenzung — läuft noch).
**Auftrag:** App performant release-fertig machen; jede Produktzusage an echten, unabhängigen
Maßstäben verifizieren. Subagents sind Teil der Methode.

---

## 0 · Prinzipien

1. **Self-Report ist kein Beweis.** Beweisartefakte (JSON/Log/PNG) unter `/tmp/gv_verif_runs/<achse>/`, unabhängig geprüft.
2. **Runner ≠ Verifier** pro Achse (nie derselbe Agent).
3. **Echte Modellläufe** für alles Semantische; kein Mock im entscheidenden Beweis.
4. **Bestehendes wiederverwenden** (Inventar T4): `gv_ui_e2e.mjs`, `gv_product_run.py`, `gv_eval_longhorizon.py`,
   `evalharness.py`, Hermes-Perf-Harness, Update-Repro, 700+ Tests.
5. **Zahlen nur mit Quelle**; Unsicheres = „nicht verifiziert“.
6. **Kernlücke laut Recherche:** Crash-Recovery, Budget/Neustart und Laufzeit-Tool-Bereitstellung
   sind in *keinem* öffentlichen Benchmark eine eigene Kennzahl — genau dort liegt God's Voice
   Differenzierung, und genau dort bauen wir eigene Testdesigns (Abschnitt 3).

---

## 1 · Verifikations-Achsen mit Benchmark-Zuordnung

| # | Achse | Fremd-Anker | Selbstbau-Ergänzung | Subagent-Zuschnitt |
|---|-------|-------------|---------------------|--------------------|
| A | Langzeit-Autonomie (Crash-Recovery, Neustart, Pausen, Budget, viele Turns) | **LHTB** (bester Fit), Terminal-Bench via Harbor, TheAgentCompany | D1 Fail-Once-SIGKILL, D2 Budget/Pause | Runner: Benchmark-Subset + Fault-Injection · Verifier: Properties + Δ-Pass-Rate |
| B | Tool-Lernkette (erkennen→bauen→prüfen→bereitstellen→wiederverwenden) | EvolveTool-Bench (Conformance!), ToolMaker, SkillCraft | D3 Capability-Loop-Eval (5 Module) | Runner: Trace→Mining→Builder→Live-Registrierung · Verifier: Gate + Fresh-Context-Reuse |
| C | Subagent-/Computer-Use-Tools (App-/Settings-/Screenshot-/Modelltests) | OSWorld-Verified / ScreenSpot-Pro (nur remote vergleichbar) | Bestehende `workflows.py`-Runs live + ScreenSpot lokal | Runner: echte App-Runs · Verifier: Vision-Belege + run-Records |
| D | Semantische Voice & Memory | LongMemEval (Fragesätze), HORIZON-Judge-Taxonomie, MAST | D4 Golden-Set Fehlrichtung vs. Debugging (SteerBench-Asymmetrie) | Runner: Szenario-Paare · Verifier: Blind-Auswertung der Urteile |
| E | Session-UI | — | `gv_ui_e2e.mjs` erweitern (Streaming, Vorschau, Kriterieneditor, Anfangsfragen Merge/Neuwürfeln, 10-min-Start) | Runner: CDP-Flow · Verifier: DOM-/Pixel-Belege |
| F | Release-Abnahme & Performance (Build, Install, Update, Persistenz, Fehlerbehandlung; Startup; **F15**) | — | Hermes-Perf-Harness (Rebaseline) + Update-Repro + Crash-Recovery-Test | Runner: Build+Install+Update-Flows · Verifier: Receipts + unabhängige App-Checks |

---

## 2 · Fremd-Benchmark-Katalog (Recherche 13.09.2026, Primärquellen)

| Benchmark | Misst | Leaderboard-Stand (Top-Werte) | Anforderungen | Fedora-lokal? | Rang |
|---|---|---|---|---|---|
| **LHTB** (Long-Horizon Terminal-Bench) | Langhorizont-Tasks mit versteckten Rebuild-Verifern + `continue_until_timeout`-Resume-Loop | DeepSeek V4 Flash **0,602** · Grok 4.5 0,505 · Sonnet 5 0,497 | Docker | ✅ (Docker vorhanden? sonst podman) — **bester Fit** | **1** |
| **Terminal-Bench (aktuell 4.0 via Harbor)** | Terminal-Aufgaben, E2E in echter Shell | 4.0: GPT-6 Astra 58,2 % · (Legacy 2.0: 84,7 %) | Docker, Harbor-CLI | ✅ | **2** |
| **TheAgentCompany** | Büro-/Langhorizont-Tasks über viele Rollen | TTE-MatrixAgent+DeepSeek-V3.2 42,9 % | Docker-Compose, 30+ GB, $0,28–6,78/Task | ✅ mit Aufwand | **3** |
| **METR Time Horizons 1.1** | 50 %-Erfolgshorizont (Stunden) | Claude Mythos Preview (early) **17,4 h** · Opus 4.6 12 h · Gemini 3.1 Pro 6,4 h; Verdopplung ~128,7 Tage | keine (Auswertung fremder Trajektorien) | ✅ zum Vergleichen, nicht Nachspielen (Board warnt >16 h unzuverlässig) | Referenz |
| **Vending-Bench 2** | Langzeit-Betriebswirtschaft | GPT-6 Astra $15.514,70 | Simulator **geschlossen** | ❌ nicht reproduzierbar | — |
| **SWE-Lancer** | Freelance-SWE-Tasks ($) | Board tot (Repo archiviert 07/2025) | — | ❌ | — |
| **RE-Bench** | ML-Research unter Zeitdruck | kein Live-Board | GPU-Pflicht | ❌ | — |
| **GAIA/GAIA2** | Allround-Assistenz | nur Selbstauskünfte, „nicht verifiziert" | API | ✅ | Nachrang |
| **OSWorld-Verified** | Desktop-Computer-Use | **85,4 %** (Claude Mythos Preview, steel.dev) | VM/Docker + **KVM** | ❌ lokal (kein VT-x); remote-only Vergleich | für C remote |
| **WindowsAgentArena** | Windows-Computer-Use | CUA-Skill 57,5 % (Mensch 74,5 %) | VM | ❌ lokal | für C remote |
| **WebArena / WorkArena / ScreenSpot-Pro** | Web-/GUI-Aktionen | WebArena 74,3 % · WorkArena-L1 90,3 % · ScreenSpot-Pro 82,7 % | Docker / reine Bilder | ScreenSpot ✅ (Bilder, Python) — **sofort nutzbar** | für C-lokal |
| **ToolMaker/TM-Bench** | Tool-Bau aus Spezifikation (15 Tasks, >100 Unit-Tests) | ~80 % korrekt (Paper) | Python | ✅ | B-Anker |
| **EvolveTool-Bench** (arXiv 2604.00392) | Tool-Weiterentwicklung über Zeit | **96,8 % von 222 Agenten-Tools mit Conformance 0.00 trotz Task-Erfolg** — Kernmotivation für unser Gate | Python | ✅ | B-Referenz |
| **SkillCraft** (2603.00718) | Skill-/Tool-Wiederverwendung (Token −80 %, Reuse-Rate) | Paper-Werte | Python | ✅ | B-Anker |
| **ToolBench/ToolEval · CRAFT · MetaTool · Voyager** | Tool-Nutzung/-Lernen | Leaderboards teils nur Charts („nicht verifiziert") | Python / Minecraft | teils | B-Kontext |
| **LongMemEval** | Gedächtnis über lange Sessions (5 Fähigkeiten, Judge >97 % Human-Agree) | Rankings unbrauchbar (71,2 % unabhängig vs. 94–96 % self-reported) → **Fragesätze nutzen** | Python/API | ✅ | D-Anker |
| **HORIZON-Taxonomie** | Fehlerklassen langer Läufe, Judge κ=0,84 (7 Kategorien) | Methodik, kein Board | Auswertungs-Framework | ✅ | D-Vorlage |
| **Resume Means Resume** (2608.03836) | Resume-Korrektheit: 6 Properties (TLA+-verifiziert) — LangGraph/CrewAI re-executen nach SIGKILL | Methodik + Konformance-Profil | — | ✅ übernehmen | A-Vorlage |
| **ReliabilityBench / AgentChaos** | Fault-Injection (Rate-Limits, Schema-Drift, Timeouts), pass@1 bis −50 pp | Methodik | Python | ✅ | A-Werkzeugkasten |

**Kopplungs-Blueprint (LHTB, im T1-Artefakt ausgeführt):** Task-Instruktion → godsv-`@goal`-Vertrag;
herkunftsechter Verifier als registriertes `testing_tool` (⇒ `RunEvidence(source="runner")`, nur das
zertifiziert `gate_dod`); `bench_adapter.py` schreibt Trial-JSON als Evidenz; Fault-Injection für
Crash/Budget/Neustart; Erfolgsmaß = **Δ-Pass-Rate Agent allein vs. Agent+GV (whisper)** +
abgefangene Falsch-fertig-Meldungen. Achtung `gate_tests`-Falle + Tag-Pinning.

---

## 3 · Selbstbau-Testdesigns (die belegte Lücke)

**D1 — Fail-Once-SIGKILL-Harness (Achse A):** Lauf mittendrin `kill -9` → Resume; Verdikt über die
6 Resume-Properties (Prefix Continuation, Effect Exactly-Once, Fork Determinism, Checkpoint
Validity, Consume-Once, Recovery Determinism) + Crab-Metrik (recovery-relevanter State pro Turn).
Ziel: keine Doppel-Effekte, keine verlorene Arbeit, Zustand nach Resume nachweisbar.

**D2 — Budget-/Pausen-Injektion (Achse A):** Budgetgrenze erzwungen → saubere Übergabe (kein
Datenverlust, ehrlicher Status); User-Pause mid-run → respektiert + sauberer Resume; doppelte
Events injizieren → idempotente Verarbeitung.

**D3 — Capability-Loop-Eval (Achse B, 5 Module):** (1) Reuse-Discovery aus eigenen Traces,
(2) Repetition-Classes, (3) Fresh-Context-Verifier (baut/validiert unabhängig — inkl.
Conformance-Check à la EvolveTool-Bench), (4) Provisioning (ein Aufruf, ohne Neustart — MCP-Live-
Insertion!), (5) Regressions-/Kollisionscheck beim Reuse. Golden Set, GPU-frei.

**D4 — Golden-Set „Fehlrichtung vs. normales Debugging“ (Achse D):** gepaarte Szenarien
(echte Fehlrichtung / gesundes Debuggen / kosmetische Wiederholung); Metriken:
**over-intervention-Rate** (SteerBench: Menschen/Systeme neigen zu 28 % over-refusal vs. 1 %
under-refusal) und **Zeit-vor-Schaden**; Kategorien aus HORIZON + MAST (Step Repetition = 15,7 %).

**D5 — Memory-Subset (Achse D):** LongMemEval-Fragesätze (Knowledge-Update, Abstention!) gegen
godsv-Memory/Retrieval spielen; keine Leaderboard-Vergleiche (unbrauchbar), nur eigene Vorher/Nachher.

---

## 4 · Release- und Performance-Arbeiten (aus T4; F15-Teil offen bis T5)

| Item | Bestand | Arbeit |
|---|---|---|
| **F15 „Waking up …"-Hänger** 🔴 | Ursachenkette verifiziert (siehe 4.1): Re-Arm-Zyklus entwaffnet Deadline+Watchdog; REST-404-Hot-Loop (~6/min) live gemessen | Fix in `app-overlay/patch_app*.py` (absolute Schranke ab erstem Arming + Loop-Stopp), Vitest `profile-routing.test.ts:187`-Seam, dann Build + Live-Repro |
| **Startup-Performance** | Stock-Harness vorhanden: `apps/desktop/scripts/perf/` (14 Szenarien: cold-start, first-token, keystroke, transcript; Toleranz-Gates; `--spawn` mit eigenem userData/HERMES_HOME/Port) | In GV-app-src umbiegen, **Fedora-Rebaseline** (Baseline ist darwin-arm64), Zielwerte setzen, in GV-CI aufnehmen |
| **Crash-Recovery der APP** | fehlt komplett | Test: App `kill -9` → Neustart → Session/Zustand intakt (verzahnt mit D1) |
| **Update-Kette** | Stock: `scripts/desktop-update/repro.sh` (7 Modi) + ~15 `update-*.ts`-Vitests + `test-desktop.mjs` Installer-Matrix | Gate bauen: „hermes update → build-app → App startet + alle Overlay-Anker getroffen" |
| **Persistenz** | nur manuell für ein `@goal` belegt (§9-D) | Setzung in E2E automatisieren (mehrere Settings, Neustart-Matrix) |
| **Negativtests** | fehlen | fehlendes Binary, toter CDP-Port, 401, Provider ohne Key, Fault-Injection |
| **GV-CI** | nur ruff/pytest/bandit | Node-Smoke + `gv_ui_e2e` + Perf-Gate + JSON-Receipts in CI aufnehmen |

---

---

### 4.1 F15 „Waking up …“-Hänger — Release-Blocker #1 (Eingrenzung T5, Anker selbst nachverifiziert)

**Symptom:** Neue Chat-Session in der GV-App bleibt dauerhaft in „Waking up …“; kein Turn erreicht
das Backend; der Workaround („neue Nachricht senden“) half diesmal nicht. Electron-Main-Log zeigt
einen **unbegrenzten REST-404-Hot-Loop** `{"detail":"Session not found"}` — Messreihe 69→79
Ereignisse in 100 s (≈ 6/min, live weiterlaufend). Das verletzt die Desktop-Invariante
„bounded retries → real recovery affordance, never an infinite spinner“.

**Belegte Stellen (alle in diesem Lauf selbst am Code geprüft):**
- Anzeige: `apps/desktop/src/app/chat/chat-swap-overlay.tsx:37` + `i18n/en.ts:2556`
  (`Waking up ${profile}…`), Treiber-Atom `$gatewaySwapTarget` (`store/profile.ts:387`).
- F15-Härtung im Build aktiv, aber: Deadline wird **per Open** armiert
  (`app-overlay/patch_app.py:193-207`), der 90-s-Watchdog **resettet bei jeder Wertänderung**
  (`app-overlay/patch_app_f15b.py:30-40`), der Store-Dial-Pfad
  (`store/profile.ts::ensureGatewayProfile`) hat keine eigene Schranke.
- **Ursachenkette:** Ein Re-Arm-/Retry-Zyklus < 90 s hält Watchdog und Deadline dauerhaft
  entwaffnet; der ausbleibende terminale `session.create`-Übergang lässt das Overlay stehen,
  während die 404-Schleife weiterläuft. **Bewusst offen markiert:** exakter 404-Caller/ID,
  ob Deadline/Watchdog je feuerten (Renderer-Console-Tap loggt nur Level 3 → keine Datei-Belege).

**Fix-Auftrag (P2, test-first):**
1. In `sdk/index.ts::openSession` + `store/profile.ts::ensureGatewayProfile` eine **absolute
   Schranke ab dem ersten Arming** (nicht per Open) einbauen: nach N s ohne terminalen Übergang
   → Overlay `null`, `$resumeExhaustedSessionId` setzen **und den Retry-Loop stoppen (bounded)**.
   Umsetzung in `app-overlay/patch_app*.py` (einstufige Patchkette, Anker-Asserts).
2. Vitest-Fall „Open superseded/re-armt“ → Overlay `null` + Resume-Exhausted gesetzt
   (Seam existiert: `profile-routing.test.ts:187`).
3. Build + Live-Repro an der GV-Instanz: neue Session → kein dauerhaftes „Waking up“,
   404-Loop endet innerhalb der Schranke, Session bedienbar (Belege: Launch-Log-Zählung + Screenshot).

---

## 5 · Orchestrierung (Subagents)

- Pro Achse: **1 Runner-Agent** (Fixture + Lauf + Rohartefakte) und **1 Verifier-Agent**
  (liest nur Artefakte, prüft gegen Abnahmekriterien). Beweise: `/tmp/gv_verif_runs/<achse>/`.
- Achsen laufen parallel; eine blockierte Prüfung blockiert die anderen nicht.
- Verifier-Regeln: keine Zahlen ohne Quelle, kein „bestanden“ aus Self-Report/UI-Text/Serverneustart.

## 6 · Phasen

| Phase | Inhalt | DoD |
|---|---|---|
| **P1** ✅ | Recherche T1–T4 (fertig), Plan v2 (dieses Dokument), T5 (läuft) | Plan v2 liegt; Katalog + Designs belegt |
| **P2** | Release-Blocker: **F15-Fix**, Startup-Baseline + Gates, Update-Gate, App-Crash-Recovery, Negativtests, GV-CI | F15 reproduzierbar weg; Startup-Ziel erreicht + Gate grün; Update-/Crash-Test grün |
| **P3** | Achsen A–E: je Fremd-Anker (kleines Subset) + Selbstbau-Design, Runner+Verifier | je Achse 1 echter Lauf + unabhängige Verifikation + Artefakte |
| **P4** | Release-Kandidat + Abnahmebericht (Achse F) | Build/Install/Update/Persistenz/Fehlerbehandlung belegt; Bericht mit offenen Punkten |

**DoD global:** Keine Achse gilt durch UI-Text, Serverneustart oder Self-Report als bestanden —
nur durch Artefakt + unabhängige Verifikation.
