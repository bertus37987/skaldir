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

## Nachtrag 2026-10-04 (2) — Batching/Streaming/Recovery: pro-Call-Streaming, Cancel-Gate, Exit-Code-States (VAL-HARNESS-005/006/007)

Commit `8e7ee33` (+ Nachfix im selben Feature): `agent_loop._execute` streamt Ergebnisse
**je Call in Fertigstellungsreihenfolge** (`as_completed`, neuer beobachtender
`on_result`-Hook) statt am Batch-Ende gesammelt; **Abbruch mitten im Batch** beantwortet
jeden nicht gestarteten Call mit `decision="cancelled"` (kein Neustart nach dem Stopp, kein
Re-Run bereits ausgeführter Seiteneffekte; Gate zusätzlich in `_run_one` vor jedem Handler);
`ToolOutcome.exit_code` trennt Permission (nie gelaufen → 3), Execution (gelaufen+fehlgeschlagen → 7)
und den **vom Tool gemeldeten Exit-Code** — der tiefste gemessene Wert gewinnt, der synthetische
Transport-`exit_code: 0` der Sandbox-Sandbox-Hülle benennt ein echtes `exit 7` nicht mehr in 0 um;
`write_text` meldet `state: created|updated` (TOOL-03).

### Neue Tests (`tests/test_harness_batching.py`, 13 Fälle, alle grün)
- **006-Parallel:** 3 unabhängige Calls (0,3/0,2/0,1 s) überlappen — Timestamps
  (`b0 < a1 ∧ c0 < a1 ∧ c0 < b1`), 3 verschiedene Worker-Threads, `parallel=true` im Verlauf.
- **006-Abhängigkeit:** Write→Read-Paar in EINEM Batch läuft geordnet — der Read sieht den
  geschriebenen Inhalt; Write `parallel=false`.
- **007-Streaming:** Ergebnis des schnellen Calls erreicht den Aufrufer (on_result), **bevor**
  der langsame Geschwister-Call endet; Reihenfolge = Fertigstellungsreihenfolge.
- **Teilausfall:** 1 von 3 Calls wirft — die beiden anderen Ergebnisse bleiben erhalten und
  werden dem Modell intakt zurückgemeldet; der Fehler ist mit Digest aufgezeichnet.
- **Abbruch im Batch:** erster Call (echter Seiteneffekt + `cancel_event`), die beiden
  nachfolgenden mutierenden Calls laufen **nie** (`decision=cancelled`, keine Dateien), der
  Seiteneffekt geschah genau einmal (`effects == ["one"]`), Lauf endet
  `incomplete/cancelled`, Exit 3.
- **Argumentvalidierung vor Wirkung:** falscher Typ UND fehlendes Pflichtargument →
  `invalid_arguments`, `executed=false`, **keine Datei** wurde je angelegt.
- **Exit-Code-States:** Transport-`ok` und Exit-Code getrennt (7 bleibt 7, 2 bleibt 2,
  Erfolg ohne Code = 0); Bridge-Hülle (nested `output.exit_code`) gewinnt über den
  Transport-0-Envelope (Defekt aus dem Proof-Lauf, als Unit-Test gepinnt).
- **Provider-Recovery (state-aware, begrenzt):** 429 → 1 begrenzter Retry in der geteilten
  §43-Policy, dann Antwort (Recovery bewiesen); 429 erschöpft → `failed/provider_error`,
  Status „429" benannt, genau 2 Requests (kein Endlosloop); 401 →
  `needs_credentials`/Exit 4 (eigenen Zustand, nie retryiert); Stream-Abbruch →
  `failed/provider_error` nach genau 1 Versuch.

### Dispatcher-level produktiver Nachweis (2026-10-04, dieses Feature)
Echte Kette: `tools_core.registry.register()` → `core_capability_tools(workspace, runtime)`
(die goal-run-Toolkette) → echter `AgentLoop`; Provider als lokaler Socket-Stub (einzige
gestellte Grenze). Belege mit echten Werten:

