# JEV-IMPLEMENTATION-PLAN — Auftrag `JEV_IMPLEMENTATION_GOAL.md` gegen den Ist-Stand

Quelle: `/home/willy/Documents/Design_Docs/JEV_IMPLEMENTATION_GOAL.md` (1518 Zeilen, 39 Abschnitte).
Bindend bleibt daneben `ABNAHME.md` (Regeln, Evidenzklassen §5.1, Gate §117, JEV-Abschlussgrade §117.1).
Dieser Plan ist die Arbeitsgrundlage: er benennt je Abschnitt den **Ist-Stand**, das **Delta** und den
**Eigentümer**. Nichts hier ist ein Nachweis — Nachweis ist nur, was mit Befehl + Ausgabe belegt ist.

## 0. Harte Regeln für alle Beteiligten

1. **Kein zweiter Stapel.** Keine zweite Voice-Runtime, keine zweite Policy-Engine, kein paralleler
   Goal-Store, kein separater Computer-Use-Orchestrator (§0, §34). Bestehendes wird erweitert.
2. **Jev ist advisory.** Hard Policy, Rechte, Goal-Revision, Evidence-Existenz, Artefakt-Fingerprints,
   Treasury und Runtime-Ownership bleiben deterministisch. Completion setzt nur das Runtime-Gate.
3. **Ehrlichkeit über Fortschritt.** `live_jev=false` überall dort, wo kein echter TypeSafe-Call lief.
   Der LLM-Adapter ist **kein** Jev-Livenachweis (§14). Höchstaussage heute: `JEV_READY`.
4. **Rot-first** je neuem Verhalten (Testdocstring: fehlschlagender Befehl + beobachteter Fehler).
5. **Keine Kennzahlen erfinden.** Metriken ohne Rohdaten sind `not_run`, nie `0`.
6. **Schreibgrenzen** je Worker siehe Eigentümerschaft; `audit/**`, `audit/verification-mapping.json`,
   `docs/ABNAHME-STATUS.md`, `tools/build_*.py` und die Live-Engine `~/.hermes/hermes-agent` bleiben
   beim Integrator. Worker committen nicht.

## 1. Delta-Tabelle (Abschnitt → Ist → Delta → Eigentümer)

