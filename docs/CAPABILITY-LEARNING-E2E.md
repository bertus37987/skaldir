# Capability-Learning — Ende-zu-Ende-Nachweis (Block 6)

Jeder Abschnitt trennt strikt **exists** (Code vorhanden) / **wired** (verdrahtet) /
**tested** (durch Tests belegt) / **live-verified** (im laufenden System beobachtet)
plus **Restgrenzen** (was bewusst *nicht* bewiesen ist).

## 1. Beobachtete Traces

**was capability_mining.py einliest.**

- **exists** — `TraceStep` (`src/godsv/capability_mining.py:436`): ein beobachteter Schritt
  mit `tool`, `operation` (normalisiert), `args`, `ok`. `GeneralizedCapability`
  (`src/godsv/capability_mining.py:446`): der wiederkehrende Lauf mit seinen variierenden
  Eingaben als `parameters` (plus `occurrences`, `sessions`, `evidence_sessions`,
  `suggested_type`, `synthesis_examples`). Zuführung: `traces_from_events`
  (`src/godsv/capability_mining.py:769`), Normalisierung `_traceable`
  (`:844`) und `_operation_of` (`:851`), Mining-Einstiege `mine_from_traces` (`:741`),
  `mine` (`:259`), `mine_from_requests` (`:167`), `mine_from_commands` (`:223`).
- **wired** — `mine_from_traces` (`:741`) schreibt über `CandidateStore.merge`
  (`:131`) / `set_status` (`:152`) in den Kandidaten-Store; die Registry-Sicht für die
  Suche liefern `_registry_entries` (`:937`), `_hub_entries` (`:918`),
  `_workflow_entries` (`:899`).
- **tested** — `TestSemanticGeneralization` (`tests/test_capability_learning.py:42`)
  speist echte `TraceStep`-Listen ein (`BLENDER_A`/`BLENDER_B`, `tests/test_capability_learning.py:30-41`);
  `test_mine_from_traces_records_one_candidate_not_two` (`:74`) prüft die Store-Aufnahme.
- **live-verified** — **nein.** Es gibt keinen Test und keinen Aufrufer, der
  `traces_from_events` (`src/godsv/capability_mining.py:769`) gegen einen echten
  Event-Stream laufen lässt.
- **Restgrenzen** — `traces_from_events` (`src/godsv/capability_mining.py:769`) ist
  implementiert, hat aber außerhalb von `capability_mining.py` und den Tests **keinen
  Aufrufer** (grep über `--include=*.py`): die Verdrahtung „laufende Session → Traces"
  ist offen. Belegt ist nur das Mining über explizit übergebene Trace-Listen.

## 2. Generalisierte Capability

**das Kernbeispiel.** Zweimal „open A, wait, screenshot, inspect" und
„open B, wait, screenshot, inspect" muss **eine** parametrisierte Capability werden —
**nicht** zwei Literale.

- **exists / tested** — `test_two_literal_runs_collapse_to_one_parametric_capability`
  (`tests/test_capability_learning.py:43`) ist genau dieser Fall: `generalize_traces`
  (`src/godsv/capability_mining.py:579`) über `BLENDER_A`/`BLENDER_B` ergibt
  `occurrences == 2`, `sessions == 2`, **`"A.blend" not in result.steps_signature`**,
  **`"B.blend" not in result.steps_signature`**, `parameters` nicht leer und
  `"project_path" in result.parameters`. Der variierende Pfad wird also zum Parameter,
  nicht zum Literal.
- **tested** — `test_mine_from_traces_records_one_candidate_not_two`
  (`tests/test_capability_learning.py:74`): `len(candidates) == 1` — **ein** Kandidat,
  nicht zwei; `row["occurrences"] == 2`, Signatur trägt Platzhalter (`"["`/`"{"`).
  Ergänzend: `test_different_shapes_do_not_merge` (`:56`, kein Sammelbecken),
  `test_single_occurrence_is_not_a_recurring_capability` (`:64`, Mindesthäufigkeit 2),
  `test_constant_steps_survive_as_fixed_positions` (`:67`, konstante Schritte wie `wait`
  bleiben Literal), `test_parameter_types_are_inferred` (`:81`, `type in ("path","string")`).
  Mechanik: `_token_replace` (`:492`), `_boundary_pair` (`:522`), `_step_variance` (`:537`),
  `_param_name` (`:467`), `_value_type` (`:482`), `_is_pathish` (`:460`),
  `_capability_name` (`:694`).
