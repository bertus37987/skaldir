# SYSTEM_ONE_SHADOW.md — JEV-10 Shadow Mode (§45/§88, E2E-13 §21)

Stand: 16.09.2026, Branch `godsv-supervisor-hardening`, HEAD `27913dc`, **Arbeitsbaum
(uncommitted)**: `src/godsv/system_one_policy.py` (erweitert), `src/godsv/voice.py` (additiv),
`src/godsv/system_one_shadow.py` (neu), `tests/test_system_one_shadow.py` (neu),
`tests/test_system_one_triage_pack.py` (neu). Kein Commit durch den Implementierer.

Vertragstext (Teil XV §88, wörtlich):
„Jev läuft parallel ohne Mutation. Disagreement, Latenz, Usage und Outcome werden gespeichert."

## Die ehrliche Antwort zuerst

**Erreicht ist die `contract`- und `integration`-Stufe, nicht `live`.**

* Der Shadow-Pfad existiert, läuft parallel zur bestehenden deterministischen Triage und
  **schreibt** Disagreement, Latenz, Usage und Outcome — in jeden Record.
* `mutations=0` und `route=None` sind **nicht kommentiert, sondern geprüft**: das Harness
  vergleicht einen State-Probe vor/nach dem Lauf und wirft `SystemOneContractError`, wenn sich
  etwas geändert hat; zusätzlich validiert `assert_shadow_advisory()` jeden Record selbst.
* **Kein Live-Jev**: es gibt keinen autorisierten TypeSafe-Zugang (JEV-07 bleibt
  `blocked_external_access`). Jeder Record trägt `live_jev=false`; als Provider dient ein
  Fixture (Mock-Klasse) bzw. der bestehende LLM-Adapter.
* **Kein Aufrufer im Supervisor**: `triage_with_shadow` wird heute ausschließlich von Tests und
  von der JEV-12-Auswertung gerufen. Der laufende Voice-Pfad des Supervisors liegt in der Engine
  (`~/.hermes/hermes-agent`) und wird von diesem Auftrag ausdrücklich **nicht** angefasst. Der
  Shadow läuft also auf echten Komponenten, aber nicht in einem echten Nutzerlauf.

## Was implementiert ist (mit Fundstellen)

| Baustein | Ort | Vertrag |
|---|---|---|
| Route-Leiter `observe…request_user_input` + §48-Eskalation | `src/godsv/system_one_policy.py:466` | §32/§48 |
| `propose_route(evaluation)` — benannte Regeln, erste Übereinstimmung gewinnt | `src/godsv/system_one_policy.py:543` | §47/§90 |
| `assert_shadow_advisory(record)` — Mutations-/Routen-/Aktionswächter | `src/godsv/system_one_policy.py:693` | §45 |
| `ShadowHarness` (erweitert, **derselbe** Klasse wie vorher) | `src/godsv/system_one_policy.py:721` | §45/JEV-10 |
| `ShadowHarness.run(...)` — Disagreement/Latenz/Usage/Stale/Inert im Record | ebenda | §88/§21 |
| `ShadowHarness.observe_outcome(...)` — Outcome des echten Laufs nachtragen | `src/godsv/system_one_policy.py:843` | §88 |
| `ShadowHarness.summary()` — gemessene Zählungen/Summen | `src/godsv/system_one_policy.py:873` | §88 |
| `triage_with_shadow(...)` — deterministische Triage + Shadow daneben | `src/godsv/voice.py:587` | §45/§98 |

Der bestehende Record-Vertrag des Vorgängerstands bleibt gültig (`mode`, `mutations`, `route`,
`disagreement`, `latency_ms`, `usage`) und wird nur erweitert — `tests/test_system_one_policy.py`
(committet) läuft unverändert grün.

### Was der Record pro Lauf enthält (echter Auszug, §49-Felder)

```text
$ .venv/bin/python - <<'PY'
from godsv.system_one_shadow import PRODUCTION_CASES, evaluate_case
case = [c for c in PRODUCTION_CASES if c.case_id == "recovery_side_effect_unsafe_replay"][0]
rec = evaluate_case(case).record
print({k: rec[k] for k in ("mode","mutations","route","selected_route","policy_rule",
      "baseline_route","proposed_route","proposed_rule","disagreement","stale","inert",
      "context_shadow","outcome","latency_ms","cost")})
print({k: rec["evaluation"][k] for k in ("question_pack_id","question_pack_version",
      "state_fingerprint","provider","provider_model","provider_access_mode","live_jev",
      "mode","latency_ms","usage","status")})
PY
```

