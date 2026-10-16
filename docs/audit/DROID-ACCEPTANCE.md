# DROID-ACCEPTANCE — Abschlussbericht der Mission (Skaldir Agent 1.5.0)

- **Mission-ID:** `bd0343b1-8ba4-44e5-b350-f725c4854f7c`
- **Projekt:** Skaldir Agent (`/home/willy/Agentic_Projects/Skaldir_Agent`)
- **Stand:** 2026-10-16 · Branch `main`
- **Referenzmatrix:** [`docs/audit/DROID-FEATURE-MATRIX.md`](DROID-FEATURE-MATRIX.md) (206 ID-Zeilen, 0 Duplikate, `AUDIT_TESTFILES_CHECKED 119`)
- **Übernahmedokument:** [`docs/audit/DROID-TAKEOVER.md`](DROID-TAKEOVER.md)

---

## 1. Tatsächliche Änderungen (Dateien & Commits M1–M6)

Seit Übernahme (`95b69d6`) wurden folgende produktive Commits auf `main` eingebracht:

| Commit | Meilenstein / Bereich | Geänderte Dateien & Wirkung |
|---|---|---|
| `69acba2`, `715447d`, `f667366`, `829acdb` | **M1/M2 (P0 Input & Calc)** | `src/skaldir/input_backend.py`, `src/skaldir/cu_host_session.py`, `src/skaldir/amd_ods_builder.py`, `tests/test_input_backend.py`, `tests/test_live_keyboard_backend_gate.py`, `tests/test_live_desktop_launch_env.py`: 6 P0-Eingabedefekte behoben, ehrliche Zustandsmaschine (`BACKEND_MISSING` / `CONSENT_MISSING` / `PROBE_FAILED` / `INPUT_SENT` / `TARGET_OBSERVED` / `APP_EFFECT_VERIFIED`), typisierte ODS-Erzeugung (`AMD_Latest_Stock_*.ods`). |
| `04e1098` .. `03cf974` | **M3/M4 (Harness & Web/Browser)** | `src/skaldir/agent_loop.py`, `src/skaldir/tools_core/bridge.py`, `src/skaldir/tools_core/web.py`, `src/skaldir/browser_layer.py`, `src/skaldir/processes.py`: Chat-Standardtest live belegt (`HARNESS-CHAT-STANDARDTEST.md`), schlüsselfreie Web-Suche (`ddg`/`brave`/`bing`/`wikipedia`), Single-Launch Browser-Session-Guard. |
| `5be3207`, `957cc28` | **M5 (`VAL-TOOL-001`)** | `docker/market-research-dashboard/`, `src/skaldir/container_dashboard_service.py`, `src/skaldir/toolbuilder.py`: Docker-Tool `market_research_snapshot` (`skaldir-tool-market-research:v1`, Port 3400) gebaut, getestet, registriert (`DOCKER-BUILDER-EVIDENCE.md`). |
| `d2dad75`, `d5612d9` | **M5 (`VAL-TOOL-002`)** | `src/skaldir/act_service.py`, `src/skaldir/capability.py`, `docs/audit/ABNAHME-GUI-SUBTOOL.md`: Parametrisierter GUI-Subtool-Lifecycle (`subtool.zen_capture_window`) inkl. zweier Parametersätze (`alpha`/`beta`) live verifiziert. |
| `ca97ece`, `4d03158` | **M6 (`VAL-MEM-001` / `MEM-02`)** | `SOUL.md`, `docs/audit/DROID-FEATURE-MATRIX.md`: `SOUL.md` im Repo-Root (`## Role`, `## Rules`, `## Conventions`, `## Commands`) angelegt und über `skaldir context show` in die Prompt-Ladder eingebunden. |
| `5448373`, `40a5b8e` | **M5/M6 (`VAL-TOOL-005`, `tool-surface-honesty`)** | `src/skaldir/goal_run.py`, `src/skaldir/agent_loop.py`, `src/skaldir/completion.py`, `src/skaldir/tools_core/terminal.py`, `src/skaldir/tools_core/process.py`, `tools/tool_reuse_live_proof.py`, `docs/audit/ABNAHME-TOOL-REUSE.md`: `tool_search`, `knowledge_search`, `knowledge_read` und `KIND_CONTAINER`-Tools im Agent-Loop/Goal-Run verdrahtet (`FIND-M3-UT-02/03/04/05/07`); Live-Reuse von `market_research_snapshot` in frischem Agenten-Run bewiesen. |
| `d95f63e` | **M5 (`VAL-TOOL-003`)** | `src/skaldir/compose_service.py`, `src/skaldir/tools_call_cli.py`, `docs/audit/ABNAHME-COMPOSE-WORKFLOW.md`: False-Positive Error-Check bei leerem `ToolResult.error` behoben; heterogener Workflow `mixed_docker_tool` (`docker` + Python Registry `tool`) live ausgeführt. |
| `76211e1` | **M5 (`VAL-TOOL-004`)** | `docs/audit/ABNAHME-SCHEDULING.md`: Live-CLI-Nachweis für `Europe/Berlin`-Scheduling, headless `schedule run --ticks 1` und Ledger-Deduplizierung (`exec-9d6e1e7f8c60`). |
| `b6a23f3`, `0b693d9` | **M6 (`VAL-STEER-001`, `VAL-APR-001`, `VAL-CU-001`, `VAL-CREATIVE-001/002`)** | `src/skaldir/surface_cli.py`, `src/skaldir/workflows.py`, `src/skaldir/chatbar.py`, `tests/fixtures/video/kdenlive_acceptance.sh`, `docs/audit/ABNAHME-M6-STEER-CU-KREATIV-UI.md`: Xlib-First Window-Capture in `workflows.screenshot`, Tab-Umschaltung der Approval-Modi in `ChatBar` bei leerem Prompt, `stop detect` Origin-Guard, Kdenlive/FFmpeg-Export und Blender-Headless-Abnahme. |

