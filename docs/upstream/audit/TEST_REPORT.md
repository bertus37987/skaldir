# TEST_REPORT.md — lebender Nachweisbericht (Kapitel 06 §8, ABNAHME §114)

Stand: 16.09.2026 · Branch `godsv-supervisor-hardening` · HEAD `8da8b3d` (+ uncommittete
Parallel-Wellen — siehe Abschnitt 5).

Nur ausgeführte Läufe stehen hier, mit Befehl, Zählung und Exit-Code. **Ein grüner Unit-Test
belegt kein Kriterium** (ABNAHME §0, §5.2): dieser Bericht trennt deshalb am Ende ausdrücklich,
was die Suite beweist und was nur ein Live-Lauf beweisen kann.

## 1. Der kanonische Suite-Lauf

```bash
cd ~/projects/gods-voice && .venv/bin/python -m pytest -q -p no:randomly
```

| Zeitpunkt | Baum | Gesammelt | passed | skipped | failed | RC | Wanduhr |
|---|---|---|---|---|---|---|---|
| 19:02:24–19:04:58 | HEAD `b7a287a`, ohne die späteren Parallel-Dateien | 1515 | **1508** | **7** | **0** | **0** | 154.356 s |
| 19:23:00–19:25:37 (**letzter kanonischer Lauf**) | HEAD `8da8b3d`, **inkl.** der neuen System-One-Module | 1632 | **1625** | **7** | **0** | **0** | **156.612 s** |
| 19:13:20–19:15:55 (Gegenprobe, `-vv`) | HEAD `8da8b3d`, gleicher Testbestand | 1632 | **1625** | **7** | **0** | **0** | 154.82 s |

Die Gegenprobe (`pytest -p no:randomly -vv --tb=no`) endet mit der pytest-eigenen Zeile

```text
============ 1625 passed, 7 skipped, 1 warning in 154.82s (0:02:34) ============
```

Die beiden kanonischen `-q`-Läufe liefern dieselbe Zählung; dieses Projekt konfiguriert
`addopts = "-q"` (`pyproject.toml:42`), wodurch pytest die abschließende Zählzeile in diesem
Projekt **nicht** druckt (nachgeprüft: auch ein Einzeltest-Lauf zeigt keine Summenzeile). Die
Zahlen 1515/1508/7 und 1632/1625/7 stammen deshalb aus dem Fortschrittsstrom (72 Zeichen je
Zeile, `s` = skip) und sind gegengerechnet: `1508 + 7 = 1515` bzw. `1625 + 7 = 1632` — und
sie stimmen mit der `-vv`-Summenzeile exakt überein.

Drei Läufe, Streuung 1.5 % (154.36 / 154.82 / 156.61 s). Der Baum hat sich während der
Messreihe zweimal verändert (parallele Wellen); der letzte kanonische Lauf und die
`-vv`-Gegenprobe liegen beide auf HEAD `8da8b3d`.

### Die 7 Skips sind benannt (keine stille Lücke)

```text
tests/test_computer_use.py::TestContainerRunner::test_cli_action_roundtrip        SKIPPED
tests/test_computer_use.py::TestContainerRunner::test_close_removes_container     SKIPPED
tests/test_computer_use.py::TestContainerGui::test_gui_geometry_and_window_flow   SKIPPED
tests/test_computer_use.py::TestContainerGui::test_executor_step_verifies_via_observe SKIPPED
tests/test_docker_runtime.py::test_live_round_trip                                SKIPPED (live docker tier, opt-in)
tests/test_docker_runtime.py::test_port_conflict_live                             SKIPPED (live docker tier, opt-in)
tests/test_docker_runtime.py::test_image_missing_live                             SKIPPED (live docker tier, opt-in)
```

Die drei Docker-Live-Fälle sind opt-in und **liefen in dieser Session tatsächlich** — mit
Freigabe-Schalter:

```bash
GV_B6_LIVE_DOCKER=1 .venv/bin/python -m pytest -q -p no:randomly tests/test_docker_runtime.py
# → 25 passed, 0 skipped, 0 failed · RC=0 · 15.02 s
```

