# JEV_CU_PACK.md — Computer-Use Pack, GUIState-Kontrakt und visuelle Subtools

Stand: 16.09.2026, Branch `godsv-supervisor-hardening`, uncommitted working tree.
Deckt `JEV_IMPLEMENTATION_GOAL.md` §10 (GUIState §10.2, Computer-Use-Pack §10.3),
§11 (visuelle Subtools), §12 (System-2-Eskalation), §19 (CU-Dataset), §30 (E2E-J3
inkl. Gegenfall), §33 Worker B. Ergänzt (nicht ersetzt)
`docs/audit/COMPUTER_USE_SUBTOOLS.md` und `docs/audit/SYSTEM_ONE_GUISTATE.md`.

## 0. Ehrliche Einordnung

Alles hier ist `contract` / `mock` / `integration` mit Test-Doubles. **Es gibt auf
diesem Host keinen echten Desktop** — reproduzierbar:

```bash
$ cd ~/projects/gods-voice
$ .venv/bin/python -m godsv cu check
dry-run: verfügbar
sandbox (bwrap): verfügbar
container (podman): nicht verfügbar (Image fehlt: docker.io/library/alpine:latest)
container GUI (godsv-cu-x11): nicht verfügbar (Image fehlt: localhost/godsv-cu-x11:latest)
```

Damit ist **kein** Subtool gegen eine reale App gefahren, kein Anker aus echten
Pixeln gelesen und kein GUI-Effekt real beobachtet. Die Anker/Szenarien in den
Tests sind erklärte Daten, keine Wahrnehmung. Was das für E2E-07/§15 und die
§56-Pflichtfälle bedeutet: sie bleiben `not_run` (siehe §7).

Es wurde **kein** zweiter Orchestrator gebaut: die Kette nutzt den bestehenden
`CUExecutor` + `DryRunRunner`/`ContainerRunner`, die bestehende `CUPolicy`, den
bestehenden GUIState-Vertrag, die bestehende Question-Registry und den bestehenden
internen `SystemOneEvaluation`-Vertrag.

## 1. GUIState gegen die volle §10.2-Feldliste (§10.2)

Die §10.2-Liste ist als `GUI_STATE_CONTRACT_FIELDS` (17 Namen, Reihenfolge des
Auftrags) hinterlegt; `GUIState.contract_view()` berichtet **jedes** Feld mit
`value`, `channel` und `provenance`. Additiv, ohne die §52-Namen umzubenennen:

| §10.2-Feld | Träger im State | provenance |
|---|---|---|
| `app_id` | `app_id` (**neu**, kanonisch) | perception |
| `app_name` | `application` (§52-Name bleibt) | perception |
| `app_version` | `app_version` (**neu**) | perception |
| `window_id` | `window_id` (**neu**) | perception |
| `window_title` | `window_title` | perception |
| `focused` | `focused` | perception |
| `document` | `document` | perception |
| `dialog_state` | `blocking_dialog` (§52-Name bleibt) | perception |
| `visible_semantic_controls` | `visible_anchors` (§52-Name bleibt) | perception |
| `current_goal_step` | `goal_step` (Caller-Kontext) | context |
| `candidate_actions` | `action_candidates` (Caller-Kontext) | context |
| `previous_action` | `previous_action` | perception |
| `expected_transition` | `expected_transition` (**neu**) | perception |
| `observed_transition` | `observed_changes` (§52-Name bleibt) | perception |
| `user_takeover_state` | `user_takeover_state` (**neu**) | perception |
| `capture_timestamp` | `capture_timestamp` (**neu**) | perception |
| `observation_fingerprint` | `fingerprint` (abgeleitet) | derivation |

