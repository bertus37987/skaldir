# SYSTEM_ONE_TRIAGE_PACK.md — JEV-12 Voice-Triage-Pack (§47/§90)

Stand: 16.09.2026, Branch `godsv-supervisor-hardening`, HEAD `27913dc`, **Arbeitsbaum
(uncommitted)**: `src/godsv/system_one_shadow.py` (neu), `src/godsv/system_one_policy.py`
(erweitert), `src/godsv/voice.py` (additiv), `tests/test_system_one_triage_pack.py` (neu).
Kein Commit durch den Implementierer.

Vertragstext (Teil XV §90, wörtlich):
„Loop, Fortschritt, Drift, Completion, Research und Recovery mit produktiven Gegenbeispielen
bewertet."

## Die ehrliche Antwort zuerst

**Bewertet und belegt auf `contract`- und `integration`-Stufe; `live` fehlt.**

* 16 Produktionsformen aus **echten** Auslöser-Arten (alle 12 aus `pause.TRIGGER_RULES` plus 9
  reale Monitor-Signal-Arten) werden je Form durch den *realen* Weg geschickt: echte
  Question-Registry → echte Komposition → echter Sanitizer → echte deterministische Voice →
  echtes `ShadowHarness`. Nur der Provider ist ein Fixture. **Evidence-Klasse: mock für den
  Provider, integration für den Weg.**
* Alle sechs geforderten Dimensionen (Loop, **Fortschritt**, Drift, Completion, Research,
  Recovery) haben mindestens ein **produktives Gegenbeispiel**: eine reale Form, in der die
  richtige Antwort „weiterarbeiten" lautet (produktives Debugging, echte Fortschritte, notwendige
  unterstützende Arbeit ohne Drift, aktuelle Completion-Evidence, ausreichende lokale Evidence,
  sicherer Replay).
* Jede Lesart kommt aus **einer** Regel-Tabelle (`propose_route`, §32-Leiter + §48-Eskalation),
  die Schatten-Record und Pack-Auswertung gemeinsam nutzen — kein zweites Regelwerk.
* Der Pfad hängt an den bestehenden Flags: `shadow_mode` aus oder Provider `disabled` ⇒ **nichts
  läuft**, `evaluate_pack_if_enabled` gibt `None` zurück, das Bestandsverhalten bleibt unberührt.
* **Offen und gemeldet:** vier echte Auslöser-Arten brauchen eine Research-Dimension, die das
  konservative `*`-Mapping des Packs nicht komponiert. Das ist als Befund dokumentiert
  (`report.coverage_gaps`), nicht still repariert — die Fixstelle liegt in
  `src/godsv/system_one_questions.py`, das außerhalb des Schreibrechts dieses Auftrags liegt.

## Der Korpus (16 Fälle, echte Auslöser-Formen)

Legende: **G** = produktives Gegenbeispiel (`counter_example=True`), Route = §32-Leiter.

