#!/usr/bin/env python3
"""Assembles docs/CODEBASE_DOCUMENTATION.md from subagent technical outputs."""

import json
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent

    # 1. Load Layer 1-4 from Subagent 1 transcript
    sub1_path = Path("/home/willy/.gemini/antigravity/brain/2ea71ea8-1ecf-4c7d-8790-b38ba9dd61fe/.system_generated/logs/transcript_full.jsonl")
    l14_text = ""
    with open(sub1_path, "r", encoding="utf-8") as f:
        for line in reversed(f.readlines()):
            data = json.loads(line)
            if data.get("tool_calls"):
                for tc in data["tool_calls"]:
                    if tc.get("name") == "send_message":
                        l14_text = tc["args"].get("Message", "")
                        break
                if l14_text:
                    break

    # 2. Load Layer 5-8 from Subagent 2 artifact
    sub2_path = Path("/home/willy/.gemini/antigravity/brain/b54674b5-9c46-4217-a0d2-bca417e92bea/CODEBASE_DOCUMENTATION_LAYERS_5_TO_8.md")
    with open(sub2_path, "r", encoding="utf-8") as f:
        l58_text = f.read()

    # Clean Layer 1-4 header
    p1_clean = l14_text
    if "# Skaldir Architecture & Codebase Documentation: Layers 1 to 4" in p1_clean:
        p1_clean = p1_clean.replace("# Skaldir Architecture & Codebase Documentation: Layers 1 to 4", "")
    p1_clean = p1_clean.strip()
    if p1_clean.startswith("This document provides a comprehensive"):
        parts = p1_clean.split("---\n", 1)
        if len(parts) > 1:
            p1_clean = parts[1].strip()

    # Clean Layer 5-8 header
    p2_clean = l58_text
    if "# Skaldir Technical Architecture & Implementation Documentation" in p2_clean:
        p2_clean = p2_clean.replace("# Skaldir Technical Architecture & Implementation Documentation", "")
    if "## Layers 5–8 and Core Subsystems" in p2_clean:
        p2_clean = p2_clean.replace("## Layers 5–8 and Core Subsystems", "")
    p2_clean = p2_clean.strip()
    if p2_clean.startswith("---"):
        p2_clean = p2_clean[3:].strip()

    header = """# Skaldir Agent: Comprehensive Codebase & Architecture Handbook

> **Distribution:** `skaldir-agent` · **Executable:** `skaldir` · **Version:** `1.5.0` · **Contract:** `schema_version 1.0`  
> **Source Directory:** `src/skaldir` · **Test Suite:** `tests/` (321 test modules, 288+ unit & golden tests)  
> **Repository:** [https://github.com/bertus37987/skaldir](https://github.com/bertus37987/skaldir)

---

## Executive Summary & System Vision

Skaldir is an autonomous, self-learning agent runtime designed for high-stakes software engineering, systems administration, and end-to-end computer use. Built on strict invariant-driven engineering, Skaldir repudiates "hallucinated completion" and unbounded tool proliferation. It operates under four fundamental operational guarantees:

1. **No Success Without Evidence:** An agent loop, tool execution, or verification routine reporting `ok: true` without an attached cryptographic evidence ledger is downgraded to exit code 7 (internal error).
2. **Deterministic Exit Codes & Structured Envelopes:** All CLI commands output a unified JSON envelope on `stdout` (`schema_version`, `ok`, `command`, `duration_ms`, `exit_code`, `data`, `error`, `evidence`) and partition human logs to `stderr`. The 9 exit codes (0–8) strictly separate usage errors, verification failures, security blocks, credential needs, and internal issues.
3. **Hardware-Isolated Computer Use:** Computer use and desktop driving operate within isolated display bridges (`Xvfb`), bubblewrap namespaces, and rootless containers. Live desktop interactions require explicit, scoped permissions with on-screen visual fencing indicators.
4. **Three-Tier Policy & Voice Guard:** Unsupervised mutations are checked prior to execution against secret path signatures, production markers, and verification disablement patterns. Three operational approval modes (`interactive`, `auto_read`, `autonomous`) govern operator intervention.

---

## Master Architecture: The 8 Operational Layers

```mermaid
graph TD
    subgraph Layer 1: EXPAND [Layer 1: EXPAND - Self-Expanding Tools]
        TB[ToolBuilder & Mining] --> CA[Container & MCP Adapters]
        CA --> TS[SQLite ToolStore & Transactional Outbox]
    end

    subgraph Layer 2: ACT [Layer 2: ACT - Computer Use & Display Bridges]
        CHS[cu_host_session] --> CP[cu_platform: Fedora / Omarchy / Debian]
        CP --> IB[input_backend & evdev/text_keys]
        CHS --> BL[browser_layer: agent-browser CDP]
    end

    subgraph Layer 3: COMPOSE [Layer 3: COMPOSE - Learned Composite Workflows]
        CS[compose_service] --> SR[StepRunners: tool / docker / api / gui]
        SR --> WF[workflows.py]
    end

    subgraph Layer 4: ROUTE [Layer 4: ROUTE - Provider & Stream Coalescence]
        MP[model_picker & roles] --> LP[llm_provider: OpenAICompatibleLLM]
        LP --> STR[streaming & stream_render MarkdownStream]
    end

    subgraph Layer 5: SUPERVISE [Layer 5: SUPERVISE - Agent Loop & Chat UI]
        AL[AgentLoop & ToolSet] --> REPL[repl & chatbar Two-Bar UI]
        AL --> TUI[tui_app 3-Pane Responsive Curses UI]
    end

    subgraph Layer 6: ENFORCE [Layer 6: ENFORCE - Goal Runs & Strict Completion]
        GR[goal_run] --> CR[completion.GateRunner: 4-Stage Gate]
        GR --> SA[subagents: In-Process Threading & Context Fences]
        GR --> JEV[system_one & jev Decision Layer]
    end

    subgraph Layer 7: VERIFY [Layer 7: VERIFY - Real-World Flow Verification]
        VS[verify_service & build_plan] --> VF[verify_flows: Browser & GUI Proofs]
        VS --> GATES[gates.py: Bubblewrap Isolation Gate]
    end

    subgraph Layer 8: PERSIST [Layer 8: PERSIST & SECURITY - Memory & Policy]
        MEM[memory & knowledge: SQLite FTS5] --> CM[context_manager & jevcomp]
        SCHED[scheduler & scheduler_worker: Europe/Berlin Cron]
        VG[Voice Guard: toolcall.py] --> AG[ApprovalGate: interactive/auto/always]
    end

    EXPAND --> ACT
    ACT --> COMPOSE
    COMPOSE --> ROUTE
    ROUTE --> SUPERVISE
    SUPERVISE --> ENFORCE
    ENFORCE --> VERIFY
    VERIFY --> PERSIST
```

---

## Table of Contents

- [Executive Summary & System Vision](#executive-summary--system-vision)
- [Master Architecture: The 8 Operational Layers](#master-architecture-the-8-operational-layers)
- [Part I: Capability Mining, Execution & Routing (Layers 1–4)](#part-i-capability-mining-execution--routing-layers-14)
  - [1. Layer 1: EXPAND (Self-Expanding Tool System)](#1-layer-1-expand-self-expanding-tool-system)
  - [2. Layer 2: ACT (Computer Use, OS Platforms & Browser Automation)](#2-layer-2-act-computer-use-os-platforms--browser-automation)
  - [3. Layer 3: COMPOSE (Learned Composite Tools)](#3-layer-3-compose-learned-composite-tools)
  - [4. Layer 4: ROUTE (Model Routing & Stream Rendering)](#4-layer-4-route-model-routing--stream-rendering)
- [Part II: Supervision, Enforcement, Verification & Security (Layers 5–8)](#part-ii-supervision-enforcement-verification--security-layers-58)
  - [5. Layer 5: SUPERVISE (Agent Loop, Core Toolset, REPL & TUI)](#5-layer-5-supervise-agent-loop-core-toolset-repl--tui)
  - [6. Layer 6: ENFORCE (Goal-Locked Runs, Strict Completion & Subagents)](#6-layer-6-enforce-goal-locked-runs-strict-completion--subagents)
  - [7. Layer 7: VERIFY (Real-World Flow Verification & Gates)](#7-layer-7-verify-real-world-flow-verification--gates)
  - [8. Layer 8: PERSIST & SECURITY (Memory, Compaction, Scheduler, Voice Guard & Approvals)](#8-layer-8-persist--security-memory-compaction-scheduler-voice-guard--approvals)
- [Part III: Cross-Platform Operating System & Display Matrix](#part-iii-cross-platform-operating-system--display-matrix)
- [Part IV: CLI Reference, Slash Verbs & Exit Codes](#part-iv-cli-reference-slash-verbs--exit-codes)
- [Part V: Developer & Contributor Guide](#part-v-developer--contributor-guide)

---
"""

    part3_matrix = """
---

## Part III: Cross-Platform Operating System & Display Matrix

Skaldir's Computer Use (ACT) layer abstracts disparate Linux display architectures into a unified behavioral protocol. The table below outlines platform-specific driver choices, input mechanisms, and fallbacks:

| Feature / Subsystem | Fedora Linux (GNOME / Mutter) | Omarchy Linux (Hyprland / wlroots) | Debian / Ubuntu (X11 / GNOME) |
| :--- | :--- | :--- | :--- |
| **Compositor / Display** | Wayland with active Xwayland (`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`) | Hyprland Wayland compositor (`HYPRLAND_INSTANCE_SIGNATURE`) | Native X11 (`DISPLAY=:0`) or Mutter Wayland |
| **Window Enumeration** | `xdotool search` + `gdbus org.gnome.Shell.Introspect` + Firefox/Zen MozLZ4 recovery | Native `hyprctl clients -j` and `hyprctl activewindow -j` (full Wayland inspection) | `wmctrl -lGpx` and `xdotool search` |
| **Window Activation** | `xdotool windowactivate` (Xwayland) or D-Bus ActivateWindow | `hyprctl dispatch focuswindow address:<id>` | `wmctrl -ia <wid>` |
| **Window Dismissal** | `xdotool windowclose` | `hyprctl dispatch closewindow address:<id>` | `wmctrl -ic <wid>` |
| **Keystroke Dispatch** | `ydotool` (uinput / `ydotoold`) with fallback to XTEST | `wtype` -> `hyprctl dispatch sendshortcut` -> `ydotool` | `xdotool key` via standard X11 XTEST |
| **Text Typing & Layout** | `text_keys.py`: XKB multi-group mapping + `ydotool key` scancodes | `wtype -- <text>` or clipboard injection (`wl-copy` + `ctrl+v`) | `xdotool type --delay 12` |
| **Screen Capture** | `xlib_capture.py`: Root window with Largest Viewable Xwayland fallback | `grim -s 1 <path>` (1:1 logical scale capture) | `scrot -z`, `maim`, or ImageMagick `import` |
| **Browser CDP Driver** | Isolated `--session skaldir` via CDP port & Playwright | Isolated `--session skaldir` via CDP port & Playwright | Isolated `--session skaldir` via CDP port & Playwright |
| **Safety Lease Overlay** | `desktop_indicator.py` (corner HUD + `Esc, Esc` breaker) | `desktop_indicator.py` or Quickshell notification | `desktop_indicator.py` |

---

## Part IV: CLI Reference, Slash Verbs & Exit Codes

### The Five Taught Entrypoints
`skaldir --help` prints exclusively the 5 fundamental commands. The rest of the surface is reachable via `/` palette in the chat or direct CLI flags:
```bash
skaldir                  # Interactive chat: status band, two bars, slash palette
skaldir setup            # Interactive credential & provider setup (OpenAI, Ollama, etc.)
skaldir run "<goal>"     # Autonomous goal-locked execution to verified pass
skaldir serve            # API surface for IDEs, editors, and external agents (--http)
skaldir --help           # Global help, --version, and --all-commands overview
```

### The 9 Deterministic Exit Codes
Every command in Skaldir terminates with an invariant-governed integer code:
- **`0` (OK):** Command completed successfully with verified evidence.
- **`1` (USAGE):** Command-line arguments invalid or required parameter missing.
- **`2` (VERIFICATION_FAILED):** Tests, criteria, or real-world assertions failed.
- **`3` (BLOCKED):** Operation blocked by policy, missing authorization, or unexecutable unknown check.
- **`4` (NEEDS_CREDENTIALS):** Required credential missing or unconfigured in vault.
- **`5` (NOT_FOUND):** Tool, session, checkpoint, or target entity does not exist.
- **`6` (CONFLICT):** Race condition, lease acquisition collision, or state conflict.
- **`7` (INTERNAL):** Unexpected runtime error or success reported without evidence.
- **`8` (UNKNOWN):** Unclassified system exception.

---

## Part V: Developer & Contributor Guide

### Environment Setup
Skaldir uses `uv` for high-speed package management:
```bash
# Clone the repository
git clone https://github.com/bertus37987/skaldir.git
cd skaldir

# Create and activate virtual environment
uv venv
source .venv/bin/activate

# Install in editable mode with development & MCP extras
uv pip install -e ".[dev,mcp]"

# Verify installation
skaldir --version
pytest tests/test_selftest.py -q
```

### Running Test Suites
```bash
# Run unit tests across toolcall and approval gates
pytest tests/test_toolcall*.py tests/test_approval*.py -v

# Run golden set verification tests
pytest tests/test_golden_set.py -q

# Run agent loop and lifecycle tests
pytest tests/test_agent_loop*.py -q
```

### Contributing Guidelines
1. **Preserve Documentation Integrity:** Do not delete docstrings or existing architectural notes unless correcting a documented bug.
2. **Strict Evidence Ledger:** Any new tool or CLI subcommand must return structured evidence in its outcome dictionary.
3. **No Privileged Containers:** All container extensions must run with unprivileged user permissions, `--network none` by default, and immutable SHA-256 digests.
"""

    full_doc = (
        header
        + "\n## Part I: Capability Mining, Execution & Routing (Layers 1–4)\n\n"
        + p1_clean
        + "\n\n## Part II: Supervision, Enforcement, Verification & Security (Layers 5–8)\n\n"
        + p2_clean
        + "\n"
        + part3_matrix
    )

    out_file = root / "docs" / "CODEBASE_DOCUMENTATION.md"
    out_file.write_text(full_doc, encoding="utf-8")
    print(f"Successfully generated {out_file} ({len(full_doc)} bytes, {len(full_doc.splitlines())} lines)")

if __name__ == "__main__":
    main()