- **live-verified** — **nein.** Kein Test generalisiert einen echt geflogenen Lauf;
  alle Traces sind Fixtures.
- **Restgrenzen** — Der Zielname `inspect_blender_project(project_path)` wird in der
  Generalisierung nur *weich* geprüft (`name.startswith("inspect_blender") or "blender" in name`,
  `tests/test_capability_learning.py:53`); die harte Zusicherung des Namens liegt in der
  Live-Insertion (`tests/test_capability_learning.py:477`, `:537`, `:568`). Merge-Kriterium ist
  Operations-/Token-Gleichheit (`_token_replace` `:492`), keine Kausalanalyse — verschiedene
  Schrittfolgen verschmelzen bewusst nicht (`:56`).

## 3. Search-before-Build

**vor jedem Bau wird gesucht: Tools, Skills, MCPs, Testing Tools, Tool Hub. Ein Duplikat
wird abgelehnt, Reuse gemeldet.**

- **exists** — `search_existing` (`src/godsv/capability_mining.py:1009`) und
  `search_before_build` (`src/godsv/capability_mining.py:1052`). Suchflächen:
  `SEARCH_SURFACES` (`src/godsv/capability_mining.py:882-889`) =
  `generated_tools, testing_tools, tool_hub, skills, mcp, workflows`. Schwellen:
  `_REUSE_MIN_OVERLAP = 2` (`:891`, Advisory-Report), `_BUILD_REFUSE_OVERLAP = 3`
  (`:896`, Refusal = echtes Duplikat). Scoring `_match_score` (`:991`), Manifest-Loader
  `_load_manifest` (`:960`).
- **wired** — der Builder ruft die Suche **vor** dem Schreiben:
  `from godsv.capability_mining import _BUILD_REFUSE_OVERLAP, search_before_build`
  (`src/godsv/toolbuilder.py:247`) und `search_before_build(...)`
  (`src/godsv/toolbuilder.py:249`). Bei `decision == "reuse"` wird der Bau abgelehnt mit
  `reuse instead of duplicate: '<name>' (<surface>) …` und `reuse_recommended` gesetzt
  (Fehlerpfad unmittelbar nach `:249`).
- **tested** — Klasse `TestSearchBeforeBuild` (`tests/test_capability_learning.py:146`):
  `test_existing_equivalent_capability_is_reused_not_duplicated` (`:147`),
  `test_duplicate_is_refused_by_the_builder` (`:163`, Ablehnung im echten Builder),
  `test_genuinely_new_capability_still_builds` (`:188`, kein False-Block),
  `test_search_covers_tools_skills_mcp_testing_and_hub` (`:197`, Abdeckung aller fünf
  Flächen).
- **live-verified** — **nein.** Die Tests laufen gegen echte Registries, aber in einem
  tmp-`HERMES_HOME`; kein Lauf gegen die produktive Registry.
- **Restgrenzen** — Die Suche ist lexikalisch (Term-Überlappung über `_match_score`,
  `src/godsv/capability_mining.py:991`) — keine Embeddings/Semantik. Ein 2-Term-Treffer
  meldet nur, erst ein 3-Term-Treffer verweigert (`:891` vs. `:896`): bewusst, damit
  legitime neue Arbeit nicht durch ein Schlagwort blockiert wird.

## 4. Validierung und Holdout

**Synthese-Beispiele MÜSSEN von Validierungs-Beispielen abweichen.** Testing Tool:
funktionierendes Ziel **PASS**, kaputtes Ziel **FAIL**. General Tool: ungesehener Input
ergibt ein strukturiertes Ergebnis.

- **exists** — `holdout_ok` (`src/godsv/capability_mining.py:1091`),
  `validation_is_holdout` (`:1104`), `validate_testing_tool_holdout` (`:1149`),
  `run_general_tool_holdout` (`:1178`), `_case_result` (`:1134`), Basisprüfung
  `validate_testing_tool` (`:362`).
