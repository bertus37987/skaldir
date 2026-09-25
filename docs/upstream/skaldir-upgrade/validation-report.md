# Validierungsbericht — MiMo Plan

Geprüfte Codeversion: `godsv-supervisor-hardening` (dirty tree, 2026-09-24)
Umgebung und Backends: Linux X11 (Xvfb), rootless podman (container-gui), bwrap sandbox
Modelle/Provider und Einstellungen: LLM-Provider via `llm_provider.py` (Tier small/mid/main)

## Änderungen

| Anforderungs-ID | Umsetzung | Codepfad | Status |
|---|---|---|---|
| INFO-01 | KnowledgeEntry + KnowledgeStore (SQLite FTS5 + Markdown) | `src/skaldir/knowledge.py` | implementiert |
| INFO-02 | `KnowledgeStore.search()` mit Scope/Kind/Status-Filter | `src/skaldir/knowledge.py` | implementiert |
| INFO-03 | `KnowledgeStore.read_lines()` + `read_file` `expected_revision` | `knowledge.py` + `tools_core/files.py` | implementiert |
| INFO-04 | `is_stale`, `valid_until`, `verified_at` auf KnowledgeEntry | `src/skaldir/knowledge.py` | implementiert |
| CTX-02 | `supports_*` Flags auf ProviderConfig | `src/skaldir/llm_provider.py` | implementiert |
| Phase 0 | 5 Regressionen repariert | `agent_loop_cli.py`, `chatbar.py`, `cu_host_session.py`, `subagents.py`, `subagent_roles.py`, `tools_core/__init__.py` | implementiert |

## Ausgeführte Prüfungen

| Test/Befehl | Umgebung | Ergebnis | Evidence |
|---|---|---|---|
| `pytest tests/test_knowledge.py` | isoliert | **23 passed** | INFO-01..04 |
| `pytest tests/test_knowledge.py tests/test_tools_core_tools.py` | isoliert | **77 passed** | INFO-03 + Regression |
| `pytest tests/test_chat_check.py tests/test_chat_screen.py tests/test_cu_live_desktop.py tests/test_subagents.py tests/test_shared_integration_16.py tests/test_chat_presentation.py tests/test_repl_wiring.py tests/test_computer_use_task.py tests/test_security_audit_fixes.py` | isoliert | **181 passed** | Phase 0 |
| `pytest tests/test_env_leak_guard.py tests/test_no_orphan_modules.py tests/test_rename_compat.py tests/test_knowledge.py` | isoliert | **59 passed** | env-leak + orphan + rename |
| `pytest tests -q -o addopts='' -p no:randomly` | Fullsuite | **7527 passed, 31 skipped, 3 failed** | Die 3 Failures sind die env-leak/orphan/rename Tests von `knowledge.py` — danach gefixt |

## Verworfene oder angepasst

| Vorschlag | Grund | Alternative | Prüfung |
|---|---|---|---|
| Eigene `info_*`-Tools | `read_file`/`search_files` erfüllen den Vertrag | `knowledge.py` | Plan §02 |
| Eigener Vektorindex | FTS5 reicht | FTS5 in `knowledge.py` | Plan §02 |
| Graphiti/Mem0/Letta | Dupliziert Zuständigkeit | `memory_store.py` | Plan §07 |
| Persistenter Python-Kernel | Weniger sicher | `code_exec.py` | Plan §03 |
| RSI/Selbstmodifikation | Prerequisites fehlen | Datenverträge in `decisions.md` | Plan §05 |
| `live_desktop_optin` erfordert Switch+Target | Owner-Rule "ANY opt-in = FULL desktop" gewinnt | `allowed = allowed_switch` | `test_cu_live_desktop.py` |

## Offene Punkte

| Fehlende Voraussetzung | Betroffener Ablauf | Nächster konkreter Schritt |
|---|---|---|
| TOOL-01: Tool-Discovery als AgentTool | Agent-Loop hat kein `tool_search` | `discovery.py:discover()` als AgentTool wrappen |
| CTX-01: Ledger mit Referenzen | Kontextwechsel verliert Referenzen | `evidence_refs`/`knowledge_refs` zum Ledger-State |
| CTX-03: Read-Dedup | Identische `read_file`-Aufrufe | Dict-Cache `(path, sha256) → result` |
| ACT-04: Re-observe nach Timeout | Crash-Reconciliation unvollständig | `check_end_state` Re-observe bei `outcome_unknown` |
| SKILL-01: Strukturierte Skill-Contracts | Keine Param-Schema | `preconditions`/`parameters`/`verification` auf `SkillCandidate` |
| RSI-01/02 | Prerequisites fehlen | Run-Daten sammeln, Evaluation definieren |
| Knowledge CLI wiring | `knowledge.py` hat keinen Product-Caller | `knowledge` CLI-Gruppe registrieren |
