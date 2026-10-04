# Harness-Ausführungsprozesse — F05-Runner-Erkennung, echte PTY/Cancel und dispatcher-level Nachweis

**Stand:** 2026-10-04 · Commit `4b0bdb2` (fix(harness): run_tests erkennt Projektrunner mit echtem Exit; echte PTY bei exec + gereapte Cancel) und
`b1b1a12` (test(evidence): Strictness folgt den ehrlichen Runner-Zuständen).
Rohlogs unter `logs/` (git-ignored); dieses Dokument ist die bereinigte Zusammenfassung.

## Was die Arbeit 2026-10-03 (Worker 7bace999) gemessen und behoben hat

### F05 — `run_tests` war pytest-verdrahtet mit leerem Schema und dauerhaft `None`-Exit
- **Schaden (gemessen):** der Goal-Run bot `run_tests` ohne Parameter als immer-`pytest` an
  (auch für ein Node-Projekt), das Ergebnis-`exit` blieb dauerhaft `None`.
- **Zustand nach dem Fix:**
  - `completion.py:3469 _detect_test_runner` liest den **eigenen** Runner des Projekts aus
    dessen Dateien, dokumentierte Priorität: `package.json` `scripts.test` → npm;
    pytest-Marker (tests/- oder test/-Verzeichnis, `conftest.py`, `pytest.ini`, `tox.ini`,
    `pyproject.toml` mit `pytest`, auch verstreute root-level `test_*.py`) → `<venv-python> -m pytest -q`;
    `Makefile` mit echtem `test:`-Ziel → `make test`; `scripts/run_tests.sh` → `/bin/sh`.
    Nichts davon → `None` = ehrlicher Zustand, kein erfundenes Ergebnis.
  - `completion.py:3562 _suite_runner_evidence` hält **vier** Enden auseinander:
    `passed` (Exit 0), `failed` (Runner lief und scheiterte / Timeout / konnte nicht starten),
    `no_tests_found` (pytest-Exit 5 oder Runner sagt es selbst) und `no_test_runner`.
    `ok` **nur** bei `passed`. RunEvidence trägt `exit_code` + `runner` als maschinenlesbare Fakten.
  - `goal_run.py:370 run_tests(argv=None)`: ohne `argv` der erkannte Runner, mit `argv`
    ein expliziter Kommando-Vektor; das Modell-Schema deklariert `argv` (Array of strings).
  - `verify_service.py:927 _suite_result` liest `evidence.exit_code` als Faktenquelle;
    Detail-Parsing ist nur noch Fallback für ältere Belege.
