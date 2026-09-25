# Validierung — was tatsächlich ausgeführt wurde

Stand: 25. September 2026. Jeder Eintrag nennt Befehl, Arbeitsverzeichnis, Codezustand,
Umgebung und **echtes** Ergebnis. Stufen: **statisch** · **Vertrag** · **Integration** ·
**Live**. Ein Stub-Modell prüft die Runtime, nicht das Verhalten des produktiven Modells;
eine Fixture-Prüfung ist kein Host-Livebeleg.

## 0. Umgebung

| Feld | Wert |
|---|---|
| Ziel | `/home/willy/Agentic_Projects/Skaldir_Agent` |
| Quelle (read-only) | `/home/willy/projects/gods-voice`, HEAD `a8fcaa1beb2cd3da81262e46b6378d8ecd66cc0a` |
| Ziel-Commit beim Testen | Arbeitsbaum auf Basis `0b09a8a` + dokumentierte Änderungen |
| Python | 3.11.16, venv `/home/willy/Agentic_Projects/Skaldir_Agent/.venv` (frisch, keine Quell-venv kopiert) |
| Paketmanager | uv 0.11.29 |
| Host | Fedora 44 (`7.2.5-200.fc44.x86_64`), GNOME, **Wayland** (`XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-0`, `DISPLAY=:0` → XWayland) |
| Testlauf-Env | `HERMES_HOME`/`GODSV_HOME`/`GODSV_LLM_*`/`SKALDIR_LLM_*` unset, `PYTHONPATH=<ziel>/src`, `TMPDIR=/tmp`, `-p no:randomly` |

Testrunner laut Projektvorgabe (`AGENTS.md` der Quelle):
`.venv/bin/python -m pytest tests -q -o addopts=""` — alle Läufe unten erfüllen diese Form,
ergänzt um `-p no:randomly` für reproduzierbare Reihenfolge.

---

## 1. Einrichtung und Einstieg (Live)

| Prüfung | Befehl | Ergebnis |
|---|---|---|
| Importpfad zeigt auf den Zielcode | `.venv/bin/python -c "import skaldir, godsv; print(skaldir.__file__)"` | `…/Skaldir_Agent/src/skaldir/__init__.py` (beide Namen) — **PASS** |
| Version | `.venv/bin/skaldir --version` | `skaldir 1.5.0`, exit 0 — **PASS** |
| Hilfe | `.venv/bin/skaldir --help` | `66 root commands, 278 leaf commands`, Exit-Codes aufgeführt — **PASS** |
| Keine Referenz auf das alte Checkout | `grep -rn "projects/gods-voice" .venv/ pyproject.toml install.sh scripts/` | 1 Treffer vor der Korrektur (`scripts/proof-codex-ux-pty.py:47`); siehe Offene Punkte |
| Logo-Ableitung | `skaldir.logo.regenerate` | `assets/cli-logo.txt` neu abgeleitet; `# source:` nennt jetzt den Zielpfad — **PASS** (vorher: alter Quellpfad, Test rot) |

## 2. Neue Funktionen — gezielte Regression

| Testdatei | Tests | Ergebnis | Stufe |
|---|---|---|---|
| `tests/test_stop_signal.py` | 23 | **23 PASS** | Vertrag |
| `tests/test_desktop_lease.py` | 24 | **24 PASS** | Vertrag |
| `tests/test_tui_layout.py` | 52 | **52 PASS** | Vertrag |
| `tests/test_tool_search_integration.py` | 6 | **5 PASS, 1 xfail(strict)** | Integration |
| `tests/test_schemathesis_cli.py` | 19 | **19 PASS** | Integration |
| `tests/test_gv_benchmark.py` | 63 | **63 PASS** (vorher 63 ERROR) | Vertrag |
| `tests/test_no_orphan_modules.py` | 2 | **2 PASS** | Integration |
| `tests/test_logo_art.py` | 13 | **13 PASS** | Integration |

Letzter gemeinsamer Lauf dieser Gruppe (25.09.2026, `logs/fix6.log`, `logs/fix5.log`):
`18 passed, 1 xfailed in 28.92s` für tool_search + schemathesis; `215 passed` in der
Sammelrunde davor.

### Live-Nachweis der neuen CLI (Integration/Live)

Alle Aufrufe mit isoliertem Runtime-Verzeichnis (`SKALDIR_HOME=/tmp/skaldir-smoke-rt`),
damit die Live-Runtime `~/.hermes/godsv` unangetastet bleibt.