Die vier Computer-Use-Fälle brauchen Podman-Container bzw. das GUI-Image
`localhost/godsv-cu-x11`; auf diesem Host fehlt es (siehe
`docs/audit/COMPUTER_USE_SUBTOOLS.md`). Sie sind damit **ehrlich** skip, nicht grün.

## 2. Neue / gezielt gefahrene Testdateien

| Testdatei | Befehl | Ergebnis | Wanduhr |
|---|---|---|---|
| `tests/test_rf09_crash_windows.py` (neu, Crash-Fenster) | `.venv/bin/python -m pytest -q -p no:randomly tests/test_rf09_crash_windows.py` | **8 passed**, 0 failed, RC=0 | 18.23 s |
| RF-Pins gesamt (`test_rf02_request_deadline`, `test_rf03_monotonic_stream_clock`, `test_rf07_interim_report`, `test_rf08_segment`, `test_rf10_stop_responsiveness`, `test_rf_test_provider`) | `.venv/bin/python -m pytest -q -p no:randomly <die sechs Dateien>` | **32 passed**, 0 failed, RC=0 | 108.32 s |
| System-One-Module (Parallel-Welle, uncommittet): `test_system_one_adapters/contract/flags/policy/questions/sanitizer` | `.venv/bin/python -m pytest -q -p no:randomly <die sechs Dateien>` | **117 passed**, 0 failed, RC=0 | 1.46 s |

Die RF-Pin-Laufzeit (108 s) und die RF-09-Pins (18 s) erklären den Großteil der
Suite-Wanduhr: die Pins bauen jeweils eine Engine-Kopie, patchen sie mit dem echten Overlay
und fahren die echten Proben (RF-09 kopiert für den vollen Round-Trip den kompletten
Engine-Baum und schickt einen echten `SIGKILL`). Zahlen siehe `docs/audit/PERFORMANCE.md`.

## 3. Smoke- und Strukturprüfungen