| Fall | Dimension | Auslöser (real) | Form in Produktion | Route / Regel | Outcome | G |
|---|---|---|---|---|---|---|
| `loop_same_strategy_no_new_information` | Loop | `loop` | dritter Fehlschlag, gleiche Root Cause, gleiche Strategie, voriger Redirect wirkungslos | `pause_and_investigate` / `loop_no_progress_without_new_information` | intervention | |
| `loop_productive_debugging` | Loop | `loop` | Loop-Signal, aber wirklich Debugging: verschiedene Fehlersignaturen, neue Beobachtung, lokalisierter Fix, Verifikation | `observe` / `no_rule_matched` | keine | **G** |
| `progress_real_but_slow` | **Fortschritt** | `diff_churn` | Diff wächst ohne Meilenstein, aber messbarer Fortschritt und neue Information | `observe` / `no_rule_matched` | keine | **G** |
| `progress_stalled_without_new_information` | **Fortschritt** | `pattern_no_new_evidence` | Schritte ohne neue Evidenz, Fortschritt 0, gleiche Strategie | `pause_and_investigate` / `loop_no_progress_without_new_information` | intervention | |
| `drift_necessary_supporting_work` | Drift | `scope_drift` | Datei außerhalb des Pakts, weil das Goal sonst nicht erreichbar ist | `observe` / `no_rule_matched` | keine | **G** |
| `drift_non_goal_conflict` | Drift | `scope_drift` | Scope-Erweiterung widerspricht einem deklarierten Non-Goal | `request_user_input` / `drift_declared_non_goal_conflict` | intervention | |
| `drift_scope_expansion_only` | Drift | `wrong_tool_call` | geplanter Toolcall erweitert Produktscope, kein Non-Goal berührt | `investigate` / `drift_scope_expansion` | intervention | |
| `completion_stub_fake_done` | Completion | `slop_stub` | `NotImplementedError`-Stub, Abschluss behauptet, Evidence nicht auf der aktuellen Revision | `request_verification` / `completion_claim_without_current_evidence` | intervention | |
| `completion_claim_with_current_evidence` | Completion | `slop_todo_placeholder` | bewusst vertagtes TODO, Abschluss mit aktueller, revisionspassender Evidence | `observe` / `no_rule_matched` | keine | **G** |
| `research_version_sensitive_fact_missing` | Research | `error_burst` | Fehler wiederholt sich; Antwort hängt an versionssensitiver Außenfaktenlage | `investigate` / `research_missing_version_sensitive_fact` | intervention | |
| `research_missing_non_version_fact` | Research | `capability_gap` | Außenfakt fehlt, aber nicht versionssensitiv → Hinweis genügt | `hint` / `research_missing_fact_hint` | keine | **G** |
| `research_sufficient_local_evidence` | Research | `known_incident_repeat` | Incident-Memory beantwortet die Frage bereits | `observe` / `no_rule_matched` | keine | **G** |
| `recovery_system_two_needed` | Recovery | `known_incident_repeat` | Ursache wirklich unklar (neue App-Version), System Two angefordert | `escalate_system_two` / `recovery_system_two_needed` | intervention | |
| `recovery_side_effect_unsafe_replay` | Recovery | `stall` | Crash-Fenster zwischen Side Effect und Receipt; Replay unsicher, **obwohl** die System-Two-Lesart niedrig bleibt | `escalate_system_two` / `recovery_possible_side_effect_unsafe_replay` | intervention | |
| `recovery_state_unclear_replay_safe` | Recovery | `stall` | Zustand unklar, kein Side Effect, Replay sicher → klären statt eskalieren | `investigate` / `recovery_state_unclear_replay_safe` | intervention | **G** |
| `hallucinated_api_needs_research` | Research | `hallucinated_api` | erfundene API; nötig wäre eine versionssensitive Research-Frage — die Komposition fragt sie nicht | `observe` / `no_rule_matched` | intervention | (Lücke) |

`loop_productive_debugging` ist der Kern des Kriteriums: der echte Auslöser `loop` feuert, die
Form ist aber gesundes Debugging — genau der „produktive Gegenfall" aus §33/S05. Die
deterministische Voice liefert dort heute einen Hinweis (`baseline=hint`), das Pack liest
`observe`: **Disagreement wird gemessen und gespeichert, nicht wegargumentiert** (§21/E2E-13).

## Beweise (Kommandos und beobachtete Ausgabe)

**1. Rot vor der Implementierung** (beide Fehlschläge real beobachtet, Modul danach sofort
zurückbenannt):

```text
$ .venv/bin/python -m pytest tests/test_system_one_triage_pack.py -p no:randomly
tests/test_system_one_triage_pack.py:35: in <module>
    from godsv.system_one_policy import (
E   ImportError: cannot import name 'ROUTE_ESCALATE_SYSTEM_TWO' from
    'godsv.system_one_policy'                                     (1 error, 0.10s)

$ mv src/godsv/system_one_shadow.py src/godsv/_shadow_red_state.py.bak && \
  .venv/bin/python -m pytest tests/test_system_one_triage_pack.py -p no:randomly
tests/test_system_one_triage_pack.py:41: in <module>
    from godsv.system_one_shadow import (
E   ModuleNotFoundError: No module named 'godsv.system_one_shadow'  (1 error, 0.07s)
$ mv src/godsv/_shadow_red_state.py.bak src/godsv/system_one_shadow.py
```

**2. Grün danach:**

```text
$ .venv/bin/python -m pytest tests/test_system_one_triage_pack.py -p no:randomly
................................                                 [100%]
32 passed in 0.11s
```

**3. Der Auswertungslauf (Pflicht-CLI, dieselbe Ausgabe steht in `render_pack_report`):**

