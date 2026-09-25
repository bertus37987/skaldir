# JEV Worker A — Voice + Completion: implementation report

Auftrag: `JEV_IMPLEMENTATION_GOAL.md` §3, §4, §5, §6, §7, §8, §9, §13, §18, §22, §23,
§24, §26, §30 (Unit/Contract/Integration/E2E-J1/E2E-J2) und der §33-Scope „Worker A“.
Branch: `godsv-supervisor-hardening`. **Nichts committet.**

Evidenzklassen nach `ABNAHME.md` §5.1: `contract` | `mock` | `integration` | `live`.
Was hier steht, ist **kein** Live-Jev-Nachweis: jeder Provider in diesem Delta ist ein
skripteter Replay-Fixture, `provider_mode = replay_fixture`, `live_jev = false`.

---

## 1. Die eine Facade

**`SystemOneService` — `src/godsv/system_one_service.py:91`.**
Provider-Kante für Business-Logik: `SystemOneService.advise()` (`:165`),
`evaluate_snapshot()` (`:233`, Sanitizer + §7-Stempel), `voice_triage()` (`:266`).

Erlaubte Kanten (§33): `Voice -> SystemOneService -> SystemOneEvaluator`,
`Completion -> SystemOneService -> SystemOneEvaluator`. Die Business-Module sehen nur
den internen Vertrag:

* `src/godsv/voice.py:537` `triage_with_system_one(..., service=None)` — `service=None`
  an `voice.py:545`
* `src/godsv/completion.py:2020` `completion_preflight(..., service=None)` — `service=None`
  an `completion.py:2028`

## 2. Der Test, der beweist, dass voice.py/completion.py keinen Provider erreichen

`tests/test_jev_service_facade.py::test_business_modules_import_with_every_provider_module_unpoisonable`

Der Test startet einen Subprozess, in dem ein `sys.meta_path`-Blocker **jeden**
Provider-/HTTP-Modulnamen (`godsv.system_one_adapters`, `godsv.llm`, `httpx`,
`requests`, `urllib.request`, `http.client`, `aiohttp`, `typesafe`) mit `ImportError`
belegt, löscht die Module aus `sys.modules` und importiert dann `godsv.voice` und
`godsv.completion`. Beide Importe gelingen (`OK`) — hätten sie einen Provider
importiert, wäre der Import gescheitert. Dazu statisch:
`test_business_modules_do_not_import_a_provider` (AST-Import-Scan) und
`test_business_modules_carry_no_vendor_endpoint` (kein `api.typesafe.ai`,
`/v1/systemone`, `Bearer `, `jev-latest` in beiden Dateien).

---

## 3. Abschnittsweise

