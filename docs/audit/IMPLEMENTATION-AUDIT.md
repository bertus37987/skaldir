# Implementierungsaudit — 134 Anforderungen gegen den Code

Stand: 25. September 2026, Ziel-Arbeitsbaum `/home/willy/Agentic_Projects/Skaldir_Agent`
(auf Basis `0b09a8a` + dokumentierte Änderungen), Quell-HEAD `a8fcaa1b`.

**Statuswerte** (aus `00-START-HIER.md`): `fehlt` · `teilweise` ·
`implementiert-unbestätigt` · `getestet` · `live-bestätigt` · `extern-blockiert` ·
`begründet-nicht-anwendbar`.

**Lesehinweis.** `getestet` heißt: ein automatisierter Test deckt das Verhalten ab und ist
hier gelaufen. `live-bestätigt` ist **nur** für das vergeben, was in dieser Sitzung auf dem
echten Host beobachtet wurde. `implementiert-unbestätigt` heißt: Code ist vorhanden und
einem aktiven Einstieg zugeordnet, aber hier nicht gegen ein echtes Ziel geprüft. Kein
Eintrag ist aus einer Command-Anzahl oder einem grünen Unit-Test abgeleitet.

---

## A. In dieser Sitzung neu implementiert und getestet

| ID | Status | Aktiver Einstieg | Reale Dateien | Nachweis | Restlücke |
|---|---|---|---|---|---|
| UI-01 | **getestet** | `skaldir tui preview` | `src/skaldir/tui.py:Screen.render`, `layout()` | `tests/test_tui_layout.py` (52), Live-Preview 100×30 | Anbindung an den Live-Run-Eventstrom |
| UI-02 | **getestet** | `skaldir tui preview` | `tui.py:chat_lines/tools_lines/goal_lines` | „chat_is_left / tools_above_goal“ via `tui layout` | Goal-Revisionen aufklappbar |
| UI-03 | **getestet** | — | `tui.py:TuiState.cursor`, `at_live_edge`, `new_events` | Auto-Follow-Test, Zähler-Test | Reattach an echten Event-Cursor |
| UI-04 | **getestet** | Tab im Prompt | `tui.py:tab_action`, `approval_modes.py:ApprovalMode.next` | `tab_action`-Test (4 Fälle) | Einbindung in `chatbar.read_key` |
| UI-05 | **getestet** | — | `tui.py:sanitize`, `_ERR_MARK`, ASCII-Faltung | Control-Sequence-/ASCII-/Fehlermarken-Tests | NO_COLOR-Live-Prüfung |
| UI-06 | **getestet** | `skaldir tui figure` | `tui.py:figure_frame`, `humor_at`, `FIGURE_FRAMES` | Determinismus, keine Prozentwerte | Live-PTY-Animation |
| UI-07 | **getestet** | — | `tui.py:sanitize` (vor Kürzung) | Truncation-/Escape-Tests | Secret-Redaktion in Details/Export |
| UI-08 | **getestet** | `skaldir tui preview/layout` | `tui.py:status_line`, `desktop_indicator` | n/a statt 0; Indikator geteilt mit `desktop_lease` | Panegrößen live |
| APR-01 | **getestet** | `skaldir tui approve --cycle` | `approval_modes.py:ApprovalMode`, `policy_for` | 3 Modi, Alias „Approve nichts“, Zyklus | Verdrahtung in `approval.py`-Engine |
| APR-02 | **getestet** | — | `approval_modes.py:ALWAYS_NEEDS_APPROVAL` | In allen 3 Modi `prompt` | Durchsetzung an der Engine |
| APR-03 | implementiert-unbestätigt | — | `approval_modes.py:policy_for` (Scope-Felder) | — | Nachrichtenversand-Scope |
| APR-04 | **getestet** | `skaldir tui approve` | `approval_modes.py:ApprovalModeStore` | Default-Wechsel ändert laufende Runs nicht | Neuprüfung wartender Aktionen |
| APR-05 | **getestet** | `skaldir tui approve` | `ApprovalModeStore.set_default` | pro Run + konfigurierbarer Default | Persistenz über Runs hinweg im UI |
| DESK-01 | **getestet** | `skaldir desk acquire/status` | `desktop_lease.py:DesktopLeaseStore.acquire` | Besitzer + Queue sichtbar | Verdrahtung in den Eingabe-Adapter |
| DESK-02 | **getestet** | `skaldir desk stop` | `desktop_lease.py:DesktopLeaseStore.fence`, `request_stop` | 4 Drop-Tests (Stopp/Freigabe/Takeover/Crash) | Fencing am echten Eingabetreiber |
| DESK-03 | **getestet** | `skaldir desk status` | `desktop_lease.py:indicator_lines` | 3 Zeilen, „controls“ nur mit Lease | Host-Integration (Compositor-Overlay) |
| DESK-04 | **getestet** | `skaldir desk resume` | `desktop_lease.py:resume`, `heartbeat` | 9 h ohne Timeout; Resume hebt Generation | — |
| STOP-01 | **getestet** | `skaldir stop detect` | `stop_signal.py:DoubleEscapeDetector` | Fenster 500 ms, Autorepeat-Filter | Physische Tastatur |
| STOP-02 | **getestet** | `skaldir desk stop` | `stop_signal` + `desktop_lease.request_stop` | Lease wird entwertet, Fence +1 | Drag/Modifier-Lösung am Treiber |
| STOP-03 | **getestet** | `skaldir stop detect --origin agent` | `stop_signal.py:Origin` | synthetisch bricht die Folge | Herkunftserkennung am Backend |
| STOP-04 | **getestet** | `skaldir stop report` | `stop_signal.py:probe_backends` | GlobalShortcuts v2 gemessen, `double_escape_sequence: unproven` | Portal-Bindung (öffnet Nutzerdialog) |
| STOP-05 | **extern-blockiert** | `skaldir stop report` | `stop_signal.py:write_report` | `double_escape_proven: false` dokumentiert | **Gruppe `input`** — Systemänderung durch den Besitzer |

