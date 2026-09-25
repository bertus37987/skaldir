# Quellen & Recherche-Notizen — Stand 11.09.2026

Methodik: Alle Einträge wurden am 11.09.2026 tatsächlich abgerufen (Doku-Seiten über Jina-Reader,
Papers über die arXiv-API, Seiten direkt). Der Standard-Such-Backend (Firecrawl) war an dem Tag mit
403 blockiert → Ersatz: `tools/bing_search.py` und `tools/arxiv_search.py` in diesem Repo.
Bing-Ergebnisse waren teils unzuverlässig; Kernfakten wurden deshalb immer direkt an der Quelle
(Doku/Papier) verifiziert.

## 1. Die Referenz-Harnesse

- **Hermes Agent** — Docs: https://hermes-agent.nousresearch.com/docs/
  - Memory: /user-guide/features/memory (MEMORY.md 2.200 Zeichen, USER.md 1.375, frozen snapshot, session_search FTS5, Security-Scanning, Memory-Provider-Plugins)
  - Skills: /user-guide/features/skills (Progressive Disclosure, agentskills.io-kompatibel, /learn, Skill-Bundles, Write-Approval-Gate)
  - Curator: /user-guide/features/curator (Nutzung, Staleness, Archiv)
  - llms.txt als Doku-Index: /docs/llms.txt
- **OpenClaw** — https://docs.openclaw.ai/
  - Releases: /releases → **2.0 = v2026.8.1** (30.08.2026; 16.977 PRs)
  - Memory: /releases/2026.8.1/memory + /concepts/memory (Built-in Memory Core, Cross-Conversation-Recall, provenienz-gated, **Dreaming**/Dream-Diaries, `openclaw memory forget` mit Provenienz-Purge)
  - Skills: /releases/2026.8.1/skills + /tools/skill-workshop (**Skill Workshop**: Proposals, Scanner inkl. Injection-Check, Grader, Rollback, auto/propose/off, Weekly Collection Review, Same-Turn-Repair; „Learn from past conversations"; 2026.9.4: „turn past conversations into reusable skills")
  - Architektur: /concepts/architecture (ein Gateway-Daemon, typed WebSocket-Protokoll, Nodes/Pairing)
  - Wikipedia: https://en.wikipedia.org/wiki/OpenClaw (Historie Warelay→Clawdbot→Moltbot→OpenClaw; Foundation 501(c)(3); Steinberger→OpenAI; ~247k Stars Stand 03/2026; Security-Scrutiny)
- **DeepSeek Harness** — https://www.deepseek.com/harness/en/ (Dev Preview, MIT)
  - „Everything is a plugin" (Cordis-Kernel): models/tools/skills/sessions/sandboxes/storage/loops/scheduling/UI
  - **Append-only Session-Log inkl. jeder Kontext-Injektion; Trajectory-View; resume/fork/search/replay**
  - GitHub: github.com/deepseek-ai/deepseek-harness · Docs: deepseek-harness.github.io · Cordis-Paper: arXiv 2608.25512
- **Claude Code** — Docs: https://docs.claude.com/en/docs/claude-code/memory
  - CLAUDE.md-Hierarchie (Managed/User/Projekt/Lokal, Imports, `.claude/rules/` path-scoped)
  - **Auto-Memory** (erste 200 Zeilen/25 KB, pro Repo, von Claude selbst geschrieben; Subagents mit eigenem Memory)
- **mini-SWE-agent** — https://github.com/SWE-agent/mini-swe-agent
  - ~100 Zeilen, bash-only, lineare History, >74 % SWE-bench verified; „beats Claude Code and Codex on DeepSWE" (Betreiber-Claim, deepswe.datacurve.ai)
- **OpenHands** — arXiv 2407.16741 · github.com/OpenHands/OpenHands (Event-Stream-Architektur)

## 2. Memory-Papers

