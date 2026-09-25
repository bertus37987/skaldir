# BENCHMARK-RAW-README.md — Rohdatenlage und Auswerteskript (§114, §97/§98, §103–§108)

Stand: 16.09.2026 · HEAD `b7a287a` · Branch `godsv-supervisor-hardening`.

Diese Datei ist der **Rohdaten-README** des Abschlussordners (§114: „Benchmark-Rohdaten
JSON/CSV/JSONL" + „Auswerteskript"). Sie beschreibt, welche Rohdaten wirklich existieren,
welches Skript sie auswertet — und was ausdrücklich **nicht** gemessen ist. Sie enthält
**keine Ergebniszahlen**; Zahlen stehen nur dort, wo sie über `docs/audit/BENCHMARK_MANIFEST.json`
mit einem realen Rohdatensatz verknüpft sind.

## 1. Wo die Rohdaten liegen

| Pfad | Inhalt | Status |
|---|---|---|
| `outputs/rf/<stamp>/raw.jsonl` | RF-Testprovider-Runde (16.09. 13:16, Engine `fb56a7e0`): eine Zeile je Modus mit Client- **und** Server-Seite (Phasen-Timestamps, `wire_tokens_est`, Transportausgang, Fehlerklasse) | **existiert** (`outputs/rf/2026-09-16_131617/`) |
| `outputs/rf/<stamp>/summary.json` | aggregierte Sicht derselben Runde + die aus der Engine abgeleiteten Stale-Schwellen je Kontextgröße (16k/64k/96k/152k) | **existiert** |
| `outputs/benchmark/*.raw.json` | Bestandsläufe aus früheren Runden (`full_eval.raw.json`, `gv_active_semantic_only.raw.json`) | **existiert**, aber vor dem ABNAHME-Vertrag entstanden — nicht als §97-Armbeleg verwendbar |
| `docs/audit/benchmark-runs/*.jsonl` | **Zielpfad** für §97-Arme A/B/C/D (Voice-Triage) und A/B (Computer Use) | **existiert nicht** — kein Lauf ist freigegeben (siehe §4) |
| `outputs/e2e40/proof-results*.json` | Stufenbeweise 1–9 der §40-Kette | **existiert** (kein Benchmark-Rohdatensatz) |

## 2. Das Auswerteskript

```bash
# Auswertung vorhandener Rohdaten (JSONL oder JSON-Array, Globs erlaubt)
.venv/bin/python tools/eval_benchmark.py docs/audit/benchmark-runs/*.jsonl
.venv/bin/python tools/eval_benchmark.py --json runs.jsonl
```

Berechnet je Konfiguration/`arm` die §97-Metriken:

`task_success` · `false_success` · `unnecessary_intervention` · `missed_error` ·
`system_2_calls` · `latency_ttft_s` · `latency_total_s` · `tokens_total` · `cost_usd` ·
`recovery_ok` (+ `tool_calls`, `screenshots`, `reobserves`, `double_side_effect`).

Zwei harte Regeln (identisch zu `tools/gv_benchmark.py`, das den Schema-Teil abdeckt):

1. **Keine Rohdaten ⇒ keine Zahlen.** Ohne lesbare Datei bzw. mit 0 geparsten Zeilen
   schreibt das Skript `no runs` nach stderr und beendet mit Exit ≠ 0. Es druckt niemals
   eine Null, einen Strich oder eine leere Tabelle, als wäre gemessen worden.
2. **Metrik ohne Nenner ist `null` mit Begründung**, niemals `0` und niemals interpoliert
   (`task_success` braucht einen Lauf mit objektivem Ausgang, Precision/Recall brauchen
   Ground-Truth-Labels, `false_success` braucht Läufe, die Abschluss behauptet haben).

Nachweis dieser beiden Regeln (in dieser Session ausgeführt):

