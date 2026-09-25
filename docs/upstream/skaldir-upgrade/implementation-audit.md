# Implementation Audit — MiMo Plan Gap-Matrix

Stand: 2026-09-24. Codeversion: `godsv-supervisor-hardening` (dirty tree).

## Gap-Matrix

| ID | Status | Codebeleg | Laufzeitbeleg | Entscheidung |
|---|---|---|---|---|
| AUD-01 | vorhanden | `runs_store.py:524-697`, `events.py:25-155` | Fullsuite grün | Behalten |
| OBS-01 | vorhanden | `events.py:25-155`, `telemetry.py:355` | `telemetry.py` Tests | Behalten |
| INFO-01 | **implementiert** | `knowledge.py` (KnowledgeEntry, KnowledgeStore) | `test_knowledge.py:23 tests` | Neu |
| INFO-02 | **implementiert** | `knowledge.py:search()` (FTS5) | `test_knowledge.py::TestINFO02Search` | Neu |
| INFO-03 | **implementiert** | `knowledge.py:read_lines()` + `tools_core/files.py` `expected_revision` | `test_knowledge.py::TestINFO03VersionedReads` | Erweitert |
| INFO-04 | **implementiert** | `knowledge.py` `is_stale`, `valid_until`, `verified_at` | `test_knowledge.py::TestINFO04Freshness` | Erweitert |
| MEM-01 | vorhanden | `memory_store.py`, `incidents.py`, `notes.py`, `ledger.py` | `test_memory_*.py` | Behalten |
| MEM-02 | vorhanden | `incidents.py:87` `failed_strategies` | `test_memory_retrieval.py` | Behalten |
| TOOL-01 | teilweise | `discovery.py:884` existiert, kein AgentTool | — | Zurückgestellt |
| TOOL-02 | vorhanden | `tool_archive.py`, `TruncationRule`, `read_archive` | `test_tool_archive.py` | Behalten |
| TOOL-03 | vorhanden | `code_exec.py` | `test_code_exec*.py` | Behalten |
| CTX-01 | teilweise | `ledger.py` | — | Zurückgestellt |
| CTX-02 | **implementiert** | `llm_provider.py` `supports_*` Flags | — | Erweitert |
| CTX-03 | fehlt | — | — | Zurückgestellt |
| ROUTE-01 | vorhanden | `modelroles.py`, `budget.py`, `provider_catalog.py` | — | Behalten |
| VOICE-01 | vorhanden | `pause.py`, `voice.py`, `voice_service.py` | — | Behalten |
| ACT-01 | vorhanden | `cu_platform.py:1046` (6 Adapter) | `test_cu_platform.py` | Behalten |
| ACT-02 | vorhanden | `act_service.py:17-28` | `test_act_service.py` | Behalten |
| ACT-03 | vorhanden | `check_end_state` (`act_service.py:1286`) | `test_act_service.py` | Behalten |
| ACT-04 | teilweise | `reap_orphaned_displays`, `runs_store.resume` | — | Zurückgestellt |
| SKILL-01 | teilweise | `skills.py:38-58` | `test_skills.py` | Zurückgestellt |
| SKILL-02 | vorhanden | `relevant_skills()`, promote/demote | `test_skills.py` | Behalten |
| LEARN-01 | vorhanden | `mine_tool_patterns()` | `test_capability_learning.py` | Behalten |
| RSI-01 | nicht anwendbar | — | — | Zurückgestellt (Prerequisites fehlen) |
| RSI-02 | nicht anwendbar | — | — | Zurückgestellt (Prerequisites fehlen) |

## Phase 0: Reparierte Regressionen

| Test | Root-Cause | Fix |
|---|---|---|
| `test_agent_tool_progress_drives_the_live_row` | `_chat_progress` druckte keine `_human`-Lines bei `chat_output=True` | `_human`-Lines nur wenn `front` vorhanden |
| `test_the_band_keeps_a_real_footer_that_streamed_past` | `_band_numbers` hardcodete `ctx n/a` bei Cold Start | Early-Return wenn kein Turn/Reading/`_saw_turn` |
| `test_the_band_says_n_a_only_after_a_run_reported_nothing` | dieselbe | dieselbe |
| `test_an_opt_in_without_a_target_is_full_desktop` | `live_desktop_optin` verlangte Switch+Target | `allowed = allowed_switch` (Owner-Rule) |
| `test_no_import_happens_while_the_children_run` | Lazy-Imports brachen Zero-Import-Invariant | `sys.modules`-Lookup |

## Neue Artefakte

| Datei | Umfang | Tests |
|---|---|---|
| `src/skaldir/knowledge.py` | ~380 Zeilen: KnowledgeEntry, KnowledgeStore (FTS5), search, read_lines, freshness | `tests/test_knowledge.py` (23) |
| `src/skaldir/tools_core/files.py` | `expected_revision` Parameter auf `read_file` | in `test_tools_core_tools.py` |
| `src/skaldir/llm_provider.py` | `supports_*` Flags auf `ProviderConfig` | — |