`guistate/v1`-Semantik bleibt unangetastet: ein nicht wahrgenommenes Feld trägt
**keinen** Wert (`value: null`, Name in `unperceived`), ein wahrgenommener Wert
nennt seinen Kanal, Kandidaten können nur schrumpfen, `certifies_effect` ist immer
`False`, `ground_truth` bleibt `app_effect`. „context"/„derivation"-Werte werden
ausdrücklich **nicht** als Wahrnehmung ausgegeben — sonst sähe ein Caller-Wert wie
ein Kanalbefund aus.

```bash
$ .venv/bin/python -m pytest tests/test_system_one_guistate.py -p no:randomly
25 passed in 0.04s
$ .venv/bin/python -c "from godsv.system_one_guistate import GUI_STATE_CONTRACT_FIELDS as C, GUI_STATE_FIELDS as F; print(len(C), len(F))"
17 14
```

## 2. Computer-Use-Pack (§10.3)

`godsv.system_one_subtools.computer_use_pack()` → `computer_use_v2`, 18 Fragen:

* **Pre-action (6)**: `correct_application`, `correct_window`, `correct_document`,
  `blocking_dialog_present`, `state_sufficient_for_action`, `stored_subtool_anchor_valid`
* **Post-action (5)**: `previous_action_succeeded`, `expected_transition_occurred`,
  `unexpected_transition_occurred`, `wrong_target_changed`, `reobserve_required`
* **Recovery (5)**: `retry_safe`, `alternate_candidate_needed`, `state_recoverable`,
  `system2_needed`, `user_takeover_detected`
* **Candidate-Choice (1)**: Optionen exakt `candidate_a, candidate_b,
  keyboard_shortcut, reobserve, escalate, abort`
* **Legacy (1)**: `computer_use.recovery_needed` (v1-ID, unverändert) — die
  Voice-Route-Regel in `system_one_policy` liest genau diese ID; sie darf durch
  das Ersetzen des v1-Packs nicht stillschweigend verschwinden.

Die **Choice-Regel ist Code, keine Konvention**:
`allowed_candidate_options()` schneidet die dokumentierten Optionen mit der
erlaubten Menge (und fügt nie etwas hinzu), `compose_computer_use_questions()`
verlangt eine explizite erlaubte Menge und verweigert eine leere,
`validate_candidate_choice()` weist jede Antwort außerhalb der erlaubten Optionen
zurück — inklusive Koordinaten und Toolnamen. Ein registrierungsfertiger
Einstieg existiert als `register_computer_use_pack(registry)`; die prozessuale
Verdrahtung in die produktive Registry bleibt beim Integrator
(`system_one_questions.py` gehört Worker A), die Tests fahren ausschließlich
mein Modul und eine frische `QuestionRegistry`.

```bash
$ .venv/bin/python -c "
from godsv.system_one_subtools import computer_use_pack
p = computer_use_pack(); print(p.ref, len(p.questions))"
computer_use_v2 18
```

## 3. Visueller Subtool-Vertrag (§11)

`VisualSubtool` trägt **16** Felder: die 15 im Auftrag §11 genannten
(`capability_id`, `version`, `supported_app_versions`, `supported_locales`,
`start_conditions`, `anchors`, `allowed_actions`, `expected_transitions`,
`completion_conditions`, `failure_conditions`, `reobserve_policy`,
`system2_escalation_policy`, `verification_contract`, `system_one_prechecks`,
`system_one_postchecks`) plus `system_one_recovery_questions` (die dritte
optionale Angabe aus ABNAHME §55 — der Auftrag schreibt „16-Feld-Vertrag", die
Auftragsliste druckt 15 Namen; §55 liefert das 16.). Validierung im Konstruktor:
leere Capability/Version/App-Versionen/Locales/Anker/Aktionen/Transitions oder ein
fremder Reobserve-/Eskalationstrigger sind Vertragsfehler.

Drei Regeln greifen hart:

1. **Applicability**: `check_applicability(subtool, app_version=…, locale=…)`
   verweigert eine nicht unterstützte App-Version oder Locale; eine **unbekannte**
   Version ist nie „wohl passend", eine Locale wird nie angenommen.