```text
$ .venv/bin/python tools/eval_benchmark.py
no runs — no raw benchmark JSONL was given. …                       → Exit 2
$ .venv/bin/python tools/eval_benchmark.py /tmp/empty_runs.jsonl
no runs — the given files parsed to zero run records. …             → Exit 2
$ .venv/bin/python tools/eval_benchmark.py docs/audit/benchmark-runs/*.jsonl
no runs — raw run file not found: docs/audit/benchmark-runs/*.jsonl → Exit 2
$ .venv/bin/python tools/eval_benchmark.py --selftest
!! SYNTHETIC FIXTURE — this runs the metric MATH only …             (Banner + Exit 0)
```

Der `--selftest` existiert ausschließlich, um die **Rechenwege** prüfbar zu machen (Raten,
Summen, „Nenner fehlt"-Pfade) und ist mit einem Banner als synthetisch gekennzeichnet —
seine Ausgabe darf nie als Messung zitiert werden.

## 3. Zeilenformat einer Roh-Zeile (eine JSON-Zeile je Lauf)

Das Format ist die Schnittmenge aus `docs/BENCHMARK-RUN-SCHEMA.json` (`run_record`) und den
Metrik-Eingaben, die §97 nennt:

```json
{
  "run_id": "…", "session_id": "…", "configuration": "A_system2_triage",
  "task_id": "GS-02", "repeat": 1,
  "started_at": "…", "finished_at": "…", "final_state": "VERIFIED_COMPLETE",
  "ground_truth": { "task_expected_success": true, "intervention_expected": true },
  "observed": {
    "task_succeeded": true, "completion_claimed": true, "evidence_satisfied": true,
    "interventions": 1, "missed_errors": 0, "system_2_calls": 2,
    "tool_calls": 12, "screenshots": 3, "reobserves": 1,
    "recovery_ok": true, "double_side_effect": false
  },
  "latency": { "ttft_s": 1.2, "total_s": 30.0 },
  "usage": { "input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.02 },
  "raw_ref": "outputs/…/session.jsonl"
}
```

`ground_truth.task_expected_success`/`intervention_expected` kommen aus dem eingefrorenen
Aufgabensatz (§38-Golden-Set / §104) — ohne Label bleibt die Metrik `null`, nicht `0`
(§105: Precision/Recall nur mit Label).

## 4. Was ausdrücklich NICHT gemessen ist

| Fehlt | Grund | Kleinste nächste Aktion |
|---|---|---|
| Stufe B — 12 Aufgaben × 3 Wiederholungen | braucht Modell-/Budgetfreigabe | Freigabe einholen, dann Läufe nach §3 schreiben |
| Stufe C — Voice-Holdout (24+24) | eingefrorener Split fehlt | Split einfrieren, Kalibrierung vom Holdout trennen |
| Stufe D — Jev-Holdout | kein autorisierter TypeSafe-/Jev-Zugang (Early Access) | LLM-Adapter-Arm (B) fahren; echter Jev-Arm (C/D) erst mit Zugang |
| Stufe E — Discovery (30 Formulierungen) | Testset v1.0.0 eingefroren, Runner existiert, Live-Lauf offen | `tools/gv_discovery_eval.py` gegen das eingefrorene Testset fahren |
| Stufe F — Performance/Langlauf (≥3 Wiederholungen, >80-Iterationen-Langlauf) | keine Budgetfreigabe für den 152k-/80-Iterationslauf | `docs/RF-LONGRUN-FREIGABE.md` |

## 5. Verhältnis zu `docs/audit/BENCHMARK_MANIFEST.json`

Das Manifest ist die **Vorregistrierung** (7 Konfigurationen, Konstanten, `runs: []`,
`status: schema_frozen_pending_runs`). Es trägt bewusst keine Ergebniswerte. Sobald ein
Rohdatensatz existiert, wird er unter `docs/audit/benchmark-runs/` abgelegt, mit
`tools/eval_benchmark.py` ausgewertet und die Ausgabe zusammen mit dem Rohdatensatz-Hash in
`docs/audit/ARTIFACT-MANIFEST.json` verzeichnet. Bis dahin bleibt der Betriebszustand
`not_run` — nicht `0`.