```text
$ .venv/bin/python -m godsv.system_one_shadow
JEV-12 voice triage pack — mode=shadow, cases=16, counter_examples=7, disagreements=15
routes: observe, hint, investigate, pause_and_investigate, request_verification, request_user_input, escalate_system_two (§32/§48)
  loop_same_strategy_no_new_information        trigger=loop                                 route=pause_and_investigate  rule=loop_no_progress_without_new_information     outcome=intervention_needed    baseline=hint     disagree=yes status=match
  loop_productive_debugging                    trigger=loop                                 route=observe                rule=no_rule_matched                              outcome=no_intervention_needed baseline=hint     disagree=yes status=match
  progress_real_but_slow                       trigger=diff_churn                           route=observe                rule=no_rule_matched                              outcome=no_intervention_needed baseline=hint     disagree=yes status=match
  progress_stalled_without_new_information     trigger=pattern_no_new_evidence              route=pause_and_investigate  rule=loop_no_progress_without_new_information     outcome=intervention_needed    baseline=hint     disagree=yes status=match
  drift_necessary_supporting_work              trigger=scope_drift                          route=observe                rule=no_rule_matched                              outcome=no_intervention_needed baseline=hint     disagree=yes status=match
  drift_non_goal_conflict                      trigger=scope_drift                          route=request_user_input     rule=drift_declared_non_goal_conflict             outcome=intervention_needed    baseline=hint     disagree=yes status=match
  drift_scope_expansion_only                   trigger=wrong_tool_call                      route=investigate            rule=drift_scope_expansion                        outcome=intervention_needed    baseline=hint     disagree=yes status=match
  completion_stub_fake_done                    trigger=slop_stub                            route=request_verification   rule=completion_claim_without_current_evidence    outcome=intervention_needed    baseline=hint     disagree=yes status=match
  completion_claim_with_current_evidence       trigger=slop_todo_placeholder                route=observe                rule=no_rule_matched                              outcome=no_intervention_needed baseline=hint     disagree=yes status=match
  research_version_sensitive_fact_missing      trigger=error_burst                          route=investigate            rule=research_missing_version_sensitive_fact      outcome=intervention_needed    baseline=hint     disagree=yes status=match
  research_missing_non_version_fact            trigger=capability_gap                       route=hint                   rule=research_missing_fact_hint                   outcome=no_intervention_needed baseline=hint     disagree=no status=match
  research_sufficient_local_evidence           trigger=known_incident_repeat                route=observe                rule=no_rule_matched                              outcome=no_intervention_needed baseline=hint     disagree=yes status=match
  recovery_system_two_needed                   trigger=known_incident_repeat                route=escalate_system_two    rule=recovery_system_two_needed                   outcome=intervention_needed    baseline=hint     disagree=yes status=match
  recovery_side_effect_unsafe_replay           trigger=stall                                route=escalate_system_two    rule=recovery_possible_side_effect_unsafe_replay  outcome=intervention_needed    baseline=hint     disagree=yes status=match
  recovery_state_unclear_replay_safe           trigger=stall                                route=investigate            rule=recovery_state_unclear_replay_safe           outcome=intervention_needed    baseline=hint     disagree=yes status=match
  hallucinated_api_needs_research              trigger=hallucinated_api                     route=observe                rule=no_rule_matched                              outcome=intervention_needed    baseline=hint     disagree=yes status=coverage_gap
  records=16 executed_routes=0 mutations=0 (shadow, §45)
  mismatches=0 outcome_mismatches=0 ok=True fully_covered=False
  COVERAGE GAPS (real trigger kinds whose composed pack cannot ask a needed dimension — reported, not silently repaired, §90):
    hallucinated_api: needs ['research'] but composes ['loop', 'completion', 'recovery'] → missing ['research']
    pattern_dependency_churn: needs ['research'] but composes ['loop', 'completion', 'recovery'] → missing ['research']
    dependency_change: needs ['research'] but composes ['loop', 'completion', 'recovery'] → missing ['research']
    outage_misdiagnosis: needs ['research'] but composes ['loop', 'completion', 'recovery'] → missing ['research']
  wall_time_s=0.00
```

(Wörtliche stdout der oben gezeigten Kommandos; `wall_time_s` schwankt zwischen Läufen.)

**4. Die belegten Pins (Auszug aus `tests/test_system_one_triage_pack.py`, 32 Tests):**
alle sechs Dimensionen vorhanden und **je Dimension** ein Gegenbeispiel; jede Fall-Lesart gleich
`expected_route` **und** `expected_rule`; jede Lesart gegen das produktive Outcome geprüft
(`outcome_mismatches == ()`); kein Gegenbeispiel pausiert oder eskaliert; jede Route auf der
§32-Leiter; `executed_routes == 0`, `mutations == 0`, `route is None` in allen 16 Records; eine
Fall-Antwort auf eine nicht gestellte Frage wird durch den **echten Antwortvertrag** abgewiesen
(`SystemOneContractError`); `report.coverage_gaps` entspricht exakt der dokumentierten Menge;
`pack_evaluation_enabled` ist `False` bei `flags=None`, all-off, `shadow_mode=False` und
`provider=disabled`, und `evaluate_pack_if_enabled` gibt dort `None` zurück.

**5. Statische Prüfung:** `ruff check` auf beiden neuen Testdateien und den drei Modulen →
`All checks passed!`; `bandit -q -r src -ll` → `RC=0`.

## Evidence-Klassen (§5.1), pro Aussage