| Run | Beobachtung (echte Werte) |
|---|---|
| A — Write→Read-Paar, EIN Batch | `status=answered`; Write `created=true, state=created`; Read sieht Inhalt; Reihenfolge write→read im Verlauf (`seq`) |
| B — Abbruch im Batch mit echter Wirkung | Terminal-Subprozess legt `cancel.flag` wirklich an (sleep 2 && touch); der zweite Terminal-Call läuft **nie** (`decision=cancelled`), Ergebnis `incomplete/cancelled`, Exit 3 |
| C — Exit-Codes über echtes Terminal | `/bin/sh -c 'exit 7'` → Transport `ok=true`, Aufzeichnung `exit_code=7`; `exit 2` → 2; `printf` → 0 (alle drei im `tool.call`-Ereignis) |

- Rohbeleg: `logs/logs/harness-batching-dispatcher-proof.json`; Skript
  `logs/harness-batching-productive-proof.py`, Exitcode 0.
- Paralleles Overlap-Messen an dieser Kette: die Parallelität selbst ist mit Timestamps
  auf Unit-Level bewiesen (siehe oben); der dispatcher-level Lauf belegt die Verdrahtung
  (Toolset-Kette, Cancel-Pfad, Exit-Codes, Rückmeldung ans Modell), nicht noch einmal die
  Overlap-Mikrometrie.

### Stage-Gate (einmal je Etappengrenze, gemessen mit echtem Exitcode)
| Prüfung | Ergebnis |
|---|---|
| `uv run python -m pytest tests/ -q` | **exit 0** — Fortschritt 100 % nur Punkte, 0 FAILED-/ERROR-Marker (Rohlog `logs/logs/gate-batching-full.log`) |
| `uv run ruff check src tests` | **exit 0** — "All checks passed" |
| `uv run python -m compileall -q src` | **exit 0** |
| CLI-Laufbarkeit | `uv run skaldir --version` → 1.5.0; `uv run skaldir selftest` → 8 Layer, 26 Probes, 0 not reachable |
| D-10-Regression | `tests/test_tool_search_integration.py` → 8 passed, exit 0 |

### BLOCK-M3-01 — Schema-Typ-Mapping (2026-10-04, Feature fix-schema-type-mapping, Commit `edeacb9`)