- **wired** — der Builder erzwingt den Holdout vor der Registrierung:
  `validation_is_holdout` (`src/godsv/toolbuilder.py:402`), bei Verstoß
  `manifest["status"] = "needs_holdout"` (`src/godsv/toolbuilder.py:407`) und Abbruch
  (`:405-410`); bei Erfolg `validation["holdout"] = {"status": "validated", …}`
  (`src/godsv/toolbuilder.py:420`). Synthese-/Holdout-Felder am Request:
  `synthesis_examples` / `holdout_inputs` (`src/godsv/toolbuilder.py:108`, `:109`).
- **tested** — Klasse `TestHoldoutValidation` (`tests/test_capability_learning.py:266`):
  `test_testing_tool_passes_working_and_fails_broken_target` (`:267`, Arbeitsziel PASS,
  kaputtes Ziel FAIL, `status == "validated"`),
  `test_a_lying_checker_cannot_be_validated` (`:287`, Immer-PASS-Checker → `"invalid"`),
  `test_holdout_examples_must_differ_from_synthesis_examples` (`:297`),
  `test_general_tool_answers_an_unseen_input_with_structured_output` (`:305`),
  `test_builder_refuses_holdout_that_reuses_synthesis_examples` (`:322`).
- **live-verified** — **nein.** Kein realer General-Tool-Lauf gegen ein Fremdsystem.
- **Restgrenzen** — „Holdout" heißt hier: Arbeits-/Kaputt-Paar ohne wiederholtes Ziel plus
  String-Disjunktheit zu `synthesis_examples` — kein statistischer Holdout, keine
  Mindest-Stichprobengröße.

## 5. Registrierung und Health-Lifecycle

**die acht Zustände, Degradations-Trigger, Quarantäne, Ruhestand.**