- **Tests:** `tests/test_project_test_runner.py` (13 Fälle): pytest grün; ein pytest-Projekt
  mit Null-Tests ist `no_tests_found` (nicht „failed"); ein Node-Projekt führt `npm test` statt
  pytest aus; npm meldet „no tests" als diesen Zustand; ein Projekt ohne Runner ist
  `no_test_runner` (keines davon je ein `ok`); ein deklariertes `argv` läuft genau statt des
  erkannten Runners; das Goal-Run-Tool löst dieselben Fälle über echtes `execute_check`
  (pytest, npm, no_tests, Ablehnung ohne Runner) und sein Schema deklariert den Vektor;
  `verify_service` liest den echten Exit aus dem Beleg. `tests/test_evidence_strictness.py`
  folgt den ehrlichen Zuständen (siehe Gate-Abschnitt).

### processes.py — PTY war ein Parent-seitiges No-op; Cancel ließ Zombies zurück
- **Schaden (gemessen):** das alte `_attach_pty` rief `os.dup2` **im Elternprozess nach `Popen`**
  — das änderte nur die eigene fd-Tabelle, das Kind las von der Pipe und `sys.stdin.isatty()`
  antwortete `False`. Nach `cancel` blieb die beendete `sh`-Prozessgruppe als unreaped
  `<defunct>` in `ps` stehen.
- **Zustand nach dem Fix:** Master/Slave-Pty wird **vor** `Popen` geöffnet, der Slave ist
  fd 0 des Kindes ab `exec`; der Master wird als `proc.stdin` dup'd (schreiben geht unverändert),
  getestet mit echtem stdin↔stdout-Roundtrip über das PTY (`TTY: True`, `GOT:`-Nachrichten in
  tests/test_processes.py). `cancel` wait()t auf das Kind (Grace 3 s) und schließt die
  stdin-Seite, damit ein blockierter Leser EOF sieht; `reaped` wird berichtet statt still
  verschwiegen. Gestartete Kinder laufen in eigener Prozessgruppe (`start_new_session=True`).
- **Tests:** `tests/test_processes.py` — ps-verifizierter No-Orphan für cancel **und** Timeout
  (Gruppe per pgid gescannt, nie per Name), PTY-Roundtrip, PTY-honest-pipe (`TTY: False`).

## Nachtrag 2026-10-04 — Stage-Gate und der fehlende dispatcher-level Nachweis

### Stage-Gate (einmal je Etappengrenze, gemessen mit echtem Exitcode)
| Prüfung | Ergebnis |
|---|---|
| `uv run python -m pytest tests/ -q` (erster Lauf) | **exit 1** — 1 Fehlschlag: `test_evidence_strictness ... test_runner_does_not_certify_an_empty_repo` |
| Ursache | Der Strictness-Test pinnte das **alte** Symptom der F05-Verdrahtung („failed") für ein Repo ganz ohne Runner. Die ehrliche Vereinbarung bleibt ungeändert: ein Repo ist in beiden Nicht-Grün-Zuständen nie grün (`ok=False`), nur der Zustandsname trägt jetzt die wahre Ursache |
| Fix (Wurzel, kein Schwächen) | Test aktualisiert: ohne Runner → `no_test_runner` (`ok=False`, nie `passed`); neuer zweiter Fall: `tests/`-Shell mit Null-Tests → `no_tests_found`/`failed`, Exitcode ≠ 0 (`b1b1a12`) |
| `uv run python -m pytest tests/ -q` (Gate-Neulauf) | **exit 0 — 8293 passed, 33 skipped** (Rohlog `logs/logs/gate-harness-full.log`) |
| `uv run ruff check src tests` | **exit 0** — "All checks passed" |
| `uv run python -m compileall -q src` | **exit 0** |

Ehrliche Randnotiz: ein früherer Aufzeichnungslauf wurde unterbrochen, bevor er die Suite
beendete (Log endet bei 21 %, Exitcode nie bewertet); der belastbare Gate-Nachweis ist der
obige saubere Neulauf mit `exit 0`. Zeitaufwand: 31:45 Minuten voller Suite-Lauf.

### Dispatcher-level produktiver Nachweis (erstmals ausgeführt — 2026-10-04)
Der überwachte Prozesspfad lief bisher nur als Unit-Test mit echten Subprozessen und ohne
aufgezeichneten dispatcher-level Lauf. Nachgeholt über die **echte** `turn_tools`-Kette
(`skaldir.goal_run.turn_tools` → `core_capability_tools` → `process`-Tool; kein Mock):

| Schritt | Beobachtung (echte Werte) |
|---|---|
| Toolset | 15 Tools inkl. `process`, `terminal`, `run_code`, `run_tests`, `edit_apply`, `web_*` |
| `process` start (Dev-Server, Port 3412 — aus 3400-3499, frei gemessen) | `ok=true`, Prozess-Id `proc-…`, `state=running`, **Startdauer 0,0017–0,0018 s** → die Agentenrunde wird nicht blockiert |
| Poll auf Bereitschaft | `READY` nach 0,05 s im inkrementellen stdout |
| HTTP-Bereitschaft über `terminal` desselben Toolsets | `exit_code=0`, Body `skaldir-proof-dev-server` |
| Späteres stdin (`do=stdin`) | write ok; das Kind antwortet `STDIN-GOT:ping-proof` (Rohr-stdin, kein PTY nötig) |
| `do=cancel` | `state=killed`, `group_signalled=true`, **`reaped=true`** |
| ps-Scan nach dem Cancel | **leer** im Scope `pgid == <Spawn-PID>` (nie Namenssuche) |
| Rohbeleg | `logs/logs/harness-dispatcher-process-proof.json` (Ereignisse mit Zeitstempel); Skript Exitcode 0 |

Frühere produktive CLI-Belege desselben Bereichs (Worker 7bace999, Rohlogs vorhanden):
`run_code` JSON-Umschlag mit `value=42` (`logs/logs/harness-run-code.json`) und Terminal
`exit_code=7` mit `ok=true` und getrenntem stdout/stderr (`logs/logs/harness-events-run-exit7.json`).

### Definitionsstand der betroffenen Verifikations-Assertionen
- **VAL-HARNESS-003** (Raw result): stdout/stderr/Exitcode/Timeout getrennt; Transport verdeckt
  den Exit nicht — Unit-Level (`tests/test_tools_core_tools.py` Exit-7/stderr-Trennung) +
  dispatcher-level Terminal-Beleg oben. Chat-Live-Lauf zur vollen Abnahme gehört dem
  Live-Acceptance-Lauf des Meilensteins.
- **VAL-HARNESS-004** (supervised): start→poll→incremental→stdin→cancel→no-orphan jetzt
  **dispatcher-level** belegt (Tabelle oben); a dev server does not block — Startdauer 2 ms.
  Unit-Level zusätzlich PTY-Option und Timeout-Fall mit echten Subprozessen.
- **CORE-07/CORE-08 Zeilennotiz** in `DROID-FEATURE-MATRIX.md` auf den Stand des Codes
  gehoben (Runner-Erkennung, ehrliche Zustände, Tests, Restlücken-Spalte).

### Offene Punkte (bewusst nicht Teil dieses Features)
- TUI-/Chat-Live-Läufe (tuistory) für VAL-HARNESS-003/004 = Live-Acceptance-Worker.
- `run_code` wurde hier nicht erneut dispatcher-level aufgerufen (bereits CLI-belegt, Rohlog oben);
  nur die Zusammensetzung des Toolsets wurde erneut geprüft.