**Defekt (reproduziert, 6 Tests rot vor dem Fix):** `core_capabilities._bound_tool` leitete
das Modell-Schema aus rohen `inspect.signature`-Annotationen ab; `tools_core/bridge.py`
hat `from __future__ import annotations` (PEP 563), Annotationen kamen als Strings an —
`ann in (int, "int")` griff nie, jeder Nicht-String landete auf `"string"`. Gemessen:
`terminal.timeout_s`/`max_output_bytes` und `web_fetch.timeout_s`/`max_bytes` als string
statt integer, `terminal.env`/`web_fetch.headers` als string statt object. Durch die echte
Loop wurde `terminal(timeout_s=30)` vom Schema-Gate abgelehnt
(`decision=invalid_arguments`, Meldung „argument 'timeout_s' has type int, but the schema
expects string") und dann trip-refused; die String-Form passierte das Gate und scheiterte
in der Registry. `tool_search` publizierte den korrekten Vertrag — die zwei
Discovery-Flächen widersprachen sich, die korrekt gehorchende Form war nicht aufrufbar.

**Fix:** `_bound_tool` löst die Annotationen mit `typing.get_type_hints(wrapper)` auf und
mappt über den neuen Helfer `_json_kind` (Union-Mitglieder gestript, `bool` vor `int`
geprüft, Generics via `get_origin`); nicht auflösbare Annotationen fallen ehrlich auf
`"string"` zurück. Kein Schema verschmälert, nichts abgeschwächt: die String-Form
(`timeout_s="30"`) wird jetzt korrekt vom Gate abgelehnt.

**Belege (echte Werte):**

| Prüfung | Ergebnis |
|---|---|
| Reproduktion vor dem Fix | `uv run pytest tests/test_core_schema_type_mapping.py -q` → **6 failed** (u. a. `terminal.timeout_s: schema says 'string', the wrapper's real annotation is 'integer'`; Loop: `invalid_arguments`) |
| Nach dem Fix | dieselbe Datei → **18 passed, exit 0** |
| Ziel-Regressionen | `test_tool_search_integration.py` + `test_harness_batching.py` + `test_docker_ensure.py` → **exit 0** (90 bestanden) |
| Produktiver Nachweis | echte Loop-Kette, Provider gestubbt (einzige gestellte Grenze): `terminal(command="printenv SKALDIR_PROBE_MARKER", timeout_s=30, env={...})` → `decision=ALLOW, executed=True, ok=True`, Kind-Exit 0, `stdout='typed-ok'` — beide vormals nicht aufrufbaren Formen (integer + object) in EINEM echten Lauf (Rohlog `logs/logs/blockm301-schema-type-proof.log`) |
| `uv run ruff check src tests` | **exit 0** (Vollmenge) |
| `uv run python -m compileall -q src` | **exit 0** |

### BLOCK-M3-02 — Browser-Tool: SessionManager-Run-Scoping (2026-10-04, Feature fix-browser-sessionmanager-scoping, Commit `83115f7`)

**Defekt (reproduziert, 5 Tests rot vor dem Fix):** `core_capabilities._browser_tool`
baute `SessionManager()` ohne das verpflichtende Positionsargument `state_dir`
(`browser_layer.py:321`, commit add4d67). Auf diesem Host meldet der echte Probe
`available=True`, der bewachte `browser_unavailable`-Pfad wurde also nie genommen:
DIREKTER Handler-Aufruf UND ein voller AgentLoop-Lauf hoben dasselbe rohe
`TypeError: SessionManager.__init__() missing 1 required positional argument:
'state_dir'` — und der Loop meldete den Crash als `decision='ALLOW', executed=True`
mit der rohen TypeError-Ausgabe: ein Crash als Erfolg verpackt. `state_dir` ist
zugleich das Zuhause des prozessübergreifenden Browser-Start-Locks (§6) — also eine
Run-Scoping-Lücke, nicht nur ein Signature-Versprecher. Zusätzlich aufgemessen:
die Handler-Aufrufe `layer.read(url=…)` / `layer.act(payload)` trafen auf KEINE reale
Signatur (`BrowserLayer.read` nimmt `tab_id`; die Signatur, die diese Aufrufform
wirklich hat, ist die Transport-Schicht `AgentBrowserBackend.read`/`act`).

**Fix (Muster `surface_cli.py:713`):** `SessionManager((rt or ws) / "browser")` — der
Session-State (inkl. Start-Lock) lebt unter dem Runtime-Verzeichnis DES Laufs; ohne
Runtime skopiert der Run-Workspace. Der Handler spricht jetzt die Transport-Signaturen
`backend.read(url=…, session=…)`/`backend.act(payload, session=…)` und meldet in jeder
Antwort `run_id` + `session_state_dir`. Nichts abgeschwächt: die Probe-Verweigerung
bleibt, wie auch die ehrliche Verben-Verweigerung im Transport.

**Belege (echte Werte):**

| Prüfung | Ergebnis |
|---|---|
| Reproduktion vor dem Fix | frische Wiederholung gegen das Vor-Fix-Modul: `uv run pytest tests/test_browser_tool_handler.py -q` → **5 failed**, alle am rohen `TypeError … missing 1 required positional argument: 'state_dir'` (Rohlog `logs/logs/blockm301-02-browser-scoping-repro.log`) |
| Nach dem Fix | dieselbe Datei → **5 passed, exit 0** (Rohlog `logs/logs/blockm301-02-browser-scoping-green.log`) |
| Backend-Stub-Regel | der Backend-Stub ist die einzige gestellte Grenze — im gesamten Regressionstest wird KEIN Browser gestartet, kein GUI, kein Netz |
| Run-Scoping belegt | nach dem Handler-Aufruf existiert `<runtime>/browser` (Session-State + Lock-Ort); ohne Runtime `<workspace>/browser` |
| Produktiver Nachweis | echte Loop-Kette über das zusammengebaute Chat/Run-Toolset, Provider gestubbt: `browser(do="read", url="https://example.invalid/page")` → `decision=ALLOW, executed=True, ok=True`, Nutzlast ist der ehrliche Zustand des Handlers selbst (`url`, `state=rendered`, `run_id`, `session_state_dir`) — KEIN Crash, KEIN Schein-ALLOW |
| Ziel-Regressionen | `test_browser_layer.py` + `test_browser_backend.py` + `test_adversarial_browser.py` + `test_core_schema_type_mapping.py` → **exit 0** |
| Stage-Gate | `uv run python -m pytest tests/ -o addopts=` → **8334 passed, 33 skipped, 0 failed** (1816 s); `uv run ruff check src tests` → **exit 0**; `uv run python -m compileall -q src` → **exit 0**; `uv run skaldir --version` → 1.5.0, `skaldir selftest` → 8 Layer, 26 Probes, 0 not reachable |

### BLOCK-M3-03 — Stiller Argument-Drop an gebundenen Core-Tools (2026-10-04, Feature fix-silently-dropped-arguments, Commit `ba2d46a`)

**Defekt (reproduziert, zuerst durch eigenen echten-Loop-Probe, 13 Tests rot vor dem
Fix):** der Registry-Vertrag von `read_file` wirbt `expected_revision`
(`tools_core/files.py`, `descriptor('read_file')`), aber die SIGNATUR von
`bridge.read_file` kannte ihn nicht → das Modell-Schema des gebundenen Tools hatte den
Parameter nicht, während die `_bound_tool`-Behandlung unbekannte kwargs still filterte
(`{k: v for k, v in args.items() if k in sig.parameters}`). Bound Tools setzten kein
`additionalProperties: False`, also akzeptierte `_argument_problem` JEDES unbekannte
Argument → der Handler verwirfte es still und meldete `ok=True`. Gemessen am echten
Loop: `read_file(path, expected_revision=<falsches sha>)` → `ALLOW/executed=True,
ok=True`, volles::content, KEIN `STALE_REFERENCE` — der bewachte Read lief UNBEWACHT;
`terminal(command=…, bogus_flag=True)` → `ALLOW/executed=True`, Argument still
verschluckt (Rohlog `logs/dropped-args-repro/repro.py`, Pre-Fix-Ausgabe im
Verlaufsprotokoll).

**Zweite, beim Fix aufgedeckte Huselhärte:** die Registry-Guard-Verweigerung war seit
`0b09a8a` selbst tot — der Mismatch-Zweig rief `ToolResult.ok({...})` als METHODE auf
(`ok` ist ein BOOL-Feld), ein bewachter Read mit falschem sha crashte also mit
`TypeError: 'bool' object is not callable`, und `_call`'s `except TypeError`-Retry
verschluckte das und führte den Aufruf ERNEUT ohne Root-Bindung aus (cwd-Fallback).
Erst die Signatur-Erweiterung machte diese tote Verweigerung überhaupt erreichbar.

**Fix (drei Teile, Muster der Vorblöcke):**
1. `bridge.read_file` nimmt `expected_revision` (Standard `""`) und reicht es durch —
   der beworbene Vertrag ist übers Netz expressibel; die Registry-Verweigerung ist
   jetzt ehrlich: `failure('read_file', 'conflict', 'STALE_REFERENCE: …')` mit leerem
   `content` und dem gefundenen sha — benannter Fehler, nie still unbewachter Read.
2. Gebundene Core-Tool-Schemas (7 Bridge-Wrapper plus `run_code`/`process`/`browser`)
   deklarieren `additionalProperties: False` → unbekannte Argumente werden am Gate mit
   `invalid_arguments` ABGEWIESEN (Meldung nennt das schuldige Argument und die
   erlaubte Menge), statt still gedroppt zu werden mit `ok=True`.
3. Neuer Regressionstest `tests/test_dropped_arguments_gate.py`: SET-Gleichheit zwischen
   der Eigenschaftsmenge jedes bridge-gebundenen Tools und dessen Registry-Vertrag
   (Required-Mengen ebenfalls), geschlossene Schemas für das ganze
   `core_capability_tools`-Set, die erzwungene Stale-Verweigerung über den echten Loop
   und der echte-Loop-Beweis für ein unbekanntes Argument. Keine bestehende Behauptung
   geschwächt; `tool_search` (Registry-Kontrakte) und Loop-Fläche stimmen deren
   Eigenschaftsmengen nun per Test überein.

**Belege (echte Werte):**

| Prüfung | Ergebnis |
|---|---|
| Reproduktion vor dem Fix | eigener Probe durch den echten Loop → Defekt 1 (unbewachter Read, `ok=True`) und Defekt 2 (unbekanntes Argument still gedroppt) beide reproduziert (Rohlog `logs/dropped-args-repro/`; Skript-Exit 0 = reproduziert) |
| Failing-first | `uv run pytest tests/test_dropped_arguments_gate.py -q` → **13 failed, exit 1** (Rohlog `logs/dropped-args-repro/failing-first.log`) |
| Nach dem Fix | dieselbe Datei → **21 passed, exit 0** |
| Regressions-Nachbarn | `test_core_schema_type_mapping` + `test_agent_loop` + `test_harness_batching` + `test_tool_search_integration` + `test_tools_core_contracts` + `test_tools_core_registry` + `test_browser_tool_handler`, 130 passed, **exit 0** |
| Produktiver Nachweis | echte Loop-Kette, Provider gestubbt (einzige gestellte Grenze, BLOCK-M3-01/02-Muster): `read_file(path, expected_revision=<falsches sha>)` → `ok=False`, benanntes `STALE_REFERENCE`, `content` leer; der korrekte sha liest normal; `terminal(command=…, bogus_flag=True)` → `decision=invalid_arguments, executed=False, ok≠True` mit benanntem `bogus_flag` (Rohlog `logs/logs/blockm303-dropped-args-proof.log`) |
| `uv run ruff check src tests` | **exit 0** (Vollmenge) |
| `uv run python -m compileall -q src` | **exit 0** |
| CLI-Entry erhalten | `uv run skaldir --version` → 1.5.0 (Repo-Venv) |
| Stage-Gate | `uv run python -m pytest tests/ -q` → **exit 0**, [100%], keine FAILED/ERROR-Zeichen (Rohlog `logs/dropped-args-repro/full-suite.log`, ~1816 s; -qq unterdrückt die Summary-Zeile, Exitcode + Fortschrittsmarker sind das Ergebnis) |

### BLOCK-M3-03 Addendum — Stiller Unbound-Retry in `bridge._call` (2026-10-04, Feature fix-unbound-call-retry, Commit `89e6fd9`)

**Defekt (reproduziert, Unit- UND echter-Loop-Ebene):** `tools_core.bridge._call`
fing JEDES `TypeError` aus dem Registry-Nahtpunkt (`registry.call`) und führte
den Aufruf ERNEUT ohne den gebundenen Root aus — kommentiert als „registry
without a `root` parameter". Diese Begründung ist tot: `registry.call` nimmt
`root`/`runtime` als explizite Keyword-Argumente (`tools_core/registry.py`),
daher kann ein `TypeError` an dieser Stelle nur aus dem Code des Runners selbst
stammen — ein Defekt, nie ein „Root-Argument nicht akzeptiert"-Signal. Der
Unbound-Retry ließ `resolve_root(None)` → `Path.cwd()` den zweiten Aufruf an den
Prozess-cwd binden: ein Runner-Defekt wurde still in einen Cwd-Fallback
verwandelt und erweiterte den Workspace der gebundenen Run. Genau das war beim
toten `read_file`-Revisionsguard (BLOCK-M3-03 oben) der Werkzeugweg, der aus der
abgestürzten Verweigerung einen ungebundenen, unbewachten Read machte.

**Analyse-Ergebnis:** Nichts an HEAD hängt vom Retry ab. Die enge
Signatur-Nachprüfung wurde bewusst NICHT gebaut — es existiert kein
legitimer Fall, der den Retry bräuchte; Entfernung ist die ehrlichere Fix.

**Fix:** der `except TypeError`-Retry in `bridge._call` ist entfernt. Ein
Runner-`TypeError` bricht jetzt IMMER als benannter Executor-Fehler durch
(ein stiller zweiter, ungebundener Aufruf gibt es nicht mehr); die
Workspace-Bindung der
Run steht, `resolve_root(None)` wird nie als Fallback-Root benutzt.

**Belege (echte Werte, Raw-Logs `logs/unbound-retry-*.log`):**

| Prüfung | Ergebnis |
|---|---|
| Reproduktion (Unit-Ebene, vor dem Fix) | Probe-Runner (echter Handler-Shape: schreibt Effekt-Marker an `ctx.root`, dann `TypeError` „simulated runner defect: 'bool' object is not callable") am ECHTEN Runner-Seam; `bridge.read_file` mit gebundenem RunContext → Runner lief 2×, 2. Aufruf gebunden an `/home/willy/Agentic_Projects/Skaldir_Agent` (Prozess-cwd), Marker im Repo-Root (Rohlog `logs/unbound-retry-failing-first.log`) |
| Failing-first (Unit) | `uv run pytest tests/test_unbound_call_retry.py -q` → **1 failed, exit 1** vor dem Fix (siehe Rohlog) |
| Failing-first (echter Loop) | derselbe Probe-Runner am echten Executor-Seam + Stub-Provider (einzige gestellte Grenze, BLOCK-M3-01/02/03-Muster): echte Loop-Kette `read_file(path)` → vor dem Fix Runner 2× — 2. Aufruf an Prozess-cwd (Rohlog `logs/unbound-retry-loop-failing-first.log`, HEAD-Bridge temporär reproduziert und danach restauriert) |
| Nach dem Fix | dieselbe Datei → **5 passed, exit 0**; Runner läuft genau 1×, gebunden an den Run-Workspace; `TypeError` bricht benannt durch (`executed=True, ok≠True, error enthält 'TypeError'`); Prozess-cwd wird nie als Fallback-Root benutzt (Marker-Nachweis auf Dateiebene, Eindeutige Namen, Strays sofort entfernt) |
| Legitime Root-Nutzung unverändert | Seambindung-Probe: gebundener Call führt weiterhin `ctx.root == workspace`, `ctx.runtime == runtime`, `ok=True` durch; ungebundener Call behält Legacy-Verhalten (`SKALDIR_TOOL_WORKSPACE` > cwd); `except`-frei für ungebundene Caller ist das alte Propagieren — kein Verhalten für legitime Registry-Einträge geändert (keine existiert, die den Retry brauchte) |
| Regressions-Nachbarn | `test_run_context` + `test_run_context_isolation` + `test_dropped_arguments_gate` + `test_tools_core_registry` + `test_core_schema_type_mapping` + `test_agent_loop` + `test_harness_batching` + `test_tool_search_integration` + `test_tools_core_contracts` + `test_browser_tool_handler`, alle **exit 0** |
| `uv run ruff check src tests` | **exit 0** (Vollmenge; 1 Import-Ordnung im neuen Test, behoben) |
| `uv run python -m compileall -q src` | **exit 0** |
| CLI-Entry erhalten | `uv run skaldir --version` → 1.5.0 (Repo-Venv) |
| Stage-Gate | `uv run python -m pytest tests/ -q` → **exit 0**, [100%], keine FAILED/ERROR-Zeichen (Rohlog `logs/unbound-retry-full-suite.log`) |