```text
{'baseline_route': 'hint', 'context_shadow': True, 'cost': None, 'disagreement': True,
 'inert': True, 'latency_ms': 49, 'mode': 'shadow', 'mutations': 0,
 'outcome': 'intervention_needed', 'policy_rule': None, 'proposed_route': 'escalate_system_two',
 'proposed_rule': 'recovery_possible_side_effect_unsafe_replay', 'route': None,
 'selected_route': None, 'stale': False}
{'live_jev': False, 'mode': 'shadow', 'provider': 'triage_case_fixture',
 'provider_access_mode': 'fixture', 'provider_model': 'triage_case_fixture_v1',
 'question_pack_id': 'voice_triage', 'question_pack_version': 'v1',
 'state_fingerprint': 'f5c8e02048db4d47e62f320c149085e91696604ccc8d9220725963ce6fd43149',
 'status': 'ok'}
```

Feld-Semantik, damit nichts doppeldeutig ist: `route`/`selected_route`/`policy_rule` sind im
Record `None` — in Shadow wurde **keine** Route gewählt und **keine** Regel hat die Antwort
konsumiert. Das Feld `evaluation.route` trägt (wie im Vorgängerstand) den *Vorschlag*, markiert
durch `mode="shadow"`; die verbindliche Auskunft ist `selected_route=None`.

## Beweise (rot-first, Kommandos und beobachtete Ausgabe)

**1. Rot vor der Implementierung** (`contract`-Nachweis der Lesart):

```text
$ .venv/bin/python -m pytest tests/test_system_one_shadow.py -p no:randomly
tests/test_system_one_shadow.py:47: in <module>
    from godsv.system_one_policy import (
E   ImportError: cannot import name 'ROUTE_ESCALATE_SYSTEM_TWO' from
    'godsv.system_one_policy'                                     (1 error, 0.10s)
```

**2. Grün danach — 20 Testfunktionen / 23 Pins (parametrisiert), u. a. der Byte-Identitätstest
des Alt-Pfads:**

```text
$ .venv/bin/python -m pytest tests/test_system_one_shadow.py -p no:randomly
.......................                                          [100%]
23 passed in 0.07s
```

Belegt darin unter anderem:

* `mutations=0`, `route=None`, `selected_route=None`, `policy_rule=None` je Record;
* Mutation wird **erkannt**: ein Evaluator, der `run_state["tool_calls"]` verändert, führt zu
  `SystemOneContractError`, `harness.violations[-1]["state"] == "mutation_detected"`,
  `harness.records == []`;
* `assert_shadow_advisory` weist `route="pause_and_investigate"`, `mutations=1` und ein
  `action`-Feld zurück;
* ein spätes Shadow-Ergebnis (`live=VersionStamp(goal_revision=6)` gegen einen mit
  `goal_revision=4` gelaufenen Snapshot) wird als `stale=True`/`inert=True` gespeichert und die
  Entscheidung des Laufs bleibt die deterministische;
* ein Providerfehler (`SystemOneTimeout`) degradiert auf den Alt-Pfad
  (`reason="shadow_error:SystemOneTimeout"`), der deterministische Hint ist identisch;
* `flags=None | all-off | enabled+disabled-provider | voice_triage` ⇒ das Ergebnis ist
  **byte-identisch** zu `triage_with_system_one(... , evaluation=None)`, inklusive der
  Budget-Nebenwirkung des `Voice`-Objekts (`delivered == 1` in beiden Pfaden).

**3. Keine Regression in den bestehenden System-One-/Voice-Suiten:**

```text
$ .venv/bin/python -m pytest tests/test_system_one_contract.py tests/test_system_one_policy.py \
    tests/test_system_one_questions.py tests/test_system_one_sanitizer.py \
    tests/test_system_one_flags.py tests/test_system_one_adapters.py \
    tests/test_system_one_shadow.py tests/test_system_one_triage_pack.py \
    tests/test_voice_inject.py tests/test_pause_hard.py tests/test_no_intervention.py \
    tests/test_false_loop_regression.py tests/test_goal_drift_gate.py -p no:randomly
206 passed in 0.59s
```

**4. Statische Prüfung:**

```text
$ .venv/bin/python -m ruff check src/godsv/system_one_policy.py src/godsv/system_one_shadow.py \
    src/godsv/voice.py tests/test_system_one_shadow.py tests/test_system_one_triage_pack.py
All checks passed!
$ .venv/bin/bandit -q -r src -ll ; echo "bandit RC=$?"
bandit RC=0
```

## Evidence-Klassen (§5.1), pro Aussage