| Aussage | Klasse |
|---|---|
| Regel-Tabelle lesbar/entscheidbar; Outcome-Zuordnung; Route ⊂ §32-Leiter | `contract` |
| Fixture-Antworten durch den echten `parse_answer`; Latenz/Usage-Felder | `mock` |
| Registry → Komposition → Sanitizer → Voice → `ShadowHarness` je Fall, 16 reale Auslöser-Formen | `integration` |
| Reale Jev-Antworten, reale Lauf-Outcomes, reale Nutzerwirkung | **nicht erreicht** |

## Ehrliche Grenzen — was hier **nicht** bewiesen ist

1. **Der Korpus ist gebaut, nicht gezogen.** 16 Formen sind von Hand aus realen Auslöser-Arten
   modelliert; es ist **keine** Zufallsstichprobe echter Trajektorien. Ein produktiver Gegenfall,
   der hier nicht steht, ist nicht bewertet.
2. **`expected_outcome` ist ein dokumentiertes Urteil**, kein gemessener Laufausgang
   (intervention_needed/no_intervention_needed sind die Ground-Truth-Annahme der jeweiligen Form).
   Es gibt **keine** echten Shadow-Läufe mit beobachtetem Outcome.
3. **Antworten kommen vom Fixture.** Das Pack wird auf seine *Regeln und Frageabdeckung* geprüft,
   nicht auf die Qualität echter Jev-Antworten (§5.2: Unit-Test ohne realen Nutzerpfad genügt
   nicht).
4. **Drift/Research/Recovery sind nur je 3–4 Formen.** Keine Abdeckung der Randfälle
   (z. B. widersprüchliche Antworten, niedrige Confidence, unbekannter GUI-State, §48-Liste), das
   bleibt JEV-18/JEV-15-17.
5. **Keine Kalibrierung.** `READ_THRESHOLD = 0.5` ist eine Leseschwelle; die per-Risk-Schwellen
   sind weder gesetzt noch eingefroren (JEV-11 offen, kein Holdout).
6. **Kein Benchmark.** Kein Vergleich der Arme A–D und keine Kosten-/Latenzmessung gegen einen
   echten Provider (JEV-19 bleibt offen).
7. **Die Abdeckungslücke ist nicht geschlossen.** Vier reale Auslöser-Arten bekommen keine
   Research-Frage; die Auswertung weist das aus, ändert aber `TRIGGER_GROUPS` nicht.
8. **§5.2:** Die eigene Suite ist kein alleiniger Nachweis; `tools/jev_contract_probe.py` (Probe
   des Integrators) prüft JEV-10/12 noch **nicht** — die Gegenprüfung durch den Integrator steht
   aus.
9. Der Arbeitsbaum enthielt während dieser Arbeit parallele, uncommittete Änderungen an
   `src/godsv/completion.py` und `src/godsv/computer_use.py` (anderer Worker, JEV-13/15/16);
   beide Dateien wurden hier nicht angefasst.

## Offener Befund (gemeldet, nicht still repariert)

`TRIGGER_GROUPS` (`src/godsv/system_one_questions.py`) kennt 8 Auslöser + `*`; die realen
Auslöser-Arten des Repos sind aber 12 (`pause.TRIGGER_RULES`) bzw. 21 Monitor-Signal-Arten. Der
Rückfall auf `*` = (`loop`, `completion`, `recovery`) ist konservativ, deckt aber die
Research-Dimension nicht ab. Für vier echte Arten ist genau das messbar falsch:

```text
$ .venv/bin/python -c "from godsv.system_one_shadow import trigger_coverage; \
    print([(g.trigger_kind, g.missing) for g in trigger_coverage()])"
[('hallucinated_api', ('research',)), ('pattern_dependency_churn', ('research',)),
 ('dependency_change', ('research',)), ('outage_misdiagnosis', ('research',))]
$ .venv/bin/python -c "from godsv.pause import TRIGGER_RULES; \
    from godsv.system_one_shadow import PRODUCTION_TRIGGER_KINDS; \
    print(sorted(set(TRIGGER_RULES) - set(PRODUCTION_TRIGGER_KINDS)))"
[]
```

Der Korpus deckt alle 12 Pause-Auslöser ab (zweite Zeile: nichts fehlt). Fix-Ort ist
`system_one_questions.py` — außerhalb des Schreibrechts dieses Auftrags. Vorschlag (nicht
ausgeführt), damit die Lücke in der zuständigen Datei geschlossen wird:
`"hallucinated_api": ("research", "recovery")`, `"dependency_change": ("research", "loop")`,
`"outage_misdiagnosis": ("research",)`, `"pattern_dependency_churn": ("research",)`.

## Reproduktion

```bash
cd ~/projects/gods-voice
.venv/bin/python -m pytest tests/test_system_one_triage_pack.py -p no:randomly
.venv/bin/python -m godsv.system_one_shadow
.venv/bin/python -c "from godsv.system_one_shadow import trigger_coverage; \
  print([(g.trigger_kind, g.missing) for g in trigger_coverage()])"
```