- **exists** — `src/godsv/capability_health.py`. Die acht Zustände: Modul-Docstring
  `:9-17`, als Tupel `STATES` (`src/godsv/capability_health.py:39-48`) =
  `CANDIDATE :40`, `BUILDING :41`, `VERIFIED :42`, `HEALTHY :43`, `DEGRADED :44`,
  `BROKEN :45`, `QUARANTINED :46`, `RETIRED :47`. Nur `HEALTHY` und `VERIFIED` dürfen
  auto-geroutet werden: `AUTO_ROUTABLE` (`:52`). Ledger + Cursor: `HEALTH_FILE` (`:36`,
  `capability_health.jsonl`), `USAGE_CURSOR_FILE` (`:37`,
  `capability_health_cursor.json`). Trigger-Katalog `TRIGGERS` (`:69-78`); harte Trigger
  `_HARD_TRIGGERS` (`:59-67`) = `false_pass, schema_mismatch, api_change,
  runtime_incompatible, dependency_incompatible`. Quarantäne-Schwelle
  `QUARANTINE_AFTER = 3` (`:56`, „drei erlaubt, der vierte entfernt"). Ableitung per
  Fold: `_fold` (`:150`), Zuweisungen `:152` (CANDIDATE), `:163-165` (BUILDING),
  `:170` (HEALTHY), `:176` (BROKEN), `:181` (VERIFIED), `:194` (RETIRED),
  `:204-210` (QUARANTINED / BROKEN / DEGRADED), `:213-214` (Digest-Wechsel → DEGRADED).
- **wired** — Schreibpfade: `record_building` (`:243`), `record_verification` (`:247`),
  `record_trigger` (`:264`), `recheck` (`:276`), `retest` (`:293`), `retire` (`:299`),
  `observe_usage` (`:345`), Trigger-Klassifikation `classify_trigger` (`:327`); Leser:
  `file_state` (`:228`), `all_states` (`:234`), `routable` (`:305`). Der Builder ist
  verdrahtet: `health.record_building(...)` (`src/godsv/toolbuilder.py:274`) und
  `health.record_verification(..., ok=True, ...)` (`src/godsv/toolbuilder.py:508`,
  in einem `try/except`, damit ein Ledger-Schreibfehler keinen guten Build killt).
  `liveinsert` liest `capability_health` (Import in `src/godsv/liveinsert.py`).
- **tested** — Klasse `TestHealthLifecycle` (`tests/test_capability_learning.py:349`):
  `test_lifecycle_states_are_complete_and_ordered` (`:350`, alle acht + `AUTO_ROUTABLE ==
  {"HEALTHY","VERIFIED"}`), `test_verified_tool_becomes_healthy_and_is_routed` (`:367`),
  `test_degradation_triggers_take_the_tool_out_of_auto_routing` (`:382`, parametrisiert),
  `test_quarantine_is_reached_after_repeated_breakage` (`:399`),
  `test_quarantined_tool_can_be_retested_and_repaired` (`:412`),
  `test_code_change_invalidates_the_verification_evidence` (`:431`),
  `test_retired_tool_is_never_routed` (`:449`),
  `test_health_survives_a_fresh_process_view` (`:458`).
- **live-verified** — **eingeschränkt.** `test_health_survives_a_fresh_process_view`
  (`tests/test_capability_learning.py:458`) liest den Zustand nach einem *frischen
  Prozess-View* neu aus dem Ledger — das ist ein Persistenz-Nachweis, aber gegen ein
  tmp-`HERMES_HOME`, nicht gegen die produktive Instanz.
- **Restgrenzen** — Health ist bewusst **kein** zweites Register (Modul-Docstring
  `src/godsv/capability_health.py:19-22`): Quelle der Wahrheit bleiben
  `generated_tools/*/manifest.yaml` + `tools.db` bzw. `tool_usage.jsonl`; Health
  protokolliert nur Beobachtungen. Keine Event-Store-Writes (`:24-26`) — dadurch
  erscheint Health **nicht** im Event-Stream, was für Audits ein blinder Fleck ist.
  `observe_usage` (`:345`) liest den Nutzungs-Cursor, aber es gibt keinen Test, der den
  produktiven Nutzungs-Ledger durchspielt.

## 6. Live-Insertion

**`apply_pending` / `hot_set` / `boundary_safe`; Hot-Set auf drei begrenzt; sichere
Modellgrenze; der MCP-Server listet das eingefügte Tool.**

- **exists** — `src/godsv/liveinsert.py`: `_approved_specs` (`:35`, nur freigegebene),
  `_ready` (`:53`), `RuntimeView` (`:68`, `list_tools` `:79`, `has` `:85`),
  `CapabilityIndex` (`:89`, `inserted` `:104`, `hot_set` `:107`, `hot` `:111`,
  `begin_call` `:115`, `end_call` `:120`, **`boundary_safe` `:123`**,
  **`apply_pending` `:131`**, `insert` `:146`, `_push_to_servers` `:184`,
  `still_available` `:211`, `runtime_view` `:222`, `mcp_server` `:227`, `snapshot` `:243`)
  plus `discover_tool_call` (`:254`). Der Deckel: **`HOT_LIMIT = 3`**
  (`src/godsv/liveinsert.py:32`), angewandt in `hot_set` (`:107-109`, „newest `HOT_LIMIT`
  capabilities, newest first").
- **wired** — die Kette steht im Modul-Docstring (`src/godsv/liveinsert.py:1-23`):
  `VERIFIED` → Registry-Update (`tools.db` + `generated_tools/<name>/manifest.yaml`,
  geschrieben vom bestehenden Builder — kein zweites Register) → nächste **sichere
  Modellgrenze** → Hot-Set (max. 3) → echte Tool-Discovery über das **dieselbe**
  Server-Objekt → echter Tool-Call über denselben MCP-Wrapper. Eingefügtes Tool wird an
  die Server gepusht: `_push_to_servers` (`:184`); `mcp_server` (`:227`) exponiert es.
- **tested** — Klasse `TestLiveInsertion` (`tests/test_capability_learning.py:476`):
  `test_verified_capability_is_discoverable_and_callable_in_the_same_process` (`:477`),
  `test_hot_set_is_capped_at_three_new_capabilities` (`:509`),
  `test_degraded_capability_leaves_the_hot_set` (`:525`),
  `test_live_insertion_happens_at_a_safe_boundary_only` (`:537` — `begin_call` →
  `apply_pending(...)["applied"] == []`, nach `end_call` `== ["inspect_blender_project"]`),
  `test_insertion_reports_a_real_error_for_an_unknown_tool` (`:552`),
  `test_mcp_server_lists_the_live_inserted_tool` (`:568`, **Modul-Level**, außerhalb der
  Klasse).
- **live-verified** — **eingeschränkt, im Test.** `test_mcp_server_lists_the_live_inserted_tool`
  (`tests/test_capability_learning.py:568`) prüft gegen das echte FastMCP-Server-Objekt und
  die echte `tools.db`/`generated_tools`-Oberfläche — aber innerhalb des Testprozesses,
  nicht gegen einen laufenden Host.
- **Restgrenzen** — Kein Aufrufer außerhalb von `liveinsert.py` und den Tests
  (grep `liveinsert|CapabilityIndex`: Treffer nur in `capability_health.py`,
  `capability_mining.py`, `toolbuilder.py`, `liveinsert.py`, Tests) — die
  **Host-seitige** Verdrahtung (Plugin/CLI ruft `apply_pending` an der echten
  Modellgrenze) fehlt.

## 7. Tatsächlicher Same-Session-Aufruf

**Hier wird strikt getrennt: was auf Testebene belegt ist — und was nicht.**

- **tested / belegt** — `test_verified_capability_is_discoverable_and_callable_in_the_same_process`
  (`tests/test_capability_learning.py:477`): Eine `RuntimeView` wird **gebunden, bevor**
  die Capability existiert (`server_handle.list_tools() == []`, `:485`). Dann baut der
  echte Builder (`:487`), `index.insert(...)` liefert `inserted is True` und `hot is True`
  (`:489-491`). Danach, **ohne Neustart, im selben Prozess, über dieselbe View**:
  `"inspect_blender_project" in server_handle.list_tools()` (`:494`)
  und ein echter Tool-Call `discover_tool_call(index, "inspect_blender_project",
  {"project_path": "/tmp/live.blend"})` → `call["ok"] is True`,
  `call["result"]["project"] == "/tmp/live.blend"` (`:496-501`), plus Auffindbarkeit über
  `route_candidates(...)` (`:502-504`).
  Belegt ist damit: **Registrierung + Discovery + Aufruf über die reale MCP-Oberfläche im
  selben Prozess, ohne Neustart.**
- **live-verified** — **NEIN — und das ist der zentrale Restpunkt.** Der echte
  **Hermes-Roundtrip ohne Restart** ist **nicht bewiesen**: dass ein laufender Hermes-Agent
  ein bereits gelesenes Tool-Schema in einem echten Turn neu einliest, erfordert einen
  echten Host-Roundtrip und wird von keinem Test dieser Datei abgedeckt. Der Test-Header
  sagt es selbst: „no Blender, **no network, no host roundtrip**"
  (`tests/test_capability_learning.py:5-6`); die Modul-Docstring-Grenze steht in
  `src/godsv/liveinsert.py:24-30` („this proves the *runtime* insertion inside one process
  … It does not by itself prove that a live Hermes agent turn re-read its tool schema —
  that requires a real host roundtrip"). Die Host-Verdrahtung fehlt zusätzlich
  aufruferseitig (siehe Abschnitt 6, Restgrenzen).
- **Restgrenzen** — Offener Punkt, **nicht** behaupten: (a) echter Hermes-Turn ohne Restart
  (`src/godsv/liveinsert.py:24-30`, `tests/test_capability_learning.py:5-6`);
  (b) Aufruf der Insertion an einer realen Modellgrenze aus dem Host heraus
  (`src/godsv/liveinsert.py:123` `boundary_safe`, `:131` `apply_pending` haben keinen
  Produktions-Aufrufer).

## 8. Testnachweis

**Befehl (genau dieser):**

```
.venv/bin/python -m pytest tests/test_capability_learning.py -q
```

**Gemessene Ausgabe** (Reproduktion 14.09.2026, Repo `/home/willy/projects/gods-voice`,
Branch `godsv-supervisor-hardening`):

```
.................................                                        [100%]
RC=0
```

`pyproject.toml:40` setzt `addopts = "-q"`; ein zusätzliches `-q` auf der Kommandozeile
schaltet die Summary-Zeile ab. Ohne das doppelte `-q` zeigt derselbe Lauf die Zahl:

```
.................................                                        [100%]
33 passed in 6.59s
```

- **33 Tests grün, RC=0.** `pytest --collect-only -q` bestätigt: `tests/test_capability_learning.py: 33`.
- **ruff clean:** `.venv/bin/ruff check src/godsv/capability_mining.py
  src/godsv/capability_health.py src/godsv/liveinsert.py tests/test_capability_learning.py`
  → `All checks passed!` (RC=0).
- **Restgrenzen** — Der Nachweis gilt für die vier eigenen Dateien. Alle acht Abschnitte
  oben sind mit `live-verified`-Status „nein/eingeschränkt" geführt; der Testlauf ersetzt
  **keinen** Host-Roundtrip.
