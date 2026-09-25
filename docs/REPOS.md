# REPOS — Clone-Liste für Skaldir Agent

Stand: 11.09.2026 · Alle Einträge per GitHub-API verifiziert (Sterne/Aktivität/Lizenz). Regel: **Clone ≠ Fork.** Wir forken nur, was wir aktiv patchen müssen; alles andere wird als Dependency, Werkzeug oder Referenz eingebunden (Upstream-Updates!). Versionen pinnen, Upstream-Watch-Policy: monatlich.

**Rollen-Legende:** 🏛️ Fundament (wir bauen darauf/übernehmen Code) · 🧱 Baustein (Dependency/Integration) · 📖 Referenz (nur lesen/abschauen) · 🧪 Evaluation · 💳 Payment/Economy

---

## Woche-1-Clone-Set (das Fundament)

```bash
mkdir -p ~/projects/gods-voice/vendor && cd ~/projects/gods-voice/vendor

# 🏛️ Host & Basis — Skaldir Agent startet als Plugin/Daemon-Schicht auf Hermes.
git clone --depth 1 https://github.com/NousResearch/hermes-agent            # MIT · 244k★

# 🏛️ Minimaler Agent-Loop als Kern-Referenz + Benchmark-Baseline
git clone --depth 1 https://github.com/SWE-agent/mini-swe-agent             # MIT · 7.4k★

# 🧱 Event-/Agent-SDK (Event-Stream-Muster, saubere Abstraktionen)
git clone --depth 1 https://github.com/OpenHands/software-agent-sdk         # MIT (prüfen) · 1.1k★

# 🧱 Computer Use (Treiber, VM-Fleets, Benchmarks)
git clone --depth 1 https://github.com/trycua/cua                           # MIT · 22.5k★

# 🧱 Tool-Suiten/APIs als MCP-Server bauen
git clone --depth 1 https://github.com/PrefectHQ/fastmcp                    # Apache-2.0 · 27.6k★

# 🧱 Vektorindex direkt in SQLite (Memory)
git clone --depth 1 https://github.com/asg017/sqlite-vec                    # MIT/Apache (prüfen) · 8.1k★

# 🧱 Sandboxing: microVMs + bwrap
git clone --depth 1 https://github.com/superradcompany/microsandbox         # Apache-2.0 · 8.2k★
#    (bubblewrap via Paketmanager: containers/bubblewrap, 8.7k★)

# 📖 Referenz: Event-Log/Plugin-Kernel, so wie DSH es macht
git clone --depth 1 https://github.com/deepseek-ai/deepseek-harness         # MIT · 220k★
git clone --depth 1 https://github.com/cordiverse/cordis                    # MIT (prüfen) · 8.4k★
```

---

## Vollständige Liste nach Rolle

### 🏛️ Fundament / bauen darauf auf

| Repo | ★ | Stand | Lizenz | Rolle & Begründung |
|---|---|---|---|---|
| **NousResearch/hermes-agent** | 244k | aktiv | MIT | Host-Basis: hat Memory (bounded+frozen), Skills (agentskills.io), Curator, **Computer-Use-Toolset**, Browser, MCP (Client+Server), Hooks, Plugins, Desktop-App, Cron, Delegation, Credential-Vault/iron-proxy (Secrets-Injection!). Skaldir Agent v0 = Plugin + Daemon-Layer darauf; Auszug aus dem Host bleibt Option (nicht Fork). |
| **SWE-agent/mini-swe-agent** | 7.4k | aktiv | MIT | Minimaler Loop (bash-only, lineare History, >74 % SWE-bench verified). Referenz für den eigenen Kern + perfekte Baseline „ohne alles". |
| **OpenHands/software-agent-sdk** | 1.1k | aktiv | MIT (prüfen) | Event-/Agent-SDK aus dem OpenHands-Ökosystem (V1); Anschauungsmaterial für sauberes Event-Streaming. |
| **trycua/cua** | 22.5k | aktiv | MIT | „Computer-use 2.0": Treiber, Cross-OS-Fleets, Benchmarks — genau der Infrastruktur-Layer für THE HANDS statt Eigenbau. |

### 🧱 Bausteine (Dependencies)