## B. Vorhandene Funktionen, in dieser Sitzung gezielt geprüft

| ID | Status | Reale Dateien | Nachweis | Restlücke |
|---|---|---|---|---|
| AUD-01 | **getestet** | `docs/audit/MIGRATION-MANIFEST.md` | Einstieg `perf_lazy.main → cli.main → repl.launch_from_bare_call → chatbar` verfolgt; `--version`/`--help`/Import live | — |
| AUD-02 | **getestet** | `docs/audit/IMPLEMENTATION-AUDIT.md` (diese Datei) | eine Gap-Matrix; `tests/test_no_orphan_modules.py` verhindert zweite unerreichbare Registry | Nachverfolgung je ID fortsetzen |
| AUD-03 | **getestet** | `docs/audit/MIGRATION-MANIFEST.md`, `.gitignore` | Datenmigration versioniert, Quelle unverändert, Rückweg benannt | Live-Runtime-Migration separat |
| TOOL-01 | teilweise | `tools_core/registry.py`, `tools_core/contract.py` | `register()` liefert 9 Core-Tools mit Descriptor+Manifest | `input_schema` in `tool_search` fehlt (xfail) |
| TOOL-02 | **getestet** | `skaldir tools …` / `agent_toolset.py:tool_search_tool` | `discovery.py:_public_row` reicht `input_schema`/`output_schema` durch; `capability_mining._typed_contract` liest den Descriptor | ohne Descriptor bleibt der Schlüssel weg (nie erfunden) |
| TOOL-03 | implementiert-unbestätigt | `cli_contract.py`, `agent_loop.py` | Feldsatz vorhanden | 5 Commands ohne `data_schema` (siehe `KNOWN_CONTRACT_GAPS`) |
| CORE-01..16 | überwiegend implementiert-unbestätigt | `agent_loop.py`, `agent_toolset.py`, `tools_core/*`, `edit_tx.py`, `code_exec.py`, `codectx.py`, `knowledge.py` | 21 Tool-Zeilen, 9 Core-Tools HEALTHY (`skaldir selftest --json`, 26 Probes, 0 fehlgeschlagen) | Produktprüfung A ausstehend |
| QA-01..05 | **getestet** | `docs/audit/VALIDATION.md` | gezielte Regressionen nach Fix; Integration nach zusammenhängenden Änderungen; keine identischen Fullsuite-Serien | QA-06: Abschlussbericht diese Datei |

## C. Vorhandene Architektur, nicht in dieser Sitzung live geprüft

Der Quellstatus (`AGENTS.md` der Quelle, Stand 2026-09-19) meldet diese Layer als vorhanden
und belegt. Diese Sitzung hat `skaldir selftest --json` ausgeführt: **8 Layer, 26 Probes,
0 nicht erreichbar, exit 0**. Das ist ein Erreichbarkeitsnachweis, **keine** Produktabnahme.