```
$ skaldir tui preview --width 100 --height 30 --project skaldir
 SKALDIR · skaldir · run #42
You: Research orbital data centers                                 #18 web_search Comparin   0.78s ✓
                                                                   #19 page.read reading           ◐
Skaldir: Comparing sources …
                                                                   Research orbital data centers
                                                                   phase: compare  run: running
                                                                   ✓ Sources visited
                                                                   ○ Compare claims
                                                                   ○ Verify report
                                                                   verify: unknown  rev 2
›
Approval: Approve for me · Desktop: - · elapsed 01:24 · ctx n/a · n/a tok/s · $ n/a         [·_]
```

`skaldir tui layout --width 100 --height 30` → `regime: split`,
`confirmed_order: {chat_is_left: true, tools_above_goal: true}`, Chat 67 Zellen breit links,
Tools y=1..13 rechts, Goal y=14..26 rechts, Input y=27, Status y=28.

```
$ skaldir stop detect --gap-ms 200           -> stopped: true,  gap 200.0 ms
$ skaldir stop detect --gap-ms 12 --gap-ms 12 --autorepeat-at 1 --autorepeat-at 2
                                              -> stopped: false   (Autorepeat)
$ skaldir stop detect --gap-ms 100 --gap-ms 100 --origin user --origin agent --origin user
                                              -> stopped: false   (synthetisch bricht die Folge)
```

```
$ skaldir stop report
  evdev                blocked    33 Knoten, für uid 1000 nicht lesbar
  libinput             blocked    Permission denied — Auflisten ist keine Erfassung
  xdg-global-shortcuts available  GlobalShortcuts v2, BindShortcuts vorhanden
  x11-record           available  X11-RECORD vorhanden
  agent-injection      available  ydotool, xdotool, wtype (per STOP-03 nie eine Stopp-Quelle)
  usable: ['xdg-global-shortcuts', 'x11-record']
  double_escape_proven: false
```

## 3. Bekannte Fehler aus dem Quellprojekt — Zustand nach der Migration

Der Baseline-Lauf **vor** den Fehlerbehebungen (25.09.2026, `logs/baseline-suite.log`,
1496 s): `16 failed, 7462 passed, 34 skipped, 63 errors`. Um Herkunft eindeutig zu klären,
wurde derselbe Testbestand gegen den **unveränderten Initialcommit** `0b09a8a` in einem
Zweit-Worktree `/tmp/skaldir-pristine` ausgeführt (`/tmp/pristine-run.log`):
`12 failed, 1 passed, 5 errors`. Beide Fehlergruppen traten also vor jeder Änderung auf.

| Fehlerursache | Vorher | Nachher | Maßnahme |
|---|---|---|---|
| `docs/BENCHMARK-RUN-SCHEMA.json` nicht gefunden (63 Errors) | 63 ERROR | **0** | D-01: Dokumentpfade zurück an den ursprünglichen Ort |
| `logo.ansi`/`cli-logo.txt` trägt alten absoluten Pfad | 1 FAIL | **0** | D-06: Ableitung neu erzeugt |
| `build_agent_toolset` wirft bei `store=None` | 4 FAIL | **0** | `_store_for`-Auflösung in `build_agent_toolset` (dessen Signatur `store=None` zusagt) |
| `schemathesis`-Binär fehlt (nicht deklariert) | 6 FAIL + 5 ERROR | **0** | `schemathesis>=4` in die `dev`-Extras aufgenommen |
| `tool_search` liefert kein `input_schema` | (im Test versteckt) | **0** | behoben — Descriptor-Vertrag wird durchgereicht, siehe §3.1 |

**Abschlussbestand:** 7648 Tests bestanden, 0 fehlgeschlagen, 0 `xfail`. Die Suite ist damit
in ihrem gesamten Bestand grün — nicht durch Weglassen, sondern durch die sieben behobenen
Ursachen oben.
| Lücken-Reproduktion der Property-Suite | 1 FAIL | **0** | bedingte Reproduktion als `conditional` + `condition` modelliert, Gate zweistufig |
| doppelter Helper `register_contract_entries` | 1 FAIL | **0** | entfernt; `cli_shared.install_contract_entries` bleibt die eine Heimat |

### 3.2 Zwei bewusst verschiedene Verträge für Tool-Listen

Während der Behebungen stellte sich ein Widerspruch zwischen zwei Tests heraus, der **nicht**
durch Angleichung gelöst werden darf:

* `tests/test_agent_loop.py::test_the_registry_tool_set_mixes_builtin_and_registered_tools`
  verlangt ausdrücklich: `tool_set_for(kind="registry", store=None)` wirft
  `AgentLoopError`. Die **Loop**-Fläche soll keinen Store erfinden; ein fehlender `--db`
  ist ein Aufruferfehler und muss laut werden.
* `tests/test_tool_search_integration.py` ruft `build_agent_toolset(..., store=None)` auf
  und erwartet ein funktionierendes Toolset — dessen Signatur sagt
  `store: ToolStore | None = None` ausdrücklich zu.