| § | Umsetzung | Evidenzklasse | Nicht bewiesen |
|---|---|---|---|
| 3 | `SystemOneService` als einzige Kante; `build_service()`; kein zweiter Runtime/Policy/Goal-Store | contract+mock | echte Providerwahl über Treasury (Worker B) |
| 4.1 | `EvaluationContext`: `workspace_fingerprint`, `caller`, `question_pack_id/version`, `budget_reservation`, `privacy_scope` (alle defaulted) | contract | — |
| 4.2 | `provider_mode` mit genau 4 Werten; LLM-Adapter nie `typesafe_jev_live`; live nur bei `live_jev=True`; `provider_access_mode` bleibt | contract | — |
| 4.3 | `question_type`, `provider_metadata`; Noul `confidence=null` im serialisierten Vertrag (kein Attribut — der Pin `hasattr` gilt) | contract | — |
| 5 | `QuestionPack.purpose/applicable_states/risk_class/fallback_policy/calibration_dataset_version` + `assert_pack_metadata` im produktiven Registry | contract | Kalibrierungsdatensätze selbst (§19/20, Wave B) |
| 6 | `CANONICAL_FEATURES`-Alias-Layer + `Question.canonical_id`; 9 kanonische IDs auflösbar, Legacy-IDs unverändert | contract | — |
| 7 | `SystemOneState.source_event_refs/workspace_fingerprint/created_at`, vom Sanitizer gestempelt, nicht im Fingerprint | contract | — |
| 8 | Gruppe `untrusted` mit genau 3 Fragen; `SECTION_GROUPS` für die fünf §8.2-Sektionen; `untrusted_content`-Trigger; Untrusted-Regeln in `propose_route`; keine Jev-Autorität | contract+integration | reale Injection-Fälle (§28 = Welle B) |
| 9 | `CompletionLedger` (16+7 Felder), `hard_checks` (9 deterministisch, vor jedem Jev-Call), Preflight-Pack (8 Noul + `evidence_fit`), `detect_shortcuts` (8), `aggregate_criteria` (Code-Aggregation), Sanity-Pack (7) über 9-Zustands-Summary, Gate bleibt Autorität | contract+integration+E2E(mock-Provider) | echte Modell-Urteile: alle Lesungen sind skriptet |
| 13 | `ThresholdProfile`: `threshold_profile_id`, `question_pack_version`, `dataset_version`, gemessene Raten (`None` = nicht gemessen), Unsicherheitsband → Eskalation, pro Action-Klasse, freeze-erzwungen | contract | **keine** Messwerte (Kalibrierung/Holdout = Wave B) |
| 18 | Shadow-Record: `trace_id`, `current_system_result`, `later_ground_truth`, `cost_if_known`; `mutations=0`, `route=None` unverändert | contract | echte Shadow-Traces gegen Jev (blocked) |
| 22 | 9 Flags inkl. `goal_completion`, `recovery`, `llm_fallback`; `goal_completion` vs `completion_precheck` im Modul-Docstring | contract | — |
| 23 | `CacheKey` (7 Pflichtfelder + Workspace + 3 Epochen); kein Reuse nach Goal/Policy/Workspace/Evidence/GUI/Tool/Pack; stale wird **refused**, nicht refreshed | contract | — |
| 24 | 5 neue Event-Arten (+6 alte) + `system_one_event_payload`-Guard (keine Secrets, kein vollständiger privater State) | contract | — |
| 26 | `SystemOneMetrics` mit allen §26-Namen; nicht Gemessenes ist `not_run`, nie `0` | contract | keine realen Zahlen (kein Live-Provider) |
| 30 | 9 neue Testdateien; E2E-J1 (gesund → observe, Loop → Eskalation → Strategiewechsel → Effekt verifiziert), E2E-J2 (mock-only bleibt offen, Gegenfall `VERIFIED_COMPLETE`) | integration+E2E | E2E mit echtem System-2-Verifier/Desktop |
| 33 | Facade + §3-Kanten, `via="system_one_service"` am Advisory | contract | — |

---

## 4. Befehle (rohe Ausgaben im Worker-Bericht an den Hauptagenten)

* `.venv/bin/python -m pytest tests/test_jev_*.py tests/test_system_one_*.py tests/test_completion*.py -p no:randomly`
* `.venv/bin/python -m pytest tests/ -p no:randomly` (volle Suite, keine parallele pytest-Instanz)
* `.venv/bin/python -m ruff check <files>` (line-length 100)
* `.venv/bin/python -m bandit -q -r src -ll`

## 5. Ehrliche Grenzen

1. **Kein Live-Jev.** Alle Provider in diesem Delta sind skriptete Fixtures
   (`replay_fixture`); `live_jev=false` überall. Höchste erreichbare Aussage: `JEV_READY`.
2. **Keine Kalibrierungs-/Holdout-Zahlen.** `measured_precision/recall/fpr/fnr` bleiben
   `None`; es gibt keine Threshold-Messung, kein Band, keine Holdout-Auswertung.
   `JEV_ACTIVE_VERIFIED` bleibt `blocked`.
3. **Kein echter Desktop / kein System-2-Verifier.** Die E2E-Arme benutzen den echten
   `GateRunner` und den deterministischen Trajectory-Reader, aber keinen LLM-Verifier.
4. **`voice.py`/`completion.py` rufen die Facade nicht selbst** — die Orchestrierung liegt
   in `SystemOneService.voice_triage()` bzw. in `completion_preflight(service=…)`. Die
   Business-Module kennen nur den internen Vertrag und die `service=`-Injektion.
5. **Kein Commit** in diesem Arbeitsblock.