| Gruppe | IDs | Reale Dateien | Status | Restlücke |
|---|---|---|---|---|
| ARCH | ARCH-01..03 | `runs_store.py`, `events.py`, `cli_contract.py`, `api_server.py` | implementiert-unbestätigt | interne Commands versionieren |
| WEB | WEB-01..04 | `tools_core/web.py`, `agent_toolset.py` | implementiert-unbestätigt | kostenloser Suchpfad live prüfen (T-12) |
| BATCH | BATCH-01..04 | `agent_loop.py`, `context_manager.py` | implementiert-unbestätigt | T-06..T-11 als Live-Lauf |
| ROLE | ROLE-01..04 | `subagents.py`, `subagent_roles.py`, `goal_run.py` | implementiert-unbestätigt | Worker-Isolation live |
| CU | CU-01..07 | `computer_use.py`, `cu_host_session.py`, `cu_platform.py`, `act_service.py` | implementiert-unbestätigt | **Wayland-Live-Abnahme** |
| BROWSER | BROWSER-01..03 | `cu_host_session.py`, `computer_use.py` | implementiert-unbestätigt | Fensterflut-Fehlerprobe (T-24) |
| SUB | SUB-01..06 | `act_cli.py`, `act_service.py`, `action_engine.py` | implementiert-unbestätigt | T-27..T-29; L-01 |
| SKILL | SKILL-01..04 | `skills.py`, `skill_builtin.py`, `skill_install.py`, `skills_builtin/` | implementiert-unbestätigt | L-01 |
| BUILD | BUILD-01..04 | `toolbuilder.py`, `buildflow.py` | implementiert-unbestätigt | T-30; Modellclient-Injektion |
| DOCKER | DOCKER-01..05 | `docker_ensure.py`, `docker_runtime.py`, `container_adapter.py`, `image_pin.py` | implementiert-unbestätigt | T-31; Produktprüfung C |
| FLOW | FLOW-01..04 | `workflows.py`, `compose_service.py`, `step_runners.py` | implementiert-unbestätigt | T-33; L-02 |
| SCHED | SCHED-01..04 | `nightshift.py`, `session_store.py` | teilweise | **kein eigener Zeitplan-Lauf nachgewiesen** (T-34..T-36) |
| GOAL | GOAL-01..02 | `goal_run.py`, `completion.py` | implementiert-unbestätigt | T-39, T-41 |
| VERIFY | VERIFY-01..03 | `verify_service.py`, `verify_flows.py`, `verify_cli.py` | implementiert-unbestätigt | PASS/FAIL/UNKNOWN live |
| RUN | RUN-01..05 | `runs_store.py`, `run_supervisor.py` | implementiert-unbestätigt | T-37..T-40; Crash-Reconciliation |
| JEV | JEV-01..02 | `jev/`, `system_one_service.py` | implementiert-unbestätigt | Live-Jev ist laut Quellstand **nicht** belegt (JEV-LIVE) |
| VOICE | VOICE-01..04 | `voice.py`, `voice_service.py`, `run_supervisor.py` | implementiert-unbestätigt | L-04 (zwei Replays) |
| MEM | MEM-01..06 | `memory_store.py`, `knowledge.py`, `knowledge_cli.py` | implementiert-unbestätigt | T-45, T-46; L-05 |
| CTX | CTX-01..05 | `context_manager.py` | implementiert-unbestätigt | T-44 |
| PERF | PERF-01..05 | `telemetry.py`, `perf_runtime.py`, `perf_progress.py` | implementiert-unbestätigt | Baseline p50/p95 unter Last |

## D. Ausdrücklich nicht als erfüllt gemeldet

| ID | Warum |
|---|---|
| STOP-05 | Der doppelte physische Escape ist **nicht** nachgewiesen. `skaldir stop report` sagt `double_escape_proven: false`. Der Host-Steuerungsmodus wird deshalb nicht als vollständig fertig freigegeben. |
| TOOL-02 (Rest) | `tool_search` nennt Fähigkeiten ohne `input_schema`. Das ist die ursprüngliche P0-Lücke der Testdatei und als `xfail(strict=True)` gepinnt. |
| SCHED-01..04 | Es existiert Code (`nightshift.py`), aber **kein** nachgewiesener Zeitplan-Lauf mit Zeitzone, Doppelzustellung und Überlappungsvermeidung. |
| Produktprüfungen B–F | Benötigen entweder eine Nutzerentscheidung (Video-/Kreativanwendung, Testobjekt) oder eine Live-Browsersitzung. |

---

## Vorgehen für die Nachverfolgung

Pro zusammenhängendem Change mehrere IDs abdecken, wenn dieselbe Zuständigkeit betroffen
ist. Für jeden hier `implementiert-unbestätigt`-Eintrag ist der nächste Schritt die
**Live-Abnahme**, nicht mehr Code. Die Test-IDs stehen in
[02-ABNAHME.md](../masterplan/02-ABNAHME.md); die Ergebnisse werden in
[VALIDATION.md](VALIDATION.md) nachgetragen.
