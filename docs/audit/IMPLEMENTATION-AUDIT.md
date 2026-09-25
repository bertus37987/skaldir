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

## C2. Abgleich der bekannten Fehler (01-MASTERPLAN §2.2)

Der Masterplan sagt: „Diese Punkte anhand aktueller Reproducer prüfen: Sie können
inzwischen repariert sein. Keine bereits behobene Lücke erneut implementieren." Das ist
hier geschehen — geprüft gegen den **aktuellen** Code, nicht gegen den Plan.

| Bekannter Fehler | Zustand | Beleg |
|---|---|---|
| Mehrfacher Zen-Start nach fehlender Fensterbeobachtung (12+ Fenster) | Regression vorhanden | `tests/test_capability_learning.py` (Fensterbeobachtung/Startverhalten) |
| stdout wurde als `verified` statt tatsächlicher Ausgabe zurückgegeben | **behoben + Regression** | `tests/test_end_state_stdout.py::test_end_state_stdout_returns_real_output_not_status_marker` und `…::test_end_state_stdout_rejects_when_output_missing` |
| `web_search` gefunden, dann irrtümlich als Shell-Binary gestartet | Dispatch läuft über den Registry-Adapter, nicht über Shell | `src/skaldir/tools_core/bridge.py:184` (`web_search` als typisierter Bridge-Call), `src/skaldir/toolcall.py:67` (Klassifikation als read-only Tool, nicht als Shell) |
| CLI-Diagnose abhängig von GUI-Endzustand; Schema-/Payload-Widersprüche | teilweise behoben; Rest als Contract-Lücken geführt | `KNOWN_CONTRACT_GAPS` in `tools/gv_schemathesis_run.py` (5 Einträge, je mit `why` + `fix`) |
| Builder ohne konfigurierten Modellclient, obwohl Hauptagent einen Provider besitzt (BUILD-03) | **behoben + Regression** | `tests/test_toolbuild_client_wiring.py` (5 Tests, u. a. `test_without_a_client_the_caller_s_blocker_is_reported`, `test_the_draft_really_goes_to_the_injected_client`) |
| COMPOSE registriert Dateien, aber tatsächlicher Aufrufweg unklar (FLOW-03) | Regression vorhanden | `tests/test_step_runners.py`, `tests/test_step_runners_gui.py`, `tests/test_cu_chain_local.py` |
| Erfolgreiches Dateischreiben mit `created:false` wird als Fehler gedeutet (T-04) | Regression vorhanden | `tests/test_checkpoints_cli.py`, `tests/test_compose_cli.py` u. a. behandeln `created: false` als Aktualisierung |
| Lange wiederholte Testbefehle ohne sichtbaren Fortschritt | beobachtet, nicht nachträglich reproduzierbar | `src/skaldir/progress_guard.py` + `run_supervisor.py` (no-progress-Streaks) |

**Ergebnis:** Keiner dieser Punkte wird erneut implementiert. Zwei sind ausdrücklich als
behoben mit Regression belegt, drei haben Regressionen am Mechanismus, einer ist über die
Contract-Lückenliste geführt. Der Rest ist als vorhandene Mechanismen zugeordnet.

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

## Anhang C3. Detaillierter Codeabgleich, Teil 2 (BUILD … QA)

Vollständige Einzelbefunde je ID mit Datei:Zeile, Test-IDs und konkreter Restlücke.
Quelle: vertiefte Einzelprüfung des Zielcodes am 25.09.2026; Stufen wie oben.

### BUILD/DOCKER/FLOW — Toolbau, Container, Workflows