| § | Gegenstand | Ist-Stand (gemessen) | Delta | Eigentümer |
|---|---|---|---|---|
| 3 | Provider-Abstraktion | `SystemOneEvaluator` (Protocol), `DisabledSystemOneEvaluator`, `LLMSystemOneAdapter`, `TypeSafeJevEvaluator` | **`RecordedSystemOneEvaluator`** (Replay) fehlt | A |
| 4.1 | `SystemOneContext` | `EvaluationContext(run_id, goal_id, goal_revision, policy_revision, event_cursor, project_id, shadow, risk_class, deadline, budget, model)` | **`caller`, `workspace_fingerprint`, `question_pack_id/version`, `budget_reservation`, `privacy_scope`** fehlen | A |
| 4.2 | `SystemOneEvaluation` | 25 Felder inkl. `latency_ms`, `usage`, `raw_ref`, `attempts`, `sdk_retries`, `live_jev`, `provider_access_mode` | **`provider_mode`** mit den vier Vertragswerten fehlt (`typesafe_jev_live\|llm_system_one_adapter\|replay_fixture\|disabled`) | A |
| 4.3 | `Answer` | `noul/choice/score` mit `value`/`choice`/`probabilities`/`confidence` | **`question_type`, `provider_metadata`**; Noul: `confidence=null` explizit pinning | A |
| 5 | Pack-Metadaten | `QuestionPack(id, version, owner, questions)` | **`purpose`, `applicable_states`, `risk_class`, `fallback_policy`, `calibration_dataset_version`** | A |
| 6 | Atomare Fragen | 36 Fragen in 5 Packs, IDs `voice.loop.same_root_cause` … | **Kanonische §6-IDs** (`same_root_cause`, `same_failed_strategy`, `new_evidence_obtained`, `meaningful_progress`, `evidence_stale`, `goal_drift_present`, `completion_claim_present`, `current_evidence_supports_claim`, `system2_needed`) auflösbar machen (Alias, keine stille Umbenennung) | A |
| 7 | State Builder | `StateSanitizer` + `state_fingerprint` | **`source_event_refs`, `workspace_fingerprint`, `created_at`** am State | A |
| 8 | Voice-Triage-Pack | 19 Fragen: Loop/Completion/Drift/Research/Recovery | **Gruppe „Untrusted content" (3 Fragen)** fehlt komplett; §8-Kandidaten (6 Output-Ziele) prüfen | A |
| 9 | CompletionLedger + Preflight | `completion_precheck` (advisory) + `GateRunner.verify(precheck=…)` | **Ledger** (16 Kriteriums- + 7 Globalfelder), **Hard Checks**, **semantischer Preflight (8 Fragen + `evidence_fit`)**, **Sanity-Pass (7 Fragen)**, Shortcut-Erkennung | A |
| 10 | Computer-Use-Pack | 5 Fragen | **Pre-action (6) + Post-action (5) + Recovery (5) + Candidate-Choice (6 Optionen)** fehlen | B |
| 11 | Visuelle Subtools | `capability`-Pack (4 Fragen) | **Subtool-Vertrag** mit 16 Feldern inkl. `system_one_prechecks/postchecks`; Blind-Replay-Verweigerung | B |
| 12 | System-2-Eskalation | `voice…system_two_needed`, `propose_route` ROUTE_ESCALATE_SYSTEM_TWO | **13 Eskalationsauslöser** als prüfbare Liste + Entscheidungsfunktion | B |
| 13 | Risk/Confidence | `RISK_POLICY` R0–R4, `ThresholdProfile` | **`threshold_profile_id`, `dataset_version`, gemessene Precision/Recall/FPR/FNR**, Unsicherheitsband, kein globaler Schwellwert | A |
| 14 | LLM-Adapter | vorhanden, `live_jev=False` | Kompatibilitätsnachweis dokumentieren (`JEV_PROVIDER_COMPATIBILITY.md`) | B |
| 15 | TypeSafeJevEvaluator | vorhanden, ohne Credential `blocked_external_access` | **HTTP/SDK-Vertrag** dokumentieren (`POST /v1/systemone`, `jev-latest`), Request-Form | B |
| 16 | Treasury | `TypeSafeJevEvaluator` fragt `credential_resolver` (`missing="treasury secret handle…"`) | **`ConnectionRef(provider=typesafe, target=system_one, scope=evaluate, secret_handle, status, revision)`** über `src/godsv/treasury.py`; kein Key in Prompt/Event/Log/Screenshot | B |
| 17 | Providerfehler | `_classify_transport_error` → Exception-Klassen | **7er-Taxonomie als Codes** (`connection_invalid`, `request_contract_error`, `provider_rate_limited`, `provider_overloaded`, `provider_timeout`, `provider_unavailable`, `adapter_error`) + Fallback-Kette | B |
| 18 | Shadow | `ShadowHarness` (record/observe_outcome/summary, Guard) | **`trace_id`, `current_system_result`, `later_ground_truth`, `cost_if_known`** | A |
| 19–20 | Kalibrierung/Holdout | `ThresholdProfile` (eingefroren vor Holdout) | **Datensätze** (Voice/Completion/CU) + **Metrik-Harness**, das ohne Rohdaten laut scheitert | B |
| 21 | Canary J0–J5 | fehlt | neues Stufenmodell + Flag-Wirkung | B |
| 22 | Feature-Flags | 5 Felder (`enabled`, `provider`, `shadow_mode`, `voice_triage`, `completion_precheck`, `computer_use`) | **`goal_completion`, `recovery`, `llm_fallback`** fehlen | A |
| 23 | Cache/Dedup | fehlt | **Cache-Key** (7 Felder) + Invalidierung nach Goal-/Policy-/Workspace-/Evidence-/GUI-/Tool-/Pack-Änderung | A |
| 24 | Runtime-Events | 6 Arten (`_evaluation`, `_shadow`, `_error`, `_blocked`, `_stale`, `_decision`) | **`requested`, `completed`, `failed`, `discarded_stale`, `escalated`** ergänzen | A |
| 25 | UI | Plugin-Smoke 158 Checks grün, Meldungen fehlen | **Nutzertexte + Thinking-Orb-Strings** in der Plugin-Oberfläche, ohne den Smoke-Vertrag zu brechen | Integrator |
| 26 | Observability | `latency_ms` auf der Evaluation | **Zeitfelder** (state_build/sanitization/queue/provider_latency/decision_policy/total + CU + Completion) + Token/Kosten | A (Voice/Completion), B (CU) |
| 27 | Benchmarkarme | `docs/BENCHMARK-RUN-SCHEMA.json`, `tools/eval_benchmark.py` (scheitert laut) | **Arme A–D (Voice), A–C (Completion), A–B (CU)** als ausführbares Gerüst, Rohdaten `not_run` | B |
| 28 | Security/Injection | `untrusted.py`, Security-Findings | **5 Injektionsfälle** + Nachweis, dass kein Ergebnis Scope/Secret/Approval/Policy/Goal/Pause/Completion ändert | Wave 2 |
| 29 | Recovery-Crashfälle | RF-09-Overlay für die Engine, `recovery`-Pack | **7 System-One-Crashfenster** (vor/mit/nach Request, nach Persist, Duplikat, spät nach Revision/Stop) | Wave 2 |
| 30 | Testmatrix | 117 + 89 Pins, `tools/jev_contract_probe.py` 31/31 | **Unit/Contract/Integration/E2E-J1/J2/J3 inkl. Gegenfälle** als eigene Prozesse | A + B, E2E durch Integrator |
| 31 | JEV-ID-Sync | ABNAHME: JEV-01…20 mit eigener Bedeutung | **Mapping-Tabelle Ziel-IDs ↔ ABNAHME-IDs**; die Nummerierung weicht ab JEV-04 systematisch ab — dokumentieren, ABNAHME gewinnt | Integrator |
| 35–37 | Grade | heute `JEV_READY` | `JEV_LIVE_TESTED` / `JEV_ACTIVE_VERIFIED` bleiben `blocked` (kein TypeSafe-Zugang) | Integrator |
| 38 | Abschlussartefakte | `docs/abnahme/*` vorhanden | **JEV_IMPLEMENTATION_REPORT / JEV_CODE_MAP / JEV_TEST_REPORT / JEV_BENCHMARK_REPORT / JEV_CALIBRATION.json / JEV_HOLDOUT_RESULTS.json / JEV_PROVIDER_COMPATIBILITY / JEV_SECURITY_REVIEW / evidence/jev/** | Wave 2 |

## 2. Wellen

* **Welle A (läuft):** Worker A (Voice + Completion), Worker B (Computer Use + Provider) — §33, max. zwei.
* **Welle B:** Security/Injection (§28), Recovery-Fenster (§29), Abschlussartefakte (§38), Benchmark-Arme.
* **Welle C:** unabhängiger Reviewer (§109–§111) mit **eigenen** Proben, danach Register/Mapping.

## 3. Was auch nach vollständiger Umsetzung `blocked` bleibt

TypeSafe-Livezugang (JEV-07, damit `JEV_LIVE_TESTED`), Kalibrierungs-/Holdout-**Messwerte** aus echten
Entwicklungsdaten, echter Desktop für die visuellen Subtools, Windows/macOS-Buildhosts und Signierung,
der 152k-/80-Iterations-Langlauf. Nach §118 bleibt das je Punkt `blocked` mit benannter nächster Aktion.
