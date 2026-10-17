# Skaldir Agent

[![Release](https://img.shields.io/badge/release-v1.5.0-blue.svg)](https://github.com/bertus37987/skaldir)
[![Python](https://img.shields.io/badge/python-3.11+-brightgreen.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![Architecture](https://img.shields.io/badge/layers-8%20autonomous%20layers-orange.svg)](docs/CODEBASE_DOCUMENTATION.md)
[![Documentation](https://img.shields.io/badge/handbook-CODEBASE__DOCUMENTATION.md-purple.svg)](docs/CODEBASE_DOCUMENTATION.md)

Self-learning agent runtime: supervisory control, incident memory, dynamic skills & tools, isolated computer use, and permission ladders.

Distribution `skaldir-agent` · executable `skaldir` (and `godsvoice` compatibility alias) · `skaldir 1.5.0`, contract `schema_version 1.0`.

---

## Technical Handbook & Documentation

📖 **The complete, authoritative codebase manual is available at [docs/CODEBASE_DOCUMENTATION.md](docs/CODEBASE_DOCUMENTATION.md)**.  
It details the architecture, class structures, sequence diagrams, failure modes, cross-platform display matrices, and security models across all eight layers.

---

## The Surface: Five Taught Entries

`skaldir --help` (exit 0) prints exactly what a session is taught, and nothing else — the rest of the product lives in the chat behind `/`:

```
skaldir 1.5.0 - the Skaldir CLI (65 root commands, 267 leaf commands in this build)

  the commands a session is taught
    skaldir                  the chat: the mark, the band, the two bars, the palette
    skaldir setup            one-time setup: the two keys, each proved by a call
    skaldir run "<goal>"     sets the goal and opens the chat attached to it
    skaldir serve            the API surface for editors and GUIs (--http)
    --help                   this screen; --version; --all-commands

  everything else lives in the chat
    /help                    every slash verb, grouped, with its command
    /sessions /resume /goal /run /steer /queue /stop /status
    /verify /runs /checkpoint /context /model /provider /tools
    /memory /agents /jev /host /docker   (all of them: /help)
    skaldir chat verbs --json      each slash verb + the command it runs

  the full surfaces
    skaldir --all-commands        every root command this build carries
    skaldir ? <command>          the overlay: usage, flags, exit codes
    skaldir <command> --help     that command's own parser text
    skaldir contract dump --json the whole inventory + a JSON schema each
    (62 more root command(s) stay reachable - nothing was removed from this build;
     they are / verbs in the chat)
```

All 65 root commands are fully contracted (`unregistered: []`) and accessible via CLI or the 101 slash verbs in the interactive chat. See [`docs/SURFACE.md`](docs/SURFACE.md) for the complete surface inventory.

---

## Core Guarantees & Philosophy

- **Eight Named Layers, One CLI Group Each**: EXPAND, ACT, COMPOSE, ROUTE, SUPERVISE, ENFORCE, VERIFY, PERSIST. `skaldir selftest --json` probes every layer read-only.
- **One JSON Envelope Per Command**: `--json` emits exactly one document on stdout (`schema_version, ok, command, godsvoice_version, started_at, duration_ms, exit_code, data, error, evidence, warnings, next_actions`) with human diagnostics routed to stderr.
- **Nine Invariant Exit Codes**: `0` OK · `1` Usage · `2` Verification Failed · `3` Blocked · `4` Needs Credentials · `5` Not Found · `6` Conflict · `7` Internal · `8` Unknown.
- **No Success Without Evidence**: Any handler or agent run reporting `ok: true` without an attached evidence payload is downgraded to exit 7.
- **Cross-Platform Isolated Computer Use**: Drives Linux desktops across Fedora GNOME (Wayland + Xwayland), Omarchy (Hyprland / wlroots), and Debian/Ubuntu (X11) via sandboxed Xvfb display bridges, uinput event synthesis, and `agent-browser` CDP automation.
- **Deterministic Voice Guard & Policy Ladders**: Pre-execution security filter preventing directory traversal, production mutation, credential leakage, and prompt injection. Three configurable approval modes: `interactive`, `auto_read`, and `autonomous`.
- **Credentials as Handles**: Raw secrets never leave the encrypted vault; isolated runners receive scoped handles (`secret://`).

---

## The Eight Layers

| Layer | Verb | What it does | CLI group (leaf commands) | Core Subsystems |
|---|---|---|---|---|
| **Self-Expanding Tool System** | **EXPAND** | Mines containers/APIs, generates typed adapters, validates in Bubblewrap sandbox, and registers via transactional outbox. | `tools`, `tools-hub`, `tools-core`, `registry`, `docker`, `mcp`, `image` (62) | `toolbuilder.py`, `docker_runtime.py`, `container_adapter.py`, `adapter_mcp.py` |
| **Computer Use & Display Bridges** | **ACT** | Drives real and virtual desktops with verified visual/DOM evidence; isolates live sessions. | `act`, `cu`, `vision` (9) | `cu_platform.py`, `input_backend.py`, `evdev_keys.py`, `text_keys.py`, `browser_layer.py` |
| **Learned Composite Tools** | **COMPOSE** | Combines multi-step CLI, Docker, API, and GUI actions into validated, callable pipelines. | `compose`, `skills`, `tools`, `workflows` (18) | `compose_service.py`, `step_runners.py`, `workflows.py` |
| **Model Routing & Decisions** | **ROUTE** | Multi-tier model selection (fast, smart, coder, vision), token streaming, and Markdown rendering. | `jev`, `context`, `model`, `provider` (10) | `llm_provider.py`, `model_picker.py`, `streaming.py`, `stream_render.py` |
| **Supervisor & Agent Loop** | **SUPERVISE** | Multi-turn agent loop, two-bar chatbar, 3-pane curses TUI, and loop hallucination circuit breakers. | `voice`, `repl`, `chat`, `tui` (6) | `agent_loop.py`, `core_capabilities.py`, `agent_toolset.py`, `repl.py`, `tui.py` |
| **Goal-Locked Execution** | **ENFORCE** | 4-stage strict completion gates, self-certification shortcut detection, in-process subagent delegation. | `completion`, `agent`, `run`, `goal`, `subagents` (26) | `goal_run.py`, `completion.py`, `subagents.py`, `runtime_delegation.py`, `system_one.py` |
| **Real-World Verification** | **VERIFY** | Proves end-user functionality against real user flows, build artifacts, and test suites (PASS/FAIL/UNKNOWN). | `verify`, `eval`, `adversarial`, `flows` (12) | `verify_service.py`, `verify_flows.py`, `gates.py`, `evidence.py` |
| **Long-Run Memory & Security** | **PERSIST** | Durable SQLite FTS5 knowledge, JevComp token compaction, Europe/Berlin Cron scheduler, Voice Guard, and approval ladders. | `runs`, `session`, `checkpoint`, `memory`, `scheduler`, `approval` (66) | `memory.py`, `context_manager.py`, `scheduler.py`, `toolcall.py`, `approval_gate.py` |

---

## Cross-Platform Linux Desktop & Computer Use

Skaldir provides native Computer Use (ACT) integration across modern Linux distributions:

- **Fedora (GNOME 45+ / Wayland + Xwayland)**:
  - Synthetic input via `ydotoold` / Linux kernel `uinput` (`evdev_keys.py`, `text_keys.py`).
  - Xwayland root capture fallback: captures largest viewable client on `BadMatch`.
  - Offline session inspection: decodes `recovery.jsonlz4` (pure-Python MozLZ4) to identify Wayland browser tabs.
- **Omarchy (Arch Linux + Hyprland + Quickshell)**:
  - Native JSON IPC inspection (`hyprctl clients -j`, `hyprctl activewindow -j`).
  - Native dispatch (`hyprctl dispatch focuswindow`, `wtype`, `wl-copy` clipboard injection).
  - 1:1 logical coordinate screenshot capture via `grim -s 1`.
- **Debian / Ubuntu (X11 / GNOME)**:
  - Standard `xdotool`, `wmctrl`, `xprop`, and `scrot` execution.
- **Browser Automation Subsystem**:
  - Headless/headed Chrome DevTools Protocol (`agent-browser`) with `@ref` accessibility tree snapshots and auto-dismissal of cookie/consent modals.

---

## Installation & Quickstart

Requires **Python ≥ 3.11**.

### 1. Install via `uv` (Recommended)

```bash
# Clone the repository
git clone https://github.com/bertus37987/skaldir.git
cd skaldir

# Install globally as a CLI tool
uv tool install --force .

# Or set up an isolated development virtualenv
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,mcp]"

skaldir --version   # skaldir 1.5.0
```

### 2. Configure Providers

```bash
skaldir setup           # Interactive wizard: sets up model endpoints & keys
skaldir setup --check   # Verified exit 0 when provider credentials respond
```

### 3. Launch

```bash
# Interactive Chat (status band, two bars, slash palette)
skaldir

# Autonomous Goal Run (runs until strictly verified)
skaldir run "Create a Docker tool for SearXNG web search"

# Run in auto-read mode (automatically approves safe read-only tools)
skaldir run "Inspect workspace and refactor tests" --approval-mode auto_read

# HTTP & MCP API Daemon
skaldir serve --http
```

---

## Approval Modes & Security Guard

Control operator intervention during agent execution using `--approval-mode`:

| Mode | Flag | Behavior |
| :--- | :--- | :--- |
| **`interactive`** | `--approval-mode interactive` | Prompts operator before every state-altering or executable tool call (default). |
| **`auto_read`** | `--approval-mode auto_read` | Automatically executes safe read-only tools (`read_text`, `list_dir`, `search_files`, `web_search`, `tools_search`). Prompts for mutating actions. |
| **`autonomous`** | `--approval-mode autonomous` | Unassisted execution guarded by Voice Guard invariants. |

*Safety Overrides (APR-02)*: Financial spend operations (`purchase`, `order`), irreversible actions (`rm -rf`, `drop table`), and Voice Guard security refusals (`toolcall.py`) **can never be bypassed** in any mode.

---

## Acceptance, Audit & Verification Matrix

Full requirement-to-evidence traceability across all Masterplan IDs, acceptance checks, and golden suites:

- **Technical Handbook:** [`docs/CODEBASE_DOCUMENTATION.md`](docs/CODEBASE_DOCUMENTATION.md)
- **Feature Matrix:** [`docs/audit/DROID-FEATURE-MATRIX.md`](docs/audit/DROID-FEATURE-MATRIX.md)
- **Acceptance Report:** [`docs/audit/DROID-ACCEPTANCE.md`](docs/audit/DROID-ACCEPTANCE.md)
- **CLI Reference:** [`docs/CLI-REFERENCE.md`](docs/CLI-REFERENCE.md)
- **Operating Runbook:** [`docs/RUNBOOK.md`](docs/RUNBOOK.md)

Quick verification commands:
```bash
skaldir selftest --json
pytest tests/test_toolcall*.py tests/test_approval*.py tests/test_golden_set.py -q
```

---

## Repository Layout

```
src/skaldir/      The core package: CLI, contract, registry, 8 layers, JEV, treasury
prompts/          Versioned system prompts and prompt fragments
tests/            Pytest suite (321 test modules)
docs/             CODEBASE_DOCUMENTATION.md, INSTALL.md, ARCHITECTURE.md, CLI-REFERENCE.md
tools/            Code generation, audit scripts, and assembly utilities
integrations/     Host integrations (Hermes plugin adapter, MCP server)
docker/           Docker-based tool extensions (e.g. searxng-search, market-research)
```

---

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