| ID | Status | Reale Dateien/Symbole | Tests | Restlücke |
|---|---|---|---|---|
| BUILD-01 | getestet | src/skaldir/toolbuilder.py:338 (ToolBuilder), :391-485 (Phasen _enter/_emit_publish_event), :523 (recover); src/skaldir/buildflow.py:210 (normalize_need), :221 (build_ke… | tests/test_toolbuilder.py::test_a_successful_build_records_every_phase_and_stays_binding_pending, ::test_publ… | Kein Ende-zu-Ende-Beleg Prompt→Discovery→Wiederverwendung im Produktlauf; Discovery-Prüfung nur als Builder-Overlap-Tests (test_three_term_overlap_refuses_the_build_before_writing) |
| BUILD-02 | getestet | src/skaldir/toolbuilder.py:298 (choose_kind); src/skaldir/adapter_http.py, src/skaldir/adapter_mcp.py, src/skaldir/container_adapter.py:191/317/432, src/skaldir/compose_… | tests/test_toolbuilder.py::test_wraps_existing_cli_prefers_cli, ::test_multi_function_api_prefers_mcp, ::test… | GUI-Subtool- und Composite-Lifecycle nur über Step-Runner-Tests; kein Test belegt einen einheitlichen Lifecycle über alle sechs Tooltypen |
| BUILD-03 | getestet | src/skaldir/toolbuilder.py:362 (Draft nutzt konfigurierten Modellclient), :1043, :2042 (Blocker 'inject the configured model client or supply source/tests') | tests/test_toolbuilder.py::test_missing_source_without_client_is_a_precise_blocker, ::test_build_registers_an… | Anzeige des Fehlers im Builder-Health (capability_health.py) nicht nachgewiesen; Nutzer-führt-Code-manuell-aus Szenario nur indirekt über Build-mit-Tests belegt |
| BUILD-04 | getestet | src/skaldir/toolbuilder.py:194 (manifest_v2_fields), :207 (validate_manifest_v2), :228 (validate_build_mounts), :317 (inspect_dependencies); src/skaldir/container_adapte… | tests/test_toolbuilder_container.py::test_rendering_validates_the_declared_inputs, ::test_parsing_checks_the_… | Timeout-Prüfung generierter Tools nur über Docker-Job-Timeouts (test_a_timed_out_job_sweeps_its_own_labels), nicht als Tool-Manifest-Feld |
| DOCKER-01 | getestet | src/skaldir/docker_ensure.py (Engine-Probe, Rootless-Podman-Fallback); src/skaldir/docker_runtime.py:543 (DockerRuntimeManager), :493 (classify_pull_error); src/skaldir/… | tests/test_docker_ensure.py::test_rootless_podman_is_the_declared_fallback, ::test_nothing_answers_is_blocked… | Explizite Engine-Kompatibilitätsprüfung nur als daemon_probe (test_daemon_probe_reports_server_version), keine Versionstoleranz-Matrix |
| DOCKER-02 | getestet | src/skaldir/docker_runtime.py:296 (verify_pinned_digest), :309 (validate_mounts), :346 (RunConfig: Mounts, Netzwerk, Ressourcen), :168 (CmdResult mit Exitcode), :222 (po… | tests/test_docker_runtime.py::test_pin_verification_rejects_mismatch, ::test_the_mount_allowlist_refuses_befo… | Reproduzierbare Dependencies nur als Undeclared-Dependency-Prüfung, kein Locking; Credentials-Handles indirekt über treasury, kein Runtime-Mount-Test dafür |
| DOCKER-03 | getestet | src/skaldir/compose_service.py:493-762 (StepSpec/CompositeSpec mit version), src/skaldir/container_adapter.py:191-569 (getypte Subtool-IO), src/skaldir/container_cli_tem… | tests/test_toolbuilder_container.py::test_the_surface_lists_the_real_verbs_and_the_absent_api_mcp, ::test_a_c… | Beleg fehlt, dass interne Container-Subtools dieselben Dispatch-/Approval-/Resultverträge wie externe Tools besitzen |
| DOCKER-04 | getestet | src/skaldir/toolbuilder.py:485 (_emit_publish_event), src/skaldir/docker_runtime.py:543, src/skaldir/compose_service.py | tests/test_compose_e2e.py::test_create_validate_run_show_through_cli_main, ::test_a_docker_step_runs_through_… | 'Über Agent aufrufen und später wiederverwenden' nur über Container-Reuse-Test (test_the_container_is_started_exactly_once_then_reused), kein Agent-Loop-E2E mit registriertem Tool |
| DOCKER-05 | fehlt |  |  | Keine Code- oder Testbasis für Zugriffsrechte/Datenverfügbarkeit/Nutzungsbedingungen der Beispiel-Tools (Stock Research, Editing-Suite, Medien-Download); keine Positionierung zu bezahlten Datenquellen auffindbar |
| FLOW-01 | teilweise | src/skaldir/agent_toolset.py:1-73 (alle Skaldir-Layer als AgentLoop-Tools, inkl. skill_expand_build_tool/skill_compose_tool/skill_verify_tool); src/skaldir/repl.py (Pros… | tests/test_chatbar.py::test_prose_is_a_question_and_a_command_is_a_command, ::test_single_word_chat_input_rea… | Kein Beleg, dass ausdrücklich genannte Fähigkeiten 'Workflow starten und planen' natursprachlich erreichbar sind; Slash-Commands als Pflichtsprache nur für Verbs widerlegt |
| FLOW-02 | teilweise | src/skaldir/workflows.py:930 (workflow_contract), src/skaldir/compose_service.py:493 (StepSpec mit precondition/end_state), :616-619 (CompositeSpec.version) | tests/test_compose_service.py::test_a_forward_reference_is_a_problem, ::test_a_step_without_its_checks_is_a_p… | Keine versionierten Ziel-/Prompttemplates, keine Schleifen mit Grenzen, keine expliziten Fehlerpfade im Workflowmodell (nur preconditions als Bedingungen) |
| FLOW-03 | teilweise | src/skaldir/workflows.py:937-1010 (workflow_runners/RunWorkflow — deterministischer Dispatch ohne LLM-Planung); src/skaldir/buildflow.py:379 (begrenzte RepairLoop) | tests/test_workflow_runner.py::test_a_real_workflow_runs_and_reports_its_own_verdict, ::test_an_empty_result_… | Replan-/Fallbackgrenze bei neuer Situation für Workflows nicht definiert (RepairLoop gilt nur für Builds) |
| FLOW-04 | teilweise | src/skaldir/workflows.py:950 (run_workflow über eindeutige workflow_id); src/skaldir/buildflow.py:210 (normalize_need für Bedarfs-Identität) | tests/test_workflow_runner.py::test_an_unknown_workflow_is_blocked_and_names_the_known_ones, ::test_the_entry… | Keine natursprachliche Workflow-Erkennung ('Starte meinen Stock-Research-Workflow'), keine Parameterergänzung aus Auftragstext, keine gezielte Klärung bei mehreren passenden Workflows |

### SCHED/GOAL/VERIFY/RUN — Zeitlauf, Ziel, Prüfung, Laufzustand

| ID | Status | Reale Dateien/Symbole | Tests | Restlücke |
|---|---|---|---|---|
| SCHED-01 | fehlt | keine (src/skaldir/host_bridge.py:957 cron_jobs ist nur lesender Host-Cron-Zugriff) |  | Kein eigener Zeittrigger-Mechanismus: keine Zeitplan-Entität mit Zeitzone, Workflowversion, Parametern, Policy oder Runs; Ausführung bei geschlossener TUI nicht implementiert |
| SCHED-02 | fehlt | keine |  | Keine Serialisierung gleicher Workflows, keine verpassten-Ausführungs-Policy, kein Singleton-Scheduler mit persistenter Ausführungs-ID (nur Build-Deduplizierung via buildflow.py:221, nicht zeitbasiert) |
| SCHED-03 | fehlt | keine (run_supervisor.py:632-709 wait/wake ist allgemein, nicht scheduler-getrieben) |  | Keine Sichtbarkeit 'Desktop belegt→Warteschlange/ waiting_approval/ präziser Blocker' für Zeitplan-Läufe; keine Regel gegen automatische Geldfreigabe aus Zeitplan, da kein Zeitplan existiert |
| SCHED-04 | fehlt | keine |  | Workflowdefinition/-aktivierung/-Pause/-Löschung als getrennte Aktionen nicht implementiert, da kein Zeitplanmodul existiert (Plan selbst legt korrekt keinen Zeitplan an) |
| GOAL-01 | getestet | src/skaldir/runs_store.py:11-13, :412-433 (Goal mit goal_text, revision_log, decisions, history_ref), :451-459 (Criterion mit typed evidence), :482 (Checkpoint); src/ska… | tests/test_runs_store.py::test_a_new_goal_gets_an_id_revision_criteria_and_a_checkpoint, ::test_a_criteria_ed… | Nichtziele (Non-Goals) fehlen als eigenes Goal-Feld; Nutzerkorrekturen nur generisch über revision_log/decisions, nicht als eigener Revisionstyp ausgewiesen |
| GOAL-02 | teilweise | src/skaldir/buildflow.py:481 (acceptance_problems), :500 (derive_acceptance_criteria); src/skaldir/goal_run.py:166 (load_criteria_file), :254 (criteria_from_workspace) | tests/test_goal_run_cli.py::test_criteria_come_from_the_workspace_plan_when_none_are_passed, ::test_a_criteri… | Gezieltes Nachfragen bei wichtiger Unklarheit vor Kriterienableitung nicht belegt; kein Test zur 'keine Zusatzfeatures erfinden'-Regel abseits der Prozess-Kriterien-Blockade (test_criteria_that_only_prove_the_process_bl… |
| VERIFY-01 | getestet | src/skaldir/verify_flows.py:1-30 (User-Flows gegen echten Browser/Desktop); src/skaldir/workflows.py:797 (verify_setting_persistence mit Neustart); src/skaldir/verify_se… | tests/test_verify_flows.py::test_a_flow_needs_an_end_state, ::test_a_browser_target_needs_a_url; tests/test_w… | Keine live-bestätigte Computer-Use-Abnahme eines Produkts (Hauptabläufe+Fehlerfälle+Neustart+Artefakte) dokumentiert; nur simulierte/monkeypatchte Flows |
| VERIFY-02 | getestet | src/skaldir/verify_service.py:366-459 (Criterion/Plan mit certifiable), :329 (run_freshness); src/skaldir/buildflow.py:379 (RepairLoop); src/skaldir/pause.py:127 (PauseS… | tests/test_verify_service.py::test_unknown_is_never_reported_as_pass_anywhere_in_the_module, ::test_only_all_… | FAIL→gezielte Reparatur ist nur für Builds implementiert (buildflow.RepairLoop), nicht für generische App-Abnahme; Validator-Lockerungs-Verbot nur testseitig geprüft |
| VERIFY-03 | getestet | src/skaldir/goal_run.py:1556 (_attempt_completion trennt Toolresultat vom Goal), :1654-1673 (Verdict-Redirects); src/skaldir/verify_service.py:385-401 (planned/certifiab… | tests/test_goal_run_cli.py::test_a_claim_is_refused_while_the_declared_checks_are_red, ::test_a_verification_… | Keine |
| RUN-01 | getestet | src/skaldir/completion.py:122-145 (STATES: WORKING/VERIFYING/BLOCKED_EXTERNAL/USER_CANCELLED/...), src/skaldir/runs_store.py:83-99 (GOAL_STATES), src/skaldir/run_supervi… | tests/test_runs_store.py::test_goal_states_match_the_completion_vocabulary; tests/test_run_supervisor.py::tes… | waiting_desktop/waiting_approval/paused_user/recovering existieren nicht als eigene Zustände (adaptiertes Modell); Terminalverbindung nur als UI-Konzept (chatbar front_for) ohne eigenen Zustand |
| RUN-02 | getestet | src/skaldir/runs_store.py:290 (boot_id für Rechnerneustart), :391-441 (Resume-Protokoll); src/skaldir/run_supervisor.py:345-709 (Lease/Heartbeat/Recovery); src/skaldir/p… | tests/test_pause_hard.py::test_full_runtime_restart_recovers_stale_pause, ::test_voice_process_killed_watchdo… | Regel 'Host-Desktop erst nach gültiger eingeloggter Sitzung und Notstopp-Verfügbarkeit' und Verbot von GUI-Klicks am Login-/Sperrbildschirm nicht belegt |
| RUN-03 | teilweise | src/skaldir/pause.py:29-32 (KIND_USER in LEASE_KINDS), :127-266 (PauseStore mit Status/Resume); src/skaldir/chatbar.py:1451-1453 (ctrl-c = interrupt) | tests/test_pause_hard.py::test_multiple_resume_events_resume_exactly_once; tests/test_pause.py::test_exactly_… | Doppel-Escape und expliziter Stop als UI-Bindung auf einen dauerhaften Nutzer-Pausestatus nicht implementiert/belegt; 'Abbrechen' als terminale Aktion ohne Pausesemantik; Resume-Exklusivität für KIND_USER nicht getestet |
| RUN-04 | getestet | src/skaldir/runs_store.py:125 (OP_STATES pending/in_flight/done/failed/unknown), :317-324 (idempotency_key), :180 (EvidenceRequired), :176 (StaleEvidence) | tests/test_runs_store.py::test_the_idempotency_key_is_a_unique_fence, ::test_resume_parks_an_unprovable_opera… | Keine; Einschränkung 'kein universelles Exactly-once für GUI/Käufe/Versand' ist im Modell abgebildet (OP_UNKNOWN/Parkierung) |
| RUN-05 | teilweise | src/skaldir/goal_run.py:1661-1665 (benannter Blocker im Completion-Gate), :2161-2163 (blocking_failures als Klartext); src/skaldir/runs_store.py (blocked_reason) | tests/test_goal_run_cli.py::test_the_cli_maps_a_pass_to_zero_and_a_blocked_goal_to_three, ::test_a_stalled_go… | Roter Blockertext mit Symbol und die vier Pflichtteile (was passierte/erhalten blieb/nötige Nutzeraktion) nicht als UI-Darstellung belegt; Hintergrundruns-Sicht nur über runs status, nicht im Vollbild-UI |

### JEV/VOICE/MEM/CTX — Entscheidung, Aufsicht, Wissen, Kontext

| ID | Status | Reale Dateien/Symbole | Tests | Restlücke |
|---|---|---|---|---|
| JEV-01 | getestet | src/skaldir/jev/kinds.py, jev/packs.py, jev/decide.py, jev/runtime.py (Capability-/Toolwahl, Subtool-Wiederverwendung, Memory-Relevanz, Kontextbudget, Triage) | tests/test_jev_facade.py::test_tool_routing_happy_path, ::test_capability_ranking_happy_path, ::test_memory_i… | Keine; Trennung zu VERIFY ist konzeptionell (eigene jev-Packs) belegt |
| JEV-02 | getestet | src/skaldir/jev/rules.py (deterministische Regeln), jev/decide.py; src/skaldir/system_one_adapters.py:19 (blocked_external_access statt Rechtevergabe) | tests/test_jev_facade.py::test_capability_ranking_borderline_is_a_gap_not_a_guess, ::test_tool_routing_below_… | 'Jev erteilt keine Rechte und ersetzt keine Approval-Policy' nur indirekt über policy-delta-Tests; kein expliziter Negativtest 'Jev-Beschluss ändert Approval-Status nie' |
| VOICE-01 | getestet | src/skaldir/voice_service.py:469-499 (Finding, category_for: Zielabweichung/Wiederholungen/Fehler), :343 (read_trajectory); src/skaldir/monitor.py:255 (Monitor) | tests/test_voice_service.py::test_reports_all_six_categories_with_evidence, ::test_goal_drift_needs_a_goal, :… | Keine; 'zuerst prüfen ob Hauptagent den Fehler bearbeitet' ist mit test_stays_silent_when_the_agent_is_already_on_it belegt |
| VOICE-02 | getestet | src/skaldir/pause.py:337-511 (VoicePause.consider mit Threshold/Cooldown), :127 (PauseStore = dieselbe Runtimekontrolle wie Nutzerpausen); src/skaldir/voice_service.py:1… | tests/test_pause.py::test_threshold_and_cooldown, ::test_contradiction_triggers_immediately, ::test_redirect_… | 'Kein vollständiger Supervisor-Aufruf nach jedem Tool' nur über ereignisgesteuerte Trajektorien-Auswertung indirekt belegt |
| VOICE-03 | getestet | src/skaldir/pause.py:511-666 (_start_research/deliverable mit Befund und nächstem Schritt); src/skaldir/subagent_briefs.py (Brief mit scope/deadline/acceptance); src/ska… | tests/test_pause.py::test_redirect_delivered_exactly_once, ::test_failed_research_abandons_but_emits; tests/t… | Beleg fehlt, dass der Rebrief die konkret betroffenen Zielkriterien nennt; 'Nutzer-Stop bleibt ausschließlich nutzerfreigabepflichtig' ohne eigenen Negativtest |
| VOICE-04 | getestet | src/skaldir/voice.py:92 (assess_intervention), src/skaldir/voice_service.py:1517-1574 (Cost-Gate vor Intervention) | tests/test_pause_hard.py::test_assessment_math_and_measured_estimated_split, ::test_cost_gate_blocks_a_low_se… | Metrik 'Zeitverlust durch Intervention' wird nicht erfasst; 'behobene Schleifen' nur als Loop-Detektion (test_diff_churn_after_many_edit_lines_without_progress), nicht als Outcome-Zähler |
| MEM-01 | getestet | src/skaldir/knowledge.py:151 (KnowledgeStore, Markdown mit Frontmatter/Index); src/skaldir/memory_store.py:467 (Memory); src/skaldir/memory.py:536 (evaluate_retrieval) | tests/test_knowledge.py::test_upsert_and_get_roundtrip, ::test_search_finds_by_content, ::test_search_returns… | Keine |
| MEM-02 | getestet | src/skaldir/project_context.py (AGENTS.md/SOUL.md-Ladder mit Ebenen und Rollen), src/skaldir/prompt_builder.py (Injektion) | tests/test_project_context.py::test_deeper_beats_shallower_and_the_ladder_breaks_the_tie, ::test_role_is_scal… | Gezieltes Laden projektübergreifender Präferenzen ist nur als Precedence-Ladder realisiert; eigene Dokumenttypen (Episoden, Fehlerwissen) liegen in memory_store/knowledge statt in benannten Dateien |
| MEM-03 | teilweise | src/skaldir/knowledge.py:151 (search mit Snippet+Zeilen, read mit Zeilenbereich) | tests/test_knowledge.py::test_search_returns_snippet_and_lines, ::test_read_line_range, ::test_search_scope_f… | Die dritte Kernfunktion — kurze Frage auf ausgewählte Dokumente mit Antwort in Originalreferenzen — ist nicht implementiert; Suche nennt Revision nur indirekt über Frontmatter |
| MEM-04 | getestet | src/skaldir/knowledge.py:147-151 (SHA-Revision), :151 (read mit 1-basierten inklusiven Zeilen); src/skaldir/memory_store.py:335 (contradiction_reason), :518 (is_expired) | tests/test_knowledge.py::test_stale_reference_on_revision_mismatch, ::test_revision_is_sha256_of_body, ::test… | Kürzungsmarker und Fortsetzungsprotokoll beim Lesen großer Ausschnitte nicht belegt; Snapshotkonsistenz (SHA gegen veränderte Datei) nur als STALE_REFERENCE-Prüfung |
| MEM-05 | getestet | src/skaldir/treasury.py (Credentials-Broker); src/skaldir/memory_store.py:411-418 (decay/ttl), :384 (evidence_weight); src/skaldir/nightshift.py (nur verdichtete Episode… | tests/test_treasury.py::test_secret_not_plaintext_on_disk, ::test_injection_scrubs_the_secret, ::test_expired… | Aufbewahrungsregeln und Zugriffsschutz für ausgelagerte private Artefakte sind nur als TTL/Scopes abgebildet, nicht als Artefakt-Policy |
| MEM-06 | teilweise | src/skaldir/nightshift.py:1-31 (Episoden→durable Memory als Vorschläge); src/skaldir/untrusted.py:1-30 (untrusted Inhalt ohne Kontrollautorität) | tests/test_nightshift.py::test_dry_run_makes_skeleton_proposal, ::test_existing_incident_not_reproposed, ::te… | Automatisches Speichern neuer Erfahrungen als Produktverhalten nicht belegt (nightshift ist ein aufgerufener Konsolidierungslauf); Schutzregel 'AGENTS.md/SOUL.md nie aus untrusted überschreiben' nicht als Code implement… |
| CTX-01 | getestet | src/skaldir/context_manager.py:365 (estimate_for), :391 (accounting_for mit measured/estimated), :439 (_budget_state); src/skaldir/jev/ (Kompakt-Signale) | tests/test_context_manager.py::test_accounting_separates_measured_and_estimated, ::test_budget_state_reports_… | Relevanzbewertung 'anhand Goal und offener Kriterien' ist nur über das feste Preserved-Set abgebildet, nicht über Kriterien-/Freshness-Abgleich pro Tool Call |
| CTX-02 | getestet | src/skaldir/context_manager.py:464-520 (Preserved), :512 (Entfernung von Call+Result gemeinsam), :546 (Kürzung behält Kopf und Toolidentität) | tests/test_context_manager.py::test_a_removal_branch_drops_call_and_result_together, ::test_a_shortened_resul… | 'Ungelöste Fehler behalten' nur indirekt über Preserved-Set; Zusammenfassungen tragen keine Quelle/Revision-Metadaten |
| CTX-03 | getestet | src/skaldir/runs_store.py:433 (history_ref), :330 (evidence_kind); src/skaldir/verify_service.py:216 (artifact_record); src/skaldir/context_manager.py:500 (Report: was g… | tests/test_context_manager.py::test_the_report_says_what_was_shortened_and_what_stayed_verbatim, ::test_a_fal… | Aufbewahrungsfristen und gezielte Abrufbarkeit der Originale über Artefakt-/History-Referenzen sind nicht durch Tests belegt |
| CTX-04 | getestet | src/skaldir/context_manager.py:174-190 (_loop_tool_calls/_loop_tool_call_id), :298-350 (TranscriptView.back, Loop-Shape-Übersetzung) | tests/test_context_manager.py::test_a_removal_branch_drops_call_and_result_together, ::test_the_translated_hi… | Abbruch-/Approvalzustand als unverlierbarer Block nur über Preserved; kein eigener Test für unknown-side-effects-Erhalt |
| CTX-05 | getestet | src/skaldir/context_manager.py:439 (_budget_state), :207 (Trigger-Fraction); src/skaldir/prompt_cache.py (Prefix-Hashing/Strategietabelle); src/skaldir/budget.py:110 (Bu… | tests/test_context_manager.py::test_the_trigger_fires_on_the_configured_fraction, ::test_maybe_compact_does_n… | Vorher/nachher-Qualitätsmessung fehlt; Messung 'Kompaktierung kostet mehr als sie spart' (Auslöse-Häufigkeit vs. Ersparnis) nicht implementiert |

### PERF/QA — Messung, Qualitätssicherung

| ID | Status | Reale Dateien/Symbole | Tests | Restlücke |
|---|---|---|---|---|
| PERF-01 | getestet | src/skaldir/telemetry.py:279-355 (ToolSpan/Turn/TelemetryRecord: Modellzeit, Toolzeit, Tokens, Kosten, Fehler), :521 (footer_line); src/skaldir/budget.py:94-110 (_RoleBu… | tests/test_telemetry.py::test_each_turn_carries_its_own_rate_and_tool_durations, ::test_no_usage_yields_label… | Wartedauer und 'erste sichtbare Rückmeldung' nur in perf_progress/perceived_speed, nicht im Telemetrierekord; konsolidiertes Gesamtbudget über Jev/Voice/Worker/Builder nicht als eine Sicht belegt |
| PERF-02 | teilweise | src/skaldir/perf_runtime.py:28 (kalt/warm über Caches); tests/test_perf_gv_harness.py, tests/test_perf_codex_compare.py als Messgerüst | tests/test_perf_cli_budget.py::test_version_within_budget, ::test_memory_budget_measured_from_a_clean_process… | Kein dokumentiertes Baseline-Protokoll mit p50/p95 und Fallzahlen über repräsentative Aufgaben im Repo; kalt/warm wird gemessen, aber nicht als Baseline ausgewiesen |
| PERF-03 | teilweise | src/skaldir/perf_runtime.py:28 (Connection-Reuse), src/skaldir/prompt_cache.py, src/skaldir/system_one_cache.py; src/skaldir/buildflow.py:221 (Wiederverwendung statt Neu… | tests/test_perf_runtime.py::test_http_client_is_reused_for_the_same_options, ::test_two_provider_calls_share_… | Die geforderte Prioritätsreihenfolge (Schleifen→Dispatch→Parallelität→Subtools→Schemas→Daten→Caches) ist nicht als dokumentierte/verifizierte Vorgehensweise belegt |
| PERF-04 | teilweise | src/skaldir/perf_progress.py (Tick-Produzent), src/skaldir/perf_runtime.py:28 (Render-Bound als Frame-Regler) | tests/test_perceived_speed.py::test_the_acknowledgment_line_is_immediate_and_unbounded, ::test_the_felt_laten… | Die Zielwerte (<100 ms UI-Quittierung, Notstopp-Schedulerreaktion) wurden nicht unter dokumentierter Last geprüft; Notstopp-Zeit bis zur letzten unerwünschten Eingabe wird nicht gemessen; kein Lastprotokoll |
| PERF-05 | getestet | src/skaldir/budget.py:110 (BudgetAccount, Rollenbudgets); src/skaldir/runs_store.py:579-644 (persistentes Budget); src/skaldir/goal_run.py:91 (GoalRunOptions) | tests/test_runs_store.py::test_budget_reserve_spend_release_keeps_the_invariant, ::test_budget_refuses_over_c… | Konkrete Nutzerlimits sind laut Anforderung selbst noch offen; Zeitsperren (nur Token-/Kostenbudgets) nicht belegt |
| QA-01 | teilweise | tests/ (Suite mit Abnahmenamen wie tests/test_testing_result_contract.py, tests/test_golden_set.py); docs/masterplan/02-ABNAHME.md | tests/test_golden_set.py, tests/test_testing_result_contract.py::test_missing_verdict_fails_closed | Keine maschinenlesbare Zuordnungstabelle Anforderungs-ID→Test nachgewiesen; die Zuordnung dieser Matrix ist der erste Schritt, nicht dokumentierter Stand |
| QA-02 | teilweise | docs/masterplan/02-ABNAHME.md; tests/test_release_extras.py, tests/test_packaging.py als Release-Prüfungen | tests/test_release_extras.py, tests/test_packaging_install.py, tests/test_packaging.py | Verfahren (Einzel-Fix→Regression, Zusammenhang→Integration, Release→Projektchecks) ist Prozessvorgabe ohne automatisierten Nachweis; kein flock-/Parallel-Lauf-Schutztest gefunden |
| QA-03 | teilweise | tests/test_testing_result_contract.py (echter Exitstatus/Verdict-Vertrag); tests/test_testing_tool_stats.py | tests/test_testing_result_contract.py::test_explicit_fail_at_exit_zero_is_not_a_pass, ::test_nonzero_exit_nev… | 'Testlogs vollständig, Fortschritt sichtbar, kein tail-Pipeline-Nachweis' ist eine Arbeitsvorgabe ohne Codebasis; Fixture-Kennzeichnung nur durch tests/fixtures/-Verzeichnis |
| QA-04 | getestet | tests/conftest.py:1-40 (Fingerprint-Guard für echtes ~/.hermes, Throwaway-HERMES_HOME je Session) | tests/test_env_leak_guard.py, tests/test_session_isolation.py | Für irreversible/finanzielle Testeffekte existiert keine ausdrücklich freigegebene Testumgebung-Dokumentation; Schutz von Browserprofilen/Kontoaktionen nicht abgebildet |
| QA-05 | getestet | src/skaldir/cli.py:3671 (prompt-basierter Einstieg), src/skaldir/cli_contract.py (contract dump/selftest), src/skaldir/slash.py (Slash-Palette), src/skaldir/complete.py | tests/test_cli_contract_dump.py, tests/test_cli_contract_selftest.py, tests/test_slash_palette.py, tests/test… | Keine |
| QA-06 | teilweise | docs/audit/WORKLOG.md, docs/audit/MIGRATION-MANIFEST.md, docs/masterplan/04-AUDIT-UND-UEBERGABE.md |  | Finaler Bericht pro Anforderung mit Testbefehl/-umgebung/-revision, Resultat und Restgrenze liegt nicht für alle 134 IDs vor; Migrations-/Rollbackhinweise und Performancevergleich nur in Teilbereichen (MIGRATION-MANIFES… |

## Anhang C4. UI-01…08 — Korrektur zum Teilergebnis

Die Detailprüfung für UI-01…08 hat **ausschließlich** die vorhandene Chat-Fläche
(`chatbar.py`, `chat_screen.py`, `repl.py`) untersucht und dort zutreffend festgestellt:
kein Split-Rendering, kein aufklappbarer Call, kein Approval-Tab-Wechsel, kein
Eck-Indikator. Das ist für diese Dateien richtig und bleibt als Befund stehen.

**Unvollständig** ist das Ergebnis, weil `src/skaldir/tui.py` — in dieser Sitzung
neu implementiert — nicht in die Suche einbezogen wurde. Die bestätigte
Vollbild-TUI liegt dort und ist getestet.

| ID | Status | Beleg in `tui.py` (+ Test) | Restlücke |
|---|---|---|---|
| UI-01 | **getestet** | `layout()`/`Screen.render` erzeugen das bestätigte geteilte Vollbild; `skaldir tui layout --width 100 --height 30` → `regime: split`, `chat_is_left: true`, `tools_above_goal: true` | Anbindung an den Live-Eventstrom |
| UI-02 | **getestet** | `chat_lines`/`tools_lines`/`goal_lines` als getrennte Bereiche; `GoalPanel` mit Kriterien, offenen Punkten, Blockern, `verify` | Goal-Revisionen aufklappbar |
| UI-03 | **getestet** | `TuiState.cursor`, `at_live_edge`, `new_events` + „N new events — return to live“ | Reattach an echten Event-Cursor |
| UI-04 | **getestet** | `tab_action(prompt, completion_open)` → `cycle_approval`/`accept_completion`; `ApprovalMode.next()` | Einbindung in `chatbar.read_key` |
| UI-05 | **getestet** | `sanitize()` entfernt Control-Sequenzen **vor** der Kürzung; `_ERR_MARK` = Symbol **und** Wort; ASCII-Faltung ohne ein nicht-ASCII-Zeichen | NO_COLOR-Live-Prüfung |
| UI-06 | **getestet** | `figure_frame(at)` deterministisch, `FIGURE_FRAMES`, `humor_at()` selten und leer meistens; nie Prozentwerte | Live-PTY-Animation |
| UI-07 | **getestet** | `sanitize(text, limit=…)` begrenzt und markiert; keine Control-Sequenz aus Tool-Daten | Secret-Redaktion in Details/Export |
| UI-08 | **getestet** | `CallRow.preview` + `CallRow.expanded`/`detail_lines()` (Argumente, Exit, Verification, Details); `desktop_indicator()` als Eck-Statusleiste | Panegrößen live |

Die Gegenaussage ist damit präzise: **die Chat-Fläche `chatbar.py` hat das bestätigte
Layout nicht — `tui.py` hat es.** Kein Vertrag wurde doppelt gebaut; `tui.py` rendert
eine Zustandsliste und führt nichts aus (UI-01: „Keine zweite Ausführungslogik im UI“).

**Aus C3 übernommene Korrektur:** UI-08 nennt „Hostindikator als kleine Statusleiste
in einer Ecke fehlt“. Für `chatbar.py` richtig; `tui.py:desktop_indicator()` und
`desktop_lease.indicator_lines()` liefern genau diese drei Zeilen und sind getestet
(`tests/test_desktop_lease.py::test_the_indicator_shows_the_task_and_the_stop_hint`).

---

## Anhang E. Zusammenfassung der aus C3 folgenden Prioritäten

| Priorität | Befund |
|---|---|
| 1 | **SCHED-01..04 `fehlt`** — kein Zeitplanmodul (keine Zeitzone, keine persistente Ausführungs-ID, keine Überlappungsvermeidung). Größte zusammenhängende Lücke im Gesamtbild. |
| 2 | **FLOW-04 `teilweise`** — keine natursprachliche Workflow-Erkennung („Starte meinen Stock-Research-Workflow“), keine Parameterergänzung, keine gezielte Klärung bei Mehrdeutigkeit. |
| 3 | **DOCKER-05 `fehlt`** — keine Positionierung zu Zugriffsrechten/Nutzungsbedingungen der Beispiel-Tools. |
| 4 | **MEM-03 `teilweise`** — die dritte Kernfunktion (kurze Frage auf ausgewählte Dokumente mit Antwort in Originalreferenzen) ist nicht implementiert. |
| 5 | **GOAL-02 / RUN-03 / UI-04-Anbindung** — gezieltes Nachfragen, Doppel-Escape→Pausenstatus, Tab-Einbindung in die bestehende Eingabeschleife. |

Diese Reihenfolge ist ein Arbeitsvorschlag aus dem Befund, keine vom Nutzer bestätigte
Priorität. SCHED erfordert eine eigene Etappe und sollte nicht nebenbei entstehen.
