# CLI-Vertragsproben — Kalibrierung und Benchmark-Arme

Jeder Aufruf wurde **direkt** (ohne Shell, ohne Pipe) gestartet; festgehalten sind der echte
Exitstatus und stdout/stderr getrennt. Kein Exitcode wurde unterdrueckt oder umgedeutet.
Der Vertrag steht im Code und ist hier gegen das Verhalten geprueft:
`2` = keine Rohdaten (fehlend/leer), `3` = Datensatz verweigert oder Usage-Fehler,
`4` = kein erkannter §27-Arm, `5` = Freeze/Report verweigert (z. B. nur Vendor-Material), `0` = Erfolg.

| Probe | rc | erwartet | passt | letzte Ausgabezeile |
| --- | --- | --- | --- | --- |
| kalibrierung: schema-gueltige Probe-Datei (KEINE Kalibrierdaten) | 0 | 0 | ja | `14 of 19 metric rows are NOT RUN (no denominator) and are reported as such instead of as a zero.` |
| kalibrierung: kein --dataset | 2 | 2 | ja | `no raw data — no dataset was given. Nothing is measured and nothing is printed as zero. Pass the` |
| kalibrierung: fehlende Datei | 2 | 2 | ja | `no raw data — raw dataset file not found: /home/willy/nope.jsonl` |
| kalibrierung: unbekanntes Szenario | 3 | 3 | ja | `raw dataset refused: voice: unknown scenario 'not_a_scenario' (known: 12 §19 scenarios)` |
| kalibrierung: --freeze ohne Messwerte | 5 | 5 | ja | `refused to freeze a threshold profile: cannot freeze 'probe': ['precision', 'recall', 'miss_rate` |
| arme: --list | 0 | 0 | ja | `(a metric with no denominator is reported as NOT MEASURABLE, never as 0)` |
| arme: keine Rohdaten | 2 | 2 | ja | `no raw data — no run files were given. The arms cannot be compared, and nothing is printed as a ` |
| arme: nicht existierende Datei | 2 | 2 | ja | `no raw data — raw run file not found: /tmp/gv-cli-contract/does-not-exist.jsonl` |
| arme: unbekannter Armname in der Datei | 4 | 4 | ja | `no §27 arm recognised in the raw runs — name the arm (e.g. A_existing_system2_triage) in each re` |
| arme: nur Vendor-Material | 5 | 5 | ja | `refused: only vendor claims were supplied — a vendor benchmark number is never our own measureme` |
| arme: gueltiger Lauf mit Katalog-Armnamen | 0 | 0 | ja | `  recovery_ok: NOT MEASURABLE — no run records observed.recovery_ok` |

Ergebnis: **11/11** Proben entsprechen dem deklarierten Vertrag.

## Was hier NICHT belegt ist

Die schema-gueltige Probe-Datei ist **kein** Kalibrierdatensatz: sie ist ausserhalb des Repos erzeugt,
als Vertragsprobe deklariert (`--dataset-version cli-contract-probe`) und traegt keine Messung.
JEV-11 bleibt daher `blocked` (kein Entwicklungsdatensatz) und JEV-19 `blocked` (keine rohen Laeufe);
belegt ist hier ausschliesslich der CLI-Vertrag, nicht ein Messwert.

## Regel fuer alle kuenftigen Audit-Pipelines

Exitstatus wird **nie** durch eine Pipe gelesen. Entweder direkt ohne Pipe, oder mit der
vollstaendigen Kette:

```bash
set -o pipefail                      # bash: der Pipeline-Status ist der erste Fehler
cmd | tee out.log; echo "rc=$?"       # mit pipefail ist das der Status von cmd, nicht von tee

# explizit je Glied, wenn der Einzelstatus gebraucht wird:
cmd | tee out.log; echo "PIPESTATUS=${PIPESTATUS[0]} rc=${PIPESTATUS[0]}"
```

Belegter Anlass: zwei frueher als „rc=0" gelesene Laeufe, die in Wahrheit `rc=2` lieferten, weil `tail`
bzw. `grep` das letzte Pipelinenglied war. Dieselbe Verwechslung hat in diesem Audit schon einmal
einen falschen PASS erzeugt.
