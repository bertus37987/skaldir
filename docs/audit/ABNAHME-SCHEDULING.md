# Abnahme VAL-TOOL-004: Scheduling & Resume

- **Feature:** `scheduling-resume` (VAL-TOOL-004)
- **Status:** PASS
- **Datum:** 2026-10-14
- **Host / Environment:** Fedora Linux, headless CLI

## 1. Übersicht

Nachweis, dass der Skaldir-Scheduler:
1. Zeitzonen (insb. `Europe/Berlin` inkl. DST-Transitions) korrekt auflöst und verrechnet.
2. Zeitpläne im Status `inactive` anlegt und erst nach expliziter Aktivierung feuert.
3. Vorgefallene Termine headless via `skaldir schedule run` abarbeitet.
4. Wiederholtes Feuern desselben Termins über das persistente Execution-Ledger dedupliziert (`exec-...`).
5. Das Ledger auch nach Löschung des Schedules zur Nachvollziehbarkeit beibehält.

## 2. Durchgeführte CLI-Befehlskette

1. **Definition:**
   ```bash
   skaldir schedule define --id sched-proof --workflow run_model_test --version 1 --timezone Europe/Berlin --at 12:00 --catch-up once --json
   ```
   Ergebnis: Status `active: false`, Hinweis `SCHED-04: defined but NOT active`.

2. **Aktivierung:**
   ```bash
   skaldir schedule activate --id sched-proof --json
   ```
   Ergebnis: `active: true`.

3. **Fälligkeitsprüfung:**
   ```bash
   skaldir schedule due --json
   ```
   Ergebnis: Fälliger Termin für `2026-10-14T12:00:00+02:00` (UTC: `2026-10-14T10:00:00Z`).

4. **Ausführung (Tick 1):**
   ```bash
   skaldir schedule run --ticks 1 --json
   ```
   Ergebnis: Termin übernommen, Execution-ID `exec-9d6e1e7f8c60` im persistenten Ledger angelegt.

5. **Deduplizierungsnachweis (Tick 2):**
   ```bash
   skaldir schedule run --ticks 1 --json
   ```
   Ergebnis: `firings: []`, keine erneute Ausführung, da bereits im Ledger registriert.

6. **Cleanup:**
   ```bash
   skaldir schedule delete --id sched-proof --json
   ```
   Ergebnis: `deleted: true`, `the execution ledger is kept: what already ran stays recorded`.

## 3. Testsuite-Evidenz

- `tests/test_scheduler.py` (32 Tests, u.a. `test_the_schedule_stores_its_timezone_version_parameters_and_policy`, DST-Transitions, Overlap-Guards) -> **32/32 PASS**
- `tests/test_scheduler_worker.py` -> **PASS**