---

## 2. Produktiv nutzbare Fähigkeiten (CLI / Chat / Runtime)

Über die installierte CLI (`skaldir` / `uv run skaldir`) sind produktiv nutzbar und belegt:

1. **Coding-Harness & Prozesse (`A`):** Datei-/Patch-/Test-/Code-Exec-Tools (`read_file`, `write_file`, `patch`, `search_files`, `list_dir`, `terminal`, `process`, `run_code`), parallele Tool-Batches und Prozessgruppen-Cleanup (`docs/audit/HARNESS-CHAT-STANDARDTEST.md`).
2. **Schlüsselfreies Web & Browser-Session-Guard (`B`):** `skaldir web search/fetch` ohne API-Key (DDG/Brave/Bing/Wikipedia), `skaldir browser ensure/status/tabs` mit Single-Launch-Schutz (`docs/audit/WEB-SEARCH-FETCH-LIVE.md`, `docs/audit/BROWSER-SESSION-GUARD.md`).
3. **Subtools, Docker-Toolbuilder, Composite-Workflows & Scheduling (`D`):**
   - Docker-Tool `market_research_snapshot` inkl. localhost-Dashboard auf Port 3400 (`docs/audit/DOCKER-BUILDER-EVIDENCE.md`).
   - Discovery & Aufruf registrierter Container-Tools in einem neuen Agenten-Run via `tool_search` (`docs/audit/ABNAHME-TOOL-REUSE.md`).
   - Parametrisierte GUI-Subtools (`skaldir act learn/run`, `docs/audit/ABNAHME-GUI-SUBTOOL.md`).
   - Gemischte Workflows (`skaldir compose validate/approve/run`, `docs/audit/ABNAHME-COMPOSE-WORKFLOW.md`).
   - Zeitzonen-echtes Scheduling (`Europe/Berlin`) inkl. Catch-up und Ledger-Dedup (`skaldir schedule define/activate/due/run`, `docs/audit/ABNAHME-SCHEDULING.md`).
4. **Goal-Execution, JEV, Memory & Approvals (`E`, `F`, `G`):**
   - Goal-Vertrag mit Verifikations-Gates (`UNKNOWN` wird nie `PASS`), JEV-Kompaktierung (52.3 % Median-Token-Reduktion bei 0 Round-Trip-Fehlern).
   - Projektkontext (`AGENTS.md` + `SOUL.md`) und Hybrid-Knowledge (`knowledge_search`/`knowledge_read`), 5-stufige Kontext-Kompaktierung.
   - 3 Approval-Modi (`Ask Everything`, `Approve for me`, `Approve Everything`), per `Tab` auf leerem Prompt umschaltbar, mit harten Schranken für Ausgaben/Destruktiv-Kommandos (`docs/audit/ABNAHME-M6-STEER-CU-KREATIV-UI.md`).
