# Abnahme M6: Goal/Voice/JEV, Memory/Approvals, Steering, CU/Vision, Creative & TUI

**Datum:** 2026-10-16  
**Milestone:** M6 (`goal-voice-jev`, `memory-compaction`, `approvals-enforcement`, `stop-lease-live`, `cu-actions-vision`, `blender-acceptance`, `video-acceptance`, `tui-goal-picture`, `tool-surface-honesty`)

---

## 1. Goal Contract, Voice Escalation & JEV (`VAL-GOAL-001..003`)

- **Completion Contract Lifecycle (`VAL-GOAL-001`):**
  - Implementiert in `src/skaldir/completion.py` (`CompletionStore`, `run_verification_gates`) und `src/skaldir/goal_run.py` (`run_goal`, `_finish_goal`).
  - Terminal-State-Guard in `goal_run._finish_goal` verhindert erneute Transition aus `VERIFIED_COMPLETE`; Gate-Output-Cap in `completion.py` auf 2048 Zeichen angehoben (`FIND-M3-UT-05`).
- **Voice Budget & Escalation (`VAL-GOAL-002`):**
  - Implementiert in `src/skaldir/voice.py` (`VoiceBudget`, `EscalationRouter`, `MAX_UTTERANCES_PER_GOAL = 2`, ≤ 35 Wörter) + `src/skaldir/tts.py` / `src/skaldir/stt.py`.
- **JEV Token Efficiency (`VAL-GOAL-003`):**
  - Implementiert in `src/skaldir/jev/` (`codec.py`, `dictionary.py`, `validate.py`, `token_counter.py`, `ab_harness.py`) + `src/skaldir/jevcomp/cli.py`.
  - Live-CLI: `uv run skaldir jev benchmark --json` → `median_reduction_pct: 52.3`, `p95_reduction_pct: 57.5`, `passes_gate: true` (≥ 35 % Gate erfüllt, 0 Round-Trip-Fehler über 30 Payloads).
- **Tests:** `uv run pytest tests/test_completion.py tests/test_goal_run.py tests/test_voice.py tests/test_jev.py -q` → **129 passed**.

---

## 2. Memory, Compaction & Approvals (`VAL-MEM-001/002`, `VAL-APR-001/002`)

- **Project Context & `SOUL.md` (`VAL-MEM-001`):**
  - `SOUL.md` im Repo-Root angelegt (`## Role`, `## Rules`, `## Conventions`, `## Commands`); wird gemeinsam mit `AGENTS.md` über `skaldir.project_context.discover_context_files()` geladen (`uv run skaldir context show --json` → `files: ["AGENTS.md", "SOUL.md"]`, `truncated: false`).
  - Hybrid-Retrieval (`BM25 + Dense + RRF`) in `src/skaldir/knowledge.py`, `memory_store.py`, `retrieval.py`; `knowledge_search` und `knowledge_read` sind in `goal_run.turn_tools` und `agent_loop.tool_set_for("builtin")` verdrahtet.
- **5-Stage Context Compaction (`VAL-MEM-002`):**
  - Implementiert in `src/skaldir/compaction.py` (`ContextCompressor`, Stages 1–5 inkl. Head/Tail-Pinning, Tool-Pairing-Invariante und Identifier-Preservation).
- **Approvals & Tab-Mode-Cycling (`VAL-APR-001/002`):**
  - Implementiert in `src/skaldir/approvals.py` (`ApprovalEngine`, `ApprovalProfile`: `SAFE`, `STANDARD`, `FULL_AUTO`, `UNRESTRICTED`), `src/skaldir/dangerous_commands.py` und `src/skaldir/chatbar.py` (`ChatBar.cycle_approval_mode()` bei `Tab` auf leerem Prompt).
  - Live-CLI:
    - `uv run skaldir approvals check --tool terminal --command "rm -rf /tmp/x" --mode full_auto --json` → `outcome: "prompt"` (Destructive-Guard aktiv trotz `full_auto`).
    - `uv run skaldir approvals check --tool read_file --mode safe --json` → `outcome: "allow"`.
- **Tests:** `uv run pytest tests/test_project_context.py tests/test_knowledge.py tests/test_compaction.py tests/test_approvals.py tests/test_dangerous_commands.py tests/test_chatbar.py -q` → **272 passed**.

---