| Repo | ★ | Stand | Lizenz | Rolle |
|---|---|---|---|---|
| **PrefectHQ/fastmcp** | 27.6k | aktiv | Apache-2.0 | MCP-Server/-Clients in Python bauen → THE FORGE (Tool-Suiten). |
| **browser-use/browser-use** | 114k | aktiv | MIT | Browser-Agent-Layer; wir nutzen eher das Konzept + eigene Playwright-Pipeline. |
| **microsoft/playwright** | 96k | aktiv | Apache-2.0 | Browser-Automation-Standard (DOM/A11y-first). |
| **Kaliiiiiiiiii-Vinyzu/patchright-python** | 1.5k | aktiv | Apache-2.0 | Undetected Playwright — **nur** wo legitime Logins an Bot-Detection scheitern; ToS-Konflikt ehrlich abwägen. |
| **daijro/camoufox** | 11.8k | aktiv | MPL-2.0 | Anti-Detect-Firefox (MPL = file-level Copyleft beachten). Gleiche ToS-Warnung. |
| **steel-dev/steel-browser** | 7.6k | aktiv | Apache-2.0 (prüfen) | Browser-Sandbox-API selbst hosten (Alternative zu Cloud-Browsern). |
| **microsoft/OmniParser** | 25k | aktiv | **CC-BY-4.0** (Modell; Nutzung prüfen!) | Screen-Parsing (Vision→Elemente) für Desktop-Computer-Use-Fallback. |
| **bytedance/UI-TARS-desktop** | 38.9k | aktiv | Apache-2.0 (prüfen) | Multimodaler GUI-Agent-Stack; UI-TARS-Modelle als spezialisierter Grounder. |
| **asg017/sqlite-vec** | 8.1k | ruhig (05/26) | MIT/Apache | Vektor-Suche in SQLite (Memory). |
| **Textualize/textual** | 37k | ruhig (07/26) | MIT | TUI-Framework. |
| **tauri-apps/tauri** | 111k | aktiv | MIT/Apache | Desktop (M5). |
| **BerriAI/litellm** | 58.5k | aktiv | MIT (prüfen) | Model-Gateway (100+ Provider, Rust-Core). |
| **containers/bubblewrap** | 8.7k | aktiv | LGPL-2.1+ | Sandbox (Flatpak-Technologie), trivial & schnell. |
| **superradcompany/microsandbox** | 8.2k | aktiv | Apache-2.0 | microVM-Runtime (stärkere Isolation für Computer-Use-Test-VMs). |

### 🧠 Memory (Ideen + Selektiv-Übernahme; wir bauen eigenes 5-Schicht-Memory)

| Repo | ★ | Stand | Lizenz | Rolle |
|---|---|---|---|---|
| **letta-ai/letta** | 24.7k | aktiv | Apache-2.0 | Memory-Blocks, Sleep-Time-Agents — Referenz für Night Shift. |
| **mem0ai/mem0** | 65k | aktiv | Apache-2.0 (prüfen) | Extraction/Update-Pipeline, Chats-Memory — Vergleichsmaßstab. |
| **getzep/graphiti** | 30.8k | aktiv | Apache-2.0 | Temporaler Wissensgraph — Option für Konflikt-/Zeitlogik in L4. |

### 🧪 Evaluation

| Repo/Seite | ★ | Stand | Rolle |
|---|---|---|---|
| **xlang-ai/OSWorld** | 3.1k | aktiv | Desktop-Computer-Use-Benchmark (Original + Verified). |
| **OSWorld 2.0** — osworld-v2.xlang.ai | — | 06/2026 | Long-Horizon-Nachfolger (108 Workflows; Bestwert ~31 % binär, 08/2026). Unsere Hands-Kern-Eval. |
| **harbor-framework/harbor** | 5.1k | aktiv | „Framework for evaluating and improving agents" — Evaluations-Infrastruktur. |
| **harbor-framework/terminal-bench** (+ -1/-2/-2.1) | 0.4–2.6k | aktiv | Terminal-Agent-Benchmark-Familie. |
| **SWE-bench/SWE-bench** | 5.8k | aktiv | Der Klassiker; Subset für Regressionstests. |
| *(Referenz)* stanford-iris-lab/meta-harness-tbench2-artifact | 1.2k | 03/2026 | „Meta-Harness: 76,4 % auf Terminal-Bench 2.0" — Beleg, dass Harness-Arbeit messbare Gewinne bringt. |

### 💳 Payment/Economy (THE TREASURY, ab M5)

| Repo | ★ | Stand | Rolle |
|---|---|---|---|
| **google-agentic-commerce/AP2** | 3.2k | aktiv | Agent Payments Protocol (Checkout-/Payment-Mandate, VDCs). Goldpfad für Stufe 2. |
| **coinbase/x402** | 152 | aktiv | HTTP-natives Bezahlen (inkl. Stablecoins) — zweiter Pfad. |
| **coinbase/agentkit** | 1.3k | aktiv | „Every agent deserves a wallet" — Wallets mit Limits (nur wenn Krypto-Pfad, getrennte Wallet-Regel!). |

---

## Was wir bewusst NICHT clonen

- **Kein Fork von Hermes/OpenClaw/DSH:** Wir bleiben Upstream-kompatibel (Ausnahme: einzelne Patches als Upstream-PRs). Strategie: Plugin/Daemon-Schicht + MCP statt Hard-Fork.
- **Keine schweren Agent-Frameworks** (LangChain-Größe): widerspricht der mini-SWE-Lehre (schlanker Kern).
- **Keine Cloud-Browser-Abhängigkeit** (Browserbase & Co.) im Kern — optional später als Provider.
- **Kein OmniParser-Code als Kern** ohne Lizenz-Klärung (CC-BY-4.0 ≠ Code-Lizenz; Modellgewichte separat bewerten).

## Empfehlung
1. Woche 1: das Clone-Set oben (9 Repos, alles MIT/Apache/microVM).
2. Beim Bau von THE HANDS: cua + Playwright zuerst, UI-TARS/OmniParser als Benchmark-Experimente (M4).
3. THE FORGE: fastmcp + MCP-Registry; Benchmarks der eigenen Suiten gegen Ökosystem-Tools.
4. Treasury: AP2-Repo lesen (Spec + Samples) — Integration erst M5/M6.