5. **Steuerung, Cross-Platform Computer-Use (Fedora Host, Omarchy Hyprland, Debian/Ubuntu), Kreativ-Pipelines & TUI (`C`, `H`):**
   - Doppel-Escape-Erkennung (`skaldir stop detect`, Wayland-libinput-Beleg), persistenter Pause-Store, Desktop-Lease mit Fencing (`skaldir desk status`).
   - **Cross-Platform Computer Use (`cu_platform.py`, `input_backend.py`, `cu_host_session.py`, `workflows.py`):**
     - **Fedora Linux (Host — GNOME/Wayland + Xwayland):** Automatische `YDOTOOL_SOCKET`-Auflösung & `ydotoold`-Autostart (`ensure_ydotoold()`) liefern live `INPUT_OBSERVED_AT_TARGET` (`delivers=True`); `GnomeAdapter` mit Xlib/Xwayland-Fallback-Capture (`_xlib_root_screenshot`), `xdotool`/`wmctrl` Fenstersteuerung und `evdev`-Keycode-Übersetzung.
     - **Omarchy Hyprland (Arch + Hyprland Wayland):** Erstklassige Unterstützung für Omarchy/Hyprland (`hyprctl clients -j` / `activewindow -j` inkl. `at`/`size`-Geometrie, `hyprctl dispatch movecursor <x> <y>`, `wlrctl pointer click` / `ydotool click 0xC0`, `wtype` / `hyprctl dispatch sendshortcut`, `grim -s 1` 1:1 logisches Screenshot-Scaling + `hyprshot` + `omarchy`).
     - **Debian / Ubuntu (GNOME Wayland/X11 & X11):** Vollständiger Adapter- und Tool-Stack (`xdotool`, `wmctrl -lGpx`/`-ia`/`-ic`, `xlib_capture`, `scrot`, `maim`, `gnome-screenshot`, `import`, `wtype`, `ydotool`).
   - Blender-Headless-Pipeline (`desk_lamp.blend` + `desk_lamp.png`), Kdenlive/MLT-Video-Pipeline (`project.kdenlive` + `export.mp4`), 3-Pane-TUI-Cockpit (`skaldir tui preview`).

---

## 3. Ausgeführte Tests & Live-Belege (Kommandos & echte Exitcodes)