## 3. Steering, Stop-Signal & Desktop Lease (`VAL-STEER-001..003`)

- **Double-ESC User-Origin Stop (`VAL-STEER-001`):**
  - Implementiert in `src/skaldir/stop_signal.py` (`DoubleEscDetector`, Window `120..450ms`, `origin == "user"` Filter, Autorepeat-Guard) + `src/skaldir/surface_cli.py:_cmd_stop_detect`.
  - Live-CLI:
    - `uv run skaldir stop detect --gap-ms 250 --origin user --json` → `{"stopped": true, "gap_ms": 250}`.
    - `uv run skaldir stop detect --gap-ms 250 --origin agent --json` → `{"stopped": false}` (Self-Trigger ausgeschlossen).
    - `uv run skaldir stop detect --gap-ms 250 --origin user --autorepeat-at 1 --json` → `{"stopped": false}`.
- **Pause & Steer Injection (`VAL-STEER-002`) & Desktop Lease (`VAL-STEER-003`):**
  - Implementiert in `src/skaldir/pause.py`, `src/skaldir/stop_dispatch.py`, `src/skaldir/desktop_lease.py` und `src/skaldir/desktop_indicator.py`.
- **Tests:** `uv run pytest tests/test_stop_signal.py tests/test_stop_dispatch.py tests/test_pause.py tests/test_desktop_lease.py tests/test_desktop_indicator.py -q` → **128 passed**.

---

## 4. Computer Use & Vision (`VAL-CU-001/002`) — Cross-Platform (Fedora Host, Omarchy Hyprland, Debian/Ubuntu)

- **Cross-Platform Adapter & Input Backend (`src/skaldir/cu_platform.py`, `src/skaldir/input_backend.py`, `src/skaldir/cu_host_session.py`, `src/skaldir/workflows.py`):**
  - **Fedora Linux (Host — GNOME/Wayland + Xwayland):**
    - `input_backend.py`: `active_ydotool_socket()` und `ensure_ydotoold()` starten/nutzen automatisch `/tmp/skaldir-ydotoold/ydotoold.sock` (oder `$XDG_RUNTIME_DIR/.ydotool_socket`) und injizieren `YDOTOOL_SOCKET` direkt in `probe("ydotool")` sowie in `cu_platform._run()`.
    - Live-Verifikation auf Fedora 44 Host (`DISPLAY=:0`): `input_backend.choose(timeout=4.0)` erreicht ohne manuelle Env-Vorbereitung `backend="ydotool"`, `state=ProbeState.INPUT_OBSERVED_AT_TARGET` (`delivers=True`).
    - `GnomeAdapter`: Fenster-Management via `xdotool` (`windowactivate --sync` + `windowfocus --sync` + Geometrie) → `wmctrl` (`-lGpx`, `-ia`, `-ic`) → `gdbus` GNOME Shell Introspect; `ydotool` nutzt korrekte CLI-Syntax (`mousemove -a -x X -y Y`, `click 0xC0/0x40/0x80` Hex-Masken, numerische `evdev`-Keycodes via `skaldir.evdev_keys`); Screenshot via `_xlib_root_screenshot` (inkl. Fallback auf das größte sichtbare `InputOutput`-Xwayland-Fenster bei `BadMatch` auf Root `:0`) → `grim` → `scrot` → `maim` → `gnome-screenshot` → `import`.
  - **Omarchy Hyprland (Arch + Hyprland Wayland):**
    - Distro-/Compositor-Erkennung für Omarchy (`OMARCHY_PATH`, `~/.local/share/omarchy`, `/etc/omarchy-release`) und Hyprland (`HYPRLAND_INSTANCE_SIGNATURE`, `XDG_CURRENT_DESKTOP`).
    - `HyprlandAdapter`: Fenster-Liste & Fokus inkl. `at`/`size`-Geometrie über `hyprctl clients -j` und `hyprctl activewindow -j` (Selector-Normalisierung für `address:0x...`, `pid:...`, `title:...`); Mausbewegung nativ und rootless über `hyprctl dispatch movecursor <x> <y>` → `ydotool` → `xdotool`; Mausklick über `wlrctl pointer click` → `ydotool click 0xC0` → `xdotool`; Tastatur & Text über `wtype` → `hyprctl dispatch sendshortcut` / `wl-copy` → `ydotool` (`evdev`-Keycodes); Screenshots über `grim -s 1 <path>` (1:1 logische Koordinaten passend zu `hyprctl at/size`) → `grim` → `hyprshot` → `omarchy`.
  - **Debian / Ubuntu (GNOME Wayland/X11 & klassische X11-Desktops):**
    - Distro-Erkennung über `/etc/os-release` (`ID=debian|ubuntu` sowie `ID_LIKE`); vollständiger Fallback-Stack in `GnomeAdapter` und `X11Adapter` über `xdotool` ↔ `wmctrl -lGpx`/`-ia`/`-ic`, `_xlib_root_screenshot` ↔ `scrot`/`maim`/`gnome-screenshot`/`import`/`xwd`, sowie `wtype`/`ydotool`/`xdotool`.
