# Decisions — MiMo Plan Übernahmeentscheidungen

Stand: 2026-09-24.

## DEC-001: Knowledge-Store als eigenes Modul

Status: **angenommen**
Anforderungen: INFO-01, INFO-02, INFO-03, INFO-04
Ist-Befund: Kein Markdown-Wissensstore mit Metadaten. `notes.py` hat FTS5, aber keinen Scope/Kind/Status-Filter. `read_file` hat offset/limit, aber keine Versionsbindung.
Entscheidung: Neues `knowledge.py` mit SQLite-FTS5-Index + Markdown-Dateien unter `<runtime>/knowledge/`.
Begründung: Kleinster konsistenter Umbau. Bestehende `notes.py`/`research.py` bleiben unverändert (getrennte Zuständigkeit).
Validierung: `tests/test_knowledge.py` (23 Tests).

## DEC-002: `expected_revision` auf `read_file`

Status: **angenommen**
Anforderungen: INFO-03
Ist-Befund: `read_file` liefert `sha256`, prüft aber keine erwartete Revision.
Entscheidung: Optionaler `expected_revision`-Parameter; bei Mismatch `STALE_REFERENCE`.
Begründung: Kleine Erweiterung des bestehenden Vertrags. Kein neues Tool nötig (Plan: "Bestehende Tools wie `search`, `read_file` und `read_lines` können diesen Vertrag erfüllen").
Validierung: `tests/test_knowledge.py::TestINFO03VersionedReads`.

## DEC-003: Eigene `info_*`-Tools

Status: **verworfen**
Anforderungen: INFO-02, INFO-03
Begründung: `read_file` hat bereits offset/limit/sha256. `search_files` existiert. Die Lücke war Metadaten + Versionsbindung, nicht der Lesemechanismus. Der Knowledge-Store erfüllt den Vertrag über `KnowledgeStore.search()` und `KnowledgeStore.read_lines()`.
Alternative: `knowledge.py` (angenommen).

## DEC-004: Eigener Vektorindex

Status: **verworfen**
Anforderungen: INFO-02
Begründung: FTS5 in `knowledge.py` reicht für den Anwendungsfall. Semantische Suche erst bei messbarem Bedarf (Plan: "Semantische Suche ergänzen, wenn sie messbar zusätzliche relevante Treffer liefert").
Alternative: FTS5 (angenommen).

## DEC-005: Graphiti/Mem0/Letta als Runtime

Status: **verworfen**
Anforderungen: MEM-01
Begründung: `memory_store.py` hat bereits typisierte Einträge, Ranking, Contradiction-Handling, Consolidation. Eine zweite Memory-Runtime dupliziert Zuständigkeiten (Plan: "Für denselben Zustand genau eine maßgebliche Zuständigkeit").
Alternative: Bestehender `memory_store.py`.

## DEC-006: Persistenter Python-Kernel

Status: **verworfen**
Anforderungen: TOOL-03
Begründung: `code_exec.py` isoliert Child-Prozess + Socket-Pair. Ein persistenter Kernel wäre weniger sicher und kein Recovery-Speicher (Plan: "Ein persistenter Kernel kann Wiederverwendung erleichtern, ist aber kein Recovery-Speicher").
Alternative: `code_exec.py` (bestehend).

## DEC-007: RSI / Selbstmodifikation

Status: **zurückgestellt**
Anforderungen: RSI-01, RSI-02
Begründung: Prerequisites nicht erfüllt — keine belastbaren Run-Daten, keine ausführbare Bewertung, keine stabile Ausgangsversion mit klaren Änderungsgrenzen (Plan Stufe D: "Fehlen diese Voraussetzungen, liefere die nötigen Datenverträge und den genaueren offenen Schritt; aktiviere keine selbstverändernde Produktionsschleife").
Offener Schritt: Run-Daten sammeln, Evaluation definieren, dann RSI-01 implementieren.

## DEC-008: Provider-Capability-Flags

Status: **angenommen**
Anforderungen: CTX-02
Ist-Befund: Keine `supports_*`-Flags auf `ProviderConfig`.
Entscheidung: `supports_tool_discovery`, `supports_programmatic_tools`, `supports_explicit_cache_breakpoints`, `supports_context_compaction` als bool-Felder.
Begründung: Plan fordert "Nicht aus dem Modellnamen ableiten; anhand aktueller Provider-Verträge und vorhandener Adapter implementieren."
Validierung: Typprüfung (dataclass-Felder).