| Prüfbereich | Kommando | Exitcode | Ergebnis / Belegartefakt |
|---|---|---|---|
| **Matrix-Integrität (`VAL-CROSS-001`)** | `rg -c "^\| [A-Z]+-[0-9]+ " docs/audit/DROID-FEATURE-MATRIX.md` + Duplikat-/Status-/Testdatei-Check | `0` | `206` IDs, `0` Duplikate, `AUDIT_TESTFILES_CHECKED 119` (`docs/audit/DROID-FEATURE-MATRIX.md`) |
| **GUI-Subtool Lifecycle (`VAL-TOOL-002`)** | `uv run pytest tests/test_capability_learning.py tests/test_act_learn.py -q` + Live-Subtool-Run (`alpha`/`beta`) | `0` | `docs/audit/ABNAHME-GUI-SUBTOOL.md`, `logs/gui-subtool-live/` |
| **Tool-Reuse im neuen Run (`VAL-TOOL-005`)** | `uv run python tools/tool_reuse_live_proof.py` | `0` | `logs/tool-reuse-live/20261014-215425.json`, `docs/audit/ABNAHME-TOOL-REUSE.md` |
| **Composite-Workflow (`VAL-TOOL-003`)** | `uv run skaldir compose run --name mixed_docker_tool --json` | `0` | `logs/compose-live/20261014-215931.json`, `docs/audit/ABNAHME-COMPOSE-WORKFLOW.md` |
| **Scheduling & Dedup (`VAL-TOOL-004`)** | `uv run skaldir schedule run --ticks 1 --json` (2×) + `uv run pytest tests/test_scheduler.py tests/test_scheduler_worker.py -q` | `0` | 43/43 Tests grün, Claim `exec-9d6e1e7f8c60` dedupliziert (`docs/audit/ABNAHME-SCHEDULING.md`) |
| **Goal, Voice & JEV (`VAL-GOAL-001..003`)** | `uv run skaldir jev benchmark --json` + `uv run pytest tests/test_completion.py tests/test_goal_run.py tests/test_voice.py tests/test_jev.py -q` | `0` | 129 passed, JEV Median-Reduktion `52.3 %` (`passes_gate: true`) |
| **Memory & Approvals (`VAL-MEM-001/002`, `VAL-APR-001/002`)** | `uv run skaldir context show --json` + `uv run pytest tests/test_project_context.py tests/test_knowledge.py tests/test_compaction.py tests/test_approvals.py tests/test_dangerous_commands.py tests/test_chatbar.py -q` | `0` | 272 passed, `AGENTS.md` + `SOUL.md` geladen |
| **Steering, Cross-Platform CU/Vision & Workflows (`VAL-STEER-001..003`, `VAL-CU-001/002`)** | `uv run skaldir stop detect --gap-ms 250 --origin user --json` + `uv run pytest tests/test_cu_platform.py tests/test_input_backend.py tests/test_stop_signal.py tests/test_stop_dispatch.py tests/test_pause.py tests/test_desktop_lease.py tests/test_desktop_indicator.py tests/test_computer_use.py tests/test_cu_action_verify.py tests/test_vision.py tests/test_vision_service.py tests/test_xlib_capture.py tests/test_workflows.py -q` | `0` | Alle grün; Live auf Fedora Host (`:0`): `input_backend.choose()` → `ydotool` (`INPUT_OBSERVED_AT_TARGET`, `delivers=True`), `GnomeAdapter.screenshot()` + `workflows.screenshot()` (`ok: true, backend: xlib`) |
| **Blender Headless (`VAL-CREATIVE-001`)** | `blender --background --python tests/fixtures/blender/build_desk_lamp.py -- /tmp/blender_out` + Re-Open-Check | `0` | `/tmp/blender_out/desk_lamp.blend` (8 Objekte, 4 Materialien) + `desk_lamp.png` (256 KB) |
| **Kdenlive / Video (`VAL-CREATIVE-002`)** | `bash tests/fixtures/video/kdenlive_acceptance.sh /tmp/kdenlive_out` | `0` | `ABNAHME_D_OK`, `/tmp/kdenlive_out/project.kdenlive` + `export.mp4` (`6.04s`, Video+Audio) |
| **TUI & CLI-Envelopes (`VAL-UI-001..003`)** | `uv run pytest tests/test_tui_app.py tests/test_tui_layout.py tests/test_tui_feed.py tests/test_tui_live.py tests/test_palette.py tests/test_status_view.py tests/test_diff_view.py tests/test_cli_contract_envelope.py -q` | `0` | Alle grün (`docs/audit/ABNAHME-M6-STEER-CU-KREATIV-UI.md`) |

---

## 4. Verbleibende Restlücken, Externe Blocker & Dokumentierte Residuals

Kein offener P0/P1-Punkt wird als erledigt ausgegeben; externe Blocker und bekannte Feinheiten sind präzise abgegrenzt:

1. **Plattform- & Host-Besonderheiten:**
   - **Tastatur-/Mauszustellung unter Wayland/X11 (`CU-03/07`, `VAL-INPUT-007`):** Durch automatische `YDOTOOL_SOCKET`-Erkennung (`/tmp/skaldir-ydotoold/ydotoold.sock`) und `ensure_ydotoold()` liefert `input_backend.choose()` auf dem Fedora-Host live `INPUT_OBSERVED_AT_TARGET` über `ydotool`/uinput. Der optionale Wayland-Portal-/EIS-Weg (`org.gnome.Mutter.RemoteDesktop`) erfordert weiterhin eine einmalige interaktive Nutzerzustimmung (`CONSENT_MISSING`), falls kein `/dev/uinput`-Zugriff besteht.
   - **Bare-Start vs. Vollbild-TUI (`UI-01`):** `skaldir` ohne Argument startet bewusst den zwei-zeiligen Chat (`repl.launch_from_bare_call`, Commit `d5ac4e3`); das Drei-Bereich-TUI liegt unter `skaldir tui`.
   - **Voice-Live-Verdrahtung (`VOICE-01`, `T-42/43`):** `voice_service` und `voice_rebrief` sind implementiert und unit-getestet, aber nicht als automatischer Hintergrund-Interventionsloop in `agent_loop` aktiv geschaltet.