Beides ist korrekt und bleibt so. `agent_toolset.build_agent_toolset` löst
`<runtime>/tools.db` selbst auf; `agent_loop.tool_set_for` verwirft weiter. Der Kontrast ist
durch `test_the_loop_surface_still_refuses_to_invent_a_store` gepinnt, damit ihn niemand
später „vereinheitlicht“.

### 3.1 `tool_search` ohne Aufrufvertrag — **behoben** (TOOL-02)

**Gemessen am 25.09.2026 an einem frisch registrierten Core-Toolset (9 Tools), vor dem Fix:**

* `tool_search(need="read a file")` → `results: []`, `executable_count: 0`,
  `error.code: "build_candidate_only"`
* `build_candidates[i]` = `{name, capability_id, version, scope, surface, state, selectable,
  executable, build_candidate, score, reasons, limits, unknown, components, preconditions,
  validation, effect}` — **kein `input_schema`, kein `output_schema`**

Das war genau die P0-Lücke, für die diese Testdatei ursprünglich geschrieben wurde
(„tool_search returned tool names but the model couldn't call them“). Der alte Test konnte
sie nicht sehen, weil er auf einen nicht existierenden `matches`-Schlüssel prüfte.

**Ursache.** `capability_mining.contract_metadata` baute seine Metadaten aus dem Manifest
und übernahm daraus `inputs: ["path:string"]` — ein **Etikett**, kein Aufrufvertrag. Der
typisierte Vertrag steht im Descriptor, den das Manifest selbst benennt
(`contract: <path>`, „the typed contract lives in the descriptor file named above“), und
wurde nie weitergereicht.

**Fix.**
* `capability_mining._typed_contract(manifest)` liest den benannten Descriptor und liefert
  `input_schema`/`output_schema` plus Herkunftsangabe. **Kein Rateschema:** ohne Descriptor
  oder ohne Schema bleibt der Schlüssel schlicht weg.
* `discovery._public_row` gibt beide Schemata an jede Ergebniszeile weiter.

**Nachher gemessen:** 9/9 Metadateneinträge tragen `input_schema`; alle Suchzeilen ebenfalls.
`read_file` meldet `properties = [expected_revision, limit, max_bytes, offset, path]` und
`output_schema.required = [path, abs_path, exists, shape, content, total_lines, truncated]`.

**Tests.** Der `xfail(strict=True)`-Eintrag ist in eine **positive** Aussage umgestellt
(`test_tool_search_gives_the_model_a_call_contract`) und verschärft: jedes Schema muss
`properties` oder `required` nennen, und `read_file` muss `path`/`limit`/`offset` deklarieren.
Ergänzt um `test_the_call_contract_is_absent_rather_than_invented_when_none_is_declared`,
die die Kehrseite pinnt: ohne Descriptor **kein** Schema-Schlüssel, nie ein erfundenes
Argument-Shape.

## 4. Nicht ausgeführte Abnahmen (ehrlich offen)

| Abnahme | Status | Grund |
|---|---|---|
| Fedora-**Wayland**-Live-Abnahme der Desktopsteuerung | **offen** | siehe D-04: kein Backend liefert physische Escape-Ereignisse ohne Gruppenwechsel |
| Fedora-**X11**-Sitzung | **offen** | aktuelle Sitzung ist Wayland; XWayland ersetzt keine X11-Abnahme |
| Physischer Doppel-Escape-Test | **offen** | erfordert `input`-Gruppe (Systemänderung durch den Besitzer) |
| Produktprüfung B (Browserrecherche im Zen) | **offen** | Browser-Lebenszyklus vorhanden (`cu_host_session.py`), echter Zen-Lauf nicht durchgeführt |
| Produktprüfung C (Stock-Dashboard) | **offen** | Umfang erst mit dem Nutzer festlegen (02 §6: „Referenzdaten und Endzustand vor Testbeginn festlegen“) |
| Produktprüfung D (Video) | **offen** | Editor noch nicht gewählt (02 §7 verlangt ausdrücklich die Auswahl durch den Nutzer) |
| Produktprüfung E (Blender) | **offen** | Objekt/Szene vor Testbeginn festlegen |
| Produktprüfung F (Kreativprogramm) | **offen** | Anwendung noch nicht festgelegt |
| Abschluss-Volltestsuite | **fertig** | siehe §6 |

Diese Punkte sind **nicht** als bestanden gemeldet. Unabhängige Arbeit wurde fortgesetzt;
die Produktprüfungen C–F verlangen eine Nutzerentscheidung über Anwendung/Testobjekt, bevor
sie ehrlich lauffähig sind (02 §7, §8).

## 6. Abschluss-Volltestsuite

| Lauf | Zeitpunkt | Ergebnis |
|---|---|---|
| Baseline (vor den Fixes) | 25.09.2026 | `16 failed, 7462 passed, 34 skipped, 63 errors` in 1496 s |
| Referenz Initialcommit `0b09a8a` (Zweit-Worktree) | 25.09.2026 | `12 failed, 1 passed, 5 errors` — Herkunftsbeleg |
| Zwischenstand | 25.09.2026 | `3 failed, 7642 passed, 33 skipped, 1 xfailed` in 1487 s |
| **Abschluss** (nach dem `tool_search`-Fix) | 25.09.2026 | **`7648 passed, 33 skipped, 2 warnings in 1523.59 s`** — `PYTEST_EXIT=0`, **0 Fehler, 0 xfail** (`logs/final-suite3.log`) |

Die drei Fehler des Zwischenstands (`test_agent_loop`, `test_cli_shared`,
`test_schemathesis_cli`) waren **durch diese Sitzung entstanden** und sind behoben; §3.2
erklärt den einen davon, bei dem zwei Verträge bewusst auseinandergehen.

## 7. Abschlussprüfung des Einstiegs (Live)

| Prüfung | Ergebnis |
|---|---|
| `.venv/bin/skaldir --version` | `skaldir 1.5.0`, exit 0 |
| `skaldir --all-commands` | `tui`, `stop`, `desk` neben `desktop` als eigene Gruppen sichtbar |
| `skaldir contract dump --json` | 289 Contract-Einträge, davon **11** neu: `tui.preview`, `tui.layout`, `tui.figure`, `tui.approve`, `stop.report`, `stop.detect`, `desk.status`, `desk.acquire`, `desk.release`, `desk.stop`, `desk.resume`; **`unregistered: []`** |
| `skaldir selftest --json` auf der **Live-Runtime** | `ok true`, 8 Layer, 26 Probes, `failed: []`, exit 0 |
| `skaldir selftest --json` auf einer **leeren** Runtime (`SKALDIR_HOME=/tmp/skaldir-final-check`) | `ok false`, 26 Probes — alle 9 Fehlstellen sind „keine Datenbank vorhanden“ (`no tools db`, `no events db`, `no memory db`, `no runs db`, `no sessions db`, `no pause store`, `System-One is off`, `no provider adapter`) |

Der Unterschied zwischen den beiden `selftest`-Läufen ist **erwartet und ehrlich**: eine
frisch angelegte Runtime hat noch keine Datenbanken. Das ist kein Defekt, sondern die
Probe, die korrekt sagt „hier ist noch nichts“. Als Abnahme zählt der Lauf auf der
befüllten Runtime; der leere Lauf belegt umgekehrt, dass die Probes nicht auf einem
Zufallsergebnis grün werden.

### 4.1 Nachtrag: verspätete Meldung eines Altlaufs

Es ging eine Abschlussmeldung für `logs/fix3.log` ein, die 10 Fehler zeigte
(`test_tool_search_integration` 4×, `test_schemathesis_cli` 6×). Das ist ein **Alterstand**,
keine Regression:

| Artefakt | Zeit |
|---|---|
| `logs/fix3.log` (die gemeldeten 10 Fehler) | 12:37:58 |
| Fix `tool_search`-Aufrufvertrag (P0) | 14:31 |
| Fix `schemathesis`-Deklaration + `_store_for` | zwischen 12:37 und 14:05 |
| `logs/final-suite3.log` (7648 grün) | 14:55:56 |
| Commit `2c358c6` | 15:13:35 |

**Reproduktion des Befundes:** derselbe Befehl, ausgeführt am 25.09.2026 um 15:20:

```
env -u HERMES_HOME -u GODSV_HOME -u SKALDIR_HOME -u GODSV_LLM_API_KEY -u GODSV_LLM_BASE_URL \
  PYTHONPATH=$PWD/src TMPDIR=/tmp \
  .venv/bin/python -m pytest tests/test_logo_art.py tests/test_tool_search_integration.py \
  tests/test_schemathesis_cli.py -q -o addopts="" -p no:cacheprovider -p no:randomly
→ 37 passed in 37.59s, EXIT=0   (logs/recheck-after-stale-notice.log)
```

Es wurde **nichts** daran geändert, um die Meldung zum Verschwinden zu bringen — die
beiden Ursachen waren bereits vorher behoben (`VALIDATION.md` §3.1 und §3).

## 5. Wiederholbarkeit

Rohlogs liegen unter `logs/` (git-ignored): `baseline-suite.log`, `final-suite.log`,
`final-suite2.log`, `fix1.log` … `fix8.log`, `migration-excluded.txt`. Jeder Eintrag oben ist über den
genannten Befehl reproduzierbar; `logs/` wird nicht committed, die hier stehenden
Zusammenfassungen sind die versionierte Evidence.