| Aussage | Klasse |
|---|---|
| Record verträgt `mutations=0`/`route=None`, Record-Schema, Mutationswächter, Stale-Bindung, Route-Regeln | `contract` (Schema/State-Machine-Nachweis) |
| Evaluator-Ersatz, Antwortübersetzung, Latenz/Usage | `mock` (isolierter Stub durch den echten Antwortvertrag) |
| Zusammenspiel Registry → Komposition → Sanitizer → Voice → Harness | `integration` (mehrere echte Komponenten, keine reale Außenwirkung) |
| Echter Jev/TypeSafe-Lauf, echter Supervisor-Lauf, echte Nutzerwirkung | **nicht erreicht (`live` fehlt)** |

## Ehrliche Grenzen — was hier **nicht** bewiesen ist

1. **Kein Live-Lauf.** Kein autorisierter Jev-Zugang; `live_jev=false` überall. Es gibt keinen
   einzigen echten Shadow-Lauf gegen einen realen Provider.
2. **Kein Aufrufer im laufenden Supervisor.** `triage_with_shadow` ist verdrahtet und getestet,
   aber kein Produktionspfad ruft es heute. Der Supervisor lebt in der Engine, die dieser Auftrag
   nicht ändern darf.
3. **Outcome ist Stellvertreter, kein Messwert.** `observe_outcome` speichert im Shadow-Test einen
   gesetzten Wert und in der JEV-12-Auswertung die dokumentierte Erwartung der Produktionsform —
   kein aus einem echten Lauf beobachtetes Ergebnis.
4. **Disagreement ist gegen den *einen* deterministischen Hint gemessen** (Voice-Baseline),
   nicht gegen echte Interventionsentscheidungen eines Laufs.
5. **Latenz/Usage** sind in Tests und Auswertung Fixture-Werte mit fester Uhr; sie belegen den
   Feldweg, nicht die reale Provider-Latenz (siehe JEV-19, offen).
6. **Keine Kalibrierung, kein Holdout.** Die Regel-Schwelle `READ_THRESHOLD=0.5` ist eine
   *Leseschwelle*, ausdrücklich keine kalibrierte Zahl (JEV-11 bleibt offen).
7. **§5.2:** Die eigene Testsuite ist kein alleiniger Nachweis. Das Harness wird vom Integrator
   gegenzuprüfen sein (unabhängige Probe außerhalb dieser Datei).
8. Der Arbeitsbaum enthielt während dieser Arbeit **parallele, uncommittete Änderungen an
   `src/godsv/completion.py` und `src/godsv/computer_use.py`** (JEV-13/JEV-15/16-Worker). Beide
   Dateien wurden von diesem Auftrag nicht angefasst; sie sind hier nur als Kontext erwähnt.

## Nicht angefasster, gemeldeter Defekt (außerhalb des Schreibrechts)

Die Frage → Dimensions-Zuordnung `TRIGGER_GROUPS` in `src/godsv/system_one_questions.py` fällt
für unbekannte Auslöser auf den konservativen `*`-Satz (`loop`, `completion`, `recovery`) zurück.
Für vier **echte** Auslöser-Arten fehlt dadurch die Research-Dimension:

```text
$ .venv/bin/python -c "from godsv.system_one_shadow import trigger_coverage; \
    print([(g.trigger_kind, g.missing) for g in trigger_coverage()])"
[('hallucinated_api', ('research',)), ('pattern_dependency_churn', ('research',)),
 ('dependency_change', ('research',)), ('outage_misdiagnosis', ('research',))]
```

Folge: bei einer erfundenen API (`hallucinated_api`) fragt das Pack keine versionssensitive
Außenfakten-Frage, obwohl §34/§47 Research genau dafür fordert. Der Implementierer hat das
**nicht** still repariert (die Zuordnung gehört in `system_one_questions.py`, das außerhalb des
Schreibrechts liegt), sondern je Auslöser sichtbar gemacht — siehe
`docs/audit/SYSTEM_ONE_TRIAGE_PACK.md`, Abschnitt „Offener Befund".
Vorgeschlagene minimale Reparatur (nicht ausgeführt): in `TRIGGER_GROUPS` je Auslöser
`"hallucinated_api": ("research", "recovery")`,
`"dependency_change": ("research", "loop")`,
`"outage_misdiagnosis": ("research",)`,
`"pattern_dependency_churn": ("research",)` ergänzen.

## Reproduktion

```bash
cd ~/projects/gods-voice
.venv/bin/python -m pytest tests/test_system_one_shadow.py -p no:randomly
.venv/bin/python -m pytest tests/test_system_one_triage_pack.py -p no:randomly
.venv/bin/python -m ruff check src/godsv/system_one_policy.py src/godsv/system_one_shadow.py \
    src/godsv/voice.py tests/test_system_one_shadow.py tests/test_system_one_triage_pack.py
.venv/bin/bandit -q -r src -ll
```