2. **Verifizierte Orchestrator-Residuals `(a)`–`(g)`:**
   - **(a) Browser `ensure()` Doppel-Liveness-Probe & `discover()`-Diagnose:** `browser_layer.ensure()` prüft Liveness zweimal (Race kann `reused` mit `live=False` liefern), und ein wiederholtes `discover()` gibt die persistierte Reconciliation-Diagnose nicht erneut aus (kosmetisch/informativ, M4-Scrutiny).
   - **(b) `browser tabs --json` vs. Transport-Tabs:** Meldet für rein transport-seitig geöffnete Tabs ein leeres Inventar, während `detach` die Transport-Wahrheit sieht (dokumentierte Nuance in `browser_layer.py`).
   - **(c) Bing-Such-Antworten:** Bing liefert unter `ok=true` vereinzelt generische organische Treffer (Anti-Bot-Verhalten der Gegenstelle; Parser arbeitet wahrheitsgetreu ohne Halluzination, Fallback über DDG/Brave/Wikipedia aktiv).
   - **(d) ToolBuilder Live-Provider Draft-Call:** Aus Budget-Gründen nicht gegen einen kostenpflichtigen Live-Provider geschossen; die `F07`-Client-Injektion ist Ende-zu-Ende über `agent run --agent-tools` gegen den Run-Stub-Endpunkt bewiesen (`BUILD-03`).
   - **(e) `RunConfig.validate` Port-Gate-Meldung:** Verweigert Port-Mappings unter `--network none` aktiv; der Hinweis könnte den erlaubten Weg (Ports ohne expliziten Network-Override deklarieren, sodass `none → bridge` promoviert wird) noch expliziter nennen.
   - **(f) `inspect_container_surface`:** Listet ausführbare Binaries im Container-`PATH`; Subcommand-basierte Werkzeuge benötigen ein Shim-Binary.
   - **(g) Umgebungs- & Altbestands-Nuancen:** Optionale Wayland-Portal-/EIS-Zustimmung (`CONSENT_MISSING`), Laufzeitabhängigkeit `xmodmap`, Yahoo-Snapshot-Differenz `630.99` (Schlusskurs) vs. `630.63` (`regularMarketPrice`) in `QUELLENPRUEFUNG-AMD.md` sowie ~74 vorbestehende, bewusst ungegatete Ruff-Hinweise unter `tools/`.

---

## 5. Reproduzierbare Start- & Abnahmeschritte

```bash
cd /home/willy/Agentic_Projects/Skaldir_Agent

# 1. CLI & Selftest prüfen
uv run skaldir --version
uv run skaldir selftest --json

# 2. Matrix-Integrität prüfen (206 ID-Zeilen, 0 Duplikate, alle Testdateien existent)
rg -c "^\| [A-Z]+-[0-9]+ " docs/audit/DROID-FEATURE-MATRIX.md
rg -o "^\| [A-Z]+-[0-9]+" docs/audit/DROID-FEATURE-MATRIX.md | sed "s/^| //" | sort | uniq -d
python3 -c "import re, pathlib; m=pathlib.Path('docs/audit/DROID-FEATURE-MATRIX.md').read_text(); files=sorted(set(re.findall(r'tests/test_[a-z0-9_]+\.py', m))); missing=[f for f in files if not pathlib.Path(f).exists()]; assert not missing, missing; print('AUDIT_TESTFILES_CHECKED', len(files))"

# 3. M5/M6 Live-Nachweise reproduzieren
uv run python tools/tool_reuse_live_proof.py
uv run skaldir compose run --name mixed_docker_tool --json
uv run skaldir jev benchmark --json
uv run skaldir stop detect --gap-ms 250 --origin user --json
blender --background --python tests/fixtures/blender/build_desk_lamp.py -- /tmp/blender_out
bash tests/fixtures/video/kdenlive_acceptance.sh /tmp/kdenlive_out
```

---

## 6. Git-Zustand inkl. uncommitteter Laufzeitdateien

- **Branch:** `main` (keine Pushes/Veröffentlichungen durchgeführt).
- **Sauberkeit der Quell- und Testpfade:** Alle Änderungen unter `src/skaldir/`, `tests/`, `docs/` und `SOUL.md` sind sauber committet und `ruff check` ist fehlerfrei.
- **Bewusst uncommittete Laufzeit-/Lock-Artefakte im Arbeitsbaum** (aus Vorlauf/Nutzerbetrieb, gemäß `AGENTS.md` nicht überschrieben oder zurückgesetzt):
  - `cu_runs.jsonl` (lokales Computer-Use-Laufprotokoll)
  - `events.db` (lokale SQLite-Event-Datenbank)
  - `uv.lock` (vorbestehende Lockfile-Aktualisierung)