- MemGPT — arXiv 2310.08560
- Sleep-time Compute — arXiv 2504.13171 + Blog letta.com/blog/sleep-time-compute (zweiter Agent pflegt Memory asynchron; „anytime"-Updates; stärkeres Modell im Sleep-Agent)
- Mem0 — arXiv 2504.19413
- Zep (temporaler Wissensgraph) — arXiv 2501.13956
- A-MEM (Zettelkasten-artige Notizen) — arXiv 2502.12110
- D-MEM (Dopamin-Gating, Importance) — arXiv 2603.14597
- „Is Agent Memory a Database?" — arXiv 2605.26252
- E-mem — arXiv 2601.21714 · V-Mem — arXiv 2608.01543 · CogMem — arXiv 2512.14118

## 3. Lernen aus Trajektorien / Skills / Tools

- Reflexion — arXiv 2303.11366
- Voyager (Skill-Library wächst autonom) — arXiv 2305.16291
- Agent Workflow Memory — arXiv 2409.07429
- **ReasoningBank** (verifizierte Reasoning-Memory, self-evolving) — arXiv 2509.25140
- ExpeRepair (Dual-Memory Repo-Repair) — arXiv 2506.10484
- SWE-Exp („memoryless explorer" als Problem) — arXiv 2507.23361
- EET (erfahrungsbasiertes Früh-Abbrechen, Kostenhebel) — arXiv 2601.05777
- MemGovern (lernt aus gelösten GitHub-Issues) — arXiv 2601.06789
- SWE-MeM (adaptives Memory-Management) — arXiv 2606.28434
- Strukturiertes Memory für Code-Agenten — arXiv 2603.13258 · Closed-Loop-Memory — arXiv 2606.05646
- ReVeal (Selbst-Verifikation vor Lernen) — arXiv 2506.11442
- Tool-Erstellung + Cognitive Memory — arXiv 2512.11303
- LATM (LLMs as Tool Makers) — arXiv 2305.17126
- Agentic Test-Time Training — arXiv 2607.03441

## 4. Verifikation & Selbstkorrektur

- CRITIC (tool-gestütztes Kritisieren) — arXiv 2305.11738
- Self-Refine — arXiv 2303.17651
- **„LLMs Cannot Self-Correct Reasoning Yet"** — arXiv 2310.01798 (Begründung für Evidenzpflicht des Mentors)
- Key-Condition-Verification — arXiv 2405.14092

## 5. Benchmarks & Evaluation

- LongMemEval — arXiv 2410.10813
- **LongMemEval-V2** („toward experienced colleagues"; recurring failure modes) — arXiv 2605.12493
- Long-Horizon-Terminal-Bench — arXiv 2607.08964
- Hack-Verifiable Terminal Bench (Reward-Hacking) — arXiv 2608.22103
- Terminal-Bench-LILT (mehrsprachig) — arXiv 2608.28641
- „Do Agent Optimizers Compound?" (Continual-Learning-Eval auf Terminal-Bench 2.0) — arXiv 2607.14004
- Regimes/ActiveGraph (auditierbare Improvement-Loops) — arXiv 2606.10241
- SWE-bench Verified / ProgramBench — swebench.com, mini-swe-agent-Docs

## 6. Security (Prompt-Injection & Privacy)

- Spotlighting — arXiv 2403.14720
- Tool-Result-Parsing-Defense — arXiv 2601.04795
- System-Level-Defenses (Position Paper) — arXiv 2603.30016
- Injection-Benchmark — arXiv 2312.14197
- Privacy-Survey — arXiv 2606.26627

## 7. Surveys & Sonstiges

- „From Storage to Experience" (Memory-Mechanismen) — arXiv 2605.06716
- „Agent Memory in the Second Half" — arXiv 2602.06052
- MultiSessionCollab (User-Präferenzen über Sessions) — arXiv 2601.02702
- CRISP (Critical Steps in Search-Agents) — arXiv 2608.01867

## 8. Bausteine & Standards (geplant)

- agentskills.io-Spezifikation (Skill-Format) · MCP/Model Context Protocol · sqlite-vec · SQLite FTS5 ·
  Textual (TUI) · Tauri 2 (Desktop) · LiteLLM (Provider-Abstraktion) · bubblewrap/firejail (Sandbox) ·
  Cordis (Plugin-Kernel, github.com/cordiverse/cordis)

## 9. Lokale Artefakte dieser Recherche

- `tools/bing_search.py` — Keyless-Bing-Suche (Fallback wg. Firecrawl-403)
- `tools/arxiv_search.py` — Batch-arXiv-Suche
- Rohdigests: `/tmp/research_pass1.md`, `/tmp/arxiv_pass.md`, `/tmp/arxiv_pass2.md`
- Quellen-Snapshots: `/tmp/f_*.txt` (Festplatte; bei Bedarf ins Repo kopieren)

## 10. Ausbaustufe 2 — Nachrecherche (11.09.2026)

**Computer Use / Evaluation:**
- OSWorld (Original): https://os-world.github.io/ — 369 Tasks; Paper-Stand: Mensch ~72,36 % vs. bestes Modell 12,24 %; „OSWorld-Verified"-Update 28.07.2025; Trajektorien auf HuggingFace (`xlangai/ubuntu_osworld_verified_trajs`).
- **OSWorld 2.0**: https://osworld-v2.xlang.ai/ — 108 Long-Horizon-Workflows, Mensch-Median ~1,6 h, ~318 Tool-Calls/Task (vs. ~30 in v1); Leaderboard (08.08.2026): best 31,4 % binär / 68,3 % partiell (Claude Opus 5 max); dokumentierte Agenten-Fehler: Constraints verlieren, Mid-Task-Infos verpassen, raten statt fragen, Verifikation überspringen, versteckten Zustand nicht rekonstruieren. → Validierung für THE VOICE.
- Harbor (Eval-Framework): github.com/harbor-framework/harbor · Terminal-Bench-Familie: harbor-framework/terminal-bench(-1/-2/-2.1) · Meta-Harness-Artifact (76,4 % TB 2.0): stanford-iris-lab/meta-harness-tbench2-artifact.

**Agent-Payments:**
- **AP2 (Agent Payments Protocol)**: https://ap2-protocol.org/ — Google, angekündigt 09/2025, v0.2, Standardisierung bei der FIDO Alliance; Verifiable Digital Credentials (Checkout-/Payment-Mandate, offen/geschlossen); Prinzip „Verifiable Intent, Not Inferred Action"; Repo: github.com/google-agentic-commerce/AP2. Integration mit A2A (a2a-protocol.org) und UCP (ucp.dev).
- x402 (HTTP-Payments): github.com/coinbase/x402 · Coinbase AgentKit: github.com/coinbase/agentkit.

**Repo-Verifikation (sternegrob, Stand 11.09.2026, via GitHub-API):** NousResearch/hermes-agent 244k · deepseek-ai/deepseek-harness 220k · openai/codex 123k · browser-use/browser-use 114k · tauri-apps/tauri 111k · microsoft/playwright 96k · OpenHands/OpenHands 87k · mem0ai/mem0 65k · BerriAI/litellm 58.5k · bytedance/UI-TARS-desktop 38.9k · Textualize/textual 37k · getzep/graphiti 30.8k · PrefectHQ/fastmcp 27.6k · microsoft/OmniParser 25k · letta-ai/letta 24.7k · Skyvern-AI/skyvern 23k · trycua/cua 22.5k · daijro/camoufox 11.8k · containers/bubblewrap 8.7k · cordiverse/cordis 8.4k · superradcompany/microsandbox 8.2k · asg017/sqlite-vec 8.1k · steel-dev/steel-browser 7.6k · SWE-agent/mini-swe-agent 7.4k · SWE-bench/SWE-bench 5.8k · harbor-framework/harbor 5.1k · google-agentic-commerce/AP2 3.2k · xlang-ai/OSWorld 3.1k · harbor-framework/terminal-bench-1 2.6k · Kaliiiiiiiiii-Vinyzu/patchright-python 1.5k · coinbase/agentkit 1.3k · OpenHands/software-agent-sdk 1.1k · coinbase/x402 152. Lizenzen stichprobenartig geprüft (MIT/Apache überwiegend; OmniParser CC-BY-4.0 beachten).
- Methodik: Firecrawl weiter 403 (11.09.2026) → Recherche über agent-reach (gh CLI, Jina Reader), Repo-Checks direkt via GitHub-API.