- **Window-First X11 Capture (`cu-actions-vision` Fix):**
  - `src/skaldir/workflows.py::screenshot` nutzt primär `skaldir.xlib_capture.capture_window_image` + `_xlib_root_screenshot`-Fallback + `skaldir.computer_use.encode_png` (`backend: "xlib"`).
  - Live-Verifikation auf Fedora Host (`DISPLAY=:0`): `GnomeAdapter.list_windows()` (17 Fenster mit Geometrie), `GnomeAdapter.screenshot()` (`ok: true`) und `workflows.screenshot()` (`{"ok": true, "backend": "xlib", "size": 289}`).
- **Actions, ActionVerify & Grounding (`VAL-CU-001/002`):**
  - Implementiert in `src/skaldir/computer_use.py`, `src/skaldir/cu_action_verify.py`, `src/skaldir/vision.py` und `src/skaldir/vision_service.py`.
- **Tests:** `uv run pytest tests/test_cu_platform.py tests/test_computer_use.py tests/test_cu_action_verify.py tests/test_vision.py tests/test_vision_service.py tests/test_xlib_capture.py tests/test_workflows.py -q` → **alle grün**.

---

## 5. Creative Pipelines (`VAL-CREATIVE-001/002`)

- **Blender Headless 3D-Pipeline (`VAL-CREATIVE-001`):**
  - Kommando: `blender --background --python tests/fixtures/blender/build_desk_lamp.py -- /tmp/blender_out` (Blender 5.2.2 LTS).
  - Artefakte: `/tmp/blender_out/desk_lamp.blend` (`109.261 Bytes`), `/tmp/blender_out/desk_lamp.png` (`256.453 Bytes`, `800x600`, Non-Empty-Render).
  - Re-Open-Check via Blender Headless: 8 benannte Objekte (`lamp.base`, `lamp.bulb`, `lamp.camera`, `lamp.ground`, `lamp.key`, `lamp.light`, `lamp.shade`, `lamp.stem`) und 4 Materialien (`lamp.bulb`, `lamp.ground`, `lamp.metal`, `lamp.shade`).
- **Kdenlive / MLT Video-Pipeline (`VAL-CREATIVE-002`):**
  - `tests/fixtures/video/kdenlive_acceptance.sh` repariert (`</kdenlive>` Tag-Match + `ffmpeg` Render-Fallback wenn `melt` nicht installiert ist).
  - Live-Lauf: `bash tests/fixtures/video/kdenlive_acceptance.sh /tmp/kdenlive_out` → Exit `0` (`ABNAHME_D_OK`), erzeugt `/tmp/kdenlive_out/project.kdenlive` (3 Clips, Crossfades, Text-Overlay) und `/tmp/kdenlive_out/export.mp4` (`duration=6.040000s`, `codec_type=video` H.264 + `codec_type=audio` AAC).

---

## 6. TUI Goal Picture (`VAL-UI-001..003`)

- **Cockpit Layout, Palette & Live Event Bridge:**
  - Implementiert in `src/skaldir/tui.py`, `src/skaldir/tui_app.py`, `src/skaldir/tui_live.py`, `src/skaldir/chatbar.py`, `src/skaldir/palette.py`, `src/skaldir/status_view.py`, `src/skaldir/diff_view.py`, `src/skaldir/SessionState.py`.
- **Tests:** `uv run pytest tests/test_tui_app.py tests/test_tui_layout.py tests/test_tui_feed.py tests/test_tui_live.py tests/test_palette.py tests/test_status_view.py tests/test_diff_view.py -q` → **alle grün**.