2. **Blind-Replay-Verbot**: `check_blind_replay(...)` meldet, welche
   Startbedingungen/Anker **aktuell nicht** beobachtet sind; `assert_no_blind_replay`
   wirft `BlindReplayRefused` statt zu klicken (§56: „Blindes Weiterklicken nach
   verlorenem Anchor ist FAIL").
3. **Sechs Jev-Checks** in `evaluate_subtool_step`: `start_condition_valid`,
   `anchor_reached`, `expected_transition_happened`, `procedure_still_applicable`,
   `new_observation_needed`, `system2_takeover` → genau eine Entscheidung
   (proceed / reobserve / escalate / complete). Die Entscheidung trägt nur eine
   Aktion, die das Subtool bereits als erlaubt deklariert hat — keine Koordinate,
   kein Shell-Kommando.

`SubtoolRegistry` (ein Registry, keine zweite Ablage): Duplikat wird abgewiesen,
eine unbekannte Version ist ein Fehler statt eines Nachbar-Fallbacks, `revoke`
macht eine Version unbenutzbar (§56 „Subtoolversion widerrufen").

```bash
$ .venv/bin/python -m pytest tests/test_jev_subtools.py -p no:randomly
21 passed
```

## 4. System-2-Eskalation (§12)

13 Auslöser als prüfbare Liste (`ESCALATION_TRIGGERS`, plus die Auftragsformulierung
je Zeile in `ESCALATION_TRIGGER_REASONS`) und eine Entscheidungsfunktion
`system_two_escalation_decision(...)`: unzureichender State, niedrige
Choice/Score-Confidence, Noul im kalibrierten Unsicherheitsband, widersprüchliche
Fragen, neue unbekannte UI, fehlende Candidate Action, komplexe Ursache,
Research-Bedarf, destructive/high-risk, Goal-Interpretation, Policy-Unklarheit,
User-Präferenz, App-Version außerhalb Subtool-Applicability.

Zwei Eigenschaften machen sie ehrlich statt eifrig:

* **Ein unbekanntes Signal ist kein Pass.** Jeder Check ist dreiwertig; ein nicht
  messbarer Check (kein eingefrorenes Profil, kein Kalibrierband, kein State)
  eskaliert und steht in `unmeasured`. Es gibt **keine globale Schwelle** und
  keine erfundene Confidence (§13).
* **Eskalation ist advisory.** `authorizes_action` ist immer `False`, der Datensatz
  enthält keine Route und keine Aktion; die bestehenden `allowed_kinds` bleiben
  unverändert.

```bash
$ .venv/bin/python -m pytest tests/test_computer_use_jev.py -p no:randomly
8 passed
```

## 5. E2E-J3 und der Gegenfall (§30)

`tests/test_computer_use_jev_e2e.py` fährt die Kette mit den echten Komponenten
und Doubles nur dort, wo eine Außenwirkung nötig wäre:

```text
Gegenwart: korrekter GUIState (accessibility_tree, Anker Render/Output Properties)
  → Frage-Set aus computer_use_v2 mit bereits erlaubten Kandidaten
  → Evaluierung über LLMSystemOneAdapter (Double-LLM; live_jev=False)
  → guistate_gate: allowed_kinds UNVERÄNDERT, nur vorab erlaubte Kandidaten
  → validate_candidate_choice → candidate_a (gemappt auf die erlaubte Aktion)
  → system_two_escalation_decision: kein Trigger
  → evaluate_subtool_step: proceed mit deklarierter Aktion
  → CUExecutor.run + DryRunRunner: 1 Aktion, verifiziert, cu_*-Events im EventStore
  → neue Beobachtung → GUIState revision 2 (previous = alt)
  → effect_check: effect_observed True, certifies False
```

Gegenfall (falsches Fenster / Anker weg): `check_blind_replay` verweigert den
gespeicherten Makroablauf, `evaluate_subtool_step` reobserve-t (oder eskaliert),
die Eskalationsentscheidung feuert, und **der Runner führt null Aktionen aus**
(`runner.actions == []`, `cu_action` fehlt im EventStore). Ein Versuch ohne
Erwartung wird zusätzlich vom bestehenden Executor abgewiesen (`PolicyViolation`).
Ein dritter Test fährt denselben Pfad ohne Provider-Zugang: die Fallback-Kette
landet auf der System-2-Stimme (`system2_voice_fallback`), ohne eine
System-One-Antwort zu erfinden.

Ohne echten Desktop ist das `integration` mit Test-Doubles, **kein** Live-GUI-Beweis.

```bash
$ .venv/bin/python -m pytest tests/test_computer_use_jev_e2e.py -p no:randomly
3 passed
```

## 6. Canary und Kalibrierung für Computer Use

* **Canary (§21)**: J0…J5 mit Flag-Zuordnung und Effekt je Stufe in
  `godsv.system_one_canary`; `J2` shadow ohne Produktwirkung, `J3` advisory ohne
  Aktionsautorität, `J4`/`J5` nur mit eingefrorenem Threshold-Profil (`J5`
  zusätzlich Holdout + unabhängiger Review). Ein Sprung über Stufen ist nur mit
  explizitem Grund **und** Nachweis erlaubt und meldet `skipped_stages`; Rollback
  ist immer erlaubt. Hard Policy bleibt auf jeder Stufe außen
  (`stage_invariants`). Der LLM-Readiness-Adapter kappt bei `J3`.
* **CU-Dataset (§19)**: `COMPUTER_USE_DATASET_FIELDS` + die 19 §19-Szenarien
  (correct/wrong app, correct/wrong window, modal, action success/no-op/wrong
  target, layout/app-version change, focus stolen/user takeover, anchor
  valid/invalid, retry safe/unsafe, reobserve/System-2 needed). Aus Rohdaten
  entstehen precision/recall/false-intervention/miss rate, escalation rate,
  confidence bins, latency, tokens, cost, System-2-Calls gespart,
  task-success impact sowie CU-Zähler (`reobserves`, `screenshots`,
  `wrong_gui_actions` inkl. Blindklick).

```bash
$ .venv/bin/python -m pytest tests/test_jev_canary_stages.py tests/test_jev_provider_calibration.py -p no:randomly
30 passed
```

## 7. Nicht gefahren (je Punkt die kleinste nächste Aktion)

| Was | Status | Warum | Kleinste nächste Aktion |
|---|---|---|---|
| `JEV-17` visuelles Subtool gegen eine **echte** App | `not_run` | Container-GUI-Image `localhost/godsv-cu-x11` fehlt (siehe `cu check`) | Image bauen (`docker/`-Rezept) und den Render-Ablauf einmal im Xvfb-Container fahren; Anker aus echter Wahrnehmung ersetzen |
| §56-Pflichtfälle (14) real | `not_run` | kein Subtool gegen echte UI | nach dem Image die 14 Fälle als Pin-Tests fahren |
| Reale GUIState-Beobachtung | `not_run` | headless Host | Desktop-Host; `live_display=True` erst dann wahr |
| CU-Holdout-Zahlen (§20) | `not_run` | keine eigenen Entwicklungsdaten | eigene CU-Runs (JSONL) sammeln, `python -m godsv.system_one_calibration --kind computer_use --dataset …` |
| Stufen `J4`/`J5` als **reale** Aktivierung | `blocked` | eingefrorenes Threshold-Profil + Holdout fehlen | erst Kalibrierung/Holdout, dann `stage_transition` mit Nachweis |
| E2E-J3 auf echtem Desktop | `not_run` | wie oben | Container-GUI-Pfad + `ContainerRunner` im CI mit gebautem Image |