| Prüfung | Befehl | Ergebnis |
|---|---|---|
| Desktop-Panel-Contract | `node tools/check_desktop_plugin.mjs` | **`desktop panel: contract OK`**, **158 ok / 0 fail** (3 Wiederholungen, 0.06 s) |
| Audit-Register | `python3 tools/build_audit_register.py --check` | `audit register current — 97 criteria`, RC=0 |
| Findings-Vertrag | `.venv/bin/python tools/check_findings_criteria.py` | `findings cross-check OK — 36 findings, 15 distinct criterion ids, all present`, RC=0 |
| Engine-Regressionen (Engine-eigene Wait-/Notice-Tests) | `tools/host_engine_regression_probe.py` (Engine-Interpreter) | `13 passed, 0 failed`, RC=0, 3 × ~0.20 s |
| Alle 11 Overlays gegen die Live-Engine | `python3 host-overlay/patch_*.py --check ~/.hermes/hermes-agent` | 11 × RC=0 („would patch"), 0.186 s, **nichts geschrieben** |
| Benchmark-Auswerteskript ohne Rohdaten | `.venv/bin/python tools/eval_benchmark.py` | `no runs — …`, **RC=2** (fail-loud, keine erfundenen Nullen) |

## 4. Was diese Suite beweist — und was nur ein Live-Lauf beweist

**Bewiesen (deterministisch, reproduzierbar, ohne Modell/Netz/GUI):**

- Schemas, Parser, State Machines der Verträge (Completion-Gates, Goal-Revision,
  Event-Replay, Lease/Fencing-Komparatoren).
- Die **Wirkung** jedes RF-Overlays auf den echten Engine-Funktionen: Deadline-Grenze,
  monotone Uhr, Versuchsbudget, Fallback-Fitness, Preserve/Reintegrate, lokaler
  Zwischenstand, Segment-Cursor, Crash-Fence (echter `SIGKILL`), Stop-Latenz.
- Die Anker aller 11 Overlays gegen die installierte Engine (`--check`), inklusive des
  lauten Abbruchs bei Anker-Drift.
- Der Panel-Contract der Chat-Oberfläche (158 Checks) inkl. „kein erfundenes Verb bei
  unbekanntem Zustand", „Kriterium-Update sendet Revisionsfence, nie Status", „verifizierter
  Vertrag friert den Editor".
- Auditierbarkeit: 97 Kriterien im Register, 36 Findings gegen das Register geprüft,
  Artefakt-Hashes real.

**NICHT bewiesen — nur ein Live-Lauf kann das:**

| Aussage | Warum die Suite nicht genügt | Nötig |
|---|---|---|
| „Der 152k-/80-Iterationen-Lauf bleibt in der Deadline" (RF-01/RF-02/RF-11) | braucht einen echten Provider mit echter Kontextgröße und echten Retries | Budgetfreigabe + echter Agent-Turn |
| „Stop wirkt am laufenden Live-Stream" (RF-10/§6.4) | die Probe fährt Engine-Funktionen mit gestubbtem Agent, kein Live-`/stop` | Live-Turn gegen echten Provider |
| „Crash gegen echten Provider ist exactly-once" (RF-09/E2E-09) | SIGKILL ist echt, der Provider nicht | Live-Turn + Kill mitten im HTTP |
| „Completion erkennt echte Stub-/Fake-Erfolge" (VD-19/VD-06) | braucht echte Modellpfade und echte Toolausgaben | Produktpilot (Stufe B) |
| „Computer Use bedient die Host-App" (S10/PX-04) | kein realer GUI-Runner/Image | Container-GUI-Image + Gerät |
| „Desktop-Chat funktioniert, Streaming/Abbruch verhalten sich richtig" (S01/S03/V2-07) | headless, kein App-Fenster | Gerätefenster + `gv_ui_e2e.mjs` |
| „Voice-Holdout Precision/Recall" (Stufe C, §105) | Holdout nicht gefahren | eingefrorener Split |
| „Jev-Antworten sind brauchbar/kalibriert" (JEV-*, §106) | kein Credential (Early Access) | TypeSafe-Zugang + Holdout |

## 5. Zwischenstand im Messfenster (nicht verschweigen)

Um 19:10:18 war die Suite **rot** — die parallele System-One-Welle schrieb genau in diesem
Fenster `src/godsv/system_one*.py` sowie `computer_use.py`, `events.py`, `llm.py`, `voice.py`
neu. Ergebnis: `1255 passed / 49 failed / 57 errors`, danach brach pytest mit einem
`INTERNALERROR` bei ~83 % ab. Das ist **kein** Nachweis eines Produktdefekts, sondern der
Zustand eines mitten im Schreiben befindlichen Baums; nach Abschluss der Welle lief dieselbe
Suite wieder vollständig grün (Abschnitt 1, Lauf 2). Diese Welle (ABNAHME-Audit) hat
ausschließlich `audit/`, `docs/` und `tools/` geschrieben — kein `src/`, `integrations/`,
`host-overlay/`, `app-overlay/` oder `tests/`.

## 6. Reproduktion in einem Block

```bash
cd ~/projects/gods-voice
.venv/bin/python -m pytest -q -p no:randomly                     # volle Suite (Zählzeile mit -vv)
GV_B6_LIVE_DOCKER=1 .venv/bin/python -m pytest -q -p no:randomly tests/test_docker_runtime.py
node tools/check_desktop_plugin.mjs                              # desktop panel: contract OK
python3 tools/build_audit_register.py --check                    # audit register current
.venv/bin/python tools/check_findings_criteria.py                # findings cross-check OK
python3 host-overlay/patch_rf10_stop_responsiveness.py --check ~/.hermes/hermes-agent
~/.hermes/hermes-agent/venv/bin/python tools/host_rf10_stop_probe.py /tmp/gv_perf10/pristine
~/.hermes/hermes-agent/venv/bin/python tools/host_rf10_stop_probe.py /tmp/gv_perf10/patched
~/.hermes/hermes-agent/venv/bin/python tools/rf_measure.py --out /tmp/gv_rf_rep1
.venv/bin/python tools/eval_benchmark.py                         # → no runs, RC=2 (korrekt)
```
